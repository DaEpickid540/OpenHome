/*
 * openHome Camera Stream
 * ───────────────────────
 * Continuous MJPEG camera with auth. Always-on. The Pi records from it.
 *
 * BOARD: AI Thinker ESP32-CAM (same board as doorbell_cam)
 *        or FREENOVE ESP32-WROVER-CAM
 *
 * DIFFERENCE from doorbell_cam:
 *   doorbell_cam:  deep sleep, wakes on button press, captures 1 JPEG
 *   camera_stream: always-on, serves continuous MJPEG stream on port 81
 *                  used for hallways, entrances, baby monitor etc.
 *
 * ENDPOINTS:
 *   GET /stream      → MJPEG stream (Pi records this)
 *   GET /capture     → Single JPEG snapshot
 *   GET /status      → Camera info JSON
 *   POST /control    → Change resolution, quality, flip (auth required)
 *
 * Pi records from: http://CAMERA_IP:81/stream
 */

#include "config.h"
#include "_openhome_security.h"
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include <time.h>

const char* DEVICE_ID   = "camera_hallway";
const char* DEVICE_TYPE = "camera_stream";
const char* LOCATION    = "hallway";

// AI Thinker ESP32-CAM pinout
#define CAM_PIN_PWDN    32
#define CAM_PIN_RESET   -1
#define CAM_PIN_XCLK     0
#define CAM_PIN_SIOD    26
#define CAM_PIN_SIOC    27
#define CAM_PIN_D7      35
#define CAM_PIN_D6      34
#define CAM_PIN_D5      39
#define CAM_PIN_D4      38
#define CAM_PIN_D3      37
#define CAM_PIN_D2      36
#define CAM_PIN_D1      21
#define CAM_PIN_D0      19
#define CAM_PIN_VSYNC   25
#define CAM_PIN_HREF    23
#define CAM_PIN_PCLK    22

WebServer server(81);
WebServer controlServer(80);
bool registered = false;

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25]; strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

void syncNTP() {
  configTime(0, 0, "pool.ntp.org");
  struct tm t; int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_D0; config.pin_d1 = CAM_PIN_D1;
  config.pin_d2 = CAM_PIN_D2; config.pin_d3 = CAM_PIN_D3;
  config.pin_d4 = CAM_PIN_D4; config.pin_d5 = CAM_PIN_D5;
  config.pin_d6 = CAM_PIN_D6; config.pin_d7 = CAM_PIN_D7;
  config.pin_xclk     = CAM_PIN_XCLK;
  config.pin_pclk     = CAM_PIN_PCLK;
  config.pin_vsync    = CAM_PIN_VSYNC;
  config.pin_href     = CAM_PIN_HREF;
  config.pin_sscb_sda = CAM_PIN_SIOD;
  config.pin_sscb_scl = CAM_PIN_SIOC;
  config.pin_pwdn     = CAM_PIN_PWDN;
  config.pin_reset    = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA;   // 640x480, good balance
  config.jpeg_quality = 12;
  config.fb_count     = 2;               // 2 frame buffers for streaming
  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("[CAM] init FAILED — check ribbon cable / board type");
    return;
  }
  // Auto exposure, auto white balance
  sensor_t* s = esp_camera_sensor_get();
  if (s) { s->set_framesize(s, FRAMESIZE_VGA); s->set_quality(s, 12); }
}

// ─── MJPEG STREAM ────────────────────────────────────────
void handleStream() {
  // Auth check — the Pi sends its key
  if (!server.hasHeader("X-OpenHome-Key") ||
      server.header("X-OpenHome-Key") != String(API_KEY)) {
    server.send(401, "text/plain", "unauthorized");
    return;
  }

  WiFiClient client = server.client();
  // MJPEG multipart response
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
  client.println("Connection: keep-alive");
  client.println();

  while (client.connected()) {
    // Keep /control responsive while a stream client is connected
    controlServer.handleClient();
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { delay(10); continue; }

    client.printf("--frame\r\n");
    client.printf("Content-Type: image/jpeg\r\n");
    client.printf("Content-Length: %d\r\n\r\n", fb->len);
    client.write(fb->buf, fb->len);
    client.println();
    esp_camera_fb_return(fb);

    delay(50);   // ~20 FPS max; Pi's ffmpeg will take what it can
  }
}

