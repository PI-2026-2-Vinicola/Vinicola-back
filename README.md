# OASIS — Backend, IoT e IA

**Observação Agroambiental Sensorizada, Inteligente e Sustentável**

API, firmware e pipeline de visão computacional da OASIS, solução de IoT + IA para monitoramento e classificação de uvas do Projeto Integrador **“Inteligência de Dados no Vale do São Francisco”**.

```
 DEVICE                  EDGE                           CLOUD
┌──────────────┐ HTTP/  ┌──────────────────────┐ HTTPS ┌───────────────────────────────────────────┐
│ ESP32 + Cam  │─MQTT──▶│ Gateway (edge/)      │──────▶│ API FastAPI (app/)                        │
│ (+ DHT22)    │        │ brilho · nitidez ·   │       │ /ingest → análise → classificação → banco │──▶ Painel web
│ iot/esp32-cam│        │ resize · fila offline│       │ /imports · /sensors · /readings · /stats  │    (Vinicola-Front)
└──────────────┘        └──────────────────────┘       │ /iot/thingspeak · /embrapa/*              │
                                                        └───────┬──────────────────┬────────────────┘
 Sensores do canal ──▶ ThingSpeak ◀──── API REST (leitura) ─────┘                  │
                                    Embrapa AgroAPI ◀── OAuth2 · RespondeAgro · Agritec v2
                                                          Banco de dados (Vinicola-bd)
```

## Estrutura

| Pasta | Camada | Conteúdo |
| --- | --- | --- |
| `iot/esp32-cam/` | Device | Firmware Arduino: captura, NTP, DHT22 opcional, HTTP multipart ou MQTT, deep sleep |
| `edge/` | Edge | Gateway que avalia a qualidade da imagem, redimensiona, encaminha e mantém fila offline |
| `app/` | Cloud | API FastAPI (detalhes abaixo) |
| `ml/` | IA | Classes do modelo, treino YOLO, inferência e guia de anotação |
| `tests/` | Qualidade | 98 testes Pytest cobrindo API, importação, análise de imagem, edge e integrações (com respostas simuladas) |

Dentro de `app/`:

| Arquivo | Responsabilidade |
| --- | --- |
| `main.py` | Inicialização, CORS, cabeçalhos de segurança, GZip, tratamento de erros e mensagens de validação em português |
| `config.py` | Configurações via variáveis de ambiente / `.env` e validação para produção |
| `models.py` | Modelo relacional (SQLAlchemy) — espelha os scripts do repositório Vinicola-bd |
| `schemas.py` | Contrato JSON (camelCase), o mesmo tipado no frontend |
| `routers/` | `auth`, `users`, `sensors`, `ingest`, `readings`, `stats`, `imports`, `system`, `integrations` |
| `services/detector.py` | Análise de cor (padrão) e YOLO (quando há pesos treinados com as classes da OASIS) |
| `services/thingspeak.py` | Leitura do canal do ThingSpeak (REST), conversão dos campos, cache e falhas |
| `services/embrapa.py` | AgroAPI da Embrapa: OAuth2, RespondeAgro v1 e Agritec v2 (Zarc), cache e falhas |
| `services/sanitize.py` | Sanitização por lista de permissões do HTML das respostas do RespondeAgro |
| `services/external.py` | Erro padronizado, cache com expiração e limite de requisições das integrações |
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
                                     # integrações: THINGSPEAK_* e EMBRAPA_* (opcionais; ver seções abaixo)
python -m uvicorn app.main:app --reload
python -m app.cli check-integrations # confere ThingSpeak e Embrapa com chamadas reais
```

Na primeira execução a API cria as tabelas, o catálogo de variedades e o **administrador inicial** (`OASIS_ADMIN_EMAIL`). Se `OASIS_ADMIN_PASSWORD` estiver vazio, uma senha aleatória é exibida **uma única vez** no terminal — troque-a em *Minha conta*. Nenhum sensor ou leitura é criado automaticamente.

- Documentação interativa (fora de produção): http://localhost:8000/docs
- Frontend: no repositório `Vinicola-Front`, `npm run dev` já encaminha `/api` para `http://localhost:8000`.

### Comandos administrativos

