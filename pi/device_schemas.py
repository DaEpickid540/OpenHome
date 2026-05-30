"""
openHome RAG Device Schema
───────────────────────────
This is the GROUND TRUTH knowledge base the AI loads into RAG memory on startup.
It defines every device type, every field, what is writable vs readable,
and exactly what the AI can and cannot do.

The AI queries this on every action to verify:
  1. Does this device_id exist and is it the right type?
  2. Is the field I want to change in "writable"?
  3. Is the value I'm setting in the allowed range?
  4. Am I trying to fake a physical sensor reading (not allowed)?

Load into RAG via: python3 seed_rag.py
"""

DEVICE_SCHEMAS = {

  # ── BATTERY SENSORS (read-only, AI cannot command) ──────────────────

  "door_sensor": {
    "controllable": False,
    "description": "Reed switch door/window sensor. Wakes on open or close.",
    "state_values": ["open", "closed"],
    "writable": [],
    "readable": ["state", "boot_count"],
    "severity_map": {"open": "medium", "closed": "none"},
    "ai_rules": [
      "CANNOT close or open a door by writing state — that is a physical object",
      "CANNOT change boot_count",
      "CAN notify user when state='open' during night hours",
      "CAN log event when state='open' during day hours",
    ],
    "example_payload": {
      "device_id": "door_front",
      "type": "door_sensor",
      "location": "front_door",
      "state": "open",
      "severity": "medium",
      "controllable": False,
      "writable": [],
      "readable": ["state", "boot_count"],
      "boot_count": 47,
      "timestamp": "2026-01-01T23:14:00"
    }
  },

  "motion_sensor": {
    "controllable": False,
    "description": "PIR motion sensor. Wakes on motion detected.",
    "state_values": ["detected", "clear"],
    "writable": [],
    "readable": ["state", "trigger_count", "boot_count"],
    "severity_map": {"detected": "low", "clear": "none"},
    "ai_rules": [
      "CANNOT trigger or clear motion — that is a physical event",
      "CANNOT change trigger_count",
      "CAN notify user when state='detected' during night hours",
      "CAN log event when state='detected' during day hours",
      "Should consider household pets before alerting — check RAG memory for 'dog' or 'cat'",
    ],
    "example_payload": {
      "device_id": "motion_hallway",
      "type": "motion_sensor",
      "location": "hallway",
      "state": "detected",
      "severity": "low",
      "controllable": False,
      "writable": [],
      "readable": ["state", "trigger_count", "boot_count"],
      "trigger_count": 12,
      "boot_count": 12,
      "timestamp": "2026-01-01T02:30:00"
    }
  },

  "flood_sensor": {
    "controllable": False,
    "description": "Moisture sensor. Wakes when water is detected.",
    "state_values": ["wet", "dry"],
    "writable": [],
    "readable": ["state", "moisture_pct", "boot_count"],
    "severity_map": {"wet": "high", "dry": "none"},
    "ai_rules": [
      "CANNOT dry a floor by setting state to dry",
      "CANNOT change moisture_pct",
      "CAN notify user at 'high' severity when state='wet'",
      "Should always notify on wet regardless of time of day",
    ],
    "example_payload": {
      "device_id": "flood_basement",
      "type": "flood_sensor",
      "location": "basement",
      "state": "wet",
      "severity": "high",
      "controllable": False,
      "writable": [],
      "readable": ["state", "moisture_pct", "boot_count"],
      "moisture_pct": 73,
      "boot_count": 3,
      "timestamp": "2026-01-01T15:00:00"
    }
  },

  "smoke_co_sensor": {
    "controllable": False,
    "description": "MQ-2 + MQ-7 dual smoke and CO sensor. Sounds local buzzer immediately.",
    "state_values": ["alert", "clear"],
    "writable": [],
    "readable": ["state", "smoke_detected", "co_detected", "smoke_pct", "co_pct", "alert_count"],
    "severity_map": {"alert_smoke_and_co": "critical", "alert_co": "high", "alert_smoke": "medium", "clear": "none"},
    "ai_rules": [
      "CANNOT clear a smoke/CO alarm by writing state",
      "CANNOT change smoke_pct or co_pct",
      "MUST notify at 'critical' severity when both smoke_detected and co_detected",
      "MUST notify at 'high' when co_detected alone (CO is invisible and deadly)",
      "MUST trigger all_lights alert mode",
      "Local buzzer fires automatically before WiFi — AI notification is secondary",
    ],
    "example_payload": {
      "device_id": "smoke_kitchen",
      "type": "smoke_co_sensor",
      "location": "kitchen",
      "state": "alert",
      "severity": "critical",
      "controllable": False,
      "writable": [],
      "readable": ["state", "smoke_detected", "co_detected", "smoke_pct", "co_pct", "alert_count"],
      "smoke_detected": True,
      "co_detected": True,
      "smoke_pct": 68,
      "co_pct": 45,
      "alert_count": 1,
      "boot_count": 1,
      "timestamp": "2026-01-01T03:00:00"
    }
  },

  "panic_button": {
    "controllable": False,
    "description": "Manual panic button. Hold 2s to arm. Press again within 10s to cancel.",
    "state_values": ["panic", "cancelled", "armed"],
    "writable": [],
    "readable": ["state", "panic_count", "boot_count"],
    "severity_map": {"panic": "critical", "cancelled": "none", "armed": "none"},
    "ai_rules": [
      "CANNOT trigger or cancel panic by writing state",
      "MUST notify at 'critical' severity when state='panic'",
      "MUST trigger all_lights alert mode when state='panic'",
      "Should log when state='cancelled' — no alert needed",
    ],
    "example_payload": {
      "device_id": "panic_bedroom",
      "type": "panic_button",
      "location": "bedroom",
      "state": "panic",
      "severity": "critical",
      "controllable": False,
      "writable": [],
      "readable": ["state", "panic_count", "boot_count"],
      "panic_count": 1,
      "boot_count": 89,
      "timestamp": "2026-01-01T01:00:00"
    }
  },

  "doorbell": {
    "controllable": False,
    "description": "ESP32-CAM doorbell. Wakes on button press, captures photo.",
    "state_values": ["ring", "idle"],
    "writable": [],
    "readable": ["state", "ring_count", "has_photo", "boot_count"],
    "severity_map": {"ring": "low", "idle": "none"},
    "ai_rules": [
      "CANNOT press the doorbell or clear a ring by writing state",
      "CAN notify at 'low' severity normally, 'medium' if ring occurs after 11pm",
      "If has_photo=true, mention snapshot is available in notification",
    ],
    "example_payload": {
      "device_id": "doorbell_front",
      "type": "doorbell",
      "location": "front_door",
      "state": "ring",
      "severity": "low",
      "controllable": False,
      "writable": [],
      "readable": ["state", "ring_count", "has_photo", "boot_count"],
      "ring_count": 3,
      "has_photo": True,
      "boot_count": 3,
      "timestamp": "2026-01-01T14:30:00"
    }
  },

  "rfid_lock": {
    "controllable": False,
    "description": "RFID card reader. Whitelisted cards unlock the door physically. AI cannot override.",
    "state_values": ["granted", "denied", "idle"],
    "writable": [],
    "readable": ["state", "uid", "authorized", "result"],
    "severity_map": {"denied": "high", "granted": "none", "idle": "none"},
    "ai_rules": [
      "CANNOT grant or deny access by writing state",
      "CANNOT add or remove cards from whitelist via command",
      "CANNOT unlock the door via command — lock is hardware-only",
      "CAN notify at 'high' severity when state='denied'",
      "Should flag repeated denied scans (3+ in 10 minutes) at 'critical'",
    ],
    "example_payload": {
      "device_id": "rfid_front_door",
      "type": "rfid_lock",
      "location": "front_door",
      "state": "denied",
      "severity": "high",
      "controllable": False,
      "writable": [],
      "readable": ["state", "uid", "authorized", "result"],
      "uid": "A9B8C7D6",
      "authorized": False,
      "result": "denied",
      "timestamp": "2026-01-01T22:05:00"
    }
  },

  # ── SENSORS WITH DISPLAY (read-only, always-on) ─────────────────────

  "climate_monitor": {
    "controllable": False,
    "description": "DHT22 temp/humidity monitor with OLED. Reports every 5 minutes.",
    "state_values": ["comfortable", "uncomfortable", "alert"],
    "writable": [],
    "readable": ["state", "temp_f", "humidity"],
    "severity_map": {"alert": "medium", "uncomfortable": "none", "comfortable": "none"},
    "ai_rules": [
      "CANNOT change temperature by writing state",
      "CANNOT change humidity by writing state",
      "CAN suggest adjusting thermostat setpoint if temp is out of comfort range",
      "If temp_f > 85 or humidity > 70, notify at 'medium' and suggest thermostat adjustment",
    ],
    "example_payload": {
      "device_id": "climate_bedroom",
      "type": "climate_monitor",
      "location": "bedroom",
      "state": "alert",
      "severity": "medium",
      "controllable": False,
      "writable": [],
      "readable": ["state", "temp_f", "humidity"],
      "temp_f": 87.2,
      "humidity": 72.0,
      "timestamp": "2026-01-01T14:00:00"
    }
  },

  "air_quality": {
    "controllable": False,
    "description": "MQ-135 air quality monitor with OLED. Reports every 3 minutes.",
    "state_values": ["good", "moderate", "poor", "hazardous"],
    "writable": [],
    "readable": ["state", "aqi_pct", "aqi_label"],
    "severity_map": {"hazardous": "critical", "poor": "high", "moderate": "medium", "good": "none"},
    "ai_rules": [
      "CANNOT change air quality by writing state",
      "CAN notify at matching severity level",
      "At 'hazardous': notify critical, suggest ventilation",
      "At 'poor': notify high",
    ],
    "example_payload": {
      "device_id": "airquality_living",
      "type": "air_quality",
      "location": "living_room",
      "state": "poor",
      "severity": "high",
      "controllable": False,
      "writable": [],
      "readable": ["state", "aqi_pct", "aqi_label"],
      "aqi_pct": 82,
      "aqi_label": "poor",
      "timestamp": "2026-01-01T18:00:00"
    }
  },

  # ── ACTUATORS (controllable, AI can command) ─────────────────────────

  "smart_plug": {
    "controllable": True,
    "description": "Relay-controlled smart plug. Always-on with local HTTP server.",
    "state_values": ["on", "off"],
    "writable": ["state"],
    "readable": [],
    "command_schema": {"state": "on | off | toggle"},
    "severity_map": {"on": "none", "off": "none"},
    "ai_rules": [
      "CAN send: {action: control_device, device_id: '...', state: 'on'|'off'|'toggle'}",
      "CANNOT change state by writing to sensor payload — must send command via /command endpoint",
    ],
    "example_payload": {
      "device_id": "plug_living_room",
      "type": "smart_plug",
      "location": "living_room",
      "state": "off",
      "severity": "none",
      "controllable": True,
      "writable": ["state"],
      "readable": [],
      "ip": "192.168.1.51",
      "port": 80,
      "timestamp": "2026-01-01T12:00:00"
    },
    "example_command": {"action": "control_device", "device_id": "plug_living_room", "state": "on"}
  },

  "rgb_lights": {
    "controllable": True,
    "description": "WS2812B LED strip via FastLED. Supports modes and colors.",
    "state_values": ["on", "off"],
    "mode_values": ["solid", "rainbow", "pulse", "fire", "alert"],
    "writable": ["state", "mode", "color", "brightness"],
    "readable": [],
    "command_schema": {
      "state": "on | off",
      "mode": "solid | rainbow | pulse | fire | alert",
      "color": "#RRGGBB hex string",
      "brightness": "0-255 integer"
    },
    "severity_map": {"on": "none", "off": "none"},
    "ai_rules": [
      "CAN send: {action: set_light_mode, device_id: '...', mode: '...', color: '#RRGGBB'}",
      "CAN send: {action: control_device, device_id: '...', state: 'on'|'off'}",
      "For alarm: use mode='alert' (red flashing)",
      "For normal use: use mode='solid' with a color",
      "color MUST be a valid hex string like '#FF6600' — not a color name",
      "brightness MUST be 0-255 integer",
    ],
    "example_payload": {
      "device_id": "lights_living_room",
      "type": "rgb_lights",
      "location": "living_room",
      "state": "on",
      "severity": "none",
      "controllable": True,
      "writable": ["state", "mode", "color", "brightness"],
      "readable": [],
      "mode": "solid",
      "color": "#FFD740",
      "brightness": 128,
      "num_leds": 60,
      "ip": "192.168.1.52",
      "port": 80,
      "timestamp": "2026-01-01T20:00:00"
    },
    "example_commands": [
      {"action": "set_light_mode", "device_id": "lights_living_room", "mode": "solid", "color": "#FFD740"},
      {"action": "set_light_mode", "device_id": "lights_living_room", "mode": "alert"},
      {"action": "control_device", "device_id": "lights_living_room", "state": "off"},
    ]
  },

  "thermostat": {
    "controllable": True,
    "description": "DHT22 + relay HVAC controller. Controls heat/cool/fan relays with hysteresis.",
    "state_values": ["heating", "cooling", "idle", "off"],
    "mode_values": ["heat", "cool", "auto", "off"],
    "writable": ["setpoint", "mode"],
    "readable": ["temp_f", "humidity", "hvac_action"],
    "command_schema": {
      "setpoint": "50-90 float (degrees F)",
      "mode": "heat | cool | auto | off"
    },
    "severity_map": {"heating": "none", "cooling": "none", "idle": "none", "off": "none"},
    "ai_rules": [
      "CAN send: {action: set_thermostat, device_id: '...', setpoint: 72, mode: 'auto'}",
      "CAN change setpoint (50-90°F range) and mode (heat/cool/auto/off)",
      "CANNOT change temp_f — that is a physical sensor reading",
      "CANNOT change humidity — that is a physical sensor reading",
      "CANNOT change hvac_action — that is the physical relay state",
      "setpoint MUST be between 50 and 90 (°F)",
      "Do NOT set setpoint below 55 in winter or above 85 in summer",
      "If user says 'I'm cold': increase setpoint by 2-3°F and set mode to 'heat'",
      "If user says 'I'm hot': decrease setpoint by 2-3°F and set mode to 'cool'",
      "Check RAG memory for household preferred temp before adjusting",
    ],
    "example_payload": {
      "device_id": "thermostat_main",
      "type": "thermostat",
      "location": "hallway",
      "state": "heating",
      "severity": "none",
      "controllable": True,
      "writable": ["setpoint", "mode"],
      "readable": ["temp_f", "humidity", "hvac_action"],
      "setpoint": 72.0,
      "mode": "auto",
      "temp_f": 68.5,
      "humidity": 45.0,
      "hvac_action": "heating",
      "ip": "192.168.1.53",
      "port": 80,
      "timestamp": "2026-01-01T07:00:00"
    },
    "example_commands": [
      {"action": "set_thermostat", "device_id": "thermostat_main", "setpoint": 74},
      {"action": "set_thermostat", "device_id": "thermostat_main", "mode": "cool"},
      {"action": "set_thermostat", "device_id": "thermostat_main", "setpoint": 70, "mode": "heat"},
    ]
  },

  "garage_door": {
    "controllable": True,
    "description": "Relay pulses HVAC wall button to toggle garage door. Reed switch + ultrasonic for state.",
    "state_values": ["open", "closed"],
    "writable": ["state"],
    "readable": ["car_present"],
    "command_schema": {"state": "open | close | toggle"},
    "severity_map": {"open": "medium", "closed": "none"},
    "ai_rules": [
      "CAN send: {action: control_device, device_id: '...', state: 'open'|'close'|'toggle'}",
      "Command sends a relay PULSE to the wall button — same as pressing it physically",
      "CANNOT read or change car_present — that is an ultrasonic sensor reading",
      "If sending 'open' when already open: hub will skip (already in state)",
      "If sending 'close' when already closed: hub will skip",
      "Should NOT auto-close if car_present=true (car might be in the way)",
    ],
    "example_payload": {
      "device_id": "garage_door_main",
      "type": "garage_door",
      "location": "garage",
      "state": "open",
      "severity": "medium",
      "controllable": True,
      "writable": ["state"],
      "readable": ["car_present"],
      "car_present": True,
      "ip": "192.168.1.54",
      "port": 80,
      "timestamp": "2026-01-01T19:00:00"
    },
    "example_command": {"action": "control_device", "device_id": "garage_door_main", "state": "close"}
  },

    "appliance_sensor": {
    "controllable": False,
    "description": "Washer/dryer/dishwasher cycle detector.",
    "state_values": ["idle","running","spinning","done"],
    "writable": [], "readable": ["state","vibration_rms","audio_rms","in_cycle"],
    "severity_map": {"done":"medium","running":"none","spinning":"none","idle":"none"},
    "ai_rules": ["CANNOT start/stop appliance","Notify when state=done"],
  },

  "camera_stream": {
    "controllable": True,
    "description": "Always-on MJPEG camera stream.",
    "state_values": ["streaming","offline"],
    "writable": ["quality","resolution","flip","mirror"],
    "readable": ["stream_url"],
    "command_schema": {"quality":"1-100","resolution":"QVGA|VGA|SVGA"},
    "severity_map": {"streaming":"none","offline":"low"},
    "ai_rules": ["CAN adjust quality/resolution","Cannot analyze video"],
  },

  "listener_satellite": {
    "controllable": True,
    "description": "Voice listener (ESP32-S3 or Pi Zero 2 with mic, speaker, screen). Records audio, sends to Pi for STT, plays back TTS replies. Like a Google Home / Echo but fully local.",
    "state_values": ["idle", "listening", "thinking", "speaking"],
    "writable": ["state", "voice", "volume", "screen_text"],
    "readable": ["last_transcript", "last_reply", "battery_pct"],
    "command_schema": {
      "state":       "idle | listening | speaking",
      "voice":       "Piper voice name (e.g. en_US-amy-medium)",
      "volume":      "0-100 integer",
      "screen_text": "string to display"
    },
    "severity_map": {"idle": "none", "listening": "none", "thinking": "none", "speaking": "none"},
    "ai_rules": [
      "CAN send: {action: control_device, device_id: '...', state: 'idle|listening|speaking'}",
      "CAN send screen_text to display a message on the satellite's screen",
      "CANNOT read past conversations from a satellite — only the most recent transcript",
      "When a user speaks to a listener, the transcript arrives as a regular nl_command — respond normally",
      "Should keep spoken replies SHORT (1 sentence) — long replies are awkward over TTS",
    ],
    "example_payload": {
      "device_id": "listener_kitchen",
      "type": "listener_satellite",
      "location": "kitchen",
      "state": "listening",
      "severity": "none",
      "controllable": True,
      "writable": ["state", "voice", "volume", "screen_text"],
      "readable": ["last_transcript", "last_reply", "battery_pct"],
      "voice": "en_US-amy-medium",
      "volume": 75,
      "last_transcript": "turn off the kitchen lights",
      "last_reply": "Lights off.",
      "ip": "192.168.1.60",
      "port": 80,
      "timestamp": "2026-01-01T14:30:00"
    },
    "example_commands": [
      {"action": "control_device", "device_id": "listener_kitchen", "state": "speaking"},
    ]
  },
}


