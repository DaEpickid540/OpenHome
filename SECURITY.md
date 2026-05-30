# openHome Security Guide

How openHome protects against unauthorized access, and the steps you should
take to lock it down properly.

## Threat model — what we actually defend against

| Threat | Defended? | How |
|---|---|---|
| Someone on your WiFi controlling your devices | ✅ Yes | API key on every request |
| Spoofed sensor events (fake "smoke clear", fake panic) | ✅ Yes | HMAC-signed payloads |
| Replaying captured commands | ⚠️ Partial | HMAC + (optional) timestamps |
| LAN traffic sniffing | ⚠️ Optional | HTTPS / Tailscale (see below) |
| Internet attackers | ✅ Yes, if you follow rules | Never port-forward; use Tailscale |
| Flipper Zero cloning your RFID card | ❌ No (by design) | See "RFID reality check" below |

## Layer 1 — API key (always on)

Every request to the hub must carry the header `X-OpenHome-Key: <your key>`.
Without it, the hub returns `401 Unauthorized`. This stops anyone on your
network who doesn't have the key — which is everyone except your own devices
and dashboard.

The hub also sends this key when it commands actuators (plugs, lights,
thermostat, garage), and those actuators reject any `/control` request that
doesn't carry it. So an attacker can't directly command a smart plug either.

## Layer 2 — HMAC signatures (sensors)

The dangerous attack isn't turning your lights off — it's **lying to the AI**.
If an attacker could POST a fake `{"type":"smoke_co_sensor","state":"clear"}`
during a real fire, or a fake panic at 3am, that's a safety problem.

To prevent this, battery sensors sign their payload with a **separate** HMAC
key. The signature (`X-OpenHome-Sig`) is an HMAC-SHA256 over the exact request
body. The hub recomputes it and rejects any mismatch. Because the signature is
keyed and covers the whole body, an attacker can't forge a valid event even if
they somehow learned the API key.

`/sensor`, `/register`, and `/snapshot` all require a valid signature.

## Setup (do this once)

### 1. Generate keys on the Pi
```bash
cd pi
python3 gen_keys.py
```
This creates `pi/secrets_config.py` (gitignored) and prints two keys:
```
API_KEY  = a1b2c3...
HMAC_KEY = d4e5f6...
```

### 2. Put both keys in every ESP32
Edit each `esp32/<device>/config.h`:
```cpp
#define API_KEY   "a1b2c3..."   // same as hub
#define HMAC_KEY  "d4e5f6..."   // same as hub
```
Re-flash each device.

### 3. Dashboard
The dashboard prompts for the API key on first load and stores it in
`sessionStorage` (cleared when the tab closes — never written to disk).
If you enter the wrong key you'll get re-prompted on the next action.

### 4. Verify
Try `curl http://<pi-ip>:8765/devices` with no key → should return 401.
Add `-H "X-OpenHome-Key: <key>"` → should return your devices.

## Layer 3 — Network isolation (free, do tonight)

Even with auth, put your IoT devices on a **separate network** so a compromised
device can't reach your laptop, phone, or NAS.

- Most routers have a **Guest Network** toggle — put all ESP32s + the Pi on it.
- Better: a dedicated **IoT VLAN** if your router/switch supports it.
- The dashboard and the Pi need to talk, so keep them on the same segment, or
  allow that one path through your firewall.

This is the single most effective thing you can do and costs nothing.

## Layer 4 — Remote access (Tailscale, free)

**Never port-forward the hub to the internet.** That exposes it to the entire
world and bots scan for open ports constantly.

Instead use [Tailscale](https://tailscale.com) — a free mesh VPN:

```bash
# On the Pi
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Install Tailscale on your phone/laptop too. Now you can reach the hub at its
Tailscale IP (100.x.x.x) from anywhere, fully encrypted, with nothing exposed
to the public internet. Set the dashboard `HUB_URL` to the Tailscale IP when
away from home.

## Layer 5 — HTTPS (optional, for the cautious)

On a trusted home LAN behind a VLAN, plain HTTP is usually acceptable since
the API key + HMAC already authenticate everything. If you want traffic
encrypted on the wire too:

- Easiest: run [Caddy](https://caddyserver.com) as a reverse proxy in front of
  the hub — it auto-manages certs.
- Or generate a self-signed cert and run uvicorn with `--ssl-keyfile` /
  `--ssl-certfile`. Note: browsers will warn on self-signed certs, and ESP32s
  need the cert fingerprint pinned, which complicates firmware.

For most home setups, **VLAN + API key + HMAC + Tailscale** is the sweet spot.
HTTPS is belt-and-suspenders.

## RFID reality check

The `rfid_lock` whitelists cards by UID. **UIDs can be cloned** by a Flipper
Zero or any cheap NFC tool in seconds — this is true of nearly all hobby RFID
projects. Two honest options:

1. **Treat RFID as convenience, not security.** Keep a real deadbolt as the
   actual lock. The RFID just saves you fishing for keys.
2. **Upgrade to challenge-response.** Use MIFARE DESFire cards and authenticate
   against an encrypted sector instead of the UID. This is a meaningful firmware
   project — not done in this repo yet.

Importantly: the AI **cannot** unlock the door over the network. The lock logic
is hardware-only and `controllable: false`. So a network attacker can't open it
even if they breach the hub — they'd need to physically clone a card.

## If a key leaks

1. Re-run `python3 gen_keys.py` (overwrites `secrets_config.py`)
2. Update `config.h` on every ESP32 and re-flash
3. Clear the dashboard key (close the tab, or it auto-clears on 401)

Old keys instantly stop working because the hub only knows the new ones.

## Disabling auth for debugging

If you need to debug without auth (e.g. testing a sensor with `curl`), set
`auth_enabled: false` in the hub settings (dashboard → settings, or
`PATCH /settings`). **Re-enable it before going live.** It defaults to on.
