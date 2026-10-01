# OASIS — Backend, IoT e IA

**Observação Agroambiental Sensorizada, Inteligente e Sustentável**

API, firmware e pipeline de visão computacional da OASIS, solução de IoT + IA para monitoramento e classificação de uvas do Projeto Integrador **“Inteligência de Dados no Vale do São Francisco”**.

```
 DEVICE                  EDGE                           CLOUD
┌──────────────┐ HTTP/  ┌──────────────────────┐ HTTPS ┌───────────────────────────────────────────┐
│ ESP32 + Cam  │─MQTT──▶│ Gateway (edge/)      │──────▶│ API FastAPI (app/)                        │
│ (+ DHT22)    │        │ brilho · nitidez ·   │       │ /ingest → análise → classificação → banco │──▶ Painel web
│ iot/esp32-cam│        │ resize · fila offline│       │ /imports · /sensors · /readings · /stats  │    (Vinicola-Front)
└──────────────┘        └──────────────────────┘       └───────────────────────────────────────────┘
                                                                        │
                                                          Banco de dados (Vinicola-bd)
```

## Estrutura

| Pasta | Camada | Conteúdo |
| --- | --- | --- |
| `iot/esp32-cam/` | Device | Firmware Arduino: captura, NTP, DHT22 opcional, HTTP multipart ou MQTT, deep sleep |
| `edge/` | Edge | Gateway que avalia a qualidade da imagem, redimensiona, encaminha e mantém fila offline |
| `app/` | Cloud | API FastAPI (detalhes abaixo) |
| `ml/` | IA | Classes do modelo, treino YOLO, inferência e guia de anotação |
| `tests/` | Qualidade | 50+ testes Pytest cobrindo API, importação, análise de imagem e edge |

Dentro de `app/`:

| Arquivo | Responsabilidade |
| --- | --- |
| `main.py` | Inicialização, CORS, cabeçalhos de segurança, GZip, tratamento de erros e mensagens de validação em português |
| `config.py` | Configurações via variáveis de ambiente / `.env` e validação para produção |
| `models.py` | Modelo relacional (SQLAlchemy) — espelha os scripts do repositório Vinicola-bd |
| `schemas.py` | Contrato JSON (camelCase), o mesmo tipado no frontend |
| `routers/` | `auth`, `users`, `sensors`, `ingest`, `readings`, `stats`, `imports`, `system` |
| `services/detector.py` | Análise de cor (padrão) e YOLO (quando há pesos treinados) |
| `services/classifier.py` | Regras que transformam detecções em qualidade, maturação e classificação |
| `services/imports.py` | Leitura e validação de CSV / Excel / JSON |
| `services/sensors.py` | Status calculado dos sensores (online, atenção, offline, inativo) |
| `seed.py` · `cli.py` · `demo.py` | Catálogo e primeiro administrador · comandos administrativos · dados de demonstração sob comando |

## Executando

```bash
python -m venv .venv
source .venv/bin/activate            # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # defina JWT_SECRET (e, se quiser, OASIS_ADMIN_PASSWORD)
python -m uvicorn app.main:app --reload
```

Na primeira execução a API cria as tabelas, o catálogo de variedades e o **administrador inicial** (`OASIS_ADMIN_EMAIL`). Se `OASIS_ADMIN_PASSWORD` estiver vazio, uma senha aleatória é exibida **uma única vez** no terminal — troque-a em *Minha conta*. Nenhum sensor ou leitura é criado automaticamente.

- Documentação interativa (fora de produção): http://localhost:8000/docs
- Frontend: no repositório `Vinicola-Front`, `npm run dev` já encaminha `/api` para `http://localhost:8000`.

### Comandos administrativos

```bash
python -m app.cli create-user --name "Maria Lima" --email maria@empresa.com --role gestor
python -m app.cli reset-password --email maria@empresa.com
python -m app.cli check-config        # valida as configurações de produção
python -m app.cli seed-demo --days 30 # dados SINTÉTICOS (sensores DEMO-01..03, origem "demonstracao")
python -m app.cli clear-demo          # remove todos os dados de demonstração
```

Os dados de demonstração existem apenas para apresentar a interface sem hardware: não têm coordenadas nem imagens, ficam marcados como “Demonstração” no painel e não entram nos números da página pública.

### Com PostgreSQL (Docker)

```bash
git clone https://github.com/PI-2026-2-Vinicola/Vinicola-bd ../Vinicola-bd
cp .env.example .env               # defina JWT_SECRET, POSTGRES_PASSWORD e OASIS_ADMIN_PASSWORD
docker compose up --build          # API :8000 · edge :8081 · Postgres 127.0.0.1:5432
```