```bash
python -m app.cli create-user --name "Maria Lima" --email maria@empresa.com --role gestor
python -m app.cli reset-password --email maria@empresa.com
python -m app.cli check-config        # valida as configurações de produção
python -m app.cli check-integrations  # consulta ThingSpeak e Embrapa de verdade e lista campos, livro e culturas
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
| `OASIS_ANALYSIS_TIMEOUT_SECONDS` | `30` | Tempo máximo da análise de uma imagem |
| `THINGSPEAK_CHANNEL_ID` | vazio | ID do canal (vazio = monitoramento não configurado) |
| `THINGSPEAK_READ_API_KEY` | vazio | Read API Key, só para canal privado |
| `THINGSPEAK_CACHE_SECONDS` / `THINGSPEAK_REFRESH_SECONDS` | `15` / `60` | Cache no servidor e atualização automática do painel (mín. 15 s) |
| `THINGSPEAK_FIELD_UNITS` | vazio | Unidades informadas pelo responsável (`field1=°C,field2=%`) |
| `THINGSPEAK_API_URL` / `THINGSPEAK_TIMEOUT_SECONDS` | `https://api.thingspeak.com` / `10` | Endereço e tempo limite |
| `EMBRAPA_CONSUMER_KEY` / `EMBRAPA_CONSUMER_SECRET` | vazio | Credenciais da aplicação na loja da AgroAPI |
| `EMBRAPA_CACHE_HOURS` / `EMBRAPA_AGRITEC_CACHE_HOURS` | `24` / `168` | Cache do RespondeAgro e do Agritec |
| `EMBRAPA_BOOK_ID` | `uva` | Livro da Coleção 500 Perguntas 500 Respostas usado na aba Uvas |
| `EMBRAPA_RATE_LIMIT_PER_MINUTE` | `30` | Consultas públicas à Embrapa por IP e por minuto |
| `EMBRAPA_API_URL` / `EMBRAPA_TIMEOUT_SECONDS` | `https://api.cnptia.embrapa.br` / `15` | Endereço e tempo limite |

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
| GET | `/analysis/model` | autenticado | Detector em uso, classes que ele identifica, limites de envio e limitações |
| GET | `/public/integrations` | público | Situação das integrações (só booleanos e descrições, sem IDs nem credenciais) |
| GET | `/iot/thingspeak?period=recent\|24h\|7d\|30d&results=` | autenticado | Canal do ThingSpeak: campos, últimos valores, série e horário da última leitura |
| GET | `/embrapa/questions?q=&page=&pageSize=&scope=grape\|all` | público (limitado por IP) | Busca no RespondeAgro (livro `uva` ou toda a coleção) |
| GET | `/embrapa/questions/{id}` · `/embrapa/books` | público (limitado por IP) | Pergunta por identificador · livros indexados |
| GET | `/embrapa/zarc/cultures` · `/embrapa/zarc?cultureId=&ibge=` | público (limitado por IP) | Culturas de uva do Agritec · janelas de plantio do Zarc |
| GET | `/readings/{id}/embrapa` | leitura (limitado por IP) | Anomalias detectadas na leitura (YOLO ou análise de cor) cruzadas com o livro de uva do RespondeAgro |
| GET | `/embrapa/municipalities` | público | Municípios do Submédio São Francisco aceitos na consulta do Zarc |

O JSON é em **camelCase**. Erros sempre trazem `detail` em português (validação: `detail` + `errors`). As caixas de detecção seguem o padrão YOLO normalizado (`x, y, largura, altura`, entre 0 e 1).

## Análise das imagens

O fluxo de `/ingest`: validação do arquivo (JPEG/PNG/WEBP, limite de tamanho, proteção contra imagens gigantes) → correção de orientação e **remoção de EXIF/GPS** → detector → classificação → gravação da imagem (máx. 1600 px) e da miniatura (360 px) → leitura com código `OA-00001`.

`app/services/detector.py` tem duas implementações reais:

- **Análise de cor** (`color`, padrão sem modelo): segmentação HSV que localiza a região do cacho e mede manchas acastanhadas (mancha leve/podridão), mofo acinzentado e bagas fora de cor (tintas em véraison). A variedade é a cadastrada no talhão do sensor; o estágio de maturação vem da cor das bagas. Limitações conhecidas: depende de enquadramento e luz; em uvas brancas não distingue bagas verdes da folhagem; foi calibrada com imagens sintéticas e deve ser ajustada com fotos de campo.
- **YOLO** (`yolo`): Ultralytics com os pesos em `OASIS_MODEL_PATH` e as classes de `ml/dataset.yaml` (variedades + anomalias). Com `auto`, é usado assim que o arquivo de pesos existir.

