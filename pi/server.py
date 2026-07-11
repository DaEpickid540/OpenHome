from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import asyncio
from datetime import datetime
from pathlib import Path

import storage
from seed_rag import seed_on_startup
import users as users_mod
import rag_memory
from ai_brain import query_llm, natural_language_cmd, get_log as get_ai_log
from action_executor import execute_actions, get_exec_log, _sign_command
from security import security_middleware
from voice_service import voice_router
from scheduler import start_scheduler, stop_scheduler, add_schedule, update_schedule, delete_schedule, list_schedules, nl_to_cron, seed_default_schedules
from presence import start_presence, stop_presence, get_presence, add_device as add_presence_device, remove_device as remove_presence_device, anyone_home
from backup import backup_loop, create_backup, list_backups, restore_backup
from zigbee_bridge import start_zigbee_bridge
from camera_service import camera_router, register_camera, get_cameras, start_all_cameras

# Load all persistent state from disk before anything else
storage.init()

app = FastAPI(title="openHome Hub", version="0.5.0")

# Security: API key on all requests + HMAC signature on sensor data.
# Toggle with settings["auth_enabled"] (default True).
app.middleware("http")(security_middleware)
app.include_router(voice_router)
app.include_router(camera_router)

SNAPSHOT_DIR = Path("snapshots")
SNAPSHOT_DIR.mkdir(exist_ok=True)
app.mount("/snapshots", StaticFiles(directory="snapshots"), name="snapshots")

# devices is the live registry — held in storage, accessed as a dict
def _devices():
    return storage.get("devices")


# ─── REGISTER ─────────────────────────────────────────────
@app.post("/register")
async def register_device(request: Request):
    data = await request.json()
    device_id = data.get("device_id")
    if not device_id:
        return JSONResponse({"error": "no device_id"}, status_code=400)
    storage.set_item("devices", device_id, { **data, "last_seen": now(), "registered": True })
    print(f"[REGISTER] {device_id} @ {data.get('ip')}:{data.get('port')}")
    if data.get("type") == "camera_stream" and data.get("ip"):
        # register_camera() populates camera_service's own _streams registry,
        # which live-view/recording depend on — without this call it was
        # never invoked, so camera devices registered fine in the general
        # registry but recording/live-view silently never worked.
        register_camera(device_id, data["ip"], data.get("port", 81))
    return {"ok": True}

# ─── SENSOR ───────────────────────────────────────────────
@app.post("/sensor")
async def receive_sensor(request: Request):
    data = await request.json()
    device_id = data.get("device_id")
    if not device_id:
        return JSONResponse({"error": "no device_id"}, status_code=400)

    storage.set_item("devices", device_id, { **data, "last_seen": now() })
    alert = None
    dtype = data.get("type")

    if dtype == "door_sensor":
        if data.get("state") == "open":
            alert = build_alert("door_open", data, "medium")

    elif dtype == "motion_sensor":
        alert = build_alert("motion", data, "low")

    elif dtype == "flood_sensor":
        if data.get("state") == "wet":
            alert = build_alert("flood", data, "high", extra={"moisture_pct": data.get("moisture_pct")})

    elif dtype == "smoke_co_sensor":
        smoke = data.get("smoke_detected", False)
        co    = data.get("co_detected", False)
        if smoke or co:
            label = "smoke+CO" if (smoke and co) else ("smoke" if smoke else "CO")
            alert = build_alert("smoke_co", data, data.get("severity", "high"),
                extra={"smoke": smoke, "co": co, "label": label})

    elif dtype == "climate_monitor":
        if data.get("comfort") == "alert":
            alert = build_alert("climate_alert", data, "medium",
                extra={"temp_f": data.get("temp_f"), "humidity": data.get("humidity")})

    elif dtype == "thermostat":
        # Thermostat reports are informational; only alert on extreme readings
        temp = data.get("temp_f", 70)
        if temp and (temp > 90 or temp < 45):
            alert = build_alert("thermostat_extreme", data, "high",
                extra={"temp_f": temp, "setpoint": data.get("setpoint")})

    elif dtype == "air_quality":
        if data.get("severity") in ("high", "critical"):
            alert = build_alert("air_quality", data, data.get("severity"),
                extra={"aqi_label": data.get("aqi_label"), "aqi_pct": data.get("aqi_pct")})

    elif dtype == "rfid_lock":
        # Link scan to a user if we recognize the card
        user = users_mod.find_by_rfid(data.get("uid"))
        entry = { **data, "timestamp": now() }
        if user:
            entry["user_name"] = user["name"]
        storage.append("access_log", entry)
        if not data.get("authorized") and data.get("result") == "denied":
            alert = build_alert("rfid_denied", data, "high", extra={"uid": data.get("uid")})

    elif dtype == "doorbell":
        alert = build_alert("doorbell", data, "low", extra={"has_photo": data.get("has_photo", False)})

    elif dtype == "garage_door":
        if data.get("state") == "open":
            alert = build_alert("garage_open", data, "medium", extra={"car_present": data.get("car_present")})

    elif dtype == "panic_button":
        if data.get("state") == "panic":
            alert = build_alert("panic", data, "critical")
        elif data.get("state") == "cancelled":
            alert = build_alert("panic_cancelled", data, "low")

    if alert:
        storage.append("alerts", alert)
        asyncio.create_task(_ai_process(alert))

    return {"ok": True, "device_id": device_id}


