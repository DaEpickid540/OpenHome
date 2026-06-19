#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <FastLED.h>

const char* DEVICE_ID   = "lights_living_room";
const char* DEVICE_TYPE = "rgb_lights";
const char* LOCATION    = "living_room";

// WRITABLE: state, mode, color, brightness
// READABLE: none beyond writable fields
const int LED_PIN   = 5;
const int NUM_LEDS  = 60;

CRGB leds[NUM_LEDS];
WebServer server(80);
uint8_t brightness = 128;
CRGB    solidColor = CRGB::White;
String  currentMode = "solid";
bool    stripOn = true;
uint8_t hue = 0;
uint8_t pulseVal = 0;
int8_t  pulseDir = 1;
unsigned long lastFrame = 0;

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
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS).setCorrection(TypicalLEDStrip);
  FastLED.setBrightness(brightness);
  FastLED.clear(); FastLED.show();
  connectWiFi();
  syncNTP();
  registerWithHub();
  setupRoutes();
  const char* hdrKeys[] = {"X-OpenHome-Key", "X-OpenHome-Sig"};
  server.collectHeaders(hdrKeys, 2);
  server.begin();
  for (int i = 0; i < NUM_LEDS; i++) { leds[i] = CRGB::White; FastLED.show(); delay(10); }
  setMode("solid");
}

void loop() {
  server.handleClient();
  if (millis() - lastFrame >= 20) { lastFrame = millis(); runFrame(); }
}

void runFrame() {
  if (!stripOn || currentMode == "off" || currentMode == "solid") return;
  if (currentMode == "rainbow") { fill_rainbow(leds, NUM_LEDS, hue++, 7); FastLED.show(); }
  else if (currentMode == "pulse") {
    pulseVal += pulseDir * 3;
    if (pulseVal >= 255 || pulseVal <= 10) pulseDir *= -1;
    fill_solid(leds, NUM_LEDS, solidColor);
    FastLED.setBrightness(pulseVal); FastLED.show();
  }
  else if (currentMode == "fire") { runFire(); FastLED.show(); }
  else if (currentMode == "alert") {
    static bool fl = false; static unsigned long lf = 0;
    if (millis() - lf > 250) { fl = !fl; fill_solid(leds, NUM_LEDS, fl ? CRGB::Red : CRGB::Black); FastLED.show(); lf = millis(); }
  }
}

void runFire() {
  static byte heat[60];
  for (int i = 0; i < NUM_LEDS; i++) heat[i] = qsub8(heat[i], random8(0, 4));
  for (int k = 2; k < NUM_LEDS; k++) heat[k] = (heat[k]+heat[k-1]+heat[k-2])/3;
  if (random8() < 80) { int y = random8(7); heat[y] = qadd8(heat[y], random8(160,255)); }
  for (int j = 0; j < NUM_LEDS; j++) leds[j] = HeatColor(heat[j]);
}

void setMode(String mode) {
  currentMode = mode;
  FastLED.setBrightness(brightness);
  if (mode == "off")    { stripOn = false; fill_solid(leds, NUM_LEDS, CRGB::Black); FastLED.show(); }
  else if (mode == "solid") { stripOn = true; fill_solid(leds, NUM_LEDS, solidColor); FastLED.show(); }
  else                  { stripOn = true; }
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void buildPayload(JsonDocument& doc, bool forRegister) {
  char hexColor[8];
  sprintf(hexColor, "#%02X%02X%02X", solidColor.r, solidColor.g, solidColor.b);
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = stripOn ? "on" : "off";
  doc["severity"]     = "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("state"); w.add("mode"); w.add("color"); w.add("brightness");
  doc.createNestedArray("readable");
  doc["mode"]         = currentMode;
  doc["color"]        = hexColor;
  doc["brightness"]   = brightness;
  doc["num_leds"]     = NUM_LEDS;
  doc["timestamp"]    = isoTimestamp();
  if (forRegister) {
    doc["ip"]   = WiFi.localIP().toString();
    doc["port"] = 80;
  }
}

void registerWithHub() {
  StaticJsonDocument<384> doc; buildPayload(doc, true);
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

// Echo new state to hub after any change.
void reportToHub() {
  StaticJsonDocument<384> doc; buildPayload(doc, false);
  String p; serializeJson(doc, p);
  postSigned("/sensor", p);
}

void setupRoutes() {
  server.on("/status", HTTP_GET, []() {
    StaticJsonDocument<384> doc; buildPayload(doc, false);
    String res; serializeJson(doc, res);
    server.send(200, "application/json", res);
  });
  server.on("/control", HTTP_POST, []() {
    if (!server.hasArg("plain")) { server.send(400); return; }
    String body = server.arg("plain");
    StaticJsonDocument<192> doc;
    if (deserializeJson(doc, body)) { server.send(400, "application/json", "{\"error\":\"bad json\"}"); return; }
    if (!checkSignedCommand(server, body, doc["ts"] | 0L)) { server.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return; }
    if (doc.containsKey("brightness")) {
      brightness = constrain((int)doc["brightness"], 0, 255);
      FastLED.setBrightness(brightness); FastLED.show();
    }
    if (doc.containsKey("color")) {
      String hex = String((const char*)doc["color"]); hex.replace("#","");
      long rgb = strtol(hex.c_str(), nullptr, 16);
      solidColor = CRGB((rgb>>16)&0xFF, (rgb>>8)&0xFF, rgb&0xFF);
      if (currentMode == "solid") { fill_solid(leds, NUM_LEDS, solidColor); FastLED.show(); }
    }
    if (doc.containsKey("state") || doc.containsKey("mode")) {
      String state = doc["state"] | "on";
      String mode  = doc["mode"]  | currentMode;
      if (state == "off") setMode("off"); else setMode(mode);
    }
    server.send(200, "application/json", "{\"ok\":true}");
    reportToHub();   // echo new state so AI/dashboard see it
  });
}
