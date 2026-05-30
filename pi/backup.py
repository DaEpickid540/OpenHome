"""
openHome Backup
────────────────
Automated daily backup of all hub state to a timestamped tar.gz.
Runs on a schedule; also callable on demand via POST /backup.

Backup includes: devices, alerts, access_log, users, ai_memory,
reasoning_log, schedules, presence, settings — everything in data/.

Default location: ~/openhome-backups/
Keep last 30 backups.
"""

import asyncio
import json
import os
import tarfile
from datetime import datetime, timedelta
from pathlib import Path

import storage

BACKUP_DIR   = Path(os.environ.get("BACKUP_DIR", Path.home() / "openhome-backups"))
KEEP_BACKUPS = int(os.environ.get("BACKUP_KEEP", 30))


def create_backup() -> str:
    """Create a timestamped tar.gz of all hub data. Returns the file path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"openhome_{ts}.tar.gz"

    # Write a fresh JSON dump alongside the raw data files
    dump = {
        "backup_ts":     datetime.now().isoformat(),
        "version":       "0.6.0",
        "devices":       storage.get("devices"),
        "alerts":        storage.get("alerts")[-500:],   # last 500
        "access_log":    storage.get("access_log")[-500:],
        "users":         storage.get("users"),
        "ai_memory":     storage.get("ai_memory"),
        "schedules":     storage.get("schedules") if "schedules" in storage._cache else [],
        "presence":      storage.get("presence") if "presence" in storage._cache else {},
        "settings":      storage.get("settings"),
    }
    dump_path = storage.DATA_DIR / "backup_manifest.json"
    with open(dump_path, "w") as f:
        json.dump(dump, f, indent=2)

    with tarfile.open(path, "w:gz") as tar:
        tar.add(storage.DATA_DIR, arcname="data")

    # Clean up manifest
    dump_path.unlink(missing_ok=True)

    # Prune old backups
    _prune()
    print(f"[BACKUP] Created {path} ({path.stat().st_size // 1024}KB)")
    return str(path)


def _prune():
    backups = sorted(BACKUP_DIR.glob("openhome_*.tar.gz"), reverse=True)
    for old in backups[KEEP_BACKUPS:]:
        old.unlink()
        print(f"[BACKUP] Pruned {old.name}")


def restore_backup(path: str) -> dict:
    """Restore hub data from a backup tar.gz. Overwrites current data."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "file not found"}
    with tarfile.open(p, "r:gz") as tar:
        tar.extractall(storage.DATA_DIR.parent)
    storage.init()   # reload from disk
    return {"ok": True, "restored_from": str(p)}


def list_backups() -> list:
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(BACKUP_DIR.glob("openhome_*.tar.gz"), reverse=True)
    return [{"file": b.name, "size_kb": b.stat().st_size // 1024,
             "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat()}
            for b in backups]


async def backup_loop():
    """Daily backup at 3am. Starts immediately with a first backup."""
    create_backup()   # backup on startup
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            create_backup()
        except Exception as e:
            print(f"[BACKUP] Failed: {e}")
