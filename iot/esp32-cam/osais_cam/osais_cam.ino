/*
 * OSAIS — firmware do sensor (ESP32-CAM AI-Thinker / ESP32-S3 + OV5640)
 *
 * Fluxo: acorda → conecta ao Wi-Fi → sincroniza o relógio (NTP) → captura JPEG →
 *        envia ao gateway de edge (HTTP multipart ou MQTT) → deep sleep.
 *
 * Placa: "AI Thinker ESP32-CAM" (Arduino-ESP32 ≥ 2.0). Bibliotecas: esp32-camera (inclusa),
 *        PubSubClient (apenas se USE_MQTT = 1).
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <time.h>
#include "config.h"

#if USE_MQTT
#include <PubSubClient.h>
WiFiClient mqttNet;
PubSubClient mqtt(mqttNet);
#endif

// Pinagem AI-Thinker ESP32-CAM
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22
#define FLASH_GPIO_NUM     4

RTC_DATA_ATTR uint32_t bootCount = 0;  // sobrevive ao deep sleep

void goToSleep(uint32_t minutes) {
  Serial.printf("[OSAIS] Dormindo por %u min\n", minutes);
  esp_sleep_enable_timer_wakeup((uint64_t)minutes * 60ULL * 1000000ULL);
  esp_deep_sleep_start();
}

bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (psramFound()) {
    c.frame_size = FRAMESIZE_UXGA;  // 1600x1200
    c.jpeg_quality = 12;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size = FRAMESIZE_SVGA;  // 800x600
    c.jpeg_quality = 14;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
  }
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  s->set_whitebal(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_gain_ctrl(s, 1);
  return true;
}

bool connectWiFi(uint32_t timeoutMs = 15000) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < timeoutMs) delay(250);
  return WiFi.status() == WL_CONNECTED;
}

int readBattery() {
#if BATTERY_ADC_PIN >= 0
  float v = analogReadMilliVolts(BATTERY_ADC_PIN) * 2.0 / 1000.0;  // divisor 1:2
  int pct = (int)((v - BATTERY_VMIN) / (BATTERY_VMAX - BATTERY_VMIN) * 100.0);
  return constrain(pct, 0, 100);
#else
  return -1;
#endif
}

String isoTimestamp() {
  time_t now = time(nullptr);
  if (now < 1700000000) return "";  // relógio não sincronizado
  struct tm t;
  gmtime_r(&now, &t);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &t);
  return String(buf);
}

// Envia a imagem como multipart/form-data — mesmo formato aceito por /capture (edge) e /api/v1/ingest (API).
int postImage(camera_fb_t *fb, int battery, int rssi) {
  WiFiClient client;
  if (!client.connect(EDGE_HOST, EDGE_PORT)) return -1;
  const String boundary = "----OSAISBoundary7MA4YWxk";
  String head;
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"sensor_id\"\r\n\r\n" + SENSOR_ID + "\r\n";
  String ts = isoTimestamp();
  if (ts.length()) head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"captured_at\"\r\n\r\n" + ts + "\r\n";
  if (battery >= 0) head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"battery\"\r\n\r\n" + String(battery) + "\r\n";
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"signal\"\r\n\r\n" + String(rssi) + "\r\n";
  head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"captura.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n";
  String tail = "\r\n--" + boundary + "--\r\n";

  client.printf("POST %s HTTP/1.1\r\n", EDGE_PATH);
  client.printf("Host: %s\r\n", EDGE_HOST);
  client.printf("X-Device-Token: %s\r\n", DEVICE_TOKEN);
  client.printf("X-Firmware: %s\r\n", FIRMWARE);
  client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary.c_str());
  client.printf("Content-Length: %u\r\n", head.length() + fb->len + tail.length());
  client.print("Connection: close\r\n\r\n");
  client.print(head);
  for (size_t i = 0; i < fb->len; i += 1024) client.write(fb->buf + i, min((size_t)1024, fb->len - i));
  client.print(tail);

  uint32_t start = millis();
  while (!client.available() && millis() - start < 20000) delay(20);
  String status = client.readStringUntil('\n');  // "HTTP/1.1 201 Created"
  client.stop();
  int code = status.length() > 12 ? status.substring(9, 12).toInt() : -2;
  Serial.printf("[OSAIS] Resposta: %s\n", status.c_str());
  return code;
}

#if USE_MQTT
bool publishImage(camera_fb_t *fb, int battery, int rssi) {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(fb->len + 512);
  if (!mqtt.connect(SENSOR_ID, SENSOR_ID, DEVICE_TOKEN)) return false;
  String base = String("osais/sensores/") + SENSOR_ID;
  String meta = "{\"battery\":" + String(battery) + ",\"signal\":" + String(rssi) + ",\"capturedAt\":\"" + isoTimestamp() + "\",\"firmware\":\"" + FIRMWARE + "\"}";
  mqtt.publish((base + "/meta").c_str(), meta.c_str());
  bool ok = mqtt.publish((base + "/captura").c_str(), fb->buf, fb->len);
  mqtt.disconnect();
  return ok;
}
#endif

void setup() {
  Serial.begin(115200);
  bootCount++;
  Serial.printf("\n[OSAIS] %s · boot #%u\n", SENSOR_ID, bootCount);

  if (!connectWiFi()) {
    Serial.println("[OSAIS] Wi-Fi indisponível");
    goToSleep(10);
  }
  configTime(TZ_OFFSET_SECONDS, 0, "pool.ntp.org", "time.google.com");
  struct tm local;
  if (getLocalTime(&local, 5000) && (local.tm_hour < DAY_START_HOUR || local.tm_hour >= DAY_END_HOUR)) {
    Serial.println("[OSAIS] Fora do período de luz — aguardando");
    goToSleep(CAPTURE_INTERVAL_MIN);
  }

  if (!initCamera()) {
    Serial.println("[OSAIS] Falha ao iniciar a câmera");
    goToSleep(5);
  }
  // Descarta os primeiros quadros para estabilizar exposição e balanço de branco.
  for (int i = 0; i < 3; i++) {
    camera_fb_t *warm = esp_camera_fb_get();
    if (warm) esp_camera_fb_return(warm);
    delay(150);
  }
#if USE_FLASH
  pinMode(FLASH_GPIO_NUM, OUTPUT);
  digitalWrite(FLASH_GPIO_NUM, HIGH);
  delay(80);
#endif
  camera_fb_t *fb = esp_camera_fb_get();
#if USE_FLASH
  digitalWrite(FLASH_GPIO_NUM, LOW);
#endif
  if (!fb) {
    Serial.println("[OSAIS] Captura falhou");
    goToSleep(5);
  }
  Serial.printf("[OSAIS] Imagem capturada: %u bytes\n", fb->len);

  int battery = readBattery();
  int rssi = WiFi.RSSI();
#if USE_MQTT
  bool ok = publishImage(fb, battery, rssi);
#else
  int code = postImage(fb, battery, rssi);
  bool ok = code >= 200 && code < 300;
#endif
  esp_camera_fb_return(fb);
  Serial.println(ok ? "[OSAIS] Enviada com sucesso" : "[OSAIS] Falha no envio (o edge fará nova tentativa na próxima captura)");
  goToSleep(CAPTURE_INTERVAL_MIN);
}

void loop() {}
