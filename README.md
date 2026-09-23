# OSAIS — Backend, IoT e IA

**Observação Agroambiental Sensorizada, Inteligente e Sustentável**

API, firmware e pipeline de visão computacional da OSAIS, a solução de IoT + IA para monitoramento e classificação de uvas do Projeto Integrador **“Inteligência de Dados no Vale do São Francisco”**.

```
 DEVICE                 EDGE                          CLOUD
┌──────────────┐  HTTP/ ┌─────────────────────┐ HTTPS ┌──────────────────────────────────────────┐
│ ESP32 + Cam  │──MQTT─▶│ Gateway (edge/)     │──────▶│ API FastAPI (app/)                       │
│ iot/esp32-cam│        │ brilho · nitidez ·  │       │  /ingest → YOLO → classificação → banco  │──▶ Dashboard
└──────────────┘        │ resize · fila offline│       │  /sensors /readings /stats              │    (Vinicola-Front)
                        └─────────────────────┘       └──────────────────────────────────────────┘
                                                                      │
                                                         Banco de dados (Vinicola-bd)
```

## Estrutura

| Pasta | Camada | Conteúdo |
| --- | --- | --- |
| `iot/esp32-cam/` | Device | Firmware Arduino do ESP32-CAM: captura, NTP, HTTP multipart ou MQTT, deep sleep |
| `edge/` | Edge | Gateway que checa a qualidade da imagem, prepara, encaminha e guarda em fila offline |
| `app/` | Cloud | API FastAPI: autenticação JWT, ingestão, detector (simulado/YOLO), classificação, histórico e indicadores |
| `ml/` | IA | Dataset YOLO, treino, inferência e guia de anotação |
| `tests/` | Qualidade | Testes Pytest da API, das regras de classificação e do edge |

## Executando a API

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

- Documentação interativa: http://localhost:8000/docs
- Na primeira execução o banco (SQLite, por padrão) recebe **6 sensores e cerca de 1.400 leituras** de demonstração.
- Usuários de demonstração (senha `osais2026`): `admin@osais.agr.br`, `gestor@osais.agr.br` e `operador@osais.agr.br`.
- Token de demonstração dos dispositivos: `osais-dev-s-001` … `osais-dev-s-006`.

Para conectar o frontend, use `VITE_API_URL=http://localhost:8000` no repositório `Vinicola-Front`.

### Com PostgreSQL (Docker)

```bash
git clone https://github.com/PI-2026-2-Vinicola/Vinicola-bd ../Vinicola-bd
docker compose up --build        # API :8000 · edge :8081 · Postgres :5432
```

MySQL: `DATABASE_URL=mysql+pymysql://osais:osais@localhost:3306/osais` (esquema em `Vinicola-bd/mysql`).

## Endpoints (`/api/v1`)

| Método | Rota | Acesso | Descrição |
| --- | --- | --- | --- |
| POST | `/auth/login` | público | Login e JWT (`accessToken` + `user`) |
| POST | `/auth/recover` | público | Recuperação de senha |
| GET | `/auth/me` | autenticado | Usuário atual |
| POST | `/ingest` | dispositivo (`X-Device-Token`) ou usuário | Recebe a imagem, executa o detector, classifica e armazena |
| POST | `/simulate/{sensor_id}` | usuário | Gera uma leitura simulada (demonstração sem hardware) |
| GET | `/sensors` · `/sensors/{id}` | leitura* | Sensores e status |
| POST | `/sensors/{id}/heartbeat` | dispositivo | Bateria, sinal e firmware |
| GET | `/readings` | leitura* | Histórico com filtros: `sensor_id`, `variety_id`, `quality`, `classification`, `date_from`, `date_to`, `days`, `q`, `limit`, `offset` (total no cabeçalho `X-Total-Count`) |
| GET | `/readings/{id}` · `/readings/{id}/image` | leitura* | Detalhe da análise e imagem capturada |
| GET | `/stats/summary` · `/stats/by-day` · `/stats/by-variety` | leitura* | Indicadores do dashboard |
| GET | `/varieties` | público | Variedades cadastradas |

\* Com `PUBLIC_READ=true` (padrão da demonstração), a leitura é pública. Em produção, use `PUBLIC_READ=false` para exigir login.

O JSON é em **camelCase**, no mesmo formato do tipo `Reading` do frontend. As caixas de detecção seguem o padrão YOLO normalizado (`x, y, largura, altura`, entre 0 e 1).

## Visão computacional

`app/services/detector.py` define a interface `Detector`, com duas implementações:

- **`MockDetector`** (`OSAIS_DETECTOR=mock`): determinístico, gera caixas coerentes para demonstrar todo o fluxo.
- **`YoloDetector`** (`OSAIS_DETECTOR=yolo`): usa o Ultralytics YOLO com os pesos em `OSAIS_MODEL_PATH`.

As regras de `app/services/classifier.py` transformam as detecções em resultado:

| Detecções | Qualidade | Classificação |
| --- | --- | --- |
| Só cachos | Boa | APROVADA |
| Anomalia leve (`maturacao_desigual`, `baga_irregular`, `mancha_leve`) | Atenção | EM OBSERVAÇÃO |
| Anomalia grave (`podridao`, `baga_murcha`, `lesao`) | Necessita atenção | REVISÃO NECESSÁRIA |

Treinamento e anotação estão descritos em [`ml/README.md`](ml/README.md).

## Testes

```bash
pytest -q
```

## Enviando uma imagem

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Device-Token: osais-dev-s-001" \
  -F sensor_id=S-001 -F image=@uva.jpg
```

---

A classificação é baseada na análise computacional da imagem e **não substitui a avaliação agronômica profissional**.
