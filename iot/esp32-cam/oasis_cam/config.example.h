// Copie este arquivo para config.h e ajuste os valores do seu dispositivo.
#pragma once

// ---------- Rede ----------
#define WIFI_SSID      "NOME_DA_REDE"
#define WIFI_PASSWORD  "SENHA_DA_REDE"

// ---------- Destino ----------
// Envie para o gateway de edge (recomendado) ou direto para a API na nuvem.
// Gateway de edge:  EDGE_HOST = IP do gateway na rede da fazenda, porta 8081, caminho /capture
// API direta:       EDGE_HOST = domínio da API,  porta 8000/443,        caminho /api/v1/ingest
#define EDGE_HOST      "192.168.0.10"
#define EDGE_PORT      8081
#define EDGE_PATH      "/capture"

// ---------- Identidade do sensor ----------
// Código e token exibidos UMA única vez ao cadastrar o sensor em OASIS → Sensores → Novo sensor
// (ou em "Gerar novo token"). O token antigo deixa de funcionar quando um novo é gerado.
#define SENSOR_ID      "S-001"
#define DEVICE_TOKEN   "COLE_AQUI_O_TOKEN_DO_DISPOSITIVO"
#define FIRMWARE       "v2.0.0"

// ---------- Captura ----------
#define CAPTURE_INTERVAL_MIN  90   // intervalo entre capturas (deep sleep)
#define DAY_START_HOUR        6    // só captura com luz natural
#define DAY_END_HOUR          18
#define TZ_OFFSET_SECONDS     (-3 * 3600)  // America/Recife (UTC-3)
#define USE_FLASH             0    // 1 = aciona o LED flash durante a captura

// ---------- Bateria (divisor resistivo opcional) ----------
#define BATTERY_ADC_PIN       -1   // -1 = sem medição; ex.: 33 com divisor 100k/100k
#define BATTERY_VMAX          4.2
#define BATTERY_VMIN          3.3

// ---------- Temperatura e umidade (DHT22 opcional) ----------
#define DHT_PIN               -1   // -1 = sem sensor; ex.: 13 (GPIO livre na ESP32-CAM)

// ---------- MQTT (alternativa ao HTTP) ----------
#define USE_MQTT              0
#define MQTT_HOST             "192.168.0.10"
#define MQTT_PORT             1883
