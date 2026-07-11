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
# Import the key from security.py rather than re-deriving it here: that
# module fails closed (refuses to boot) if secrets_config.py is missing,
# so this stays in sync instead of having its own fail-open fallback.
from security import compute_hmac, API_KEY

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

# Shared by every device command below — one retry on transient failures
# (previously each call site made a single attempt and, in _post(), a bare
# `except:` discarded the actual error entirely).
async def _send_command(ip: str, port: int, payload: dict, retries: int = 1, backoff: float = 0.5):
    """POST a signed command to a device. Returns (ok, error_str, status_code)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                body, headers = _sign_command(payload)
                resp = await client.post(f"http://{ip}:{port}/control", content=body, headers=headers)
            return True, None, resp.status_code
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                await asyncio.sleep(backoff)
    return False, last_err, None


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
    ip   = device.get("ip")
    port = device.get("port", 80)

    if not ip:
        return {"ok": False, "error": f"{device_id} is battery-powered, can't receive commands"}

    ok, err, status = await _send_command(ip, port, {"state": state})
    # Persist so AI + dashboard see the (optimistic, if unreachable) new state immediately
    storage.update_item("devices", device_id, {"state": state})
    if ok:
        print(f"[EXEC] {device_id} → {state} ({status})")
        return {"ok": True, "action": "control_device", "device_id": device_id, "state": state}
    print(f"[EXEC] {device_id} unreachable after retry: {err}")
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

    ok, err, status = await _send_command(ip, port, payload)
    # Mirror new values into registry + persist (optimistic if unreachable)
    storage.update_item("devices", device_id, payload)
    if ok:
        print(f"[EXEC] {device_id} → thermostat {payload}")
        return {"ok": True, "action": "set_thermostat", "device_id": device_id, **payload}
    print(f"[EXEC] thermostat {device_id} unreachable after retry: {err}")
    return {"ok": False, "action": "set_thermostat", "device_id": device_id, "error": "device unreachable"}


async def _set_light_mode(action: dict, devices: dict) -> dict:
    device_id = action.get("device_id")
    mode      = action.get("mode", "solid")
    color     = action.get("color")   # optional hex string

    if device_id not in devices:
        return {"ok": False, "error": f"device not found: {device_id}"}

    device = devices[device_id]
    ip   = device.get("ip")
    port = device.get("port", 80)

    if not ip:
        return {"ok": False, "error": "no IP"}

    payload = {"state": "on", "mode": mode}
    if color:
        payload["color"] = color

    ok, err, _ = await _send_command(ip, port, payload)
    storage.update_item("devices", device_id, payload)
    if ok:
        print(f"[EXEC] {device_id} → mode:{mode} color:{color or 'none'}")
        return {"ok": True, "action": "set_light_mode", "device_id": device_id, "mode": mode}
    print(f"[EXEC] {device_id} unreachable after retry: {err}")
    return {"ok": False, "action": "set_light_mode", "device_id": device_id, "error": "device unreachable"}


async def _all_lights(action: dict, devices: dict) -> dict:
    mode = action.get("mode", "on")
    results = []

    strip_tasks = []
    for did, d in devices.items():
        if d.get("type") not in ("rgb_lights", "smart_plug"):
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
    success = sum(1 for r in results if r is True)
    print(f"[EXEC] all_lights → {mode} ({success}/{len(strip_tasks)} devices)")
    return {"ok": True, "action": "all_lights", "mode": mode, "devices_reached": success}


async def _post(ip: str, port: int, payload: dict) -> bool:
    ok, err, _ = await _send_command(ip, port, payload)
    if not ok:
        # Previously a bare `except:` discarded this entirely — all_lights
        # only ever showed a success count, never why a device was missed.
        print(f"[EXEC] all_lights POST to {ip}:{port} failed after retry: {err}")
    return ok


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