MySQL/MariaDB: `DATABASE_URL=mysql+pymysql://oasis:SENHA@localhost:3306/oasis` (esquema em `Vinicola-bd/mysql`).

## Variáveis de ambiente

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | Em `production`, a API não sobe com configuração insegura e desativa `/docs` |
| `DATABASE_URL` | `sqlite:///./oasis.db` | Banco (SQLite, PostgreSQL ou MySQL) |
| `JWT_SECRET` | valor de desenvolvimento | Segredo dos tokens (≥ 32 caracteres em produção) |
| `JWT_EXPIRES_MINUTES` | `480` | Validade da sessão |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Bloqueio após tentativas de login falhas |
| `OASIS_ADMIN_EMAIL` / `_NAME` / `_PASSWORD` | `admin@oasis.agr.br` / … / vazio | Administrador inicial |
| `OASIS_DETECTOR` | `auto` | `auto` (YOLO se houver pesos, senão cor), `color` ou `yolo` |
| `OASIS_MODEL_PATH` / `OASIS_MODEL_CONFIDENCE` | `models/oasis-grapes.pt` / `0.35` | Pesos e confiança mínima do YOLO |
| `OASIS_TIMEZONE` | `America/Recife` | Fuso usado nos filtros e gráficos por dia/hora |
| `OASIS_FARM_NAME` | vazio | Nome da propriedade exibido no painel |
| `STORAGE_DIR` | `storage` | Pasta das imagens e miniaturas |
| `MAX_UPLOAD_MB` / `MAX_IMPORT_MB` / `MAX_IMPORT_ROWS` | `8` / `10` / `20000` | Limites de envio |
| `SENSOR_OFFLINE_FACTOR` | `3` | Offline após N × intervalo de captura sem comunicação |
| `PUBLIC_READ` | `false` | Leitura sem login (proibido em produção) |
| `CORS_ORIGINS` | `http://localhost:5173,…` | Origens do frontend |

## Endpoints (`/api/v1`)

| Método | Rota | Acesso | Descrição |
| --- | --- | --- | --- |
| POST | `/auth/login` | público | Login (bloqueio após tentativas falhas) → `accessToken`, `expiresIn`, `user` |
| GET | `/auth/me` | autenticado | Usuário atual |
| POST | `/auth/password` | autenticado | Troca da própria senha |
| GET · POST · PATCH | `/users` · `/users/{id}` | admin | Usuários: criar, alterar perfil, desativar, redefinir senha |
| GET | `/sensors` · `/sensors/{id}` | autenticado | Sensores com status calculado, nº de análises e última leitura |
| POST · PATCH | `/sensors` · `/sensors/{id}` | admin | Cadastro e edição (o token do dispositivo é exibido uma vez) |
| POST | `/sensors/{id}/token` | admin | Gera novo token e invalida o anterior |
| POST | `/sensors/{id}/heartbeat` | dispositivo | Bateria, sinal, firmware, temperatura e umidade |
| POST · GET | `/sensors/{id}/environment` | dispositivo/usuário · autenticado | Medições ambientais |
| GET | `/sensors/{id}/telemetry` | autenticado | Histórico de bateria e sinal |
| POST | `/ingest` | dispositivo (`X-Device-Token`) ou usuário | Recebe a imagem, analisa, classifica e grava |
| GET | `/readings` | autenticado | Histórico paginado (`page`, `pageSize`, `sort`) com filtros `days`, `dateFrom`, `dateTo`, `sensorId`, `varietyId`, `quality` (lista), `classification`, `maturation`, `block`, `source`, `q` |
| GET | `/readings/export` | autenticado | CSV com os mesmos filtros |
| GET · DELETE | `/readings/{codigo}` | autenticado · admin | Detalhe / exclusão |
| GET | `/readings/{codigo}/image[?size=thumb]` | autenticado | Imagem processada ou miniatura |
| GET | `/stats/summary` | autenticado | Indicadores do período + período anterior + ambiente |
| GET | `/stats/by-day` · `/stats/by-hour` | autenticado | Séries por dia (com dias vazios) e por hora local |
| GET | `/stats/breakdown?by=` | autenticado | Totais por `sensor`, `variety`, `maturation`, `classification`, `source` ou `block` |
| GET | `/stats/environment` | autenticado | Médias diárias de temperatura, umidade, luminosidade e solo |
| POST | `/imports/preview` | admin, gestor | Valida o arquivo e mostra o resultado sem gravar |
| POST | `/imports` | admin, gestor | Valida novamente e grava em uma transação |
| GET | `/imports` · `/imports/{id}` · `/imports/templates/{tipo}` | admin, gestor | Histórico e modelos CSV |
| GET | `/varieties` · `/public/overview` | público | Catálogo e números agregados da página inicial |
| GET | `/system/status` · `/audit` | admin | Estado do sistema e trilha de auditoria |