async def _ai_process(alert: dict):
    try:
        actions = await query_llm(alert, _devices())
        if actions:
            await execute_actions(actions, _devices())
    except Exception as e:
        print(f"[AI] Processing error: {e}")


# ─── AI ───────────────────────────────────────────────────
@app.post("/ai/command")
async def ai_command(request: Request):
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)
    actions = await natural_language_cmd(text, _devices())
    results = await execute_actions(actions, _devices())
    return {"ok": True, "text": text, "actions": actions, "results": results}

@app.get("/ai/log")
async def ai_log(limit: int = 20):
    return get_ai_log(limit)

@app.get("/ai/exec_log")
async def ai_exec_log(limit: int = 30):
    return get_exec_log(limit)



@app.post("/ai/reseed")
async def reseed_schemas():
    """Force re-seed the RAG with device schemas (use after firmware updates)."""
    from seed_rag import reseed
    await reseed()
    return {"ok": True, "memories": len(storage.get("ai_memory"))}

# ─── RAG MEMORY ───────────────────────────────────────────
@app.post("/ai/memory")
async def add_memory(request: Request):
    """Teach the AI something. Body: {"text": "...", "category": "...", "user_id": "..."}"""
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)
    result = await rag_memory.add_memory(
        text, data.get("category", "general"), data.get("user_id"))
    return {"ok": True, **result}

@app.get("/ai/memory")
async def list_memory(category: str = None, user_id: str = None):
    return rag_memory.list_memories(category, user_id)

@app.delete("/ai/memory/{memory_id}")
async def delete_memory(memory_id: str):
    ok = rag_memory.delete_memory(memory_id)
    return {"ok": ok}


# ─── USERS ────────────────────────────────────────────────
@app.post("/users")
async def create_user(request: Request):
    data = await request.json()
    name = data.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    user = users_mod.create_user(
        name, data.get("role", "member"),
        data.get("rfid_uid"), data.get("preferences"))
    return {"ok": True, "user": user}

@app.get("/users")
async def list_users():
    return users_mod.list_users()

@app.get("/users/{user_id}")
async def get_user(user_id: str):
    u = users_mod.get_user(user_id)
    if not u:
        return JSONResponse({"error": "not found"}, status_code=404)
    return u

@app.patch("/users/{user_id}")
async def update_user(user_id: str, request: Request):
    data = await request.json()
    if "preferences" in data:
        u = users_mod.update_preferences(user_id, data["preferences"])
    else:
        u = users_mod.update_user(user_id, data)
    if not u:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True, "user": u}

@app.delete("/users/{user_id}")
async def delete_user(user_id: str):
    return {"ok": users_mod.delete_user(user_id)}


# ─── SETTINGS ─────────────────────────────────────────────
@app.get("/settings")
async def get_settings():
    return storage.get("settings")

@app.patch("/settings")
async def update_settings(request: Request):
    data = await request.json()
    if data.get("auth_enabled") is False:
        # Disabling auth opens every endpoint (including /ai/command) to
        # anyone on the LAN with no further checks — make that loud rather
        # than a silent, undetectable settings write.
        print("[SECURITY] ⚠ auth_enabled set to False — all endpoints are now unauthenticated")
    for k, v in data.items():
        storage.set_setting(k, v)
    return {"ok": True, "settings": storage.get("settings")}


