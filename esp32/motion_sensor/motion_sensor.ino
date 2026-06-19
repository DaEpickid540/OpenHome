#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID   = "motion_hallway";
const char* DEVICE_TYPE = "motion_sensor";
const char* LOCATION    = "hallway";

// WRITABLE: none — AI cannot fake motion or clear it
// READABLE: state, trigger_count
const int PIR_PIN = 14;

RTC_DATA_ATTR uint32_t bootCount    = 0;
RTC_DATA_ATTR uint32_t triggerCount = 0;

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
  pinMode(PIR_PIN, INPUT);
  delay(200);

  bool motion = digitalRead(PIR_PIN);
  if (motion) {
    triggerCount++;
    connectAndReport();
  }
  goToSleep();
}

void connectAndReport() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(500);
  if (WiFi.status() != WL_CONNECTED) return;
  syncNTP();

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = "detected";       // standardized: "detected" | "clear"
  doc["severity"]     = "low";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("trigger_count"); r.add("boot_count");
  doc["trigger_count"] = triggerCount;
  doc["boot_count"]    = bootCount;
  doc["timestamp"]     = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  WiFi.disconnect(true);
}

void goToSleep() {
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIR_PIN, HIGH);
  esp_deep_sleep_start();
}
void loop() {}
