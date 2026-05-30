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

const char* DEVICE_ID   = "climate_bedroom";
const char* DEVICE_TYPE = "climate_monitor";
const char* LOCATION    = "bedroom";

// WRITABLE: none — AI cannot change temperature by writing state
// READABLE: state, temp_f, humidity, comfort
const int DHT_PIN  = 14;
const int DHT_TYPE = DHT22;
#define SCREEN_W 128
#define SCREEN_H 64

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
DHT dht(DHT_PIN, DHT_TYPE);
WebServer server(80);

float tempF = 70.0, humidity = 50.0;
const float TEMP_LOW = 65.0, TEMP_HIGH = 80.0;
const float HUM_LOW  = 30.0, HUM_HIGH  = 60.0;
const unsigned long REPORT_MS = 5 * 60 * 1000;
unsigned long lastReport = 0;

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

String comfortState() {
  if (tempF > 85.0 || humidity > 70.0) return "alert";
  if (tempF < TEMP_LOW || tempF > TEMP_HIGH || humidity < HUM_LOW || humidity > HUM_HIGH)
    return "uncomfortable";
  return "comfortable";
}

void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

void setup() {
  Serial.begin(115200);
  dht.begin();
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.setTextColor(SSD1306_WHITE);
  connectWiFi();
  syncNTP();
  readAndReport();
  server.begin();
}

void loop() {
  server.handleClient();
  static unsigned long lastRead = 0;
  if (millis() - lastRead > 5000) { readSensors(); updateDisplay(); lastRead = millis(); }
  if (millis() - lastReport > REPORT_MS) reportToHub();
}

void readSensors() {
  float h = dht.readHumidity(), c = dht.readTemperature();
  if (!isnan(h) && !isnan(c)) { humidity = h; tempF = (c * 9.0/5.0) + 32.0; }
}

void readAndReport() { readSensors(); updateDisplay(); reportToHub(); }

void updateDisplay() {
  display.clearDisplay();
  display.setTextSize(1); display.setCursor(0,0); display.print("TEMP");
  display.setTextSize(3); display.setCursor(0,12); display.printf("%.1f",tempF);
  display.setTextSize(1); display.print("F");
  display.drawFastHLine(0,38,128,SSD1306_WHITE);
  display.setTextSize(1); display.setCursor(0,42); display.print("HUMIDITY");
  display.setTextSize(2); display.setCursor(0,52); display.printf("%.1f%%",humidity);
  display.display();
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void reportToHub() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  String comfort = comfortState();

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = comfort;         // standardized: "comfortable" | "uncomfortable" | "alert"
  doc["severity"]     = comfort == "alert" ? "medium" : "none";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("temp_f"); r.add("humidity");
  doc["temp_f"]       = tempF;
  doc["humidity"]     = humidity;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  lastReport = millis();
}
