#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>

const char* DEVICE_ID   = "garage_door_main";
const char* DEVICE_TYPE = "garage_door";
const char* LOCATION    = "garage";

// WRITABLE: state (open/close/toggle via relay pulse)
// READABLE: state, car_present
// NOTE: AI can command toggle/open/close — the relay pulses the wall button
const int RELAY_PIN       = 26;
const int RELAY_PULSE_MS  = 300;
const int REED_PIN        = 14;
const int TRIG_PIN        = 27;
const int ECHO_PIN        = 25;
const int CAR_DETECT_CM   = 150;
const int AUTO_CLOSE_MIN  = 10;

WebServer server(80);
bool doorOpen = false;
bool carPresent = false;
unsigned long openedAt = 0;

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
  pinMode(RELAY_PIN, OUTPUT); digitalWrite(RELAY_PIN, LOW);
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT); pinMode(ECHO_PIN, INPUT);
  connectWiFi();
  syncNTP();
  readSensors();
  registerWithHub();
  setupRoutes();
  const char* hdrKeys[] = {"X-OpenHome-Key", "X-OpenHome-Sig"};
  server.collectHeaders(hdrKeys, 2);
  server.begin();
}

void loop() {
  server.handleClient();
  static unsigned long lastPoll = 0;
  if (millis() - lastPoll > 2000) {
    bool prev = doorOpen;
    readSensors();
    if (doorOpen != prev) {
      if (doorOpen) openedAt = millis();
      reportState();
    }
    if (AUTO_CLOSE_MIN > 0 && doorOpen &&
        millis() - openedAt > (unsigned long)(AUTO_CLOSE_MIN * 60000)) {
      pulseRelay();
      reportState();
    }
    lastPoll = millis();
  }
}

void readSensors() {
  doorOpen   = (digitalRead(REED_PIN) == LOW);
  carPresent = (measureDistanceCm() < CAR_DETECT_CM);
}

long measureDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long d = pulseIn(ECHO_PIN, HIGH, 30000);
  if (d == 0) return 9999;   // timeout / sensor unplugged — NOT "0 cm away"
  return d * 0.034 / 2;
}

void pulseRelay() {
  digitalWrite(RELAY_PIN, HIGH); delay(RELAY_PULSE_MS); digitalWrite(RELAY_PIN, LOW);
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void buildPayload(JsonDocument& doc, bool forRegister) {
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = doorOpen ? "open" : "closed";
  doc["severity"]     = doorOpen ? "medium" : "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("state");                        // AI can send open/close/toggle
  JsonArray r = doc.createNestedArray("readable");
  r.add("car_present");
  doc["car_present"]  = carPresent;
  doc["timestamp"]    = isoTimestamp();
  if (forRegister) {
    doc["ip"]   = WiFi.localIP().toString();
    doc["port"] = 80;
  }
}

void registerWithHub() {
  StaticJsonDocument<384> doc; buildPayload(doc, true);
  String payload; serializeJson(doc, payload);
  postSigned("/register", payload);
}

void reportState() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  StaticJsonDocument<384> doc; buildPayload(doc, false);
  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
}

void setupRoutes() {
  server.on("/status", HTTP_GET, []() {
    StaticJsonDocument<256> doc;
    doc["device_id"]   = DEVICE_ID;
    doc["state"]       = doorOpen ? "open" : "closed";
    doc["car_present"] = carPresent;
    String res; serializeJson(doc, res);
    server.send(200, "application/json", res);
  });
  server.on("/control", HTTP_POST, []() {
    if (!server.hasArg("plain")) { server.send(400); return; }
    String body = server.arg("plain");
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, body)) { server.send(400, "application/json", "{\"error\":\"bad json\"}"); return; }
    if (!checkSignedCommand(server, body, doc["ts"] | 0L)) { server.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return; }
    String state = doc["state"] | "toggle";
    bool shouldAct = (state == "toggle") ||
                     (state == "open" && !doorOpen) ||
                     (state == "close" && doorOpen);
    if (shouldAct) pulseRelay();
    server.send(200, "application/json",
      "{\"ok\":true,\"action\":\"" + String(shouldAct ? "pulsed" : "skipped") + "\"}");
  });
}