# ── GLOBAL AI RULES ───────────────────────────────────────────────────────────
GLOBAL_RULES = [
  # Identity
  "You are NOVA, the openHome AI brain. Reply ONLY with a valid JSON array of actions.",
  "If no action is needed, reply with []. Never add explanation or markdown.",

  # Core constraint
  "You CANNOT change physical sensor readings by writing to them. state='open' on a door_sensor means the door IS open — you did not open it and cannot close it.",
  "You can only control devices where controllable=true.",
  "You can only write fields listed in that device's 'writable' array.",
  "Fields in 'readable' are for your information only — do not try to set them.",

  # Action format
  "control_device: use for smart_plug (state on/off/toggle) and garage_door (state open/close/toggle)",
  "set_thermostat: use for thermostat only. Fields: setpoint (50-90 float), mode (heat/cool/auto/off)",
  "set_light_mode: use for rgb_lights. Fields: mode (solid/rainbow/pulse/fire/alert), color (#RRGGBB)",
  "all_lights: shortcut to set all rgb_lights and smart_plugs at once. mode: on/off/alert",
  "notify: always include message (string) and severity (low/medium/high/critical)",
  "log: for informational events that don't need user attention",

  # Safety
  "Never set thermostat setpoint below 50 or above 90",
  "Never close garage_door if car_present=true",
  "Always notify at 'critical' for: smoke, CO, panic, flood",
  "Always trigger all_lights alert for: panic, smoke/CO",
]


