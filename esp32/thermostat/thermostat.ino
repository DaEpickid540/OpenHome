#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>

const char* DEVICE_ID   = "thermostat_main";
const char* DEVICE_TYPE = "thermostat";
const char* LOCATION    = "hallway";

// WRITABLE: setpoint, mode   — AI can change target temp and heat/cool/auto/off
// READABLE: temp_f, humidity, hvac_action  — AI reads these but cannot write them
// NOTE: hvac_action ("heating"/"cooling"/"idle") is physical relay state — not writable
const int DHT_PIN  = 4;
const int DHT_TYPE = DHT22;
const int RELAY_HEAT = 25;
const int RELAY_COOL = 26;
const int RELAY_FAN  = 27;
const int BTN_UP   = 32;
const int BTN_DOWN = 33;
const int BTN_MODE = 34;
#define SCREEN_W 128
#define SCREEN_H 64

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
DHT dht(DHT_PIN, DHT_TYPE);
WebServer server(80);

float currentTemp = 70.0, currentHum = 50.0;
float setpoint = 72.0;
String mode = "auto";           // heat | cool | auto | off
String hvac_action = "idle";    // heating | cooling | idle  — renamed from "action" to avoid JSON key clash
const float HYSTERESIS = 1.0;
const unsigned long REPORT_MS  = 60 * 1000;
const unsigned long CONTROL_MS = 10 * 1000;
unsigned long lastReport = 0, lastControl = 0, lastDisplay = 0;

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

// State for the hub: the primary state IS the hvac_action for thermostats
String stateString() {
  if (mode == "off") return "off";
  return hvac_action;                    // "heating" | "cooling" | "idle"
}

void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

void setup() {
  Serial.begin(115200); dht.begin();
  pinMode(RELAY_HEAT, OUTPUT); pinMode(RELAY_COOL, OUTPUT); pinMode(RELAY_FAN, OUTPUT);
  allRelaysOff();
  pinMode(BTN_UP, INPUT_PULLUP); pinMode(BTN_DOWN, INPUT_PULLUP); pinMode(BTN_MODE, INPUT_PULLUP);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C); display.setTextColor(SSD1306_WHITE);
  connectWiFi();
  syncNTP(); readSensors(); registerWithHub(); setupRoutes(); const char* hdrKeys[] = {"X-OpenHome-Key"};
  server.collectHeaders(hdrKeys, 1);
  server.begin();
}

void loop() {
  server.handleClient(); handleButtons();
  unsigned long now = millis();
  if (now - lastDisplay > 2000)  { readSensors(); updateDisplay(); lastDisplay = now; }
  if (now - lastControl > CONTROL_MS) { evaluateHVAC(); lastControl = now; }
  if (now - lastReport > REPORT_MS)   { reportToHub();  lastReport  = now; }
}

void readSensors() {
  float h = dht.readHumidity(), c = dht.readTemperature();
  if (!isnan(h) && !isnan(c)) { currentHum = h; currentTemp = (c*9.0/5.0)+32.0; }
}

void evaluateHVAC() {
  String prev = hvac_action;
  if (mode == "off")  { allRelaysOff(); hvac_action = "idle"; }
  else if (mode == "heat") {
    if (currentTemp < setpoint - HYSTERESIS)  startHeating();
    else if (currentTemp >= setpoint)         stopAll();
  }
  else if (mode == "cool") {
    if (currentTemp > setpoint + HYSTERESIS)  startCooling();
    else if (currentTemp <= setpoint)         stopAll();
  }
  else { // auto
    if (currentTemp < setpoint - HYSTERESIS)      startHeating();
    else if (currentTemp > setpoint + HYSTERESIS) startCooling();
    else                                          stopAll();
  }
  if (hvac_action != prev) reportToHub();
}

void startHeating() { digitalWrite(RELAY_COOL,LOW); digitalWrite(RELAY_HEAT,HIGH); digitalWrite(RELAY_FAN,HIGH); hvac_action="heating"; }
void startCooling() { digitalWrite(RELAY_HEAT,LOW); digitalWrite(RELAY_COOL,HIGH); digitalWrite(RELAY_FAN,HIGH); hvac_action="cooling"; }
void stopAll()      { allRelaysOff(); hvac_action="idle"; }
void allRelaysOff() { digitalWrite(RELAY_HEAT,LOW); digitalWrite(RELAY_COOL,LOW); digitalWrite(RELAY_FAN,LOW); }

