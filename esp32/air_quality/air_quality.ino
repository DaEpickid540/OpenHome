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

const char* DEVICE_ID   = "airquality_living";
const char* DEVICE_TYPE = "air_quality";
const char* LOCATION    = "living_room";

// WRITABLE: none — AI cannot clean the air by writing state
// READABLE: state, aqi_pct, aqi_label
const int MQ135_AO_PIN = 34;
const int MQ135_DO_PIN = 14;
#define SCREEN_W 128
#define SCREEN_H 64

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
const int AQI_GOOD = 800, AQI_MODERATE = 1500, AQI_POOR = 2500;
const unsigned long REPORT_MS = 3 * 60 * 1000;
unsigned long lastReport = 0;
int rawAQI = 0;
float smoothedAQI = 0;

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

// Returns standardized state: "good" | "moderate" | "poor" | "hazardous"
String aqiState(float val) {
  if (val < AQI_GOOD)     return "good";
  if (val < AQI_MODERATE) return "moderate";
  if (val < AQI_POOR)     return "poor";
  return "hazardous";
}
String aqiSeverity(String state) {
  if (state == "hazardous") return "critical";
  if (state == "poor")      return "high";
  if (state == "moderate")  return "medium";
  return "none";
}

void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

void setup() {
  Serial.begin(115200);
  pinMode(MQ135_DO_PIN, INPUT);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  showWarmup();
  connectWiFi();
  syncNTP();
  readAndReport();
}

void loop() {
  static unsigned long lastRead = 0;
  if (millis() - lastRead > 3000) { readSensor(); updateDisplay(); lastRead = millis(); }
  if (millis() - lastReport > REPORT_MS) reportToHub();
}

void readSensor() {
  rawAQI = analogRead(MQ135_AO_PIN);
  smoothedAQI = (smoothedAQI == 0) ? rawAQI : (0.2 * rawAQI + 0.8 * smoothedAQI);
}

void readAndReport() { readSensor(); updateDisplay(); reportToHub(); }

void showWarmup() {
  for (int i = 30; i >= 0; i--) {
    display.clearDisplay(); display.setTextSize(1); display.setCursor(15,5);
    display.print("AirWatch Warming Up"); display.setTextSize(2);
    display.setCursor(52,30); display.print(i); display.display(); delay(1000);
  }
}

void updateDisplay() {
  String state = aqiState(smoothedAQI);
  int pct = constrain(map((int)smoothedAQI, 0, 4095, 0, 100), 0, 100);
  display.clearDisplay(); display.setTextSize(1); display.setCursor(0,0);
  display.print("AIR QUALITY");
  display.setTextSize(2); display.setCursor(0,16); display.print(state);
  display.drawRect(0,50,128,10,SSD1306_WHITE);
  display.fillRect(1,51,map(pct,0,100,0,126),8,SSD1306_WHITE);
  display.display();
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void reportToHub() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  int pct = constrain(map((int)smoothedAQI, 0, 4095, 0, 100), 0, 100);
  String state = aqiState(smoothedAQI);

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = state;           // "good" | "moderate" | "poor" | "hazardous"
  doc["severity"]     = aqiSeverity(state);
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("aqi_pct"); r.add("aqi_label");
  doc["aqi_pct"]      = pct;
  doc["aqi_label"]    = state;           // kept for dashboard display
  doc["timestamp"]    = isoTimestamp();
  lastReport = millis();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
}
