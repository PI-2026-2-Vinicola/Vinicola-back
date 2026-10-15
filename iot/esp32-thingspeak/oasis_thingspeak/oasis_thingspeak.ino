/*
 * OASIS — estação ESP32 → ThingSpeak (temperatura, umidade e localização)
 *
 * Fluxo: Wi-Fi → (a cada GEO_REFRESH_MIN) estimativa de posição pelas redes Wi-Fi próximas
 *        → (a cada SEND_INTERVAL_S) leitura do DHT → publicação no canal do ThingSpeak.
 *
 * O que a API OASIS lê do canal (página Monitoramento):
 *   field1 = Temperatura (°C), field2 = Umidade (%), lat/long = posição do dispositivo.
 *   status = texto de diagnóstico (origem e precisão da posição, sinal Wi-Fi, firmware).
 * Valores inválidos não são enviados: um campo ausente nunca vira zero.
 *
 * Bibliotecas (Arduino IDE → Library Manager):
 *   - ArduinoJson (Benoit Blanchon), versão 7
 *   - PubSubClient (Nick O'Leary)                — somente com USE_MQTT 1
 *   - DHT sensor library + Adafruit Unified Sensor — somente com DHT_PIN ≥ 0
 * Placa: esp32 by Espressif (core 2.x ou 3.x), qualquer ESP32 com Wi-Fi.
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <math.h>

#include "config.h"
#include "certs.h"

#if USE_MQTT
#include <PubSubClient.h>
#endif
#if DHT_PIN >= 0
#include <DHT.h>
#endif

#define FIRMWARE "oasis-thingspeak 1.0.0"
#define USER_AGENT FIRMWARE " (OASIS PI 2026.2 Vinicola; ESP32)"

static_assert(SEND_INTERVAL_S >= 15, "O ThingSpeak gratuito aceita no maximo um envio a cada 15 s.");

// ------------------------------------------------------------------ estado
struct Position {
  bool valid = false;
  double lat = 0, lng = 0;
  float accuracy = NAN;      // metros (somente geolocalização por Wi-Fi)
  const char *source = "";   // "wifi" ou "fixa"
};

Position position;
unsigned long lastSendMs = 0, lastGeoMs = 0;
bool sentOnce = false, geoTried = false;

#if DHT_PIN >= 0
DHT dht(DHT_PIN, DHT_TYPE);
#endif

#if USE_MQTT
WiFiClientSecure mqttNet;
PubSubClient mqtt(mqttNet);
#endif

// ------------------------------------------------------------------ utilidades
bool due(unsigned long last, unsigned long intervalMs) { return millis() - last >= intervalMs; }

bool validCoordinate(double lat, double lng) {
  return !isnan(lat) && !isnan(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180 && !(lat == 0 && lng == 0);
}

// Codificação de URL para os valores do payload (o ThingSpeak exige em MQTT e HTTP).
String urlEncode(const String &s) {
  static const char hex[] = "0123456789ABCDEF";
  String out;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (isalnum((unsigned char)c) || c == '-' || c == '_' || c == '.' || c == '~') out += c;
    else { out += '%'; out += hex[(c >> 4) & 0xF]; out += hex[c & 0xF]; }
  }
  return out;
}

// ------------------------------------------------------------------ Wi-Fi
bool ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.printf("[wifi] conectando a \"%s\"...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) delay(250);
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[wifi] falhou; nova tentativa no proximo ciclo");
    return false;
  }
  Serial.printf("[wifi] conectado: IP %s, sinal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

// ------------------------------------------------------------------ localização
// Envia as redes Wi-Fi visíveis (BSSID + intensidade) a um serviço compatível com a API
// Ichnaea/Google Geolocation e recebe {"location":{"lat","lng"},"accuracy"}.
// Redes ocultas e as que terminam em "_nomap" (pedido de exclusão do dono) são ignoradas.
bool geolocateByWifi() {
  int n = WiFi.scanNetworks(false, false);
  JsonDocument req;
  req["considerIp"] = false;  // só redes Wi-Fi: a posição pelo IP seria a do provedor, não a do vinhedo
  JsonArray aps = req["wifiAccessPoints"].to<JsonArray>();
  int used = 0;
  for (int i = 0; i < n && used < 20; i++) {
    String ssid = WiFi.SSID(i);
    if (ssid.length() == 0 || ssid.endsWith("_nomap")) continue;
    JsonObject ap = aps.add<JsonObject>();
    ap["macAddress"] = WiFi.BSSIDstr(i);
    ap["signalStrength"] = WiFi.RSSI(i);
    ap["channel"] = WiFi.channel(i);
    used++;
  }
  WiFi.scanDelete();
  if (used < 2) {
    Serial.printf("[geo] apenas %d rede(s) visivel(is); sao necessarias ao menos 2\n", used);
    return false;
  }

  WiFiClientSecure tls;
  tls.setCACert(GEO_ROOT_CA);
  HTTPClient http;
  http.setTimeout(15000);
  if (!http.begin(tls, GEO_URL)) {
    Serial.println("[geo] URL invalida");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.setUserAgent(USER_AGENT);
  String body;
  serializeJson(req, body);
  int code = http.POST(body);
  String text = code > 0 ? http.getString() : "";
  http.end();
  if (code != 200) {
    // 404 = o serviço não conhece estas redes; erros negativos = falha de conexão/TLS
    Serial.printf("[geo] sem posicao (HTTP %d) %s\n", code, code == 404 ? "- redes ainda nao mapeadas no servico" : "");
    return false;
  }

  JsonDocument res;
  if (deserializeJson(res, text)) {
    Serial.println("[geo] resposta invalida");
    return false;
  }
  double lat = res["location"]["lat"] | NAN, lng = res["location"]["lng"] | NAN;
  float acc = res["accuracy"] | NAN;
  if (!validCoordinate(lat, lng) || isnan(acc) || acc > GEO_MAX_ACCURACY_M) {
    Serial.printf("[geo] estimativa descartada (precisao %.0f m)\n", acc);
    return false;
  }
  position.valid = true;
  position.lat = lat;
  position.lng = lng;
  position.accuracy = acc;
  position.source = "wifi";
  Serial.printf("[geo] %.6f, %.6f (precisao %.0f m, %d redes)\n", lat, lng, acc, used);
  return true;
}

void refreshPosition() {
  bool ok = false;
#if USE_WIFI_GEO
  ok = geolocateByWifi();
#endif
  if (!ok && !position.valid && validCoordinate(FIXED_LAT, FIXED_LNG)) {
    position.valid = true;
    position.lat = FIXED_LAT;
    position.lng = FIXED_LNG;
    position.accuracy = NAN;
    position.source = "fixa";
    Serial.println("[geo] usando a posicao fixa do config.h");
  }
  // Se a consulta falhar, a última posição válida continua sendo enviada.
}

// ------------------------------------------------------------------ montagem do envio
String buildPayload() {
  String p;
  auto add = [&](const String &key, const String &value) {
    if (p.length()) p += '&';
    p += key + '=' + urlEncode(value);
  };

#if DHT_PIN >= 0
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (!isnan(t) && t >= -40 && t <= 80) add("field1", String(t, 1));
  else Serial.println("[dht] temperatura invalida; field1 nao enviado");
  if (!isnan(h) && h >= 0 && h <= 100) add("field2", String(h, 1));
  else Serial.println("[dht] umidade invalida; field2 nao enviado");
#endif

  if (position.valid) {
    add("lat", String(position.lat, 6));
    add("long", String(position.lng, 6));
  }

  if (p.length() == 0) return p;  // nada medido: não publica um registro vazio

  String status = String("geo ") + (position.valid ? position.source : "indisponivel");
  if (position.valid && !isnan(position.accuracy)) status += " " + String((int)position.accuracy) + " m";
  status += " | wifi " + String(WiFi.RSSI()) + " dBm | " FIRMWARE;
  add("status", status);
  return p;
}

// ------------------------------------------------------------------ publicação
#if USE_MQTT
bool ensureMqtt() {
  if (mqtt.connected()) return true;
  Serial.println("[mqtt] conectando a mqtt3.thingspeak.com:8883...");
  if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
    Serial.println("[mqtt] conectado");
    return true;
  }
  // 4 = usuário/senha recusados, 5 = não autorizado (dispositivo sem acesso ao canal)
  Serial.printf("[mqtt] falhou (estado %d)\n", mqtt.state());
  return false;
}

bool publish(const String &payload) {
  if (!ensureMqtt()) return false;
  String topic = "channels/" + String(TS_CHANNEL_ID) + "/publish";
  bool ok = mqtt.publish(topic.c_str(), payload.c_str());
  Serial.printf("[mqtt] %s %s\n", ok ? "publicado:" : "falha ao publicar:", payload.c_str());
  return ok;
}
#else
bool publish(const String &payload) {
  WiFiClientSecure tls;
  tls.setCACert(THINGSPEAK_ROOT_CA);
  HTTPClient http;
  http.setTimeout(15000);
  http.begin(tls, "https://api.thingspeak.com/update");
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  http.setUserAgent(USER_AGENT);
  int code = http.POST("api_key=" + urlEncode(TS_WRITE_API_KEY) + "&" + payload);
  String body = code > 0 ? http.getString() : "";
  http.end();
  // Sucesso: HTTP 200 com o número do registro; "0" = recusado (chave errada ou intervalo < 15 s).
  bool ok = code == 200 && body.toInt() > 0;
  Serial.printf("[http] %s (HTTP %d, resposta \"%s\") %s\n", ok ? "registro criado" : "falha", code, body.c_str(), payload.c_str());
  return ok;
}
#endif

// ------------------------------------------------------------------ ciclo
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n" FIRMWARE " — canal " + String(TS_CHANNEL_ID));
#if DHT_PIN >= 0
  dht.begin();
#endif
#if USE_MQTT
  mqttNet.setCACert(THINGSPEAK_ROOT_CA);
  mqtt.setServer("mqtt3.thingspeak.com", 8883);
  mqtt.setBufferSize(512);
#endif
  ensureWifi();
}

void loop() {
  if (!ensureWifi()) {
    delay(5000);
    return;
  }
#if USE_MQTT
  mqtt.loop();
#endif

  if (!geoTried || due(lastGeoMs, GEO_REFRESH_MIN * 60000UL)) {
    refreshPosition();
    geoTried = true;
    lastGeoMs = millis();
  }

  if (!sentOnce || due(lastSendMs, SEND_INTERVAL_S * 1000UL)) {
    String payload = buildPayload();
    if (payload.length() == 0) Serial.println("[envio] nada para enviar neste ciclo");
    else publish(payload);
    // Conta o intervalo mesmo em caso de falha, para respeitar o limite de 15 s do ThingSpeak.
    sentOnce = true;
    lastSendMs = millis();
  }
  delay(100);
}