void handleCapture() {
  if (!server.hasHeader("X-OpenHome-Key") ||
      server.header("X-OpenHome-Key") != String(API_KEY)) {
    server.send(401); return;
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { server.send(503, "text/plain", "frame failed"); return; }
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void handleStatus() {
  if (!checkServerAuth(server)) { server.send(401, "text/plain", "unauthorized"); return; }
  StaticJsonDocument<256> doc;
  doc["device_id"] = DEVICE_ID;
  doc["type"]      = DEVICE_TYPE;
  doc["location"]  = LOCATION;
  doc["streaming"] = true;
  doc["stream_url"] = "http://" + WiFi.localIP().toString() + ":81/stream";
  doc["timestamp"] = isoTimestamp();
  String res; serializeJson(doc, res);
  server.send(200, "application/json", res);
}

// ─── CONTROL (port 80) ────────────────────────────────────
void handleControl() {
  if (!controlServer.hasArg("plain")) { controlServer.send(400); return; }
  String body = controlServer.arg("plain");
  StaticJsonDocument<192> doc;
  if (deserializeJson(doc, body)) { controlServer.send(400, "application/json", "{\"error\":\"bad json\"}"); return; }
  if (!checkSignedCommand(controlServer, body, doc["ts"] | 0L)) {
    controlServer.send(401, "application/json", "{\"error\":\"unauthorized\"}"); return;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (!s) { controlServer.send(503, "application/json", "{\"error\":\"no camera\"}"); return; }
  if (doc.containsKey("quality"))    s->set_quality(s, (int)doc["quality"]);
  if (doc.containsKey("flip"))       s->set_vflip(s, (bool)doc["flip"]);
  if (doc.containsKey("mirror"))     s->set_hmirror(s, (bool)doc["mirror"]);
  if (doc.containsKey("resolution")) {
    String res = doc["resolution"];
    if (res == "QVGA")  s->set_framesize(s, FRAMESIZE_QVGA);
    else if (res == "VGA")  s->set_framesize(s, FRAMESIZE_VGA);
    else if (res == "SVGA") s->set_framesize(s, FRAMESIZE_SVGA);
  }
  controlServer.send(200, "application/json", "{\"ok\":true}");
}

// ─── HUB REGISTRATION ────────────────────────────────────
void registerWithHub() {
  StaticJsonDocument<512> doc;
  doc["device_id"]    = DEVICE_ID;
  doc["type"]         = DEVICE_TYPE;
  doc["location"]     = LOCATION;
  doc["state"]        = "streaming";
  doc["severity"]     = "none";
  doc["controllable"] = true;
  JsonArray w = doc.createNestedArray("writable");
  w.add("quality"); w.add("resolution"); w.add("flip"); w.add("mirror");
  doc.createNestedArray("readable");
  doc["stream_url"]   = "http://" + WiFi.localIP().toString() + ":81/stream";
  doc["ip"]           = WiFi.localIP().toString();
  doc["port"]         = 80;
  doc["timestamp"]    = isoTimestamp();
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void setup() {
  Serial.begin(115200);
  connectWiFi();
  syncNTP();
  setupCamera();
  registerWithHub();

  // Stream server (port 81)
  const char* hdrs[] = {"X-OpenHome-Key", "X-OpenHome-Sig"};
  server.collectHeaders(hdrs, 2);
  server.on("/stream",  HTTP_GET,  handleStream);
  server.on("/capture", HTTP_GET,  handleCapture);
  server.on("/status",  HTTP_GET,  handleStatus);
  server.begin();

  // Control server (port 80)
  controlServer.collectHeaders(hdrs, 2);
  controlServer.on("/control", HTTP_POST, handleControl);
  controlServer.begin();

  Serial.printf("[CAM] Ready — stream: http://%s:81/stream\n",
    WiFi.localIP().toString().c_str());
}

void loop() {
  server.handleClient();
  controlServer.handleClient();
}
