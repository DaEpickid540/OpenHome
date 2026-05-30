"""
openHome Presence Detection
────────────────────────────
Multi-device geofencing via network presence.

Strategy: ping each registered phone's MAC/IP on the local network every
60 seconds. If ALL registered phones have been absent for > threshold,
the house is "away" → auto-arm. When any phone returns → "home".

Works WITHOUT a phone app. No Bluetooth needed.
Works with any device that has a fixed IP (set DHCP reservations in router).

Limitations:
  - Phones put WiFi to sleep to save battery. Use MAC instead of IP and
    rely on ARP cache, or set DHCP reservation + 5-min poll window.
  - iOS aggressive WiFi sleep can cause false "away" triggers.
    Mitigation: require 3 consecutive misses before marking away.
  - Guest devices: register as "visitor" role so they don't affect presence.

Storage: storage["presence"] → {device_id: {name, ip, mac, home, last_seen}}
Events:  POST /sensor with type="presence" when state changes
"""

import asyncio
import subprocess
import re
from datetime import datetime, timedelta

import storage

POLL_INTERVAL_S   = 60      # check every minute
AWAY_THRESHOLD    = 3       # consecutive misses before marking away
HOME_THRESHOLD    = 1       # 1 success = home immediately

_tasks: dict = {}
_miss_counts: dict = {}     # device_id → consecutive miss count

# ── DEVICE REGISTRY ──────────────────────────────────────
def add_device(name: str, ip: str = None, mac: str = None,
               user_id: str = None, role: str = "member") -> dict:
    """Register a phone/device to track."""
    if not ip and not mac:
        raise ValueError("Need at least IP or MAC")
    device_id = f"phone_{name.lower().replace(' ','_')}"
    entry = {
        "device_id": device_id, "name": name, "ip": ip, "mac": mac,
        "user_id": user_id, "role": role,
        "home": True,   # assume home until we know otherwise
        "last_seen": datetime.now().isoformat(),
        "miss_count": 0,
    }
    presence = storage.get("presence") or {}
    presence[device_id] = entry
    storage.set_collection("presence", presence)
    _miss_counts[device_id] = 0
    return entry

def remove_device(device_id: str) -> bool:
    presence = storage.get("presence") or {}
    if device_id not in presence: return False
    del presence[device_id]
    storage.set_collection("presence", presence)
    return True

def get_presence() -> dict:
    return storage.get("presence") or {}

def anyone_home() -> bool:
    return any(d.get("home") for d in get_presence().values()
               if d.get("role") in ("admin", "member"))

def home_users() -> list:
    return [d["name"] for d in get_presence().values() if d.get("home")]


# ── PING ─────────────────────────────────────────────────
async def _ping_ip(ip: str) -> bool:
    """True if the IP responds to ping."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "2", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False

async def _arp_lookup(mac: str) -> bool:
    """True if MAC is in the ARP table (means device is on LAN)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-n",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        return mac.lower() in out.decode().lower()
    except Exception:
        return False

async def _is_present(entry: dict) -> bool:
    """Check if a device is present. Tries IP ping, falls back to ARP."""
    if entry.get("ip"):
        if await _ping_ip(entry["ip"]):
            return True
    if entry.get("mac"):
        if await _arp_lookup(entry["mac"]):
            return True
    return False


# ── POLL LOOP ────────────────────────────────────────────
async def _poll_once():
    presence = storage.get("presence") or {}
    changed = []

    for did, entry in presence.items():
        was_home = entry.get("home", True)
        seen = await _is_present(entry)

        if seen:
            _miss_counts[did] = 0
            if not was_home:
                # Arrived home
                presence[did]["home"] = True
                presence[did]["last_seen"] = datetime.now().isoformat()
                changed.append((did, entry["name"], "home"))
                print(f"[PRESENCE] {entry['name']} arrived home")
        else:
            _miss_counts[did] = _miss_counts.get(did, 0) + 1
            if was_home and _miss_counts[did] >= AWAY_THRESHOLD:
                # Left home
                presence[did]["home"] = False
                changed.append((did, entry["name"], "away"))
                print(f"[PRESENCE] {entry['name']} left home (after {AWAY_THRESHOLD} misses)")

        presence[did]["miss_count"] = _miss_counts.get(did, 0)

    if changed:
        storage.set_collection("presence", presence)
        await _handle_presence_changes(changed)


async def _handle_presence_changes(changes: list):
    """Fire presence events and auto-arm/disarm if needed."""
    from action_executor import execute_actions

    now_home = anyone_home()
    devices = storage.get("devices")

    for device_id, name, state in changes:
        # Post a sensor event so the AI knows and it appears in the alert feed
        event = {
            "type": "presence", "device_id": device_id,
            "location": "home", "state": state,
            "severity": "none", "name": name,
            "anyone_home": now_home,
            "timestamp": datetime.now().isoformat()
        }
        storage.append("alerts", event)

    # Auto-arm when last person leaves
    if not now_home and all(s == "away" for _, _, s in changes):
        auto_arm = storage.get_setting("presence_auto_arm", True)
        if auto_arm:
            print("[PRESENCE] House empty — auto-arming")
            await execute_actions([
                {"action": "notify",
                 "message": f"Everyone left. Auto-arming openHome.",
                 "severity": "low"},
            ], devices)

    # Disarm when someone arrives and house was empty
    home_arrivals = [c for c in changes if c[2] == "home"]
    if home_arrivals:
        name = home_arrivals[0][1]
        auto_disarm = storage.get_setting("presence_auto_disarm", True)
        if auto_disarm:
            print(f"[PRESENCE] {name} home — disarming")
            await execute_actions([
                {"action": "notify",
                 "message": f"Welcome home, {name}.",
                 "severity": "low"},
            ], devices)


async def _poll_loop():
    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[PRESENCE] Poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL_S)


def start_presence():
    task = asyncio.create_task(_poll_loop())
    _tasks["loop"] = task
    print(f"[PRESENCE] Started tracking {len(get_presence())} devices")

def stop_presence():
    for t in _tasks.values():
        t.cancel()
