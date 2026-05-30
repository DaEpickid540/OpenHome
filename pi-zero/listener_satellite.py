#!/usr/bin/env python3
"""
openHome Listener Satellite — Pi Zero 2 W version
──────────────────────────────────────────────────
Higher-quality alternative to the ESP32-S3 listener. Use this if you want:
  - A real touch screen UI (HyperPixel, Waveshare, etc.)
  - Better mic quality (USB conference mic, ReSpeaker HAT)
  - A wake word ("hey aria") instead of push-to-talk
  - Camera/AI vision in the future

HARDWARE:
  - Raspberry Pi Zero 2 W ($15)
  - USB or I2S microphone
  - USB or I2S speaker / 3.5mm audio out
  - Optional: 2-4" touch screen

WHY PI ZERO INSTEAD OF ESP32:
  - Whisper too big for ESP32 PSRAM, so STT/TTS always happen on the main hub
  - This satellite is "just" a thin client (audio in/out + UI)
  - Pi Zero handles audio routing, wake word, and a GUI much more easily

INSTALL on the Pi Zero:
  sudo apt install python3-pip portaudio19-dev
  pip install sounddevice websocket-client numpy openwakeword
  python3 listener_satellite.py

THIS DOES NOT RUN ON THE MAIN HUB. It runs on the Zero satellite.
"""

import asyncio
import json
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import websocket   # `pip install websocket-client`

# ── CONFIG ────────────────────────────────────────────────
HUB_IP    = os.environ.get("HUB_IP",   "192.168.1.100")
HUB_PORT  = int(os.environ.get("HUB_PORT", 8765))
API_KEY   = os.environ.get("OPENHOME_KEY", "CHANGE_ME")
DEVICE_ID = os.environ.get("DEVICE_ID", "listener_living_room")
LOCATION  = os.environ.get("LOCATION",  "living_room")
VOICE     = os.environ.get("VOICE",     "en_US-amy-medium")

SAMPLE_RATE      = 16000
CHUNK_SAMPLES    = 512
PLAYBACK_RATE    = 22050     # Piper output

# Wake word — uses openwakeword (pip install openwakeword).
# "hey_nova" is trained on the built-in models. Also works: "hey_jarvis", "alexa".
# Set to None for push-to-talk (Enter key) mode.
WAKE_WORD = "hey_nova"  # or None for PTT


# ── AUDIO RECORDING ───────────────────────────────────────
class Recorder:
    def __init__(self):
        self.q = queue.Queue()
        self.active = False
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK_SAMPLES, callback=self._cb)
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        if self.active:
            self.q.put(bytes(indata))

    def start(self): self.active = True
    def stop(self):  self.active = False
    def drain(self):
        chunks = []
        try:
            while True: chunks.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return chunks


# ── WEBSOCKET CLIENT ──────────────────────────────────────
class HubClient:
    def __init__(self):
        url = f"ws://{HUB_IP}:{HUB_PORT}/voice/stream?key={API_KEY}&voice={VOICE}"
        self.ws = websocket.WebSocketApp(url,
            on_open=self._open, on_message=self._msg,
            on_close=self._close, on_error=self._err)
        threading.Thread(target=self.ws.run_forever, daemon=True).start()
        self.connected = False
        self.audio_done = threading.Event()

    def _open(self, ws):
        self.connected = True
        print("[WS] connected")

    def _msg(self, ws, msg):
        if isinstance(msg, (bytes, bytearray)):
            # WAV audio — play it
            self._play_wav(msg)
        else:
            data = json.loads(msg)
            if "transcript" in data:
                print(f"[YOU] {data['transcript']}")
            if "reply" in data:
                print(f"[NOVA] {data['reply']}")
            if data.get("done"):
                self.audio_done.set()

    def _close(self, ws, *a): self.connected = False; print("[WS] closed")
    def _err(self, ws, e):    print(f"[WS] err: {e}")

    def _play_wav(self, wav_bytes):
        import io, wave
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            pcm  = wf.readframes(wf.getnframes())
        audio = np.frombuffer(pcm, dtype=np.int16)
        sd.play(audio, samplerate=rate)
        sd.wait()

    def send_audio_chunk(self, chunk: bytes):
        if self.connected: self.ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)

    def end_utterance(self):
        if self.connected: self.ws.send(json.dumps({"end_of_utterance": True}))


