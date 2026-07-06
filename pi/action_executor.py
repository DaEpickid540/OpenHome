"""
openHome Action Executor
────────────────────────
Takes an action list from ai_brain.py and executes each one
against the real device registry. Handles failures gracefully.
"""

import httpx
import asyncio
import json
import time
from datetime import datetime
from typing import Callable

import storage
from security import compute_hmac
from zigbee_bridge import send_zigbee_command

try:
    from secrets_config import API_KEY
except ImportError:
    API_KEY = "CHANGE_ME_RUN_GEN_KEYS"

# Every outbound command is timestamped and HMAC-signed over the exact
# body bytes, so a device can verify it came from the real hub and
# reject replays of sniffed commands (devices check ts freshness).
def _sign_command(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps({**payload, "ts": int(time.time())},
                      separators=(",", ":")).encode()
    return body, {
        "Content-Type":   "application/json",
        "X-OpenHome-Key": API_KEY,
        "X-OpenHome-Sig": compute_hmac(body),
    }

NTFY_ENABLED = True

def _ntfy_url():
    topic = storage.get_setting("ntfy_topic", "openhome-alerts")
    return f"https://ntfy.sh/{topic}"

# Execution log
exec_log = []

# ── MAIN EXECUTOR ────────────────────────────────────────
async def execute_actions(actions: list[dict], devices: dict):
    """
    Execute a list of AI-generated actions.
    devices = live device registry from server.py
    """
    results = []
    for action in actions:
        result = await _dispatch(action, devices)
        results.append(result)
        exec_log.append({
            "timestamp": datetime.now().isoformat(),
            "action":    action,
            "result":    result
        })

    if len(exec_log) > 200:
        exec_log.pop(0)

    return results


def get_exec_log(limit: int = 30) -> list:
    return exec_log[-limit:]


# ── DISPATCH ─────────────────────────────────────────────
async def _dispatch(action: dict, devices: dict) -> dict:
    kind = action.get("action")

    try:
        if kind == "control_device":
            return await _control_device(action, devices)

        elif kind == "set_thermostat":
            return await _set_thermostat(action, devices)

        elif kind == "set_light_mode":
            return await _set_light_mode(action, devices)

        elif kind in ("notify", "alert"):
            return await _notify(action)

        elif kind == "log":
            msg = action.get("message", "")
            print(f"[AI LOG] {msg}")
            return {"ok": True, "action": "log", "message": msg}

        elif kind == "all_lights":
            return await _all_lights(action, devices)

        else:
            return {"ok": False, "error": f"unknown action: {kind}"}

    except Exception as e:
        print(f"[EXEC] Error executing {kind}: {e}")
        return {"ok": False, "action": kind, "error": str(e)}


# ── ACTIONS ───────────────────────────────────────────────
async def _control_device(action: dict, devices: dict) -> dict:
    device_id = action.get("device_id")
    state     = action.get("state", "toggle")

    if device_id not in devices:
        return {"ok": False, "error": f"device not found: {device_id}"}

    device = devices[device_id]

    # Zigbee devices are controlled via Z2M MQTT, not HTTP — their registered
    # ip/port point at the MQTT broker, which doesn't speak HTTP.
    if device.get("source") == "zigbee":
        ok = send_zigbee_command(device.get("location", device_id), {"state": state})
        if ok:
            storage.update_item("devices", device_id, {"state": state})
            print(f"[EXEC] {device_id} → {state} (zigbee)")
        return {"ok": ok, "action": "control_device", "device_id": device_id, "state": state,
                **({} if ok else {"error": "zigbee bridge not connected"})}

    ip   = device.get("ip")
    port = device.get("port", 80)

    if not ip:
        return {"ok": False, "error": f"{device_id} is battery-powered, can't receive commands"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            body, headers = _sign_command({"state": state})
            resp = await client.post(f"http://{ip}:{port}/control", content=body, headers=headers)
        # Persist so AI + dashboard see the new state immediately
        storage.update_item("devices", device_id, {"state": state})
        print(f"[EXEC] {device_id} → {state} ({resp.status_code})")
        return {"ok": True, "action": "control_device", "device_id": device_id, "state": state}
    except Exception as e:
        # Don't persist — the device never got the command, and persisting
        # made the dashboard show state changes that didn't happen
        print(f"[EXEC] {device_id} unreachable: {e}")
        return {"ok": False, "action": "control_device", "device_id": device_id, "error": "device unreachable"}


async def _set_thermostat(action: dict, devices: dict) -> dict:
    device_id = action.get("device_id")
    if device_id not in devices:
        return {"ok": False, "error": f"device not found: {device_id}"}

    device = devices[device_id]
    ip   = device.get("ip")
    port = device.get("port", 80)
    if not ip:
        return {"ok": False, "error": "no IP"}

    payload = {}
    if "setpoint" in action: payload["setpoint"] = action["setpoint"]
    if "mode" in action:     payload["mode"]     = action["mode"]
    if not payload:
        return {"ok": False, "error": "nothing to set"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            body, headers = _sign_command(payload)
            resp = await client.post(f"http://{ip}:{port}/control", content=body, headers=headers)
        # Mirror new values into registry + persist
        storage.update_item("devices", device_id, payload)
        print(f"[EXEC] {device_id} → thermostat {payload}")
        return {"ok": True, "action": "set_thermostat", "device_id": device_id, **payload}
    except Exception as e:
        print(f"[EXEC] thermostat {device_id} unreachable: {e}")
        return {"ok": False, "action": "set_thermostat", "device_id": device_id, "error": "device unreachable"}


async def _set_light_mode(action: dict, devices: dict) -> dict:
    device_id = action.get("device_id")
    mode      = action.get("mode", "solid")
    color     = action.get("color")   # optional hex string

    if device_id not in devices:
        return {"ok": False, "error": f"device not found: {device_id}"}

    device = devices[device_id]

    payload = {"state": "on", "mode": mode}
    if color:
        payload["color"] = color

    if device.get("source") == "zigbee":
        ok = send_zigbee_command(device.get("location", device_id), payload)
        if ok:
            storage.update_item("devices", device_id, payload)
            print(f"[EXEC] {device_id} → mode:{mode} (zigbee)")
        return {"ok": ok, "action": "set_light_mode", "device_id": device_id, "mode": mode,
                **({} if ok else {"error": "zigbee bridge not connected"})}

    ip   = device.get("ip")
    port = device.get("port", 80)

    if not ip:
        return {"ok": False, "error": "no IP"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            body, headers = _sign_command(payload)
            await client.post(f"http://{ip}:{port}/control", content=body, headers=headers)
        storage.update_item("devices", device_id, payload)
        print(f"[EXEC] {device_id} → mode:{mode} color:{color or 'none'}")
        return {"ok": True, "action": "set_light_mode", "device_id": device_id, "mode": mode}
    except Exception as e:
        print(f"[EXEC] {device_id} unreachable: {e}")
        return {"ok": False, "action": "set_light_mode", "device_id": device_id, "error": "device unreachable"}


async def _all_lights(action: dict, devices: dict) -> dict:
    mode = action.get("mode", "on")
    results = []

    strip_tasks = []
    zigbee_reached = 0
    for did, d in devices.items():
        if d.get("type") not in ("rgb_lights", "smart_plug"):
            continue

        if d.get("source") == "zigbee":
            cmd = {"state": "off"} if mode == "off" else {"state": "on"}
            if send_zigbee_command(d.get("location", did), cmd):
                zigbee_reached += 1
            continue

        ip   = d.get("ip")
        port = d.get("port", 80)
        if not ip:
            continue

        if mode == "off":
            strip_tasks.append(_post(ip, port, {"state": "off"}))
        elif mode == "on":
            strip_tasks.append(_post(ip, port, {"state": "on"}))
        elif mode == "alert":
            strip_tasks.append(_post(ip, port, {"state": "on", "mode": "alert"}))
        else:
            strip_tasks.append(_post(ip, port, {"state": "on", "mode": mode}))

    results = await asyncio.gather(*strip_tasks, return_exceptions=True)
    success = sum(1 for r in results if r is True) + zigbee_reached
    total   = len(strip_tasks) + zigbee_reached
    print(f"[EXEC] all_lights → {mode} ({success}/{total} devices)")
    return {"ok": True, "action": "all_lights", "mode": mode, "devices_reached": success}


async def _post(ip: str, port: int, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            body, headers = _sign_command(payload)
            await client.post(f"http://{ip}:{port}/control", content=body, headers=headers)
        return True
    except:
        return False


async def _notify(action: dict) -> dict:
    message  = action.get("message", "openHome alert")
    severity = action.get("severity", "low")

    # Console always
    print(f"[NOTIFY] [{severity.upper()}] {message}")

    # ntfy.sh push notification
    if NTFY_ENABLED:
        priority_map = {"low": "low", "medium": "default", "high": "high", "critical": "urgent"}
        priority = priority_map.get(severity, "default")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    _ntfy_url(),
                    content=message.encode(),
                    headers={
                        "Title":    "openHome",
                        "Priority": priority,
                        "Tags":     _severity_emoji(severity),
                    }
                )
            print(f"[NOTIFY] ntfy.sh push sent")
        except Exception as e:
            print(f"[NOTIFY] ntfy.sh failed: {e}")

    return {"ok": True, "action": "notify", "message": message, "severity": severity}


def _severity_emoji(severity: str) -> str:
    return {
        "low":      "bell",
        "medium":   "warning",
        "high":     "rotating_light",
        "critical": "sos,rotating_light"
    }.get(severity, "bell")
