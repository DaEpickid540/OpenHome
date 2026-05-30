"""
openHome Voice Service
───────────────────────
Runs on the Pi alongside the hub. Handles audio in/out for listener satellites.

PIPELINE:
  Listener (ESP32/Pi Zero) → audio bytes → WebSocket /voice/stream
    → faster-whisper STT (local, runs on Pi 4 CPU)
      → POST to hub /ai/command with transcript
        → NOVA returns actions + spoken response
          → Piper TTS (local, custom voice)
            → WAV bytes → back over WebSocket
              → Listener plays audio

WHY THESE TOOLS:
  faster-whisper — CTranslate2-optimized Whisper. tiny.en model runs at ~1x
    real-time on a Pi 4. Free, local, no API.
  Piper           — fast neural TTS by Rhasspy. ~70MB model files. Has 100+
    voices, all open-source, OR train your own.
  No ElevenLabs, no Google, no OpenAI calls.

CUSTOM VOICE:
  See voices/README.md for training your own Piper voice from ~30 min of
  recordings. Or grab any of the prebuilt voices from
  https://huggingface.co/rhasspy/piper-voices

INSTALL:
  pip install faster-whisper piper-tts websockets soundfile numpy httpx
  Download a Piper voice:
    mkdir -p voices && cd voices
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
    wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
"""

import asyncio
import io
import json
import os
import wave
from pathlib import Path

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
try:
    from speaker_id import identify_speaker
    _HAS_SPEAKER_ID = True
except ImportError:
    _HAS_SPEAKER_ID = False
from fastapi.responses import Response

try:
    from secrets_config import API_KEY
except ImportError:
    API_KEY = "CHANGE_ME_RUN_GEN_KEYS"

# ── CONFIG ────────────────────────────────────────────────
WHISPER_MODEL    = "tiny.en"        # tiny.en | base.en | small.en
WHISPER_DEVICE   = "cpu"
WHISPER_COMPUTE  = "int8"           # int8 is plenty for command parsing
PIPER_VOICE_DIR  = Path(__file__).parent / "voices"
PIPER_DEFAULT    = "en_US-amy-medium"   # change to your voice filename (without .onnx)
HUB_BASE         = "http://localhost:8765"
SAMPLE_RATE      = 16000            # 16kHz mono PCM throughout

# Lazy load so the hub boots fast even if voice deps aren't installed
_whisper = None
_piper   = None

def _load_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        print(f"[VOICE] Loading Whisper {WHISPER_MODEL}…")
        _whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                compute_type=WHISPER_COMPUTE)
        print("[VOICE] Whisper ready")
    return _whisper

def _load_piper(voice_name: str = None):
    """Loads (or switches) the Piper voice. voice_name = file basename."""
    global _piper, _piper_name
    name = voice_name or PIPER_DEFAULT
    if _piper is not None and _piper_name == name:
        return _piper
    from piper import PiperVoice
    model_path = PIPER_VOICE_DIR / f"{name}.onnx"
    if not model_path.exists():
        raise FileNotFoundError(f"Piper voice not found: {model_path}. "
                                f"Download from huggingface.co/rhasspy/piper-voices")
    print(f"[VOICE] Loading Piper voice {name}…")
    _piper = PiperVoice.load(str(model_path))
    _piper_name = name
    print("[VOICE] Piper ready")
    return _piper

_piper_name = None


