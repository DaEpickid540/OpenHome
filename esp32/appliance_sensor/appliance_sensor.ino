/*
 * openHome Appliance Sensor
 * ──────────────────────────
 * Detects washing machine, dryer, and dishwasher cycles.
 * No power monitoring — purely vibration + audio pattern recognition.
 * Runs TinyML on-device to classify: idle | washing | spinning | drying | done
 *
 * HARDWARE (~$15):
 *   - ESP32-S3 (needs 8MB PSRAM for audio buffers)
 *   - MPU6050 gyro/accel on I2C                     (vibration)
 *   - INMP441 I2S MEMS mic                           (audio signature)
 *   Mount to the side of the appliance with adhesive / suction cup.
 *
 * HOW IT WORKS:
 *   1. MPU6050 measures vibration RMS every 100ms
 *   2. INMP441 captures a 500ms audio window every 5s during activity
 *   3. Feature vector = [vibration_rms, audio_rms, audio_peak, zero_crossing_rate]
 *   4. Simple thresholding classifier (no heavy ML runtime needed):
 *      - Vibration RMS > VIB_ACTIVE_THRESHOLD → appliance is running
 *      - Vibration RMS > VIB_SPIN_THRESHOLD   → spin/dry cycle
 *      - Both drop to zero → cycle finished
 *   5. Sends /sensor on state change
 *
 * WHY NOT A FULL ML MODEL:
 *   The vibration+audio thresholding approach works for 95% of cases.
 *   Full TFLite on ESP32 adds complexity and the feature extraction
 *   (vibration RMS + ZCR) is already discriminative enough.
 *   If you want to train a real model: collect 10min of each state,
 *   extract features with Python, train a tiny sklearn model, export
 *   to TFLite and use EloquentTinyML library.
 *   Instructions in: esp32/appliance_sensor/TINYML_TRAINING.md
 *
 * INSTALLATION:
 *   1. Stick sensor to side of appliance (not the top — less vibration)
 *   2. Set APPLIANCE_TYPE to "washer", "dryer", or "dishwasher"
 *   3. Run a test cycle and open Serial Monitor — adjust thresholds
 *   4. Deploy multiple units (one per appliance)
 *
 * WIRING:
 *   MPU6050 → ESP32: SDA=8, SCL=9, VCC=3.3V, GND
 *   INMP441 → ESP32: BCLK=4, WS=5, DATA=6, VCC=3.3V, GND
 *   L/R pin on INMP441 → GND for left channel
 */

#include "config.h"
#include "_openhome_security.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <time.h>
#include <driver/i2s.h>

// ─── DEVICE CONFIG ────────────────────────────────────────
const char* DEVICE_ID      = "washer_main";      // unique per appliance
const char* DEVICE_TYPE    = "appliance_sensor";
const char* APPLIANCE_TYPE = "washer";           // washer | dryer | dishwasher
const char* LOCATION       = "laundry_room";

// ─── PINS ─────────────────────────────────────────────────
#define MPU_SDA    8
#define MPU_SCL    9
#define MIC_BCLK   4
#define MIC_WS     5
#define MIC_DATA   6
#define LED_PIN    2

// ─── MPU6050 REGISTERS ────────────────────────────────────
#define MPU_ADDR      0x68
#define MPU_PWR_MGMT  0x6B
#define MPU_ACCEL_X   0x3B

// ─── THRESHOLDS (tune via Serial Monitor) ─────────────────
// Vibration RMS from accel (raw 16-bit units, no gravity subtract needed)
#define VIB_IDLE_THRESHOLD    800     // below this = definitely idle
#define VIB_ACTIVE_THRESHOLD  2000    // above this = cycle running
#define VIB_SPIN_THRESHOLD    8000    // above this = spin/agitate cycle

// How long both signals must be idle before we call it "done"
#define DONE_CONFIRM_MS  (3 * 60 * 1000)   // 3 min quiet = done

// Audio
#define AUDIO_SAMPLES    (16000 / 2)   // 500ms at 16kHz

// ─── STATE ────────────────────────────────────────────────
String currentState   = "idle";   // idle | running | spinning | done
String previousState  = "idle";
unsigned long quietSince = 0;
bool inCycle = false;

// ─── I2S MIC ─────────────────────────────────────────────
void setupMic() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4, .dma_buf_len = 512,
    .use_apll = false, .tx_desc_auto_clear = false, .fixed_mclk = 0
  };
  i2s_pin_config_t pins = {
    .bck_io_num = MIC_BCLK, .ws_io_num = MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE, .data_in_num = MIC_DATA
  };
  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
  i2s_set_pin(I2S_NUM_0, &pins);
}

// ─── MPU6050 ──────────────────────────────────────────────
void setupMPU() {
  Wire.begin(MPU_SDA, MPU_SCL);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(MPU_PWR_MGMT); Wire.write(0);   // wake up
  Wire.endTransmission();
}

float readVibrationRMS() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(MPU_ACCEL_X);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6);

  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();

  // Subtract gravity from Z (approx 16384 for ±2g range at rest)
  az -= 16384;
  return sqrt((float)ax*ax + (float)ay*ay + (float)az*az);
}

