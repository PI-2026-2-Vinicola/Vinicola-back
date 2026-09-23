# Gateway de edge OSAIS

Camada **Edge** da arquitetura: roda perto dos sensores (Raspberry Pi, mini PC ou roteador com Linux) e faz o processamento inicial antes de mandar as imagens para a nuvem.

```
ESP32-CAM ──HTTP/MQTT──▶ Edge Gateway ──HTTPS──▶ API OSAIS (/api/v1/ingest)
                         · checa brilho e nitidez
                         · descarta capturas inúteis
                         · redimensiona e comprime
                         · guarda em fila offline
```

## Executar

```bash
pip install -r edge/requirements.txt
OSAIS_API_URL=http://localhost:8000 uvicorn edge.gateway:app --host 0.0.0.0 --port 8081
```

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `OSAIS_API_URL` | `http://localhost:8000` | API na nuvem |
| `OSAIS_EDGE_QUEUE` | `edge/queue` | Pasta da fila offline |
| `OSAIS_EDGE_RETRY_SECONDS` | `60` | Intervalo de reenvio |
| `OSAIS_MQTT_HOST` / `OSAIS_MQTT_PORT` | — / `1883` | Ativa a assinatura MQTT |
| `OSAIS_TOKEN_S_001`, … | — | Token de cada sensor (modo MQTT) |

## Endpoints

- `POST /capture` recebe o mesmo `multipart` do firmware e responde com a análise da nuvem, com `202` quando a imagem foi para a fila ou com `422` quando foi descartada (o motivo vem na resposta).
- `GET /status` informa os contadores de recebidas, descartadas, encaminhadas e pendentes na fila.

## Testar sem hardware

```bash
curl -X POST http://localhost:8081/capture \
  -H "X-Device-Token: osais-dev-s-001" \
  -F sensor_id=S-001 -F image=@uva.jpg
```
