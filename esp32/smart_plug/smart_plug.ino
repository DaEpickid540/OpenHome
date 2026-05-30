#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>

const char* DEVICE_ID   = "plug_living_room";
const char* DEVICE_TYPE = "smart_plug";
const char* LOCATION    = "living_room";

// WRITABLE: state (on/off/toggle)
// READABLE: none beyond state
const int RELAY_PIN = 26;
const int LED_PIN   = 2;

WebServer server(80);
bool relayState = false;

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT); pinMode(LED_PIN, OUTPUT);
  setRelay(false);
  connectWiFi();
  syncNTP();
  registerWithHub();
  setupRoutes();
  const char* hdrKeys[] = {"X-OpenHome-Key"};
  server.collectHeaders(hdrKeys, 1);
  server.begin();
}

void loop() { server.handleClient(); }

void setRelay(bool on) {
  relayState = on;
  digitalWrite(RELAY_PIN, on ? HIGH : LOW);
  digitalWrite(LED_PIN,   on ? HIGH : LOW);
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void buildPayload(JsonDocument& doc, bool forRegister) {
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = relayState ? "on" : "off";
  doc["severity"]     = "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("state");                        // AI can send on/off/toggle
  doc.createNestedArray("readable");     // nothing read-only beyond state
  doc["timestamp"]    = isoTimestamp();
  if (forRegister) {
    doc["ip"]   = WiFi.localIP().toString();
    doc["port"] = 80;
  }
}

void registerWithHub() {
  StaticJsonDocument<256> doc; buildPayload(doc, true);
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

// Send state echo to hub whenever we change. Lets the AI + dashboard
// see the new state even if the change came from /control or future
// physical button input.
void reportToHub() {
  StaticJsonDocument<256> doc; buildPayload(doc, false);
  String p; serializeJson(doc, p);
  postSigned("/sensor", p);
}

void setupRoutes() {
  server.on("/status", HTTP_GET, []() {
    StaticJsonDocument<128> doc;
    doc["device_id"] = DEVICE_ID; doc["state"] = relayState ? "on" : "off";
    String res; serializeJson(doc, res);
    server.send(200, "application/json", res);
  });
  server.on("/control", HTTP_POST, []() {
    if (!checkServerAuth(server)) { server.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return; }
    if (!server.hasArg("plain")) { server.send(400); return; }
    StaticJsonDocument<64> doc;
    deserializeJson(doc, server.arg("plain"));
    String state = doc["state"] | "toggle";
    if (state == "on")       setRelay(true);
    else if (state == "off") setRelay(false);
    else                     setRelay(!relayState);
    server.send(200, "application/json",
      "{\"ok\":true,\"state\":\"" + String(relayState ? "on" : "off") + "\"}");
    // Echo new state back to hub so AI/dashboard see it
    reportToHub();
  });
}
