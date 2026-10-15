// Copie este arquivo para config.h (na mesma pasta) e preencha. O config.h não é versionado.
#pragma once

// ---------- Wi-Fi ----------
#define WIFI_SSID         "NOME_DA_REDE"
#define WIFI_PASSWORD     "SENHA_DA_REDE"

// ---------- Canal do ThingSpeak ----------
#define TS_CHANNEL_ID     3497846

// Envio: 1 = MQTT (dispositivo MQTT criado em Devices → MQTT no ThingSpeak), 0 = HTTP (Write API Key).
#define USE_MQTT          1
// Credenciais do dispositivo MQTT (Devices → MQTT → seu dispositivo). O dispositivo precisa
// ter permissão de publicação no canal TS_CHANNEL_ID.
#define MQTT_CLIENT_ID    "COLE_O_CLIENT_ID"
#define MQTT_USERNAME     "COLE_O_USERNAME"
#define MQTT_PASSWORD     "COLE_O_PASSWORD"
// Usado somente com USE_MQTT 0 (aba API Keys do canal).
#define TS_WRITE_API_KEY  "COLE_A_WRITE_API_KEY"

// Intervalo entre envios. O plano gratuito do ThingSpeak aceita no mínimo 15 s; a página
// Monitoramento da OASIS atualiza a cada 60 s.
#define SEND_INTERVAL_S   60

// ---------- Temperatura e umidade (DHT) ----------
// Campos do canal: field1 = Temperatura (°C), field2 = Umidade (%).
#define DHT_PIN           4     // GPIO do pino de dados; -1 = sem sensor
#define DHT_TYPE          DHT22 // DHT22 (AM2302) ou DHT11

// ---------- Localização ----------
// 1 = estima a posição pelas redes Wi-Fi próximas (serviço de geolocalização abaixo).
#define USE_WIFI_GEO      1
// beaconDB: gratuito e sem chave (https://beacondb.net). Alternativa com chave, mesmo formato:
//   "https://www.googleapis.com/geolocation/v1/geolocate?key=SUA_CHAVE" (exige trocar GEO_ROOT_CA).
#define GEO_URL           "https://api.beacondb.net/v1/geolocate"
#define GEO_REFRESH_MIN   30    // reconsulta a posição a cada N minutos (o aparelho é fixo no vinhedo)
#define GEO_MAX_ACCURACY_M 2000 // descarta estimativas piores que isso

// Posição fixa (opcional): usada quando a geolocalização está desligada ou falha.
// Deixe 0 e 0 para não enviar posição nesses casos.
#define FIXED_LAT         0.0
#define FIXED_LNG         0.0