**Situação do YOLO:** nenhum peso treinado foi publicado (`models/` só tem `.gitkeep`); a análise de cor é o detector ativo. A integração foi verificada com a biblioteca Ultralytics 8.4 e um modelo genérico (`yolo11n.pt`, COCO) apenas para conferir o código: modelos **sem nenhuma classe da OASIS são recusados** (no modo `auto` a API volta para a análise de cor; no modo `yolo`, não inicia), e classes fora do catálogo são descartadas na inferência — a saída de um modelo genérico nunca vira variedade ou anomalia. As caixas são recortadas nas bordas da imagem. `GET /api/v1/analysis/model` informa o detector em uso e só as classes que ele realmente produz.

A análise roda fora do laço de eventos da API, com limite de `OASIS_ANALYSIS_TIMEOUT_SECONDS` (504 se exceder); falhas do detector devolvem 503 sem gravar a leitura. Na primeira inferência em CPU, o YOLO leva alguns segundos para aquecer.

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

## Integração com o ThingSpeak

A página **Monitoramento** do painel usa `GET /api/v1/iot/thingspeak`, que consulta `GET https://api.thingspeak.com/channels/{THINGSPEAK_CHANNEL_ID}/feeds.json` ([documentação](https://www.mathworks.com/help/thingspeak/readdata.html)).

- **Credenciais necessárias:** canal público → só `THINGSPEAK_CHANNEL_ID`; canal privado → também `THINGSPEAK_READ_API_KEY` (aba *API Keys* do canal). As credenciais MQTT de um dispositivo (client ID, usuário e senha) autenticam publicação/assinatura no broker MQTT do ThingSpeak e **não** são aceitas pela API REST de leitura; ficam no dispositivo que publica no canal.
- **Períodos:** `recent` = últimos N registros (`results`, 10–1000); `24h`, `7d` e `30d` usam `days` + `timescale` (primeiro registro de cada janela de 10 min, 1 h e 4 h). `average` não é usado porque, segundo a documentação, trata NaN como zero.
- **Localização:** a leitura pede `location=true`. A posição vem, em ordem, dos parâmetros `lat`/`long` de cada registro, de campos do canal chamados `Latitude`/`Longitude` (que saem dos gráficos) ou da posição fixa do canal; 0,0 é tratado como ausente. A página Monitoramento mostra a última posição e o trajeto no mapa.
- **Campos:** `field1`…`field8` com o nome definido no canal (é a única descrição do significado de cada campo). Unidades só aparecem se informadas em `THINGSPEAK_FIELD_UNITS`. Valores viram número quando possível (aceita vírgula decimal); texto, vazio, NaN e infinito ficam de fora dos gráficos; campos de texto mostram o último valor.
- **Atualização e limites:** cache de `THINGSPEAK_CACHE_SECONDS` (mín. 15 s — o intervalo mínimo de atualização de um canal no plano gratuito); o painel recarrega a cada `THINGSPEAK_REFRESH_SECONDS`. Os valores não são gravados no banco da OASIS.
- **Falhas:** resposta `-1`/400/401/403 → acesso negado (orienta sobre a Read API Key); 404 → canal inexistente; 429 → limite; tempo esgotado → 504; JSON inválido → 502. Se houver resposta válida de até 1 h, ela é devolvida com `stale: true` e o motivo em `warning`.
- **Validação:** `python -m app.cli check-integrations` lista o canal, os campos e os últimos valores. A Read API Key vai na URL (exigência da API de leitura) e, por isso, os logs de URL do httpx são silenciados em `main.py`.

## Integração com a Embrapa (AgroAPI)

A aba **Uvas** consome duas APIs da [AgroAPI](https://www.agroapi.cnptia.embrapa.br/portal/), sempre pela API OASIS (as credenciais nunca chegam ao navegador). Documentação usada: [primeiros passos](https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/agroapi-primeiros-passos.pdf), [RespondeAgro](https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/respondeagro.pdf) e [Agritec](https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/agritec.pdf), além dos contratos Swagger/OpenAPI da loja.

- **Autenticação:** `POST https://api.cnptia.embrapa.br/token` com `grant_type=client_credentials` e Basic `consumer key:consumer secret` (aplicação criada na loja e inscrita nas duas APIs). O token (3.600 s) é guardado em memória e renovado sozinho; um 401 com token em cache provoca uma renovação e nova tentativa.
- **RespondeAgro v1:** `POST /respondeagro/v1/_search/template` com `query_one_book` (livro `EMBRAPA_BOOK_ID`, padrão `uva`), `query_all` ou `book_ids`, e `GET /respondeagro/v1/_doc/{id}`. A resposta (formato Elasticsearch) traz pergunta, resposta em HTML (com imagens em base64), capítulo, livro, ano e links do livro em PDF/EPUB. O HTML é sanitizado por lista de permissões (`services/sanitize.py`).
- **Agritec v2:** `GET /agritec/v2/culturas` (culturas com “uva” no nome — nenhum ID é fixado no código), `GET /agritec/v2/zoneamento?idCultura=&codigoIBGE=&risco=todos`, `GET /agritec/v2/culturas/{id}/informacoes-adicionais` e `GET /agritec/v2/solos`. Só municípios do Submédio São Francisco listados em `app/catalog.py` são aceitos.
- **Detecção → Embrapa:** em cada leitura, as anomalias do modelo (confiança ≥ 0,5, uma por classe, graves primeiro) viram buscas no livro de uva (`ANOMALY_EMBRAPA_QUERY` em `app/catalog.py`; 3 respostas por anomalia, com o mesmo cache). Sem anomalias, nenhuma chamada é feita; sem credenciais, nada é exibido no lugar.
- **Limites e cache:** RespondeAgro gratuito até 100 mil requisições/mês; Agritec gratuito por um mês e até 1.000 requisições. Cache de 24 h (RespondeAgro) e 7 dias (Agritec), limite de 30 consultas públicas por minuto por IP e reaproveitamento da última resposta (até 7 dias) se a AgroAPI falhar.
- **Erros tratados:** credenciais recusadas no `/token` (`invalid_client`), acesso negado do gateway (900901/900908), cota (429), indisponibilidade (5xx), tempo esgotado e corpo de erro do Agritec (`error.summary`/`fieldErrors`).
- **Verificação sem credenciais:** o gateway da AgroAPI confere o caminho antes da autenticação (recurso inexistente → 404; existente → 401), o que permitiu confirmar todos os recursos usados; o fluxo de token foi exercitado contra o `/token` real com credenciais inválidas.

## Limitações conhecidas e pendências

- **Credenciais externas:** sem `THINGSPEAK_CHANNEL_ID` (e a Read API Key, se o canal for privado) o Monitoramento mostra “não configurado”; sem `EMBRAPA_CONSUMER_KEY`/`EMBRAPA_CONSUMER_SECRET` a aba Uvas informa a indisponibilidade do conteúdo da Embrapa — nada é simulado.
- **Agritec:** cultivares, adubação e produtividade atendem culturas que não incluem a uva; só o Zarc é usado. Se a lista de culturas não tiver uva, a aba informa isso.
- **YOLO:** depende de imagens do vinhedo anotadas e validadas por um responsável técnico (ver `ml/README.md`).
- **Cache e limite por processo:** com várias instâncias da API, cada uma mantém o seu (para escalar, usar Redis).

## Segurança

Senhas com PBKDF2-SHA256 (240 mil iterações) e política mínima; JWT com expiração; bloqueio de login por e-mail e IP; perfis `admin`, `gestor` e `operador` verificados no servidor; usuário desativado perde o acesso imediatamente; token individual por dispositivo (exibido uma vez, guardado como hash); CORS restrito; cabeçalhos `nosniff`, `X-Frame-Options`, `Referrer-Policy`; `Cache-Control: no-store` nas respostas da API; consultas parametrizadas (SQLAlchemy) e busca textual com curingas escapados; CSV exportado protegido contra injeção de fórmulas; imagens reprocessadas e sem metadados; erros internos não expõem detalhes; auditoria de logins, alterações e importações. Integrações: credenciais só no servidor (`.env` fora do git), logs de URL do httpx silenciados, HTML externo sanitizado, tempos limite, cache e limite de consultas públicas por IP.

## Testes

```bash
python -m pytest -q
```

A suíte não chama serviços externos: o `conftest.py` desativa ThingSpeak e Embrapa e os testes das integrações usam `httpx.MockTransport` com respostas no formato da documentação oficial (incluindo os exemplos do RespondeAgro).

## Enviando uma imagem pelo terminal

```bash
# código e token exibidos ao cadastrar o sensor no painel
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Device-Token: $TOKEN_DO_SENSOR" \
  -F sensor_id=S-001 -F image=@uva.jpg
```

---

A classificação é baseada na análise computacional da imagem e **não substitui a avaliação agronômica profissional**.