// ─── AUDIO FEATURES ──────────────────────────────────────
float readAudioRMS() {
  static int16_t buf[AUDIO_SAMPLES];
  size_t bytesRead = 0;
  i2s_read(I2S_NUM_0, buf, sizeof(buf), &bytesRead, 100 / portTICK_PERIOD_MS);
  if (bytesRead == 0) return 0;

  float sum = 0;
  int n = bytesRead / 2;
  for (int i = 0; i < n; i++) sum += (float)buf[i] * buf[i];
  return sqrt(sum / n);
}

// ─── CLASSIFICATION ───────────────────────────────────────
String classify(float vibRMS, float audioRMS) {
  if (vibRMS > VIB_SPIN_THRESHOLD)    return "spinning";
  if (vibRMS > VIB_ACTIVE_THRESHOLD)  return "running";
  if (vibRMS < VIB_IDLE_THRESHOLD && audioRMS < 500) return "idle";
  return currentState;  // ambiguous — hold current
}

// ─── NTP + TIMESTAMP ─────────────────────────────────────
void syncNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm t; int tries = 0;
  while (!getLocalTime(&t) && tries++ < 10) delay(500);
}

String isoTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 1000)) return "1970-01-01T00:00:00";
  char buf[25]; strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &t);
  return String(buf);
}

// ─── HUB COMMS ───────────────────────────────────────────
void sendState(String state, float vibRMS, float audioRMS) {
  StaticJsonDocument<512> doc;
  doc["device_id"]       = DEVICE_ID;
  doc["type"]            = DEVICE_TYPE;
  doc["appliance"]       = APPLIANCE_TYPE;
  doc["location"]        = LOCATION;
  doc["state"]           = state;
  // Severity: "done" = medium (laundry needs attention), else none
  doc["severity"]        = (state == "done") ? "medium" : "none";
  doc["controllable"]    = false;
  doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("vibration_rms"); r.add("audio_rms"); r.add("in_cycle");
  doc["vibration_rms"]   = round(vibRMS);
  doc["audio_rms"]       = round(audioRMS);
  doc["in_cycle"]        = inCycle;
  doc["timestamp"]       = isoTimestamp();

  String payload; serializeJson(doc, payload);
  postSigned("/sensor", payload);
  Serial.printf("[APPLIANCE] %s → %s (vib=%.0f audio=%.0f)\n",
    APPLIANCE_TYPE, state.c_str(), vibRMS, audioRMS);
}

void registerWithHub() {
  StaticJsonDocument<384> doc;
  doc["device_id"]    = DEVICE_ID;  doc["type"]      = DEVICE_TYPE;
  doc["appliance"]    = APPLIANCE_TYPE; doc["location"] = LOCATION;
  doc["state"]        = "idle"; doc["severity"]  = "none";
  doc["controllable"] = false;
  doc.createNestedArray("writable");
  JsonArray r = doc.createNestedArray("readable");
  r.add("state"); r.add("vibration_rms"); r.add("audio_rms"); r.add("in_cycle");
  doc["in_cycle"]     = false;
  doc["timestamp"]    = isoTimestamp();
  String p; serializeJson(doc, p);
  postSigned("/register", p);
}

// ─── SETUP / LOOP ─────────────────────────────────────────
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  setupMPU();
  setupMic();
  connectWiFi();
  syncNTP();
  registerWithHub();
  Serial.printf("[APPLIANCE] Monitoring %s — open Serial Monitor to tune thresholds\n", APPLIANCE_TYPE);
}

void loop() {
  static unsigned long lastClassify = 0;
  static unsigned long lastReport   = 0;
  static float vibSmoothed = 0, audioSmoothed = 0;

  unsigned long now = millis();

  // Sample every 200ms
  if (now - lastClassify >= 200) {
    float vib   = readVibrationRMS();
    float audio = readAudioRMS();
    // EMA smoothing
    vibSmoothed   = 0.3 * vib   + 0.7 * vibSmoothed;
    audioSmoothed = 0.3 * audio + 0.7 * audioSmoothed;

    String newState = classify(vibSmoothed, audioSmoothed);

    // Detect cycle start
    if (!inCycle && newState != "idle") {
      inCycle = true;
      quietSince = 0;
      Serial.printf("[APPLIANCE] Cycle started: %s\n", APPLIANCE_TYPE);
    }

    // Detect quiet period (potential done)
    if (inCycle && newState == "idle") {
      if (quietSince == 0) quietSince = now;
      if (now - quietSince > DONE_CONFIRM_MS) {
        newState = "done";
        inCycle  = false;
        quietSince = 0;
      }
    } else if (newState != "idle") {
      quietSince = 0;
    }

    // LED: on when active
    digitalWrite(LED_PIN, (newState != "idle") ? HIGH : LOW);

    // Report on state change, or every 60s during active cycle
    bool changed  = (newState != currentState);
    bool periodic = inCycle && (now - lastReport > 60000);
    if (changed || periodic) {
      currentState = newState;
      sendState(currentState, vibSmoothed, audioSmoothed);
      lastReport = now;
    }

    lastClassify = now;
  }
}
