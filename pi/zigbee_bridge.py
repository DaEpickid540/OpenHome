"""
openHome Zigbee Bridge
───────────────────────
Bridges Zigbee2MQTT → openHome hub so you can use Zigbee devices
(IKEA Tradfri, Philips Hue, Aqara sensors, etc.) alongside WiFi ESP32 nodes.

Architecture:
  Zigbee USB dongle → Zigbee2MQTT (on Pi) → MQTT broker (Mosquitto) →
  this bridge → openHome /sensor or /register endpoint

Hardware needed:
  - Zigbee USB coordinator: CC2652 stick (~$15, Texas Instruments based)
    Recommended: Sonoff Zigbee 3.0 USB Dongle Plus or SLZB-06P7
  - Already on Pi: USB port

Install:
  # Mosquitto MQTT broker
  sudo apt install mosquitto mosquitto-clients
  sudo systemctl enable mosquitto

  # Zigbee2MQTT
  sudo npm install -g zigbee2mqtt
  # Configure /opt/zigbee2mqtt/data/configuration.yaml (see Z2M docs)
  # Set serial.port to your dongle e.g. /dev/ttyUSB0

  pip install paho-mqtt

Usage:
  from zigbee_bridge import start_zigbee_bridge
  asyncio.create_task(start_zigbee_bridge())

Device mapping:
  Z2M exposes device capabilities (on_off, brightness, temperature, etc.)
  We map these to openHome device types and payload format automatically.
"""

import json
import asyncio
import hmac
import hashlib
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
    _HAS_MQTT = True
except ImportError:
    _HAS_MQTT = False
    print("[ZIGBEE] paho-mqtt not installed — run: pip install paho-mqtt")

import storage

try:
    from secrets_config import API_KEY, HMAC_KEY
except ImportError:
    API_KEY = HMAC_KEY = "CHANGE_ME"

MQTT_BROKER  = "localhost"
MQTT_PORT    = 1883
MQTT_TOPIC   = "zigbee2mqtt/#"
Z2M_BASE     = "zigbee2mqtt"
HUB_URL      = "http://localhost:8765"

# ── TYPE MAPPING ─────────────────────────────────────────
# Zigbee device feature → openHome type
def _classify_device(exposes: list) -> str:
    """Classify a Zigbee device from its Z2M 'exposes' list."""
    features = {f.get("name","") for f in exposes} if exposes else set()
    for item in (exposes or []):
        features.update(f.get("name","") for f in item.get("features",[]))

    if "temperature" in features and "humidity" in features:
        return "climate_monitor"
    if "temperature" in features:
        return "climate_monitor"
    if "contact" in features:
        return "door_sensor"
    if "occupancy" in features:
        return "motion_sensor"
    if "water_leak" in features:
        return "flood_sensor"
    if "smoke" in features:
        return "smoke_co_sensor"
    if "brightness" in features:
        return "rgb_lights"   # dimmable light
    if "state" in features:
        return "smart_plug"   # basic on/off
    return "generic_zigbee"


def _build_payload(z2m_name: str, z2m_msg: dict, device_type: str) -> dict:
    """Convert a Z2M message to openHome unified payload."""
    now = datetime.now().isoformat()
    payload = {
        "device_id":    f"zb_{z2m_name.replace(' ','_').lower()}",
        "type":         device_type,
        "location":     z2m_name,   # Z2M friendly name (user sets this in Z2M)
        "severity":     "none",
        "timestamp":    now,
        "source":       "zigbee",
    }

    # State mapping
    if device_type == "door_sensor":
        payload["state"]        = "open" if not z2m_msg.get("contact", True) else "closed"
        payload["controllable"] = False
        payload["writable"]     = []
        payload["readable"]     = ["state"]
        payload["severity"]     = "medium" if payload["state"] == "open" else "none"

    elif device_type == "motion_sensor":
        payload["state"]        = "detected" if z2m_msg.get("occupancy") else "clear"
        payload["controllable"] = False
        payload["writable"]     = []
        payload["readable"]     = ["state"]

    elif device_type == "climate_monitor":
        temp_c = z2m_msg.get("temperature", 0)
        temp_f = round(temp_c * 9/5 + 32, 1)
        hum    = z2m_msg.get("humidity", 0)
        comfort = "alert" if temp_f > 85 or hum > 70 else \
                  "uncomfortable" if temp_f < 65 or temp_f > 80 else "comfortable"
        payload["state"]        = comfort
        payload["controllable"] = False
        payload["writable"]     = []
        payload["readable"]     = ["state","temp_f","humidity"]
        payload["temp_f"]       = temp_f
        payload["humidity"]     = hum
        payload["severity"]     = "medium" if comfort == "alert" else "none"

    elif device_type == "flood_sensor":
        wet = z2m_msg.get("water_leak", False)
        payload["state"]        = "wet" if wet else "dry"
        payload["controllable"] = False
        payload["writable"]     = []
        payload["readable"]     = ["state"]
        payload["severity"]     = "high" if wet else "none"

    elif device_type in ("rgb_lights", "smart_plug"):
        on = z2m_msg.get("state", "OFF") == "ON"
        payload["state"]        = "on" if on else "off"
        payload["controllable"] = True
        payload["writable"]     = ["state","brightness"] if "brightness" in z2m_msg else ["state"]
        payload["readable"]     = []
        payload["brightness"]   = z2m_msg.get("brightness", 255)

    else:
        payload["state"]        = str(z2m_msg.get("state", "unknown"))
        payload["controllable"] = False
        payload["writable"]     = []
        payload["readable"]     = list(z2m_msg.keys())

    return payload


