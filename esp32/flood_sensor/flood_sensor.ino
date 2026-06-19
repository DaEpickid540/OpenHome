#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID   = "flood_basement";
const char* DEVICE_TYPE = "flood_sensor";
const char* LOCATION    = "basement";

// WRITABLE: none — AI cannot dry a floor by setting state to dry
// READABLE: state, moisture_pct
const int SENSOR_DO_PIN = 14;
const int SENSOR_AO_PIN = 34;
const int WET_THRESHOLD = 2000;

RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR bool lastWet = false;

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
  delay(100);
  bootCount++;
  pinMode(SENSOR_DO_PIN, INPUT);

  bool isWet  = (digitalRead(SENSOR_DO_PIN) == LOW);
  int rawLevel = analogRead(SENSOR_AO_PIN);
  int moisturePct = map(rawLevel, 0, 4095, 0, 100);
  bool wet = isWet || rawLevel > WET_THRESHOLD;
  // Report while wet, AND once when it dries out — otherwise the hub
  // shows "wet" forever after a flood.
  if (wet || wet != lastWet) {
    lastWet = wet;
    connectAndReport(wet, moisturePct);
  }
  goToSleep();
}

void connectAndReport(bool isWet, int pct) {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(500);
  if (WiFi.status() != WL_CONNECTED) return;
  syncNTP();

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = isWet ? "wet" : "dry";
  doc["severity"]     = isWet ? "high" : "none";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("moisture_pct"); r.add("boot_count");
  doc["moisture_pct"] = pct;
  doc["boot_count"]   = bootCount;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  WiFi.disconnect(true);
}

void goToSleep() {
  esp_sleep_enable_ext0_wakeup((gpio_num_t)SENSOR_DO_PIN, LOW);
  esp_sleep_enable_timer_wakeup(5ULL * 60 * 1000000);
  esp_deep_sleep_start();
}
void loop() {}
