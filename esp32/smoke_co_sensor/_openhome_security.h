/*
 * openHome Security Helper (ESP32)
 * ─────────────────────────────────
 * Shared functions for authenticated, signed communication with the hub.
 * Uses the ESP32's built-in mbedtls for HMAC-SHA256 (no extra library).
 *
 * Copy this file into each sketch folder (Arduino compiles per-folder).
 * The repo ships a copy in every folder already.
 *
 * Usage in a sketch:
 *   #include "config.h"
 *   #include "_openhome_security.h"
 *   ...
 *   postSigned("/sensor", payload);        // signs + sends with both headers
 *   postWithKey("/register", payload);     // API key only (no signature)
 *
 * For actuators receiving /control:
 *   - collect BOTH headers:  {"X-OpenHome-Key", "X-OpenHome-Sig"}
 *   - verify with checkSignedCommand(server, body, ts) — see below
 */
#ifndef OPENHOME_SECURITY_H
#define OPENHOME_SECURITY_H

#include <HTTPClient.h>
#include <WebServer.h>
#include <mbedtls/md.h>
#include <time.h>

// Compute HMAC-SHA256 of `msg` using HMAC_KEY, return lowercase hex string.
inline String hmacSign(const String& msg) {
  byte hmacResult[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_type_t mdType = MBEDTLS_MD_SHA256;
  const size_t keyLen = strlen(HMAC_KEY);

  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(mdType), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)HMAC_KEY, keyLen);
  mbedtls_md_hmac_update(&ctx, (const unsigned char*)msg.c_str(), msg.length());
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);

  String hex = "";
  for (int i = 0; i < 32; i++) {
    char buf[3];
    sprintf(buf, "%02x", hmacResult[i]);
    hex += buf;
  }
  return hex;
}

inline String hubUrl(const String& path) {
  return "http://" + String(HUB_IP_ADDR) + ":" + String(HUB_PORT_NUM) + path;
}

// POST with API key + HMAC signature (for /sensor, /register, /snapshot).
inline int postSigned(const String& path, const String& payload) {
  HTTPClient http;
  http.begin(hubUrl(path));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-OpenHome-Key", API_KEY);
  http.addHeader("X-OpenHome-Sig", hmacSign(payload));
  int code = http.POST(payload);
  http.end();
  return code;
}

// POST with API key only (rarely needed; sensor/register use postSigned).
inline int postWithKey(const String& path, const String& payload) {
  HTTPClient http;
  http.begin(hubUrl(path));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-OpenHome-Key", API_KEY);
  int code = http.POST(payload);
  http.end();
  return code;
}

// POST raw bytes (e.g. JPEG snapshot) with key + signature over the bytes.
inline int postSignedBytes(const String& path, uint8_t* data, size_t len) {
  // HMAC over the raw bytes
  byte hmacResult[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)HMAC_KEY, strlen(HMAC_KEY));
  mbedtls_md_hmac_update(&ctx, data, len);
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);
  String sig = "";
  for (int i = 0; i < 32; i++) { char b[3]; sprintf(b, "%02x", hmacResult[i]); sig += b; }

  HTTPClient http;
  http.begin(hubUrl(path));
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-OpenHome-Key", API_KEY);
  http.addHeader("X-OpenHome-Sig", sig);
  int code = http.POST(data, len);
  http.end();
  return code;
}

// Constant-time string comparison — doesn't leak where the mismatch is.
inline bool constantTimeEq(const String& a, const String& b) {
  if (a.length() != b.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < a.length(); i++) diff |= (uint8_t)a[i] ^ (uint8_t)b[i];
  return diff == 0;
}

// ── For always-on actuators that RECEIVE commands ──────────
// Legacy key-only check (read-only endpoints like /status, /stream).
inline bool checkServerAuth(WebServer& server) {
  if (!server.hasHeader("X-OpenHome-Key")) return false;
  return constantTimeEq(server.header("X-OpenHome-Key"), String(API_KEY));
}

// Full verification for state-changing /control requests:
//   1. correct API key
//   2. valid HMAC-SHA256 signature over the exact raw body (X-OpenHome-Sig)
//   3. body's "ts" (unix seconds) is fresh (±60s) and strictly increasing,
//      so a sniffed command can't be replayed
// Freshness is only enforced once NTP has synced (time() past year 2020),
// so a device that boots before WiFi/NTP doesn't lock the hub out.
// The sketch must collect BOTH headers via server.collectHeaders().
inline bool checkSignedCommand(WebServer& server, const String& body, long ts) {
  static long lastAcceptedTs = 0;
  if (!server.hasHeader("X-OpenHome-Key") || !server.hasHeader("X-OpenHome-Sig")) return false;
  if (!constantTimeEq(server.header("X-OpenHome-Key"), String(API_KEY))) return false;
  if (!constantTimeEq(server.header("X-OpenHome-Sig"), hmacSign(body))) return false;
  long now = (long)time(nullptr);
  if (now > 1577836800L) {                     // NTP synced (past 2020-01-01)
    if (ts <= lastAcceptedTs) return false;    // replayed or reordered
    if (labs(now - ts) > 60) return false;     // stale or clock-skewed
  }
  lastAcceptedTs = ts;
  return true;
}

#endif // OPENHOME_SECURITY_H