# ── HUB POSTING ──────────────────────────────────────────
async def _post_to_hub(endpoint: str, payload: dict):
    import httpx
    body = json.dumps(payload).encode()
    sig  = hmac.new(HMAC_KEY.encode(), body, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{HUB_URL}{endpoint}", content=body,
                headers={"Content-Type":"application/json",
                         "X-OpenHome-Key": API_KEY,
                         "X-OpenHome-Sig": sig})
    except Exception as e:
        print(f"[ZIGBEE] Post failed: {e}")


# ── MQTT CALLBACKS ────────────────────────────────────────
_device_types: dict = {}     # friendly_name → device type
_loop = None

def _on_connect(client, userdata, flags, rc):
    print(f"[ZIGBEE] MQTT connected rc={rc}")
    client.subscribe(MQTT_TOPIC)

def _on_message(client, userdata, msg):
    """Handle incoming Z2M messages. Runs in paho's thread → schedule on asyncio loop."""
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return

    # Device bridge messages (topology/availability)
    if topic == f"{Z2M_BASE}/bridge/devices":
        for dev in (payload if isinstance(payload, list) else []):
            name = dev.get("friendly_name","")
            exposes = dev.get("definition",{}).get("exposes",[])
            dtype = _classify_device(exposes)
            _device_types[name] = dtype
            # Register with hub as always-on (Zigbee devices have mains power usually)
            register_payload = _build_payload(name, {}, dtype)
            register_payload["ip"]   = MQTT_BROKER
            register_payload["port"] = MQTT_PORT
            if _loop:
                asyncio.run_coroutine_threadsafe(
                    _post_to_hub("/register", register_payload), _loop)
        return

    # Availability
    if topic.endswith("/availability"):
        return

    # State update from a device
    parts = topic.split("/")
    if len(parts) == 2:
        friendly_name = parts[1]
        dtype = _device_types.get(friendly_name, "generic_zigbee")
        sensor_payload = _build_payload(friendly_name, payload, dtype)
        if _loop:
            asyncio.run_coroutine_threadsafe(
                _post_to_hub("/sensor", sensor_payload), _loop)


# ── ZIGBEE CONTROL (hub → Z2M → device) ──────────────────
def send_zigbee_command(friendly_name: str, command: dict) -> bool:
    """Send a command to a Zigbee device via Z2M MQTT. Returns True if published."""
    if not _HAS_MQTT or not hasattr(send_zigbee_command, "_client"):
        return False
    topic = f"{Z2M_BASE}/{friendly_name}/set"
    # Map openHome state format to Z2M format
    z2m_cmd = {}
    if "state" in command:
        z2m_cmd["state"] = "ON" if command["state"] == "on" else "OFF"
    if "brightness" in command:
        z2m_cmd["brightness"] = command["brightness"]
    if "color" in command:
        # Convert hex to RGB
        h = command["color"].lstrip("#")
        z2m_cmd["color"] = {"r": int(h[0:2],16), "g": int(h[2:4],16), "b": int(h[4:6],16)}
    info = send_zigbee_command._client.publish(topic, json.dumps(z2m_cmd))
    return info.rc == 0  # MQTT_ERR_SUCCESS


# ── STARTUP ───────────────────────────────────────────────
async def start_zigbee_bridge():
    global _loop
    if not _HAS_MQTT:
        print("[ZIGBEE] Bridge disabled — install paho-mqtt")
        return

    _loop = asyncio.get_event_loop()
    # paho-mqtt 2.x requires an explicit callback API version; 1.x has no such arg
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()
    client.on_connect = _on_connect
    client.on_message = _on_message
    send_zigbee_command._client = client

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print(f"[ZIGBEE] Bridge started → {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        print(f"[ZIGBEE] Could not connect to MQTT broker: {e}")
        print("[ZIGBEE] Is Mosquitto running? sudo systemctl start mosquitto")