# ── RAG SEEDING ───────────────────────────────────────────────────────────────
def get_rag_entries():
  """Returns flat list of (text, category) tuples to load into RAG on startup."""
  entries = []

  # One entry per device type explaining rules
  for dtype, schema in DEVICE_SCHEMAS.items():
    rules_text = " ".join(schema["ai_rules"])
    entries.append((
      f"{dtype} device rules: {rules_text}",
      "device_rules"
    ))
    entries.append((
      f"{dtype} writable fields: {schema['writable']}. readable fields: {schema['readable']}. controllable: {schema['controllable']}",
      "device_schema"
    ))
    entries.append((
      f"{dtype} state values: {schema['state_values']}. severity map: {schema['severity_map']}",
      "device_states"
    ))
    if "command_schema" in schema:
      entries.append((
        f"{dtype} command format: {schema['command_schema']}",
        "command_format"
      ))

  # Global rules
  for rule in GLOBAL_RULES:
    entries.append((rule, "global_rule"))

  return entries


if __name__ == "__main__":
  import json
  print(f"Device schemas: {len(DEVICE_SCHEMAS)}")
  print(f"RAG entries:    {len(get_rag_entries())}")
  for dtype, schema in DEVICE_SCHEMAS.items():
    print(f"  {dtype}: writable={schema['writable']} readable={schema['readable']}")
