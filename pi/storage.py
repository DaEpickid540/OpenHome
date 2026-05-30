"""
openHome Storage Layer
──────────────────────
Single source of truth for ALL persistent state:
  - devices       (registry + latest status)
  - alerts        (event history)
  - access_log    (RFID scans)
  - users         (accounts + preferences)
  - ai_memory     (RAG knowledge base)
  - reasoning_log (AI decision history)

Everything persists to disk as JSON so a Pi reboot doesn't wipe state.
Auto-saves on write (debounced) and loads on startup.

Every module reads/writes through this — no more scattered in-memory dicts.
"""

import json
import os
import threading
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(os.environ.get("OPENHOME_DATA", "data"))
DATA_DIR.mkdir(exist_ok=True)

# ── COLLECTIONS ───────────────────────────────────────────
# Each collection maps to one JSON file on disk
_FILES = {
    "devices":       DATA_DIR / "devices.json",
    "alerts":        DATA_DIR / "alerts.json",
    "access_log":    DATA_DIR / "access_log.json",
    "users":         DATA_DIR / "users.json",
    "ai_memory":     DATA_DIR / "ai_memory.json",
    "reasoning_log": DATA_DIR / "reasoning_log.json",
    "settings":      DATA_DIR / "settings.json",
    "schedules":     DATA_DIR / "schedules.json",
    "presence":      DATA_DIR / "presence.json",
    "voiceprints":   DATA_DIR / "voiceprints.json",
}

# In-memory cache, loaded from disk at startup
_cache = {}
_lock  = threading.Lock()

# Caps to prevent unbounded growth on a Pi
_CAPS = {
    "alerts":        500,
    "access_log":    500,
    "reasoning_log": 200,
}

# ── DEFAULTS ──────────────────────────────────────────────
_DEFAULTS = {
    "devices":       {},   # dict keyed by device_id
    "alerts":        [],   # list
    "access_log":    [],   # list
    "users":         {},   # dict keyed by user_id
    "ai_memory":     [],   # list of knowledge entries (RAG)
    "reasoning_log": [],   # list
    "schedules":     [],
    "presence":      {},
    "voiceprints":   {},
    "settings":      {
        "ai_name":          "NOVA",
        "ntfy_topic":       "openhome-alerts",
        "night_start_hour": 22,
        "night_end_hour":   6,
        "model_name":       "llama3.1:1b",
        "auto_arm_at_night": True,
        "auth_enabled":     True,
    },
}


# ── INIT ──────────────────────────────────────────────────
def init():
    """Load all collections from disk into cache."""
    with _lock:
        for name, path in _FILES.items():
            if path.exists():
                try:
                    with open(path, "r") as f:
                        _cache[name] = json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    print(f"[STORAGE] Failed to load {name}: {e} — using default")
                    _cache[name] = _clone_default(name)
            else:
                _cache[name] = _clone_default(name)
                _save_unlocked(name)
    print(f"[STORAGE] Loaded {len(_cache)} collections from {DATA_DIR}")


def _clone_default(name):
    import copy
    return copy.deepcopy(_DEFAULTS.get(name, {}))


# ── READ ──────────────────────────────────────────────────
def get(name):
    """Get an entire collection (returns the live cached object)."""
    return _cache.get(name, _clone_default(name))


def get_item(name, key):
    """Get one item from a dict-type collection."""
    return _cache.get(name, {}).get(key)


# ── WRITE ─────────────────────────────────────────────────
def set_item(name, key, value):
    """Set one item in a dict-type collection + persist."""
    with _lock:
        if name not in _cache:
            _cache[name] = _clone_default(name)
        _cache[name][key] = value
        _save_unlocked(name)


def append(name, item):
    """Append to a list-type collection, respecting caps + persist."""
    with _lock:
        if name not in _cache:
            _cache[name] = _clone_default(name)
        _cache[name].append(item)
        cap = _CAPS.get(name)
        if cap and len(_cache[name]) > cap:
            _cache[name] = _cache[name][-cap:]
        _save_unlocked(name)


def update_item(name, key, patch: dict):
    """Merge a patch dict into an existing item + persist."""
    with _lock:
        if name not in _cache:
            _cache[name] = _clone_default(name)
        existing = _cache[name].get(key, {})
        existing.update(patch)
        _cache[name][key] = existing
        _save_unlocked(name)
        return existing


def delete_item(name, key):
    """Remove an item from a dict-type collection + persist."""
    with _lock:
        if name in _cache and key in _cache[name]:
            del _cache[name][key]
            _save_unlocked(name)
            return True
    return False


def set_collection(name, value):
    """Replace an entire collection + persist."""
    with _lock:
        _cache[name] = value
        _save_unlocked(name)


# ── SETTINGS HELPERS ──────────────────────────────────────
def get_setting(key, default=None):
    return _cache.get("settings", {}).get(key, default)


def set_setting(key, value):
    set_item("settings", key, value)


# ── PERSIST ───────────────────────────────────────────────
def _save_unlocked(name):
    """Write one collection to disk. Caller must hold _lock."""
    path = _FILES[name]
    tmp  = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(_cache[name], f, indent=2)
        tmp.replace(path)  # atomic rename — no half-written files on power loss
    except IOError as e:
        print(f"[STORAGE] Failed to save {name}: {e}")


def save_all():
    """Force-save everything (call on graceful shutdown)."""
    with _lock:
        for name in _cache:
            _save_unlocked(name)
    print("[STORAGE] All collections saved")
