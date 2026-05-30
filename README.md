# openHome 🏠

Open source local-AI smart home system. ESP32 sensor/actuator nodes talk to a Raspberry Pi hub running a fine-tuned LLM. No cloud. No subscription. No data leaving your house.

---

## Repo layout

```
openHome/
├── esp32/                         # Arduino IDE sketches — one folder per device
│   ├── config.example.h           # template: copy to config.h in each folder
│   ├── _openhome_security.h       # shared HMAC + auth helper (copied to each folder)
│   ├── PAYLOAD_STANDARD.h         # JSON payload contract reference
│   └── door_sensor/               # each device: folder name must match .ino name
│       ├── door_sensor.ino
│       ├── config.h               # WiFi, hub IP, API key (gitignored)
│       └── _openhome_security.h
│   └── ... (13 devices total)
│
├── pi/                            # Raspberry Pi hub
│   ├── server.py                  # FastAPI — all HTTP endpoints
│   ├── security.py                # API key + HMAC middleware
│   ├── gen_keys.py                # generates secrets_config.py (run once)
│   ├── storage.py                 # unified persistence — survives reboots
│   ├── ai_brain.py                # LLM reasoning layer
│   ├── action_executor.py         # runs AI actions against devices
│   ├── rag_memory.py              # household knowledge base (semantic search)
│   ├── device_schemas.py          # ground truth: writable/readable per device type
│   ├── seed_rag.py                # seeds schemas into RAG on startup
│   ├── users.py                   # household members + preferences
│   ├── setup_ai.sh                # installs Ollama + pulls base model
│   ├── Modelfile                  # Ollama recipe for your fine-tuned model
│   └── requirements.txt
│
├── dashboard/
│   └── index.html                 # single-file web UI, no build step
│
├── dataset/
│   ├── openhome_train.jsonl       # 1718 training examples
│   ├── openhome_val.jsonl         # 191 validation examples
│   ├── generate_training_data.py  # regenerate or expand the dataset
│   └── train_unsloth.py           # LoRA fine-tune script (Unsloth)
│
├── SECURITY.md                    # threat model, setup steps, Tailscale guide
├── LICENSE
└── README.md
```

---

## How it works

```
ESP32 Sensors  (door · motion · flood · smoke/CO · doorbell · panic)
    │  WiFi POST /sensor  (HMAC-SHA256 signed payload)
    ▼
Raspberry Pi Hub  (:8765)
    ├─ Verifies API key + HMAC signature on every inbound event
    ├─ NOVA (Qwen/Llama via Ollama) reasons about the event
    │    └─ queries RAG memory for household context + device rules
    ├─ Executes validated JSON action list
    │    ├─ sends commands to actuators (with API key header)
    │    └─ pushes notifications via ntfy.sh
    └─ Persists all state to disk (survives Pi reboots)

ESP32 Actuators (plug · lights · thermostat · RFID lock · garage)
    └─ verify X-OpenHome-Key on every /control request, reject unauthorized
```

Sensors push signed events. The AI reasons with household context. Actuators take authenticated commands. Nothing leaves your LAN unless you set up Tailscale.

---

## Devices (13 types)

| Device | Type constant | Power | AI-controllable |
|---|---|---|---|
| Door sensor | `door_sensor` | Battery | No — physical object |
| Motion sensor | `motion_sensor` | Battery | No |
| Flood sensor | `flood_sensor` | Battery | No |
| Smoke/CO sensor | `smoke_co_sensor` | Battery | No |
| Panic button | `panic_button` | Battery | No |
| Doorbell cam | `doorbell` | Battery | No |
| Climate monitor | `climate_monitor` | Always-on | No |
| Air quality | `air_quality` | Always-on | No |
| RFID lock | `rfid_lock` | Always-on | No — hardware only |
| Thermostat | `thermostat` | Always-on | Yes — `setpoint`, `mode` |
| Smart plug | `smart_plug` | Always-on | Yes — `state` on/off/toggle |
| RGB lights | `rgb_lights` | Always-on | Yes — `state`, `mode`, `color`, `brightness` |
| Garage door | `garage_door` | Always-on | Yes — `state` open/close/toggle |
| **Voice listener** | `listener_satellite` | Always-on | Yes — `state`, `voice`, `volume` |

All 13 share an identical JSON payload structure. Every payload carries `device_id`, `type`, `location`, `state`, `severity`, `controllable`, `writable[]`, `readable[]`, and `timestamp`. The AI reads `writable` and `readable` on every action — it cannot close a door by writing its state, cannot clear a smoke alarm, cannot unlock the RFID lock over the network.

**State values by type:**

