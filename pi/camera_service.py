"""
openHome Camera Service
────────────────────────
Live video from ESP32-CAM cameras → Pi → your PC's HDD.

WHAT IT DOES:
  - Receives MJPEG streams from ESP32-CAM devices
  - Records to your PC's HDD via SMB/NFS mount, or via the hub endpoint
    that your PC pulls from
  - Motion-triggered recording (only saves when something is happening)
  - Circular buffer: keeps last N hours, auto-deletes old footage
  - Live view endpoint: dashboard embeds a live MJPEG stream

STORAGE:
  Video is stored as individual MP4 clips (via ffmpeg) or raw MJPEG segments.
  Recommended: mount your PC's HDD as a network share on the Pi:

    # On your PC (Linux):
    sudo apt install samba
    # share /mnt/cameras drive as "cameras"

    # On the Pi:
    sudo mkdir /mnt/cameras
    sudo mount -t cifs //YOUR_PC_IP/cameras /mnt/cameras -o guest

    # Set RECORDING_DIR below to /mnt/cameras

  Or just use the Pi's local SD card (lower capacity, fine for 24-48h clips).

INSTALL:
  sudo apt install ffmpeg
  pip install av     # optional, for ffmpeg binding (or just use subprocess)

ESP32-CAM STREAMING:
  The camera_stream ESP32 sketch (esp32/camera_stream/) runs a continuous
  MJPEG HTTP server. The Pi pulls from it.
"""

import asyncio
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse

import storage
# Import from security.py rather than re-deriving here: it fails closed
# (refuses to boot) if secrets_config.py is missing, instead of silently
# using a public placeholder key.
from security import API_KEY

# ── CONFIG ────────────────────────────────────────────────
RECORDING_DIR     = Path(os.environ.get("RECORDING_DIR", Path.home() / "openhome-video"))
CLIP_DURATION_MIN = 10       # new clip every N minutes
KEEP_HOURS        = 48       # delete footage older than N hours
MOTION_ONLY       = True     # only record when motion is detected
STREAM_QUALITY    = 10       # JPEG quality 1-100 (lower = smaller, faster)

camera_router = APIRouter()


# ── CAMERA REGISTRY ───────────────────────────────────────
# Always-on ESP32-CAM devices register normally with the hub.
# We track their stream URLs here.
_streams: dict = {}   # device_id → {"ip": ..., "port": ..., "recording": bool}


def register_camera(device_id: str, ip: str, port: int = 81):
    """Called when a camera device registers with the hub."""
    _streams[device_id] = {
        "device_id": device_id,
        "ip": ip,
        "port": port,
        "stream_url": f"http://{ip}:{port}/stream",
        "recording": False,
        "last_frame": None,
    }
    print(f"[CAM] Registered {device_id} at {ip}:{port}")


def get_cameras() -> dict:
    return _streams


# ── LIVE PROXY (dashboard embeds this) ────────────────────
async def _mjpeg_proxy(stream_url: str) -> AsyncGenerator[bytes, None]:
    """Pull MJPEG from ESP32-CAM and forward to browser."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("GET", stream_url,
                                 headers={"X-OpenHome-Key": API_KEY}) as resp:
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                yield chunk


@camera_router.get("/cameras/{device_id}/live")
async def live_stream(device_id: str):
    """MJPEG live stream endpoint. Embed in dashboard as <img src='/cameras/.../live'>"""
    if device_id not in _streams:
        return Response("camera not found", status_code=404)
    stream_url = _streams[device_id]["stream_url"]
    return StreamingResponse(
        _mjpeg_proxy(stream_url),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@camera_router.get("/cameras")
async def list_cameras():
    return list(_streams.values())


@camera_router.get("/cameras/{device_id}/recordings")
async def list_recordings(device_id: str, hours: int = 24):
    """List recorded clips for a camera in the last N hours."""
    cam_dir = RECORDING_DIR / device_id
    if not cam_dir.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    clips = []
    for f in sorted(cam_dir.glob("*.mp4"), reverse=True):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime > cutoff:
            clips.append({
                "file": f.name,
                "size_mb": round(f.stat().st_size / 1e6, 1),
                "created": mtime.isoformat(),
                "url": f"/cameras/{device_id}/recordings/{f.name}"
            })
    return clips


# ── RECORDING ENGINE ──────────────────────────────────────
async def _record_clip(device_id: str, duration_s: int):
    """Record a single clip from an ESP32-CAM using ffmpeg."""
    cam = _streams.get(device_id)
    if not cam: return

    cam_dir = RECORDING_DIR / device_id
    cam_dir.mkdir(parents=True, exist_ok=True)

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = cam_dir / f"{ts}.mp4"

    # ffmpeg pulls the MJPEG stream and encodes to H.264 MP4
    cmd = [
        "ffmpeg", "-y",
        "-i", cam["stream_url"],
        "-t", str(duration_s),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        str(out)
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=duration_s + 30)
        print(f"[CAM] Recorded {out.name} ({out.stat().st_size // 1024}KB)")
    except Exception as e:
        print(f"[CAM] Recording failed for {device_id}: {e}")
        # asyncio.wait_for's timeout only cancels the await, not the ffmpeg
        # child itself — without this it keeps running detached forever.
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if out.exists(): out.unlink()


async def _record_loop(device_id: str):
    """Continuous recording loop for a camera."""
    while True:
        try:
            if MOTION_ONLY:
                # Check if there's been recent motion near this camera
                alerts = storage.get("alerts")
                recent_motion = any(
                    a.get("type") == "motion" and
                    a.get("device_id", "").replace("motion_", "") in device_id and
                    (datetime.now() - datetime.fromisoformat(a["timestamp"])).seconds < 300
                    for a in alerts[-20:]
                    if a.get("timestamp")
                )
                if not recent_motion:
                    await asyncio.sleep(30)
                    continue

            _streams[device_id]["recording"] = True
            await _record_clip(device_id, CLIP_DURATION_MIN * 60)
            _streams[device_id]["recording"] = False
            _prune_old_recordings(device_id)
        except Exception as e:
            # This loop is launched via create_task() and never awaited —
            # an uncaught exception here (bad timestamp, unlink permission
            # error, etc.) would kill recording for this camera permanently
            # and silently. Log and keep the loop alive instead.
            print(f"[CAM] _record_loop error for {device_id}: {e}")
            await asyncio.sleep(30)


def _prune_old_recordings(device_id: str):
    """Delete recordings older than KEEP_HOURS."""
    cam_dir = RECORDING_DIR / device_id
    if not cam_dir.exists(): return
    cutoff = time.time() - (KEEP_HOURS * 3600)
    for f in cam_dir.glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"[CAM] Pruned {f.name}")


_recording_tasks: dict = {}

def start_recording(device_id: str):
    """Start continuous recording for a camera."""
    if device_id not in _recording_tasks:
        task = asyncio.create_task(_record_loop(device_id))
        _recording_tasks[device_id] = task
        print(f"[CAM] Recording started for {device_id}")

def stop_recording(device_id: str):
    task = _recording_tasks.pop(device_id, None)
    if task: task.cancel()
    if device_id in _streams:
        _streams[device_id]["recording"] = False

def start_all_cameras():
    """Called on hub startup to begin recording all registered cameras."""
    for device_id in _streams:
        start_recording(device_id)