# ── REGISTER WITH HUB ─────────────────────────────────────
def register_with_hub():
    import requests, datetime
    payload = {
        "device_id": DEVICE_ID, "type": "listener_satellite", "location": LOCATION,
        "state": "idle", "severity": "none", "controllable": True,
        "writable": ["state", "voice", "volume", "screen_text"],
        "readable": ["last_transcript", "last_reply"],
        "voice": VOICE, "volume": 80,
        "ip": _local_ip(), "port": 80,
        "timestamp": datetime.datetime.now().isoformat()
    }
    body = json.dumps(payload).encode()
    import hmac, hashlib
    try:
        from secrets_config import HMAC_KEY
        sig = hmac.new(HMAC_KEY.encode(), body, hashlib.sha256).hexdigest()
    except ImportError:
        sig = ""
    try:
        requests.post(f"http://{HUB_IP}:{HUB_PORT}/register",
            data=body, timeout=5,
            headers={"Content-Type":"application/json",
                     "X-OpenHome-Key": API_KEY,
                     "X-OpenHome-Sig": sig})
        print(f"[HUB] registered as {DEVICE_ID}")
    except Exception as e:
        print(f"[HUB] register failed: {e}")


def _local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    finally:
        s.close()


# ── MAIN LOOP ─────────────────────────────────────────────
def main():
    register_with_hub()
    rec = Recorder()
    hub = HubClient()
    time.sleep(1)

    print("\n=== openHome listener ready ===")
    print("Press Enter to start/stop a recording (or Ctrl+C to quit)\n")

    if WAKE_WORD:
        _run_with_wake_word(rec, hub)
    else:
        _run_push_to_talk(rec, hub)


def _run_push_to_talk(rec, hub):
    try:
        while True:
            input("[idle] press Enter to talk: ")
            print("[listening] speak now…")
            rec.start()
            input("[recording] press Enter to stop: ")
            rec.stop()
            chunks = rec.drain()
            print(f"[sending] {sum(len(c) for c in chunks)} bytes")
            for c in chunks:
                hub.send_audio_chunk(c)
            hub.end_utterance()
            print("[thinking]")
            hub.audio_done.wait(timeout=60)
            hub.audio_done.clear()
            print()
    except KeyboardInterrupt:
        print("\nbye")


def _run_with_wake_word(rec, hub):
    """Wake word detection using openwakeword."""
    try:
        from openwakeword.model import Model as WakeModel
        ww = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    except ImportError:
        print("[WAKE] openwakeword not installed — pip install openwakeword")
        _run_push_to_talk(rec, hub)
        return
    except Exception as e:
        print(f"[WAKE] Failed to load wake word model: {e}")
        print(f"[WAKE] Falling back to push-to-talk")
        _run_push_to_talk(rec, hub)
        return

    # Constant stream for wake word detection
    WAKE_CHUNK = 1280  # 80ms @ 16kHz, required by openwakeword
    import queue
    wake_q: queue.Queue = queue.Queue()

    def _wake_cb(indata, frames, t, status):
        wake_q.put(bytes(indata))

    import sounddevice as sd
    wake_stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                                  blocksize=WAKE_CHUNK, callback=_wake_cb)
    wake_stream.start()
    print(f"[WAKE] Listening for '{WAKE_WORD}'…")

    WAKE_THRESHOLD = 0.7   # confidence threshold
    POST_WAKE_S    = 4.0   # record this many seconds after wake word

    try:
        while True:
            chunk = wake_q.get()
            import numpy as np
            audio_np = np.frombuffer(chunk, dtype=np.int16)
            ww.predict(audio_np)
            scores = ww.prediction_buffer.get(WAKE_WORD, [0])
            if scores and max(scores) > WAKE_THRESHOLD:
                print(f"[WAKE] Triggered! ({max(scores):.2f}) — recording {POST_WAKE_S}s…")
                # Drain wake queue, start real recording
                while not wake_q.empty():
                    try: wake_q.get_nowait()
                    except: break
                ww.reset()
                rec.start()
                time.sleep(POST_WAKE_S)
                rec.stop()
                chunks = rec.drain()
                if chunks:
                    for c in chunks:
                        hub.send_audio_chunk(c)
                    hub.end_utterance()
                    hub.audio_done.wait(timeout=60)
                    hub.audio_done.clear()
                print(f"[WAKE] Listening for '{WAKE_WORD}'…")
    except KeyboardInterrupt:
        wake_stream.stop()
        print("\nbye")


if __name__ == "__main__":
    main()
