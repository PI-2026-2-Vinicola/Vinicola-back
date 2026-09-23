# Firmware do sensor OSAIS (ESP32 + câmera)

Camada **Device** da arquitetura: captura a imagem dos cachos e a envia para a camada **Edge**.

| Item | Valor |
| --- | --- |
| Placa | ESP32-CAM AI-Thinker (OV2640) ou ESP32-S3 + OV5640 |
| Resolução | 1600×1200 (com PSRAM) ou 800×600 |
| Envio | HTTP `multipart/form-data` (padrão) ou MQTT (`USE_MQTT 1`) |
| Energia | Deep sleep entre capturas (`CAPTURE_INTERVAL_MIN`) |
| Autenticação | Cabeçalho `X-Device-Token`, com um token por dispositivo |

## Como gravar

1. Instale o suporte **esp32** na Arduino IDE (Boards Manager → *esp32 by Espressif*).
2. Copie `osais_cam/config.example.h` para `osais_cam/config.h` e preencha Wi-Fi, destino, `SENSOR_ID` e `DEVICE_TOKEN`.
3. Selecione a placa **AI Thinker ESP32-CAM** e ative a opção *PSRAM* quando ela existir.
4. Grave com um adaptador USB-serial (GPIO0 em GND durante o upload).

## Contrato do envio

```
POST /capture            (gateway de edge)   ou   POST /api/v1/ingest   (API)
X-Device-Token: <token do sensor>
Content-Type: multipart/form-data

sensor_id=S-001
captured_at=2026-09-23T13:30:00Z   (opcional; vem do NTP)
battery=87                          (opcional)
signal=-61                          (RSSI em dBm)
image=@captura.jpg
```

No MQTT, a imagem é publicada em `osais/sensores/{id}/captura` (payload binário JPEG) e os metadados em `osais/sensores/{id}/meta` (JSON). O gateway de edge assina esses tópicos.

## Instalação em campo

- Fixe a câmera na altura dos cachos, a 30–50 cm, com enquadramento lateral e sem contraluz.
- Use uma caixa IP65 com janela de policarbonato e um painel solar de 6 V com bateria 18650.
- Programe as capturas para o período de luz (06h–18h). Capturas escuras ou borradas são descartadas no edge.
