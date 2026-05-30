/*
 * openHome Listener Satellite — ESP32-S3 voice assistant
 * ───────────────────────────────────────────────────────
 * Like a Google Home Mini, but fully local. No cloud.
 *
 * HARDWARE (cheapest path ~$25):
 *   - ESP32-S3 dev board (e.g. ESP32-S3-DevKitC-1, needs 8MB PSRAM)
 *   - INMP441 I2S MEMS microphone           — 3 pins (BCLK, LRCK, DIN)
 *   - MAX98357A I2S amp + 4Ω speaker        — 3 pins (BCLK, LRCK, DOUT)
 *   - Push button for talk-to-speak         — 1 pin to GND
 *   - Optional: 1.3" SH1106 OLED or 2.0" TFT for status display
 *
 * PIN MAP (default, edit below):
 *   I2S MIC  : BCLK=4   LRCK=5   DIN=6
 *   I2S AMP  : BCLK=15  LRCK=16  DOUT=17
 *   BUTTON   : 9 (pulled up, press to talk)
 *   LED      : 8 (status indicator)
 *
 * BEHAVIOR:
 *   - Idle: LED off, screen shows time + AI name
 *   - Hold button → state=listening, capture audio at 16kHz mono
 *   - Release button → send {end_of_utterance} over WebSocket
 *   - Pi runs Whisper → ARIA → Piper, streams WAV back
 *   - State=speaking, play audio through I2S amp
 *   - Back to idle
 *
 * Audio path is fully local — no Google/Alexa/cloud STT or TTS.
 */

#include "config.h"
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <WebSocketsClient.h>      // by Markus Sattler — install via Library Manager
#include <driver/i2s.h>

// ─── DEVICE IDENTITY ──────────────────────────────────────
const char* DEVICE_ID   = "listener_kitchen";
const char* DEVICE_TYPE = "listener_satellite";
const char* LOCATION    = "kitchen";

// ─── PIN MAP ─────────────────────────────────────────────
#define MIC_BCLK   4
#define MIC_LRCK   5
#define MIC_DIN    6
#define SPK_BCLK  15
#define SPK_LRCK  16
#define SPK_DOUT  17
#define BUTTON_PIN 9
#define LED_PIN    8

// ─── AUDIO ────────────────────────────────────────────────
#define SAMPLE_RATE     16000
#define I2S_MIC_PORT    I2S_NUM_0
#define I2S_SPK_PORT    I2S_NUM_1
#define BUFFER_SAMPLES  512       // ~32ms chunks @ 16kHz

// ─── STATE ────────────────────────────────────────────────
String currentState = "idle";    // idle | listening | thinking | speaking
String voiceName    = "en_US-amy-medium";
int    volume       = 80;
String lastTranscript = "";
String lastReply      = "";

WebSocketsClient ws;
WebServer server(80);
bool wsConnected = false;
bool isRecording = false;
bool buttonHeld  = false;

String isoTimestamp() { return "1970-01-01T00:00:00"; }

// ─── I2S SETUP ────────────────────────────────────────────
void setupMic() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = BUFFER_SAMPLES,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };
  i2s_pin_config_t pins = {
    .bck_io_num = MIC_BCLK,
    .ws_io_num  = MIC_LRCK,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num  = MIC_DIN
  };
  i2s_driver_install(I2S_MIC_PORT, &cfg, 0, NULL);
  i2s_set_pin(I2S_MIC_PORT, &pins);
}

void setupSpeaker() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 22050,   // Piper default output rate
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 512,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };
  i2s_pin_config_t pins = {
    .bck_io_num = SPK_BCLK,
    .ws_io_num  = SPK_LRCK,
    .data_out_num = SPK_DOUT,
    .data_in_num  = I2S_PIN_NO_CHANGE
  };
  i2s_driver_install(I2S_SPK_PORT, &cfg, 0, NULL);
  i2s_set_pin(I2S_SPK_PORT, &pins);
}

// ─── WEBSOCKET EVENTS ─────────────────────────────────────
void handleWsText(const String& msg) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, msg)) return;

  if (doc.containsKey("transcript")) {
    lastTranscript = String((const char*)doc["transcript"]);
    setState("thinking");
    Serial.printf("[VOICE] User said: %s\n", lastTranscript.c_str());
  }
  if (doc.containsKey("reply")) {
    lastReply = String((const char*)doc["reply"]);
    Serial.printf("[VOICE] ARIA: %s\n", lastReply.c_str());
  }
  if (doc.containsKey("done")) {
    setState("idle");
    reportState();
  }
}

