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
 */
#ifndef OPENHOME_SECURITY_H
#define OPENHOME_SECURITY_H

#include <HTTPClient.h>
#include <mbedtls/md.h>

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

// ── For always-on actuators that RECEIVE commands ──────────
// Call at the top of your /control handler to reject unauthorized commands.
// Returns true if the request carries the correct API key.
inline bool checkServerAuth(WebServer& server) {
  if (!server.hasHeader("X-OpenHome-Key")) return false;
  return server.header("X-OpenHome-Key") == String(API_KEY);
}

#endif // OPENHOME_SECURITY_H
