#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID   = "smoke_kitchen";
const char* DEVICE_TYPE = "smoke_co_sensor";
const char* LOCATION    = "kitchen";

// WRITABLE: none — AI cannot clear a smoke alarm by writing state
// READABLE: state, smoke_detected, co_detected, smoke_pct, co_pct
const int MQ2_DO_PIN = 14;
const int MQ2_AO_PIN = 34;
const int MQ7_DO_PIN = 27;
const int MQ7_AO_PIN = 35;
const int BUZZER_PIN = 26;
const int WARMUP_MS  = 20000;

RTC_DATA_ATTR uint32_t bootCount  = 0;
RTC_DATA_ATTR uint32_t alertCount = 0;

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
  pinMode(MQ2_DO_PIN, INPUT);
  pinMode(MQ7_DO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  if (bootCount == 1) delay(WARMUP_MS);

  bool smokeAlert = (digitalRead(MQ2_DO_PIN) == HIGH);
  bool coAlert    = (digitalRead(MQ7_DO_PIN) == HIGH);
  int  smokePct   = map(analogRead(MQ2_AO_PIN), 0, 4095, 0, 100);
  int  coPct      = map(analogRead(MQ7_AO_PIN), 0, 4095, 0, 100);

  if (smokeAlert || coAlert) {
    alertCount++;
    soundBuzzer(smokeAlert, coAlert);
    connectAndReport
  syncNTP();(smokeAlert, coAlert, smokePct, coPct);
  }
  goToSleep();
}

void soundBuzzer(bool smoke, bool co) {
  int beepMs = smoke ? 100 : 500, silenceMs = smoke ? 100 : 1000;
  for (int i = 0; i < 5; i++) {
    digitalWrite(BUZZER_PIN, HIGH); delay(beepMs);
    digitalWrite(BUZZER_PIN, LOW);  delay(silenceMs);
  }
}

void connectAndReport(bool smoke, bool co, int smokePct, int coPct) {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(500);
  if (WiFi.status() != WL_CONNECTED) return;

  // severity set once, cleanly
  const char* sev = (smoke && co) ? "critical" : co ? "high" : "medium";

  StaticJsonDocument<512> doc;
  doc["device_id"]      = DEVICE_ID;
  doc["type"]           = DEVICE_TYPE;
  doc["location"]       = LOCATION;
  doc["state"]          = "alert";        // standardized: "alert" | "clear"
  doc["severity"]       = sev;
  doc["controllable"]   = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("smoke_detected"); r.add("co_detected");
  r.add("smoke_pct"); r.add("co_pct"); r.add("alert_count");
  doc["smoke_detected"] = smoke;
  doc["co_detected"]    = co;
  doc["smoke_pct"]      = smokePct;
  doc["co_pct"]         = coPct;
  doc["alert_count"]    = alertCount;
  doc["boot_count"]     = bootCount;
  doc["timestamp"]      = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  WiFi.disconnect(true);
}

void goToSleep() {
  uint64_t wakeMask = (1ULL << MQ2_DO_PIN) | (1ULL << MQ7_DO_PIN);
  esp_sleep_enable_ext1_wakeup(wakeMask, ESP_EXT1_WAKEUP_ANY_HIGH);
  esp_sleep_enable_timer_wakeup(60ULL * 1000000);
  esp_deep_sleep_start();
}
void loop() {}
