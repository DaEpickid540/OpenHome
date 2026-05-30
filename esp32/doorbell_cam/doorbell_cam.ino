#include "config.h"
#include <time.h>
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"

const char* DEVICE_ID   = "doorbell_front";
const char* DEVICE_TYPE = "doorbell";
const char* LOCATION    = "front_door";

// WRITABLE: none — AI cannot press the doorbell or clear a ring
// READABLE: state, ring_count, has_photo

const int BUTTON_PIN = 13;
const int BUZZ_PIN   = 12;

#define CAM_PIN_PWDN 32
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 0
#define CAM_PIN_SIOD 26
#define CAM_PIN_SIOC 27
#define CAM_PIN_D7 35
#define CAM_PIN_D6 34
#define CAM_PIN_D5 39
#define CAM_PIN_D4 38
#define CAM_PIN_D3 37
#define CAM_PIN_D2 36
#define CAM_PIN_D1 21
#define CAM_PIN_D0 19
#define CAM_PIN_VSYNC 25
#define CAM_PIN_HREF 23
#define CAM_PIN_PCLK 22

RTC_DATA_ATTR uint32_t bootCount = 0;
RTC_DATA_ATTR uint32_t ringCount = 0;

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
  bootCount++;
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(BUZZ_PIN, OUTPUT);
  digitalWrite(BUZZ_PIN, LOW);

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  if (cause != ESP_SLEEP_WAKEUP_EXT0 && bootCount > 1) { goToSleep(); return; }

  ringCount++;
  ringChime();
  connectWiFi();
  syncNTP();
  initCamera();
  camera_fb_t* fb = esp_camera_fb_get();
  sendEvent(fb != nullptr);
  if (fb) {
    uploadSnapshot(fb);
    esp_camera_fb_return(fb);
  }
  WiFi.disconnect(true);
  goToSleep();
}

void ringChime() {
  digitalWrite(BUZZ_PIN, HIGH); delay(300);
  digitalWrite(BUZZ_PIN, LOW);  delay(200);
  digitalWrite(BUZZ_PIN, HIGH); delay(200);
  digitalWrite(BUZZ_PIN, LOW);
}

void initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0; config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D0; config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2; config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4; config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6; config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk = CAM_PIN_XCLK; config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC; config.pin_href = CAM_PIN_HREF;
  config.pin_sscb_sda = CAM_PIN_SIOD; config.pin_sscb_scl = CAM_PIN_SIOC;
  config.pin_pwdn = CAM_PIN_PWDN; config.pin_reset = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000; config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA; config.jpeg_quality = 12; config.fb_count = 1;
  esp_camera_init(&config);
  delay(500);
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) delay(500);
}

void sendEvent(bool hasPhoto) {
  if (WiFi.status() != WL_CONNECTED) return;

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = "ring";          // standardized: "ring" | "idle"
  doc["severity"]     = "low";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("ring_count"); r.add("has_photo");
  doc["ring_count"]   = ringCount;
  doc["has_photo"]    = hasPhoto;
  doc["boot_count"]   = bootCount;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
}

void uploadSnapshot(camera_fb_t* fb) {
  postSignedBytes("/snapshot/" + String(DEVICE_ID), fb->buf, fb->len);
}

void goToSleep() {
  esp_sleep_enable_ext0_wakeup((gpio_num_t)BUTTON_PIN, LOW);
  esp_deep_sleep_start();
}
void loop() {}
