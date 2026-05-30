/*
 * openHome — Unified Payload Standard v2
 * ────────────────────────────────────────
 * Every device MUST send these fields in every payload.
 * The AI reads this exact structure to understand device state.
 *
 * SENSOR PAYLOAD (POST /sensor):
 * {
 *   "device_id":    string   — unique ID e.g. "door_front"
 *   "type":         string   — device type constant (see below)
 *   "location":     string   — human location e.g. "front_door"
 *   "state":        string   — current primary state (type-specific, see below)
 *   "severity":     string   — "none" | "low" | "medium" | "high" | "critical"
 *   "controllable": bool     — can the AI send commands to this device?
 *   "writable":     [string] — which fields the AI is allowed to change
 *   "readable":     [string] — all fields the AI can read but NOT change
 *   "timestamp":    string   — ISO 8601 e.g. "2026-01-01T12:00:00"
 *   "boot_count":   int      — (battery devices only) wakeup count
 *   ... device-specific fields ...
 * }
 *
 * REGISTER PAYLOAD (POST /register) — always-on devices only:
 * {
 *   "device_id", "type", "location",
 *   "ip":   string  — device's local IP
 *   "port": int     — device's HTTP port (always 80)
 *   "controllable": true
 *   "writable": [...]
 *   "readable": [...]
 *   "state": current state
 * }
 *
 * ── STATE VALUES PER TYPE ─────────────────────────────────
 *
 * door_sensor:     "open" | "closed"
 * motion_sensor:   "detected" | "clear"       (NOT "motion_detected")
 * flood_sensor:    "wet" | "dry"
 * smoke_co_sensor: "alert" | "clear"
 * air_quality:     "good" | "moderate" | "poor" | "hazardous"
 * climate_monitor: "comfortable" | "uncomfortable" | "alert"
 * thermostat:      "heating" | "cooling" | "idle" | "off"
 * smart_plug:      "on" | "off"
 * rgb_lights:      "on" | "off"
 * rfid_lock:       "granted" | "denied" | "idle"
 * doorbell:        "ring" | "idle"
 * garage_door:     "open" | "closed"
 * panic_button:    "panic" | "cancelled" | "armed"
 *
 * ── WRITABLE vs READABLE ─────────────────────────────────
 *
 * WRITABLE = AI can send a /command that changes this.
 * READABLE  = AI can see it but CANNOT change it via command.
 *
 * Examples:
 *   thermostat:  writable=["setpoint","mode"], readable=["temp_f","humidity","hvac_action"]
 *   door_sensor: writable=[], readable=["state"] — AI cannot close a door by writing state
 *   rgb_lights:  writable=["state","mode","color","brightness"], readable=[]
 *   smart_plug:  writable=["state"], readable=[]
 *   rfid_lock:   writable=[], readable=["state","uid","authorized"]
 *   flood_sensor:writable=[], readable=["state","moisture_pct"]
 */
