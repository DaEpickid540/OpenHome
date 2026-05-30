/*
 * openHome — Shared Configuration Template
 * ─────────────────────────────────────────
 * Each sketch folder needs its own config.h. This repo ships one with
 * placeholders in every firmware folder. Edit the folder you're flashing.
 *
 * config.h is gitignored so you never commit WiFi or keys.
 *
 * Get API_KEY and HMAC_KEY by running on the Pi:  python3 gen_keys.py
 * Paste the SAME keys here that the hub uses, or nothing will authenticate.
 */
#ifndef OPENHOME_CONFIG_H
#define OPENHOME_CONFIG_H

#define WIFI_SSID      "YOUR_WIFI"
#define WIFI_PASSWORD  "YOUR_PASS"

#define HUB_IP_ADDR    "192.168.1.100"
#define HUB_PORT_NUM   8765

// Security keys — MUST match the hub's secrets_config.py exactly.
// API_KEY  : sent on every request (X-OpenHome-Key header)
// HMAC_KEY : signs sensor payloads (X-OpenHome-Sig header)
#define API_KEY        "CHANGE_ME_RUN_GEN_KEYS"
#define HMAC_KEY       "CHANGE_ME_RUN_GEN_KEYS"

#endif // OPENHOME_CONFIG_H