# ── STT ───────────────────────────────────────────────────
def transcribe_pcm16(pcm_bytes: bytes) -> str:
    """Takes raw 16-bit PCM mono @ 16kHz, returns text."""
    import numpy as np
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) < SAMPLE_RATE * 0.3:   # <0.3s = ignore
        return ""
    model = _load_whisper()
    segments, _ = model.transcribe(audio, language="en", beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip()


# ── TTS ───────────────────────────────────────────────────
def synthesize_wav(text: str, voice: str = None) -> bytes:
    """Generates a WAV file (bytes) speaking the text."""
    if not text:
        return b""
    p = _load_piper(voice)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        p.synthesize(text, wf)
    return buf.getvalue()


# ── HUB CALL ──────────────────────────────────────────────
async def ask_aria(transcript: str, speaker: dict = None) -> dict:
    """Send the transcript to the AI brain, get actions + reply text."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Use the existing /ai/command endpoint
        r = await client.post(f"{HUB_BASE}/ai/command",
            json={"text": transcript, "voice": True, "speaker": speaker or {}},
            headers={"X-OpenHome-Key": API_KEY})
        return r.json()


def craft_spoken_reply(transcript: str, response: dict) -> str:
    """
    Builds a short natural reply from the AI's action list.
    Kept deterministic so the listener feels snappy even on small models.
    """
    actions = response.get("actions", [])
    if not actions:
        return "Sorry, I'm not sure what to do with that."

    # Prefer a notify action's message if present
    for a in actions:
        if a.get("action") == "notify" and a.get("message"):
            return a["message"]

    # Otherwise summarize what was done
    kinds = [a.get("action") for a in actions]
    if "all_lights" in kinds:
        modes = [a.get("mode") for a in actions if a.get("action") == "all_lights"]
        if "off" in modes:   return "Lights off."
        if "alert" in modes: return "Lockdown engaged."
        return "Lights on."
    if "set_thermostat" in kinds:
        for a in actions:
            if a.get("action") == "set_thermostat":
                if a.get("setpoint"):
                    return f"Thermostat set to {int(a['setpoint'])} degrees."
                if a.get("mode"):
                    return f"Thermostat switched to {a['mode']}."
    if "control_device" in kinds:
        ons  = sum(1 for a in actions if a.get("action")=="control_device" and a.get("state")=="on")
        offs = sum(1 for a in actions if a.get("action")=="control_device" and a.get("state")=="off")
        if ons and not offs:  return f"Turned on {ons} device{'s' if ons>1 else ''}."
        if offs and not ons:  return f"Turned off {offs} device{'s' if offs>1 else ''}."
        return "Done."
    if "set_light_mode" in kinds:
        return "Lights updated."
    return "Done."


# ── WEBSOCKET ENDPOINT ────────────────────────────────────
voice_router = APIRouter()

@voice_router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket):
    """
    Listener satellites connect here. Protocol:

      Client → Server: binary frames of raw PCM16 16kHz audio
                       (the satellite VAD-clips before sending)
      Client → Server: text frame {"end_of_utterance": true} when done

      Server → Client: text frame {"transcript": "..."}
      Server → Client: text frame {"reply": "..."}
      Server → Client: binary frame: WAV bytes of the spoken reply
      Server → Client: text frame {"done": true}
    """
    await ws.accept()
    # Optional: check API key in query string for ESP32 simplicity
    key = ws.query_params.get("key", "")
    if key != API_KEY:
        await ws.close(code=4401)
        return

    voice_pref = ws.query_params.get("voice", None)
    audio_buf  = bytearray()

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                audio_buf.extend(msg["bytes"])
            elif "text" in msg and msg["text"]:
                ctrl = json.loads(msg["text"])
                if ctrl.get("end_of_utterance"):
                    # Run the full pipeline
                    transcript = transcribe_pcm16(bytes(audio_buf))
                    # Speaker identification
                    speaker = {"user_id": "unknown", "user_name": "unknown", "identified": False}
                    if _HAS_SPEAKER_ID and audio_buf:
                        import io, wave
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as wf:
                            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
                            wf.writeframes(bytes(audio_buf))
                        speaker = identify_speaker(buf.getvalue())
                    await ws.send_text(json.dumps({"transcript": transcript, "speaker": speaker}))
                    if transcript:
                        reply_data = await ask_aria(transcript, speaker=speaker)
                        spoken     = craft_spoken_reply(transcript, reply_data)
                        await ws.send_text(json.dumps({"reply": spoken,
                                                       "actions": reply_data.get("actions", [])}))
                        wav = synthesize_wav(spoken, voice_pref)
                        await ws.send_bytes(wav)
                    await ws.send_text(json.dumps({"done": True}))
                    audio_buf = bytearray()
                elif ctrl.get("cancel"):
                    audio_buf = bytearray()
    except WebSocketDisconnect:
        pass


# ── HTTP TTS (for the satellite to fetch TTS without WS, optional) ─────
@voice_router.post("/voice/tts")
async def http_tts(payload: dict):
    text  = payload.get("text", "")
    voice = payload.get("voice")
    wav = synthesize_wav(text, voice)
    return Response(content=wav, media_type="audio/wav")


@voice_router.get("/voice/voices")
async def list_voices():
    """Lists installed Piper voices."""
    if not PIPER_VOICE_DIR.exists():
        return {"voices": [], "default": PIPER_DEFAULT, "dir": str(PIPER_VOICE_DIR)}
    names = sorted(p.stem for p in PIPER_VOICE_DIR.glob("*.onnx"))
    return {"voices": names, "default": PIPER_DEFAULT}