void handleWsBinary(uint8_t* data, size_t len) {
  // Incoming WAV bytes from Pi — play through speaker
  setState("speaking");
  // Skip 44-byte WAV header, play raw PCM16
  if (len > 44) {
    size_t written = 0;
    i2s_write(I2S_SPK_PORT, data + 44, len - 44, &written, portMAX_DELAY);
  }
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      Serial.println("[WS] connected");
      break;
    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.println("[WS] disconnected");
      setState("idle");
      break;
    case WStype_TEXT:
      handleWsText(String((char*)payload));
      break;
    case WStype_BIN:
      handleWsBinary(payload, length);
      break;
    default: break;
  }
}

// ─── RECORD / STREAM ─────────────────────────────────────
void streamAudioChunk() {
  static int16_t buffer[BUFFER_SAMPLES];
  size_t bytesRead = 0;
  i2s_read(I2S_MIC_PORT, buffer, sizeof(buffer), &bytesRead, 100 / portTICK_PERIOD_MS);
  if (bytesRead > 0 && wsConnected) {
    ws.sendBIN((uint8_t*)buffer, bytesRead);
  }
}

void endUtterance() {
  if (wsConnected) {
    ws.sendTXT("{\"end_of_utterance\":true}");
  }
}

// ─── STATE / LED / REPORT ─────────────────────────────────
void setState(const String& s) {
  if (currentState == s) return;
  currentState = s;
  digitalWrite(LED_PIN,
    s == "listening" ? HIGH :
    s == "speaking"  ? HIGH : LOW);
}

void buildPayload(JsonDocument& doc, bool forRegister) {
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = currentState;
  doc["severity"]     = "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("state"); w.add("voice"); w.add("volume"); w.add("screen_text");
  JsonArray r = doc.createNestedArray("readable");
  r.add("last_transcript"); r.add("last_reply");
  doc["voice"]            = voiceName;
  doc["volume"]           = volume;
  doc["last_transcript"]  = lastTranscript;
  doc["last_reply"]       = lastReply;
  doc["timestamp"]        = isoTimestamp();
  if (forRegister) {
    doc["ip"]   = WiFi.localIP().toString();
    doc["port"] = 80;
  }
}

void registerWithHub() {
  StaticJsonDocument<512> doc; buildPayload(doc, true);
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

void reportState() {
  StaticJsonDocument<512> doc; buildPayload(doc, false);
  String p; serializeJson(doc, p);
  postSigned("/sensor", p);
}

// ─── HTTP CONTROL (hub can change voice, volume, screen) ──
void setupRoutes() {
  server.on("/control", HTTP_POST, []() {
    if (!checkServerAuth(server)) { server.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return; }
    if (!server.hasArg("plain")) { server.send(400); return; }
    StaticJsonDocument<256> doc;
    deserializeJson(doc, server.arg("plain"));
    if (doc.containsKey("voice"))       voiceName = String((const char*)doc["voice"]);
    if (doc.containsKey("volume"))      volume    = constrain((int)doc["volume"], 0, 100);
    if (doc.containsKey("screen_text")) { /* TODO: render to OLED */ }
    if (doc.containsKey("state"))       setState(String((const char*)doc["state"]));
    server.send(200, "application/json", "{\"ok\":true}");
    reportState();
  });
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("[WIFI] " + WiFi.localIP().toString());
}

void connectWebSocket() {
  String wsPath = "/voice/stream?key=" + String(API_KEY) + "&voice=" + voiceName;
  ws.begin(HUB_IP_ADDR, HUB_PORT_NUM, wsPath.c_str());
  ws.onEvent(onWsEvent);
  ws.setReconnectInterval(3000);
}

// ─── SETUP / LOOP ─────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  connectWiFi();
  setupMic();
  setupSpeaker();
  registerWithHub();
  connectWebSocket();
  setupRoutes();
  const char* hdrKeys[] = {"X-OpenHome-Key"};
  server.collectHeaders(hdrKeys, 1);
  server.begin();
  Serial.println("[LISTENER] ready — hold button to talk");
}

void loop() {
  ws.loop();
  server.handleClient();

  bool pressed = (digitalRead(BUTTON_PIN) == LOW);

  // Press to start recording
  if (pressed && !buttonHeld) {
    buttonHeld = true;
    isRecording = true;
    setState("listening");
    Serial.println("[LISTENER] recording…");
  }
  // While held, stream audio
  if (isRecording && wsConnected) {
    streamAudioChunk();
  }
  // Release ends utterance
  if (!pressed && buttonHeld) {
    buttonHeld = false;
    isRecording = false;
    endUtterance();
    setState("thinking");
    Serial.println("[LISTENER] processing…");
  }
}
