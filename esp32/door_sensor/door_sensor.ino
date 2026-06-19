#include "config.h"
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/rtc_io.h>

// ─── DEVICE IDENTITY ──────────────────────────────────────
const char* DEVICE_ID   = "door_front";
const char* DEVICE_TYPE = "door_sensor";
const char* LOCATION    = "front_door";

// WRITABLE: none — AI cannot close/open a physical door by changing state
// READABLE: state, boot_count
const char* WRITABLE[]  = {};
const char* READABLE[]  = {"state", "boot_count"};

const int REED_PIN = 14;
// ──────────────────────────────────────────────────────────

RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR bool lastState = false;

String isoTimestamp() {
  // Placeholder — replace with NTP if you add time sync
  return "1970-01-01T00:00:00";
}

void setup() {
  Serial.begin(115200);
  delay(100);
  bootCount++;
  pinMode(REED_PIN, INPUT_PULLUP);

  bool doorOpen = (digitalRead(REED_PIN) == HIGH);
  if (doorOpen != lastState) {
    lastState = doorOpen;
    connectAndReport(doorOpen);
  }
  goToSleep();
}

void connectAndReport(bool isOpen) {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(500);
  if (WiFi.status() != WL_CONNECTED) return;

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = isOpen ? "open" : "closed";
  doc["severity"]     = isOpen ? "medium" : "none";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("boot_count");
  doc["boot_count"]   = bootCount;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  WiFi.disconnect(true);
}

void goToSleep() {
  // Digital-domain pullup dies in deep sleep — keep the RTC one alive
  // so the reed pin doesn't float and cause spurious wakeups.
  rtc_gpio_pullup_en((gpio_num_t)REED_PIN);
  rtc_gpio_pulldown_dis((gpio_num_t)REED_PIN);
  esp_sleep_enable_ext0_wakeup((gpio_num_t)REED_PIN, lastState ? LOW : HIGH);
  esp_deep_sleep_start();
}
void loop() {}
