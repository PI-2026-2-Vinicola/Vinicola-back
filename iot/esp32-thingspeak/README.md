# Estação ESP32 → ThingSpeak (temperatura, umidade e localização)

Firmware do dispositivo que publica no canal do ThingSpeak lido pela página **Monitoramento** da OASIS (`GET /api/v1/iot/thingspeak`).

| O que é enviado | Parâmetro no ThingSpeak | Exibição na OASIS |
| --- | --- | --- |
| Temperatura do DHT (°C, 1 casa) | `field1` | Cartão e gráfico "Temperatura" |
| Umidade do DHT (%, 1 casa) | `field2` | Cartão e gráfico "Umidade" |
| Posição estimada pelo Wi-Fi (ou fixa) | `lat` / `long` | Mapa "Localização do dispositivo" |
| Origem/precisão da posição, sinal Wi-Fi, firmware | `status` | Diagnóstico no ThingSpeak |

Leituras inválidas do sensor (desconectado, fora da faixa) **não são enviadas**: o campo fica vazio naquele registro, nunca zero.

## Como gravar

1. Arduino IDE → Boards Manager → instale **esp32 by Espressif**.
2. Library Manager → instale **ArduinoJson** (v7), **PubSubClient**, **DHT sensor library** e **Adafruit Unified Sensor**.
3. Copie `oasis_thingspeak/config.example.h` para `oasis_thingspeak/config.h` e preencha:
   - Wi-Fi (`WIFI_SSID`, `WIFI_PASSWORD`);
   - MQTT: `MQTT_CLIENT_ID`, `MQTT_USERNAME` e `MQTT_PASSWORD` do dispositivo em **Devices → MQTT** (o dispositivo precisa ter o canal na lista de canais autorizados a publicar) — ou `USE_MQTT 0` e a `TS_WRITE_API_KEY`;
   - `DHT_PIN` e `DHT_TYPE` conforme a ligação do sensor.
4. Selecione a placa (ex.: **ESP32 Dev Module**), grave e abra o Serial Monitor em **115200**.

O `config.h` está no `.gitignore`: nenhuma senha vai para o repositório.

## Ligação do DHT22

| DHT22 | ESP32 |
| --- | --- |
| VCC | 3V3 |
| DATA | GPIO 4 (`DHT_PIN`), com resistor de 10 kΩ para 3V3 (módulos com placa já trazem) |
| GND | GND |

## Localização pelo Wi-Fi

O ESP32 não tem GPS: a cada `GEO_REFRESH_MIN` minutos ele lista as redes Wi-Fi visíveis (endereço MAC do roteador e intensidade do sinal) e envia a lista ao [beaconDB](https://beacondb.net), serviço aberto e gratuito, sem chave. A resposta traz latitude, longitude e precisão.

- Precisa de **pelo menos 2 redes** visíveis. Em área rural com poucas redes, ou com redes que o serviço ainda não conhece (HTTP 404 no Serial Monitor), não há estimativa.
- Estimativas com precisão pior que `GEO_MAX_ACCURACY_M` são descartadas. A posição pelo IP não é usada, porque apontaria para o provedor de internet, não para o vinhedo.
- Se a estimativa falhar, o firmware continua enviando a última posição válida; sem nenhuma, usa `FIXED_LAT`/`FIXED_LNG` (se preenchidos) ou envia só as medições.
- Para um aparelho fixo no vinhedo, a posição fixa medida no local (ex.: pelo celular) é a mais precisa.

## Segurança

Todas as conexões usam TLS com validação do certificado (`certs.h`): MQTT na porta 8883 e HTTPS na API do ThingSpeak (DigiCert Global Root G2) e no beaconDB (ISRG Root X1/X2). Os certificados raiz valem até 2035–2040.

## Mensagens do Serial Monitor

| Mensagem | Significado |
| --- | --- |
| `[mqtt] falhou (estado 4)` / `(estado 5)` | Usuário/senha MQTT errados / dispositivo sem permissão no canal |
| `[http] falha (HTTP 200, resposta "0")` | Write API Key errada ou envio com menos de 15 s de intervalo |
| `[geo] sem posicao (HTTP 404)` | O beaconDB não conhece as redes próximas |
| `[dht] temperatura invalida` | Sensor desconectado ou pino errado |