# ─── SNAPSHOT ─────────────────────────────────────────────
@app.post("/snapshot/{device_id}")
async def receive_snapshot(device_id: str, request: Request):
    body = await request.body()
    if not body:
        return JSONResponse({"error": "no image data"}, status_code=400)
    filename = f"{device_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = SNAPSHOT_DIR / filename
    with open(path, "wb") as f:
        f.write(body)
    dev = storage.get_item("devices", device_id)
    if dev:
        storage.update_item("devices", device_id, {"latest_snapshot": f"/snapshots/{filename}"})
    print(f"[SNAPSHOT] {device_id} -> {filename} ({len(body)} bytes)")
    return {"ok": True, "file": filename, "url": f"/snapshots/{filename}"}

@app.get("/snapshots/{device_id}/latest")
async def get_latest_snapshot(device_id: str):
    files = sorted(SNAPSHOT_DIR.glob(f"{device_id}_*.jpg"), reverse=True)
    if not files:
        return JSONResponse({"error": "no snapshots"}, status_code=404)
    return FileResponse(files[0], media_type="image/jpeg")

# ─── COMMAND ──────────────────────────────────────────────
@app.post("/command")
async def send_command(request: Request):
    import httpx
    data      = await request.json()
    device_id = data.get("device_id")
    devs = _devices()
    if device_id not in devs:
        return JSONResponse({"error": "device not found"}, status_code=404)
    device = devs[device_id]
    ip   = device.get("ip")
    port = device.get("port", 80)
    if not ip:
        return JSONResponse({"error": "no IP - battery device?"}, status_code=400)

    # Pass through all control keys (state, mode, setpoint, color, brightness)
    payload = {k: v for k, v in data.items() if k != "device_id"}

    # Optimistic update: write the new state to storage immediately so the AI
    # sees it on next reasoning call, even if the device times out briefly.
    # The actuator will re-confirm with a /sensor POST so storage stays accurate.
    storage.update_item("devices", device_id, {**payload, "last_seen": now()})

    try:
        body, headers = _sign_command(payload)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"http://{ip}:{port}/control", content=body,
                                     headers=headers, timeout=5.0)
        return resp.json()
    except Exception as e:
        # Storage already updated. Caller can retry. AI sees the intended state.
        return JSONResponse({"error": "device unreachable", "detail": str(e),
                             "note": "state recorded optimistically"}, status_code=502)

# ─── READ ─────────────────────────────────────────────────
@app.get("/devices")
async def get_devices():
    return _devices()

@app.get("/devices/{device_id}")
async def get_device(device_id: str):
    d = storage.get_item("devices", device_id)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d

@app.delete("/devices/{device_id}")
async def remove_device(device_id: str):
    return {"ok": storage.delete_item("devices", device_id)}

@app.get("/alerts")
async def get_alerts(limit: int = 20, severity: str = None):
    a = storage.get("alerts")
    filtered = [x for x in a if not severity or x.get("severity") == severity]
    return filtered[-limit:]

@app.get("/access_log")
async def get_access_log(limit: int = 50):
    return storage.get("access_log")[-limit:]


# ─── SCHEDULES ────────────────────────────────────────────
@app.get("/schedules")
async def get_schedules():
    return list_schedules()

@app.post("/schedules")
async def create_schedule(request: Request):
    data = await request.json()
    # Support NL time ("every weekday at 7am") or raw cron
    cron = data.get("cron") or nl_to_cron(data.get("time",""))
    if not cron:
        return JSONResponse({"error": "unparseable time — use cron or natural language like 'every weekday at 7am'"}, status_code=400)
    rule = add_schedule(data.get("name","Unnamed"), cron,
                        data.get("actions",[]), data.get("enabled",True))
    return {"ok": True, "schedule": rule}

@app.patch("/schedules/{rule_id}")
async def patch_schedule(rule_id: str, request: Request):
    data = await request.json()
    rule = update_schedule(rule_id, data)
    if not rule:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True, "schedule": rule}

@app.delete("/schedules/{rule_id}")
async def del_schedule(rule_id: str):
    return {"ok": delete_schedule(rule_id)}


# ─── PRESENCE ─────────────────────────────────────────────
@app.get("/presence")
async def get_presence_state():
    return {"devices": get_presence(), "anyone_home": anyone_home()}

@app.post("/presence/devices")
async def add_phone(request: Request):
    data = await request.json()
    name = data.get("name","").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    device = add_presence_device(name, data.get("ip"), data.get("mac"),
                                 data.get("user_id"), data.get("role","member"))
    return {"ok": True, "device": device}

@app.delete("/presence/devices/{device_id}")
async def remove_phone(device_id: str):
    return {"ok": remove_presence_device(device_id)}


