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
#include <SPI.h>
#include <MFRC522.h>

const char* DEVICE_ID   = "rfid_front_door";
const char* DEVICE_TYPE = "rfid_lock";
const char* LOCATION    = "front_door";

// WRITABLE: none — AI cannot grant access by writing state
// READABLE: state, uid, authorized, result
// NOTE: The physical lock relay is controlled locally by the whitelist.
// The AI gets scan events but cannot override the hardware lock.
const int RC522_SS_PIN  = 5;
const int RC522_RST_PIN = 22;
const int LOCK_PIN = 26;
const int BUZZ_PIN = 27;
const int UNLOCK_DURATION_S = 5;
const bool LEARN_MODE = false;

const String WHITELIST[] = { "A1B2C3D4", "E5F67890" };
const int WHITELIST_SIZE  = 2;

#define SCREEN_W 128
#define SCREEN_H 64
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
MFRC522 rfid(RC522_SS_PIN, RC522_RST_PIN);
bool isLocked = true;

// Returns ISO-style timestamp (millis as fallback until NTP added)
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
  SPI.begin(); rfid.PCD_Init();
  pinMode(LOCK_PIN, OUTPUT); pinMode(BUZZ_PIN, OUTPUT);
  setLock(true);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  connectWiFi();
  syncNTP();
  registerWithHub();
  showReady();
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;
  String uid = getUID();
  bool authorized = !LEARN_MODE && isWhitelisted(uid);
  const char* result = LEARN_MODE ? "learn_mode" : (authorized ? "granted" : "denied");

  if (authorized) {
    beep(2, 100); setLock(false);
    displayMessage("ACCESS", "GRANTED", 1000);
    delay(UNLOCK_DURATION_S * 1000);
    setLock(true); showReady();
  } else if (!LEARN_MODE) {
    beep(3, 50);
    displayMessage("ACCESS", "DENIED", 1500);
    showReady();
  } else {
    displayMessage("LEARN MODE", uid, 2000);
  }

  reportScan(uid, authorized, result);
  rfid.PICC_HaltA(); rfid.PCD_StopCrypto1();
}

String getUID() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase(); return uid;
}
bool isWhitelisted(String uid) {
  for (int i = 0; i < WHITELIST_SIZE; i++) if (WHITELIST[i] == uid) return true;
  return false;
}
void setLock(bool locked) { isLocked = locked; digitalWrite(LOCK_PIN, locked ? LOW : HIGH); }
void beep(int n, int ms) {
  for (int i = 0; i < n; i++) {
    digitalWrite(BUZZ_PIN, HIGH); delay(ms);
    digitalWrite(BUZZ_PIN, LOW);  delay(ms);
  }
}
void showReady() {
  display.clearDisplay(); display.setTextSize(2); display.setCursor(0, 20);
  display.print(isLocked ? "  LOCKED" : " UNLOCKED"); display.display();
}
void displayMessage(String l1, String l2, int ms) {
  display.clearDisplay(); display.setTextSize(3); display.setCursor(0, 8);
  display.print(l1); display.setTextSize(2); display.setCursor(0, 42);
  display.print(l2); display.display(); delay(ms);
}
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}
void registerWithHub() {
  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["ip"]           = WiFi.localIP().toString();
  doc["port"]         = 80;
  doc["state"]        = "idle";
  doc["controllable"] = false;           // AI cannot unlock the door
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("uid"); r.add("authorized"); r.add("result");
  doc["severity"]     = "none";
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/register", payload);
}
void reportScan(String uid, bool authorized, const char* result) {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();

  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = authorized ? "granted" : "denied";
  doc["severity"]     = (!authorized && String(result) != "learn_mode") ? "high" : "none";
  doc["controllable"] = false;
  JsonArray w = doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("uid"); r.add("authorized"); r.add("result");
  doc["uid"]          = uid;
  doc["authorized"]   = authorized;
  doc["result"]       = result;
  doc["timestamp"]    = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
}
