"""
openHome Speaker Identification
─────────────────────────────────
Identifies WHO is speaking before passing a command to ARIA.
Enables per-user preferences, personalized responses, and (eventually)
access control based on voice.

HOW IT WORKS:
  1. User says a command
  2. Resemblyzer extracts a 256-dim voice embedding from the audio
  3. We compare to enrolled embeddings for each household member
  4. Best match above threshold → identified user
  5. User context injected into ARIA's prompt ("Sarah just asked...")

ENROLLMENT:
  Each user speaks ~10 seconds of natural speech (not a fixed phrase).
  Average the embeddings → store as their voice print.
  5 enrollment clips per user gives reliable ID.

ACCURACY (realistic expectations):
  Same-household speakers: ~92% accuracy
  Cross-gender: >98%
  Twins or very similar voices: ~70-80% — use a PIN fallback
  Noise impact: performance drops in loud environments

INSTALL:
  pip install resemblyzer
  (pulls in torch automatically, ~800MB — worth it)

ALTERNATIVE (lighter, worse):
  speechbrain.pretrained.EncoderClassifier
  ~100MB model, ~85% accuracy
"""

import io
import json
import math
import os
import wave
from pathlib import Path

import numpy as np

try:
    from resemblyzer import VoiceEncoder, preprocess_wav
    _HAS_RESEMBLYZER = True
except ImportError:
    _HAS_RESEMBLYZER = False
    print("[VOICE_ID] resemblyzer not installed — pip install resemblyzer")

import storage

VOICEPRINT_DIR  = Path(__file__).parent / "voiceprints"
MATCH_THRESHOLD = 0.75   # cosine similarity threshold (0-1). Tune up for stricter ID.
UNKNOWN_USER    = "unknown"

_encoder = None

def _load_encoder():
    global _encoder
    if _encoder is None:
        print("[VOICE_ID] Loading Resemblyzer encoder...")
        _encoder = VoiceEncoder()
        print("[VOICE_ID] Ready")
    return _encoder


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    """Convert WAV bytes to float32 numpy array."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def _embed_audio(audio: np.ndarray) -> np.ndarray | None:
    """Return a 256-dim embedding or None if audio too short."""
    if not _HAS_RESEMBLYZER: return None
    if len(audio) < 16000 * 1:   # need at least 1 second
        return None
    enc = _load_encoder()
    wav = preprocess_wav(audio, source_sr=16000)
    return enc.embed_utterance(wav)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


# ── ENROLLMENT ────────────────────────────────────────────
def enroll_user(user_id: str, user_name: str, wav_clips: list[bytes]) -> dict:
    """
    Enroll a user with multiple WAV clips of their voice.
    wav_clips: list of WAV byte strings (5+ clips recommended, 5-15s each)
    """
    if not _HAS_RESEMBLYZER:
        return {"ok": False, "error": "resemblyzer not installed"}

    embeddings = []
    for wav_bytes in wav_clips:
        audio = _wav_to_float32(wav_bytes)
        emb   = _embed_audio(audio)
        if emb is not None:
            embeddings.append(emb)

    if not embeddings:
        return {"ok": False, "error": "all clips too short or failed"}

    # Average embedding = voice centroid
    voice_print = np.mean(embeddings, axis=0)

    VOICEPRINT_DIR.mkdir(exist_ok=True)
    os.chmod(VOICEPRINT_DIR, 0o700)  # voiceprints are biometric data — owner-only
    vp_path = VOICEPRINT_DIR / f"{user_id}.npy"
    np.save(vp_path, voice_print)
    os.chmod(vp_path, 0o600)

    # Store metadata
    vps = storage.get("voiceprints") if "voiceprints" in storage._cache else {}
    vps[user_id] = {"user_id": user_id, "user_name": user_name,
                    "clips_used": len(embeddings), "threshold": MATCH_THRESHOLD}
    storage.set_collection("voiceprints", vps)

    print(f"[VOICE_ID] Enrolled {user_name} with {len(embeddings)} clips")
    return {"ok": True, "user_id": user_id, "clips": len(embeddings)}


def identify_speaker(wav_bytes: bytes) -> dict:
    """
    Identify who is speaking in a WAV clip.
    Returns: {"user_id": str, "user_name": str, "confidence": float, "identified": bool}
    """
    if not _HAS_RESEMBLYZER:
        return {"user_id": UNKNOWN_USER, "identified": False, "confidence": 0.0}

    audio = _wav_to_float32(wav_bytes)
    query_emb = _embed_audio(audio)
    if query_emb is None:
        return {"user_id": UNKNOWN_USER, "identified": False, "confidence": 0.0}

    best_id, best_score = UNKNOWN_USER, 0.0
    vps = storage.get("voiceprints") if "voiceprints" in storage._cache else {}

    for user_id, meta in vps.items():
        vp_path = VOICEPRINT_DIR / f"{user_id}.npy"
        if not vp_path.exists(): continue
        enrolled = np.load(vp_path)
        score = _cosine(query_emb, enrolled)
        if score > best_score:
            best_score = score
            best_id    = user_id

    threshold = MATCH_THRESHOLD
    identified = best_score >= threshold
    user_name  = vps.get(best_id, {}).get("user_name", UNKNOWN_USER) if identified else UNKNOWN_USER

    return {
        "user_id":    best_id if identified else UNKNOWN_USER,
        "user_name":  user_name,
        "confidence": round(best_score, 3),
        "identified": identified,
    }


def list_enrolled() -> list:
    vps = storage.get("voiceprints") if "voiceprints" in storage._cache else {}
    return list(vps.values())

def remove_voiceprint(user_id: str) -> bool:
    vps = storage.get("voiceprints") if "voiceprints" in storage._cache else {}
    if user_id not in vps: return False
    del vps[user_id]
    storage.set_collection("voiceprints", vps)
    p = VOICEPRINT_DIR / f"{user_id}.npy"
    if p.exists(): p.unlink()
    return True