# ─── BACKUP ───────────────────────────────────────────────
@app.get("/backup")
async def list_backup():
    return list_backups()

@app.post("/backup")
async def trigger_backup():
    path = create_backup()
    return {"ok": True, "path": path}

@app.post("/backup/restore")
async def do_restore(request: Request):
    data = await request.json()
    return restore_backup(data.get("path",""))


# ─── CAMERAS ──────────────────────────────────────────────
@app.get("/cameras_list")
async def cameras_list():
    return get_cameras()


# ─── SPEAKER ID ───────────────────────────────────────────
@app.get("/voice/speakers")
async def list_speakers():
    try:
        from speaker_id import list_enrolled
        return list_enrolled()
    except ImportError:
        return []

@app.delete("/voice/speakers/{user_id}")
async def delete_speaker(user_id: str):
    try:
        from speaker_id import remove_voiceprint
        return {"ok": remove_voiceprint(user_id)}
    except ImportError:
        return {"ok": False, "error": "speaker_id not available"}


# ─── DEVICE GROUPS ────────────────────────────────────────
@app.get("/rooms")
async def get_rooms():
    """Auto-derive rooms from device locations."""
    devices = _devices()
    rooms: dict = {}
    for did, d in devices.items():
        room = d.get("location","unknown").replace("_"," ").title()
        if room not in rooms:
            rooms[room] = {"room": room, "devices": []}
        rooms[room]["devices"].append({
            "device_id": did, "type": d.get("type"),
            "state": d.get("state"), "controllable": d.get("controllable",False)
        })
    return list(rooms.values())

@app.post("/rooms/{room_name}/command")
async def room_command(room_name: str, request: Request):
    """Send a command to all controllable devices in a room."""
    data    = await request.json()
    # Snapshot before iterating: the loop body awaits an HTTP call per
    # device, and storage.get("devices") returns the live cached dict — a
    # concurrent /register or /sensor request can insert a new key into it
    # mid-iteration and raise "dictionary changed size during iteration".
    devices = list(_devices().items())
    room_n  = room_name.lower().replace("-","_").replace(" ","_")
    results = []
    for did, d in devices:
        loc = d.get("location","").lower()
        if loc == room_n and d.get("controllable"):
            payload = {**data, "device_id": did}
            # reuse /command logic
            ip   = d.get("ip")
            port = d.get("port", 80)
            if not ip: continue
            import httpx as _httpx
            try:
                body, headers = _sign_command(data)
                async with _httpx.AsyncClient(timeout=3.0) as client:
                    await client.post(f"http://{ip}:{port}/control",
                        content=body, headers=headers)
                storage.update_item("devices", did, {**data})
                results.append({"device_id": did, "ok": True})
            except Exception as e:
                storage.update_item("devices", did, {**data})
                results.append({"device_id": did, "ok": False, "error": str(e)})
    return {"room": room_name, "results": results}


@app.get("/")
async def root():
    return {
        "name":      "openHome Hub",
        "version":   "0.5.0",
        "ai_name":   storage.get_setting("ai_name", "NOVA"),
        "devices":   len(_devices()),
        "alerts":    len(storage.get("alerts")),
        "users":     len(storage.get("users")),
        "memories":  len(storage.get("ai_memory")),
        "snapshots": len(list(SNAPSHOT_DIR.glob("*.jpg")))
    }

@app.on_event("startup")
async def _on_startup():
    await seed_on_startup()
    seed_default_schedules()
    start_scheduler()
    start_presence()
    asyncio.create_task(backup_loop())
    asyncio.create_task(start_zigbee_bridge())
    # Reconcile camera_service's _streams registry from devices already
    # known to the hub (e.g. registered before a restart), since it's
    # otherwise only populated by a fresh /register call.
    for did, d in storage.get("devices").items():
        if d.get("type") == "camera_stream" and d.get("ip"):
            register_camera(did, d["ip"], d.get("port", 81))
    start_all_cameras()

@app.on_event("shutdown")
def _on_shutdown():
    storage.save_all()
    stop_scheduler()
    stop_presence()

# ─── HELPERS ──────────────────────────────────────────────
def now():
    return datetime.now().isoformat()

def build_alert(alert_type, data, severity, extra=None):
    a = {
        "type":      alert_type,
        "device_id": data.get("device_id"),
        "location":  data.get("location"),
        "severity":  severity,
        "timestamp": now()
    }
    if extra:
        a.update(extra)
    return a

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)