O JSON é em **camelCase**. Erros sempre trazem `detail` em português (validação: `detail` + `errors`). As caixas de detecção seguem o padrão YOLO normalizado (`x, y, largura, altura`, entre 0 e 1).

## Análise das imagens

O fluxo de `/ingest`: validação do arquivo (JPEG/PNG/WEBP, limite de tamanho, proteção contra imagens gigantes) → correção de orientação e **remoção de EXIF/GPS** → detector → classificação → gravação da imagem (máx. 1600 px) e da miniatura (360 px) → leitura com código `OA-00001`.

`app/services/detector.py` tem duas implementações reais:

- **Análise de cor** (`color`, padrão sem modelo): segmentação HSV que localiza a região do cacho e mede manchas acastanhadas (mancha leve/podridão), mofo acinzentado e bagas fora de cor (tintas em véraison). A variedade é a cadastrada no talhão do sensor; o estágio de maturação vem da cor das bagas. Limitações conhecidas: depende de enquadramento e luz; em uvas brancas não distingue bagas verdes da folhagem; foi calibrada com imagens sintéticas e deve ser ajustada com fotos de campo.
- **YOLO** (`yolo`): Ultralytics com os pesos em `OASIS_MODEL_PATH` e as classes de `ml/dataset.yaml` (variedades + anomalias). Com `auto`, é usado assim que o arquivo de pesos existir.

As regras de `app/services/classifier.py`:

| Detecções | Qualidade | Classificação |
| --- | --- | --- |
| Só cachos | Boa | APROVADA |
| Anomalia leve (`maturacao_desigual`, `baga_irregular`, `mancha_leve`) | Atenção | EM OBSERVAÇÃO |
| Anomalia grave (`podridao`, `baga_murcha`, `lesao`) | Necessita atenção | REVISÃO NECESSÁRIA |
| Nenhum cacho identificado | Atenção | EM OBSERVAÇÃO (orienta revisar enquadramento/luz) |

Anomalias com confiança abaixo de 0,5 são ignoradas. Treinamento e anotação: [`ml/README.md`](ml/README.md).

## Importação de dados

Formatos: **CSV** (`;`, `,` ou tabulação; UTF-8 ou Latin-1), **Excel .xlsx** (primeira planilha) e **JSON** (lista ou `{"items": [...]}`). Tipos: `readings` (leituras históricas), `sensors` (cadastro) e `environment` (medições). Cabeçalhos em português são reconhecidos (`data_hora`, `qualidade`, `variedade`, `temperatura`…), datas em `dd/mm/aaaa hh:mm` ou ISO (sem fuso = horário da propriedade), números com vírgula decimal.

Cada linha é validada (sensor e variedade cadastrados, datas não futuras, faixas numéricas, coerência qualidade × classificação); linhas repetidas no arquivo e registros já existentes no banco são identificados. A gravação acontece em uma transação, registra o resultado em `import_jobs` (com os erros por linha) e na auditoria. Duplicados podem ser ignorados ou atualizados.

Colunas opcionais ausentes em leituras importadas recebem valores explícitos, nunca estimados: maturação “não informada”, condição visual igual ao rótulo da qualidade, variedade do talhão do sensor, 1 cacho por leitura e versão de análise “Importado”. Leituras importadas não têm imagem nem caixas de detecção.

## Segurança

Senhas com PBKDF2-SHA256 (240 mil iterações) e política mínima; JWT com expiração; bloqueio de login por e-mail e IP; perfis `admin`, `gestor` e `operador` verificados no servidor; usuário desativado perde o acesso imediatamente; token individual por dispositivo (exibido uma vez, guardado como hash); CORS restrito; cabeçalhos `nosniff`, `X-Frame-Options`, `Referrer-Policy`; `Cache-Control: no-store` nas respostas da API; consultas parametrizadas (SQLAlchemy) e busca textual com curingas escapados; CSV exportado protegido contra injeção de fórmulas; imagens reprocessadas e sem metadados; erros internos não expõem detalhes; auditoria de logins, alterações e importações.

## Testes

```bash
python -m pytest -q
```

## Enviando uma imagem pelo terminal

```bash
# código e token exibidos ao cadastrar o sensor no painel
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Device-Token: $TOKEN_DO_SENSOR" \
  -F sensor_id=S-001 -F image=@uva.jpg
```

---

A classificação é baseada na análise computacional da imagem e **não substitui a avaliação agronômica profissional**.