| Type | State values |
|---|---|
| door_sensor | `open` / `closed` |
| motion_sensor | `detected` / `clear` |
| flood_sensor | `wet` / `dry` |
| smoke_co_sensor | `alert` / `clear` |
| air_quality | `good` / `moderate` / `poor` / `hazardous` |
| climate_monitor | `comfortable` / `uncomfortable` / `alert` |
| thermostat | `heating` / `cooling` / `idle` / `off` |
| smart_plug | `on` / `off` |
| rgb_lights | `on` / `off` |
| rfid_lock | `granted` / `denied` / `idle` |
| doorbell | `ring` / `idle` |
| garage_door | `open` / `closed` |
| panic_button | `panic` / `cancelled` / `armed` |

---

## Security

Two auth layers baked into every request.

**API key** — every hub request needs `X-OpenHome-Key`. Blocks anyone on your WiFi without it. Actuators also check this header and reject unauthorized `/control` requests.

**HMAC signatures** — sensors sign their payload with a separate HMAC-SHA256 key. The hub verifies the signature before processing. An attacker can't forge fake sensor events even if they sniffed the API key, because they can't produce a valid signature without the HMAC key.

**Setup (once):**
```bash
cd pi
python3 gen_keys.py
```
Generates `secrets_config.py` (gitignored) with two random keys. Paste both into each `esp32/<device>/config.h` and re-flash. The dashboard prompts for the API key on first open — stored in sessionStorage, never written to disk.