void handleButtons() {
  static unsigned long lb = 0;
  if (millis()-lb < 250) return;
  if (digitalRead(BTN_UP)==LOW)   { setpoint=constrain(setpoint+1,50,90); evaluateHVAC(); lb=millis(); }
  if (digitalRead(BTN_DOWN)==LOW) { setpoint=constrain(setpoint-1,50,90); evaluateHVAC(); lb=millis(); }
  if (digitalRead(BTN_MODE)==LOW) {
    if (mode=="auto") mode="heat"; else if (mode=="heat") mode="cool";
    else if (mode=="cool") mode="off"; else mode="auto";
    evaluateHVAC(); reportToHub(); lb=millis();
  }
}

void updateDisplay() {
  display.clearDisplay();
  display.setTextSize(1); display.setCursor(0,0); display.print("CURRENT");
  display.setTextSize(3); display.setCursor(0,11); display.printf("%.0f",currentTemp);
  display.setTextSize(1); display.print("F");
  display.setCursor(80,0); display.print("TARGET");
  display.setTextSize(2); display.setCursor(80,11); display.printf("%.0f",setpoint);
  display.drawFastHLine(0,38,128,SSD1306_WHITE);
  display.setTextSize(1); display.setCursor(0,44); display.print("MODE: "); display.print(mode);
  display.setCursor(0,54);
  if (hvac_action=="heating")      display.print(">> HEAT");
  else if (hvac_action=="cooling") display.print("<< COOL");
  else                             display.print("-- IDLE");
  display.display();
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

// Single payload builder — used by register, sensor, and status
void buildPayload(JsonDocument& doc, bool forRegister) {
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = stateString();   // "heating" | "cooling" | "idle" | "off"
  doc["severity"]     = "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("setpoint"); w.add("mode");     // AI can change these
  JsonArray r = doc.createNestedArray("readable");
  r.add("temp_f"); r.add("humidity"); r.add("hvac_action"); // AI reads, cannot write
  doc["setpoint"]     = setpoint;
  doc["mode"]         = mode;
  doc["temp_f"]       = currentTemp;
  doc["humidity"]     = currentHum;
  doc["hvac_action"]  = hvac_action;    // renamed: avoids clash with JSON "action" key
  doc["timestamp"]    = isoTimestamp();
  if (forRegister) { doc["ip"] = WiFi.localIP().toString(); doc["port"] = 80; }
}

void registerWithHub() {
  StaticJsonDocument<512> doc; buildPayload(doc, true);
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

void reportToHub() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  StaticJsonDocument<512> doc; buildPayload(doc, false);
  String p; serializeJson(doc, p);
  postSigned("/sensor", p);
  lastReport = millis();
}

void setupRoutes() {
  server.on("/status", HTTP_GET, []() {
    StaticJsonDocument<512> doc; buildPayload(doc, false);
    String res; serializeJson(doc, res);
    server.send(200, "application/json", res);
  });
  server.on("/control", HTTP_POST, []() {
    if (!checkServerAuth(server)) { server.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return; }
    if (!server.hasArg("plain")) { server.send(400); return; }
    StaticJsonDocument<128> doc;
    deserializeJson(doc, server.arg("plain"));
    if (doc.containsKey("setpoint")) setpoint = constrain((float)doc["setpoint"], 50.0, 90.0);
    if (doc.containsKey("mode"))     mode = String((const char*)doc["mode"]);
    if (doc.containsKey("state"))    {
      String s = String((const char*)doc["state"]);
      if (s=="off") mode="off"; else if (s=="on") mode="auto"; else mode=s;
    }
    evaluateHVAC(); reportToHub();
    StaticJsonDocument<128> res;
    res["ok"]=true; res["setpoint"]=setpoint; res["mode"]=mode; res["hvac_action"]=hvac_action;
    String out; serializeJson(res, out);
    server.send(200, "application/json", out);
  });
}
