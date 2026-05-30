#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID   = "panic_bedroom";
const char* DEVICE_TYPE = "panic_button";
const char* LOCATION    = "bedroom";

// WRITABLE: none — AI cannot fake a panic or clear it by writing state
// READABLE: state, panic_count
const int BUTTON_PIN = 14;
const int LED_PIN    = 2;
const int BUZZ_PIN   = 26;
const int HOLD_MS    = 2000;
const int CANCEL_WINDOW_S = 10;

RTC_DATA_ATTR uint32_t bootCount  = 0;
RTC_DATA_ATTR uint32_t panicCount = 0;

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
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZ_PIN, OUTPUT);
  digitalWrite(BUZZ_PIN, LOW);

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  if (cause != ESP_SLEEP_WAKEUP_EXT0 && bootCount > 1) { goToSleep(); return; }

  if (HOLD_MS > 0) {
    blinkLED(3, 100);
    unsigned long s = millis();
    while (digitalRead(BUTTON_PIN) == LOW && millis() - s < HOLD_MS) delay(50);
    if (digitalRead(BUTTON_PIN) == HIGH) { goToSleep(); return; }
  }

  panicCount++;
  soundAlarm();
  connectWiFi();
  syncNTP();
  sendEvent("panic", "critical");

  unsigned long ws = millis();
  while (millis() - ws < (unsigned long)(CANCEL_WINDOW_S * 1000)) {
    if (digitalRead(BUTTON_PIN) == LOW) {
      delay(50);
      if (digitalRead(BUTTON_PIN) == LOW) {
        digitalWrite(BUZZ_PIN, LOW);
        sendEvent("cancelled", "none");
        WiFi.disconnect(true);
        goToSleep(); return;
      }
    }
    delay(100);
  }
  digitalWrite(BUZZ_PIN, LOW);
  WiFi.disconnect(true);
  goToSleep();
}

void soundAlarm() {
  for (int i = 0; i < 10; i++) {
    digitalWrite(BUZZ_PIN, HIGH); digitalWrite(LED_PIN, HIGH); delay(150);
    digitalWrite(BUZZ_PIN, LOW);  digitalWrite(LED_PIN, LOW);  delay(80);
  }
  digitalWrite(BUZZ_PIN, HIGH);
}

void blinkLED(int n, int ms) {
  for (int i = 0; i < n; i++) {
    digitalWrite(LED_PIN, HIGH); delay(ms);
    digitalWrite(LED_PIN, LOW);  delay(ms);
  }
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(300);
}

void sendEvent(const char* state, const char* severity) {
  if (WiFi.status() != WL_CONNECTED) return;

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = state;           // "panic" | "cancelled" | "armed"
  doc["severity"]     = severity;
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("panic_count"); r.add("boot_count");
  doc["panic_count"]  = panicCount;
  doc["boot_count"]   = bootCount;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
}

void goToSleep() {
  esp_sleep_enable_ext0_wakeup((gpio_num_t)BUTTON_PIN, LOW);
  esp_deep_sleep_start();
}
void loop() {}