**Also strongly recommended:**
- Put all ESP32s on a guest network or IoT VLAN (free on most routers)
- Use [Tailscale](https://tailscale.com) for remote access — never port-forward the hub

Full threat model, RFID reality check, HTTPS options: **[SECURITY.md](SECURITY.md)**

---

## Quick start

### Pi hub

```bash
cd pi
pip install -r requirements.txt
python3 gen_keys.py          # generate keys first
bash setup_ai.sh             # installs Ollama + pulls Llama 3.2 1B
python3 server.py            # hub starts on :8765
```

Run as a service (auto-start on boot) — create `/etc/systemd/system/openhome.service`:

```ini
[Unit]
Description=openHome Hub
After=network.target ollama.service

[Service]
WorkingDirectory=/home/pi/openHome/pi
ExecStart=/usr/bin/python3 /home/pi/openHome/pi/server.py
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

Then enable it: `systemctl enable openhome && systemctl start openhome`

### Dashboard

Open `dashboard/index.html`, set `HUB_URL` near the top of the script block to your Pi's IP, and enter your API key when prompted.

Or serve from the Pi: `cd dashboard && python3 -m http.server 8080`

### ESP32 firmware

1. Install the ESP32 board package in Arduino IDE (see below)
2. Install required libraries (see below)
3. Edit `esp32/<device>/config.h` — WiFi, hub IP, and both security keys
4. Set `DEVICE_ID` and `LOCATION` at the top of the `.ino`
5. Open the sketch folder in Arduino IDE, select your board + port, hit Upload
6. Open Serial Monitor at 115200 baud to confirm it connects and registers

---

## ESP32 setup

### Board support

File → Preferences → Additional Board Manager URLs:
```
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
```
Tools → Boards Manager → search **esp32** → install **esp32 by Espressif Systems**.

### Libraries (Tools → Manage Libraries)

| Library | Used by |
|---|---|
| ArduinoJson | all 13 sketches |
| Adafruit GFX Library | climate_monitor, air_quality, rfid_lock, thermostat |
| Adafruit SSD1306 | climate_monitor, air_quality, rfid_lock, thermostat |
| DHT sensor library | climate_monitor, thermostat |
| Adafruit Unified Sensor | DHT dependency |
| MFRC522 | rfid_lock |
| FastLED | rgb_lights |

`WiFi.h`, `HTTPClient.h`, `WebServer.h`, `Wire.h`, `SPI.h`, `esp_camera.h`, `esp_sleep.h`, and `mbedtls` (HMAC-SHA256) all ship with the ESP32 board package — no extra install.

### Recommended boards

| Sketches | Board |
|---|---|
| door, motion, flood, smoke, panic | ESP32-C3 Dev Module |
| doorbell cam | AI Thinker ESP32-CAM |
| climate, air_quality, thermostat, plug, lights, rfid, garage | ESP32 Dev Module or ESP32-S3 |

---

## The AI (NOVA)

Runs a fine-tuned **Qwen 2.5-0.5B** or **Llama 3.2-1B** locally on the Pi via Ollama. Rename it anything in dashboard settings.

**On every sensor event or NL command, NOVA:**
1. Pulls relevant entries from RAG memory — household facts you've taught it + device rules
2. Checks `writable` and `readable` fields on each device before outputting any action
3. Returns a validated JSON action list — only allowed actions, only device IDs that exist

**78 RAG entries seed automatically on startup** covering all 13 device types: valid state values, writable vs readable fields, command schemas, and hard constraints like "cannot close garage if car_present=true" and "temp_f is a sensor reading — not writable."

**Natural language commands** — "lock everything down", "movie mode", "I'm cold", "goodnight" — translate to the same validated action format.

**Teach it facts** via dashboard → Settings → AI Memory, or `POST /ai/memory`:
```json
{ "text": "We have a dog named Max — low motion at night is probably him", "category": "person" }
```

### Hub API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/sensor` | POST | Key + HMAC sig | ESP32 reports a sensor event |
| `/register` | POST | Key + HMAC sig | Actuator announces itself on boot |
| `/snapshot/<id>` | POST | Key + HMAC sig | Doorbell uploads a JPEG |
| `/command` | POST | Key | Direct device control |
| `/ai/command` | POST | Key | Natural language → actions |
| `/ai/memory` | GET/POST/DELETE | Key | Manage RAG knowledge base |
| `/ai/reseed` | POST | Key | Re-seed device schemas after updates |
| `/ai/log` | GET | Key | AI reasoning history |
| `/users` | GET/POST | Key | Household members |
| `/users/<id>` | PATCH/DELETE | Key | Update or remove a user |
| `/settings` | GET/PATCH | Key | AI name, model, ntfy topic, night hours, auth toggle |
| `/devices` | GET | Key | All devices + current state |
| `/alerts` | GET | Key | Event history (`?severity=high` to filter) |
| `/access_log` | GET | Key | RFID scan history |

---

## Train your own model

```bash
# Regenerate or expand training data (1909 examples already included)
python3 dataset/generate_training_data.py

# Fine-tune (free Colab T4, ~10-15 min for 0.5B)
python3 dataset/train_unsloth.py

# Load the output GGUF into Ollama on the Pi
ollama create openhome -f pi/Modelfile

# Switch to it: dashboard → Settings → Model → "openhome"
```

The training data covers 18 categories: all sensor event types with time-of-day logic, 200+ light commands, 200+ thermostat commands, routines (goodnight/morning/movie/party/lockdown/away/arrive), 100 constraint examples teaching what the AI must NOT do, and 50 no-op restraint examples so it returns `[]` instead of inventing actions.

AMD GPU note: Unsloth targets CUDA. On an RX 6700 XT, use free Colab T4 or the `transformers + peft` fallback block at the bottom of `train_unsloth.py`.

---

## Voice listeners (Google Home / Alexa replacement) 🎙

Want voice control? Add ESP32-S3 or Pi Zero 2 W "listener satellites" around the house. They:

1. **Record audio** when you hold a button (or say a wake word on the Pi Zero version)
2. **Stream to the Pi** over WebSocket
3. **Pi runs Whisper** (faster-whisper, local) → transcript
4. **Transcript goes to NOVA** → actions + spoken reply
5. **Pi runs Piper** (local neural TTS, your custom voice) → WAV
6. **Listener plays the reply** through I2S amp

Everything is local. No Google. No Alexa. No ElevenLabs subscription.

**Hardware ~$25 per listener:** ESP32-S3 + INMP441 mic + MAX98357A amp + speaker
**Or ~$15:** Pi Zero 2 W + USB mic/speaker (better quality, can use a touch screen)

**Custom voice:** train your own from 30 min of recordings, or use any of [100+ prebuilt Piper voices](https://huggingface.co/rhasspy/piper-voices). See `pi/voices/README.md`.

**Setup:** install voice deps on the Pi (`faster-whisper`, `piper-tts`), drop a `.onnx` voice into `pi/voices/`, flash the listener firmware. The hub exposes `WS /voice/stream` for the audio pipeline.

---

## Push notifications

Uses [ntfy.sh](https://ntfy.sh) — free, no account needed.

1. Install the ntfy app on your phone
2. Subscribe to a topic name of your choice
3. Dashboard → Settings → ntfy topic

---

## Cost

| | openHome | SimpliSafe |
|---|---|---|
| Hardware | ~$300 | $400–600 |
| Monthly fee | $0 | $20–30/mo |
| Owns your data | Yes | No |
| Local AI | Yes | No |
| Open source | Yes | No |

---

## Roadmap

- [x] 13 ESP32 firmware sketches, Arduino IDE ready
- [x] **Voice listener satellites** (ESP32-S3 or Pi Zero 2 W) — local STT + TTS, no cloud
- [x] Bidirectional state sync — actuators echo every change back so AI sees user-driven changes
- [x] Unified JSON payload standard across all devices
- [x] Persistent storage — all state survives Pi reboots
- [x] API key + HMAC-signed sensor authentication
- [x] Local AI reasoning (Ollama + RAG memory)
- [x] Device schema RAG — AI knows writable vs readable per device
- [x] Users, preferences, settings, AI memory management
- [x] Web dashboard — live device grid, AI command box, settings modal
- [x] 1909 training examples + Unsloth fine-tune script
- [ ] DPO refinement pass using Qwen as critic
- [ ] MIFARE DESFire RFID (challenge-response, not cloneable UID)
- [ ] Voice input on Pi
- [ ] Tailscale automated setup script

## License

MIT — see [LICENSE](LICENSE)
