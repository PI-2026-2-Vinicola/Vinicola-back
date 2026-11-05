# OASIS · YOLO com imagens reais da Embrapa

Este documento cobre a integração entre a OASIS, as fontes de dados da Embrapa e o detector YOLO. Ele trata, nesta ordem:
- o que cada fonte oferece;
- como o dataset foi montado;
- o que o modelo detecta e por quê;
- como o modelo foi treinado e avaliado;
- como ele roda na aplicação;
- o que os dados **não** permitem fazer.

Tudo o que aparece aqui está implementado no repositório. Os números vêm dos relatórios gerados pelo próprio pipeline (`ml/reports/`) e da ficha do modelo (`models/oasis-cachos.json`).

## Resumo

- **Fonte das imagens:** **Embrapa WGISD** (Wine Grape Instance Segmentation Dataset), da Embrapa Informática Agropecuária. São 300 fotografias reais de campo, com 4.430 cachos anotados manualmente por caixas, de cinco variedades. As APIs da AgroAPI que a plataforma já usa (RespondeAgro e Agritec) fornecem texto e dados agronômicos, **não imagens rotuladas**.
- **Modelo:** **YOLO26n** treinado com o WGISD para **localizar cachos de uva** (classe única `cacho`). Ele é exportado para ONNX e roda na API com o ONNX Runtime. No teste oficial do WGISD, que o modelo nunca viu, atingiu **mAP50 de 0,83**, precisão de 78,6% e recall de 73,9%, com cerca de 60 ms por foto em CPU.
- **Na aplicação:** a foto enviada vai para `POST /api/v1/ingest`. Lá, o YOLO localiza os cachos. A variedade vem do cadastro do talhão. A análise de cor examina cada cacho e aponta manchas, mofo e bagas fora de cor. O resultado é exibido como **previsão do modelo**, com a origem de cada informação.
- **Limite honesto:** nenhuma fonte da Embrapa acessível traz rótulos de **qualidade, doença ou dano**. O WGISD cobre só **duas** das seis variedades do catálogo (Cabernet Sauvignon e Syrah). Por isso **não foi treinado** um modelo de qualidade nem de variedade. A seção 10 traz o plano para o dataset próprio, e as ferramentas desse plano já estão implementadas.

## 1. Fontes de dados da Embrapa

### 1.1 APIs já integradas (AgroAPI)

A integração existente (`app/services/embrapa.py`) usa duas APIs da [AgroAPI](https://www.agroapi.cnptia.embrapa.br/portal/). A documentação oficial e os contratos Swagger/OpenAPI estão citados no código.

| API | Endpoints usados | O que devolve | Imagens? |
| --- | --- | --- | --- |
| **RespondeAgro v1** | `POST /respondeagro/v1/_search/template` (templates `query_one_book`, `query_all`, `book_ids`), `GET /respondeagro/v1/_doc/{id}` | Perguntas e respostas da Coleção 500 Perguntas 500 Respostas: pergunta, resposta em HTML, capítulo, livro, ano, links do PDF/EPUB | Só ilustrações em base64 dentro do HTML de algumas respostas. Não há caixas, rótulos, variedade nem licença de reuso nos campos devolvidos. **Não servem para treino.** |
| **Agritec v2** | `GET /agritec/v2/culturas`, `/agritec/v2/zoneamento`, `/agritec/v2/culturas/{id}/informacoes-adicionais`, `/agritec/v2/solos` | Zoneamento Agrícola de Risco Climático (Zarc), culturas e solos | Não |

- **Autenticação:** OAuth2 *client credentials*. A chamada é `POST https://api.cnptia.embrapa.br/token` com Basic `consumer key:consumer secret`. O token vale 3.600 s e é renovado automaticamente. As credenciais ficam só no servidor (`EMBRAPA_CONSUMER_KEY` e `EMBRAPA_CONSUMER_SECRET`).
- **Paginação:** o RespondeAgro pagina no estilo Elasticsearch (`from` e `size` no template). A API OASIS limita a página a 50 e o tamanho a 20.
- **Limites dos planos**, conforme a documentação oficial citada no código:
  - RespondeAgro: gratuito até 100 mil requisições por mês.
  - Agritec: gratuito por um mês e até 1.000 requisições.
  - Na OASIS: cache de 24 h e de 7 dias, e 30 consultas públicas por minuto por IP.
- **Uso nesta integração:** as anomalias apontadas na análise viram buscas no livro de uva (`ANOMALY_EMBRAPA_QUERY`). As respostas da Embrapa aparecem como recomendações na página da leitura. É o caso em que "os dados permitem recomendações".

### 1.2 Outras fontes oficiais avaliadas (verificadas em 05/11/2026)

| Fonte | O que foi consultado | Resultado |
| --- | --- | --- |
| **AgroAPI, catálogo completo** (`/store/apis/list`) | Descrição oficial das 10 APIs: AGROFIT, Agritec, AgroTermos, Bioinsumos, BovTrace, ClimAPI, RespondeAgro, SATVeg, SmartSolos Expert, Sting | **Nenhuma fornece imagens.** Tratam de defensivos registrados, Zarc, termos, bioinsumos, rastreio bovino, previsão do tempo, perguntas e respostas, séries NDVI/EVI de satélite, classificação de solos e estruturas de proteínas. |
| **Embrapa WGISD**, [GitHub](https://github.com/thsant/wgisd) e Zenodo (DOI [10.5281/zenodo.3361736](https://doi.org/10.5281/zenodo.3361736), v1.0.0) | Repositório, datasheet, EXIF, registro no Zenodo | **Usada** (seção 2). Licença: o GitHub declara CC BY-NC 4.0 e o Zenodo, **CC BY-NC-SA 4.0**. Adotamos a mais restritiva: uso não comercial, com atribuição e compartilhamento pela mesma licença. |
| **Redape**, repositório de dados de pesquisa da Embrapa (Dataverse 5.12.1) | API de busca (`/api/search`) com os termos uva, videira, vitis, grape, cacho, míldio, "Uva e Vinho", "detecção de frutos", "fruit detection" e "object detection" | Só dois datasets têm imagens de videira: PDDB e AACr3 (linhas abaixo). Os demais resultados de uva são de genoma, metabolômica e sensores químicos, e os de detecção de frutos são de laranja. |
| **PDDB / Digipathos**, Base de Imagens de Sintomas de Doenças de Plantas (`doi:10.48432/XA1OVL`, Embrapa Agricultura Digital) | Metadados, licença, termo de uso e download, com MD5 conferido, dos 15 arquivos de videira | Fotos de **videira rotuladas por fitopatologistas** (antracnose, cancro bacteriano, ferrugem, mancha das folhas, míldio, oídio, vírus e sadia). **Quase todas são de folhas e ramos**, em laboratório ou sobre cartela de calibração. Não servem para avaliar cachos. O inventário está em `ml/reports/pddb-videira.json`. Licença: CC BY-NC 4.0; o termo de uso proíbe uso comercial e exige citar Barbedo, Koenigkan e Santos (2016). |
| **AACr3**, vinhedo em 3-D (`doi:10.48432/MSOSBR`) | Metadados e lista de arquivos | Gravações brutas de sensores (bags ROS de 10 a 60 GB) para SLAM, **sem rótulos**. Não servem para treino supervisionado. |
| Alice e Infoteca-e | Acessíveis | São publicações (PDF). Servem de referência técnica, não de dataset. |

A coleta pelo Redape está implementada em `ml/collect_redape.py`, testada contra o servidor real:

```bash
python ml/collect_redape.py buscar videira
python ml/collect_redape.py arquivos doi:10.48432/XA1OVL --filtro Videira
python ml/collect_redape.py baixar   doi:10.48432/XA1OVL --filtro Videira   # MD5 conferido + manifesto
```

Nenhuma dessas fontes foi substituída por dados inventados.

## 2. Dataset

### 2.1 Origem: Embrapa WGISD

Conferido no datasheet do repositório e no EXIF das imagens originais, na versão `6910edc5ae3aae8c20062941b1641821f0c30127`:

| | |
| --- | --- |
| Instituição | Embrapa Informática Agropecuária. Autores: T. T. Santos, L. L. de Souza, A. A. Santos e S. Avila ([artigo](https://arxiv.org/abs/1907.11819)). |
| Local | Vinícola Guaspari, Espírito Santo do Pinhal-SP (−22,181018; −46,741618), com sistema de dupla poda |
| Datas (EXIF) | 27/04/2017 para Syrah e 27/04/2018 para as demais |
| Câmeras (EXIF) | Canon EOS REBEL T3i (240 fotos) e Motorola Moto Z2 Play (60 fotos) |
| Imagens | 300, em 2048 × 1365 ou 2048 × 1536 px, mais as originais em resolução completa |
| Anotação | Caixas de cachos feitas no `labelImg`, máscaras de 2.020 cachos em 137 imagens e pontos de bagas (contribuições) |
| Licença | **CC BY-NC 4.0** no GitHub e **CC BY-NC-SA 4.0** no Zenodo. Vale a mais restritiva: uso não comercial, com atribuição e compartilhamento pela mesma licença. Para uso comercial, é preciso falar com o escritório de negócios da Embrapa. |

| Variedade (prefixo) | Imagens | Cachos | No catálogo da OASIS? |
| --- | --- | --- | --- |
| Chardonnay (CDY) | 65 | 839 | não |
| Cabernet Franc (CFR) | 65 | 1.069 | não |
| Cabernet Sauvignon (CSV) | 57 | 643 | **sim** |
| Sauvignon Blanc (SVB) | 65 | 1.316 | não |
| Syrah (SYH) | 48 | 563 | **sim** |

O datasheet informa 840 cachos de Chardonnay, mas os arquivos da versão fixada têm **839**. O total real é 4.430, e não 4.431.

Nas fotos, os cachos das variedades **tintas estão verdes, antes do pintor**. Isso foi conferido visualmente em amostras e pelo matiz mediano dos pixels de uva das máscaras (H ≈ 60 de 255, verde-amarelado). O estágio fenológico não é anotado no dataset.

### 2.2 Coleta e metadados (`ml/collect_wgisd.py`)

O script baixa somente a versão fixada (fetch raso por commit) e confere:
- o commit;
- a origem;
- as contagens publicadas (300, 242 e 58).

Ele gera `wgisd-metadados.csv`, com uma linha por imagem e os campos `id`, `prefixo`, `variedade`, `tipo`, `variedade_oasis`, `divisao_oficial`, `cachos_anotados`, `possui_mascara`, `largura`/`altura` (e originais), `camera`, `data_captura`, `sha256`, `url_imagem`, `url_anotacao`, `licenca`, `instituicao`, `local`, `municipio`, `uf`, `latitude` e `longitude`.

### 2.3 Preparo (`ml/prepare_dataset.py`)

| Passo | O que é feito | Resultado no WGISD |
| --- | --- | --- |
| Validação das imagens | Decodificação completa. Imagem corrompida é excluída. | 0 excluídas |
| Validação dos rótulos | 5 campos, classe 0, coordenadas normalizadas. Caixas que passam da borda são recortadas; caixas degeneradas são descartadas. | 14 caixas recortadas e 1 descartada (6 × 1 px em `SVB_20180427_151832492`) |
| Duplicatas exatas | SHA-256 | 0 |
| Quase-duplicatas | dHash de 64 bits, até 6 bits de diferença. O grupo fica sempre na mesma divisão. | 1 par (rajada do celular, `CDY_20180427_153126820_BURST000/001`) |
| Qualidade | Brilho, contraste e nitidez (variância do laplaciano) anotados por imagem. Não excluem nada: a variação de foco e de luz faz parte do campo. | Brilho de 89 a 154, nitidez de 343 a 4.809, sem alertas |
| Normalização | RGB 8 bits, JPEG q92, **sem EXIF**, lado maior de 1024 px. A proporção é mantida, então as caixas normalizadas continuam exatas. | — |
| Divisão | Teste = divisão **oficial** do WGISD. Validação = 15% do treino oficial, estratificada por variedade, semente 42. | treino 205 · validação 37 · teste 58 |
| Aumento de dados | **Só durante o treino**, sobre as fotos reais (seção 5). Nenhuma imagem sintética é gravada no dataset. | — |

| Divisão | Imagens | Cachos | CDY | CFR | CSV | SVB | SYH |
| --- | --- | --- | --- | --- | --- | --- | --- |
| treino | 205 | 3.067 | 42 | 47 | 41 | 43 | 32 |
| validação | 37 | 512 | 8 | 8 | 7 | 8 | 6 |
| teste | 58 | 850 | 15 | 10 | 9 | 14 | 10 |

A estrutura gerada, em `ml/datasets/wgisd-cachos/` (fora do git, reproduzível pelos scripts), é esta:

```
images/{train,val,test}/  labels/{train,val,test}/  data.yaml  metadata.csv  preparo.json
```

O relatório e os metadados versionados ficam em `ml/reports/wgisd-cachos/`.

## 3. O que o YOLO detecta e por quê

| Opção | O que os dados oferecem | Decisão |
| --- | --- | --- |
| **Detecção de cachos** | 4.430 caixas manuais em 300 fotos reais | **Implementado**, com a classe `cacho` |
| Detecção de bagas ou frutos | Pontos de bagas (contribuições: 187.374 em 300 imagens), sem caixas | Não implementado. É uma evolução possível para contar bagas. |
| Segmentação de cachos | Máscaras de 2.020 cachos | Não usada no modelo, e sim para medir a análise de cor (seção 6) |
| Identificação da variedade | Um rótulo por **imagem**, de um único vinhedo, com duas datas e duas câmeras. Só 2 das 6 variedades do catálogo. | **Não implementado.** Cada variedade foi fotografada num único dia, então o modelo aprenderia o dia, a luz e a câmera em vez da variedade. E 4 variedades do catálogo ficariam de fora. A variedade continua vindo do cadastro do talhão. |
| Qualidade, deterioração, doenças, danos, regiões problemáticas | Nenhum rótulo, nem no WGISD nem nas APIs | **Não implementado como modelo** (seção 10). A aplicação mantém a análise de cor, calibrada com cachos reais e apresentada como regra. |

## 4. Anotação

- **Caixas ou segmentação:** caixas bastam para localizar cachos e alimentar a análise de cor, e são o formato do YOLO de detecção. A segmentação (YOLO-seg) é uma evolução: as máscaras do WGISD permitiriam medir a cor só nos pixels da uva.
- **Formato:** YOLO, uma linha `classe cx cy largura altura` por objeto, normalizada (0–1), com classes na ordem do `data.yaml`. O WGISD já está nesse formato e foi validado linha a linha.
- **Fluxo do dataset próprio**, implementado e testado (`tests/test_annotation_flow.py`):
  1. `python -m app.cli export-dataset --out ml/datasets/oasis-campo` exporta as fotos que a OASIS gravou (sensores e envios) com **pré-anotações** das detecções em formato YOLO. Exporta também `label_studio/tasks.json` (as pré-anotações como *predictions*), `label_studio/config.xml` (as 12 classes de `ml/dataset.yaml` e uma avaliação geral boa/atenção/crítica) e `metadata.csv`.
  2. A revisão é feita no **Label Studio**. Importe `tasks.json`, use `config.xml` e sirva as imagens com `LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true` e `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT` apontando para `ml/datasets`. Também dá para usar o **CVAT** com importação "Ultralytics YOLO", a partir de `data.yaml` e `images/` + `labels/`. **A revisão das anomalias é feita por um responsável técnico.**
  3. Exporte a revisão em formato YOLO e rode `python ml/import_annotations.py --annotations <pasta>`. O script remapeia as classes **pelo nome** (cada ferramenta numera do seu jeito), valida as caixas e divide os dados por **sensor e dia**, para que fotos quase iguais do mesmo dispositivo não caiam em divisões diferentes.
  4. `python ml/train.py --data ml/datasets/oasis-campo-anotado/data.yaml`, seguido de `ml/export_model.py`.
- **Regras de anotação:** uma caixa justa por cacho visível. Cachos distantes, no fundo, ficam de fora, como no WGISD. Nas anomalias, a caixa cobre o grupo de bagas afetadas. Na dúvida, deixe sem caixa e registre uma observação.

## 5. Treinamento

### 5.1 Versão: YOLO26n (Ultralytics 8.4)

- **Atual:** o YOLO26 é a geração vigente da Ultralytics (pacote 8.4).
- **Cabeça sem NMS (ponta a ponta):** o ONNX exportado já devolve as caixas finais `(1, 300, 6)`. A API não depende de um NMS próprio para este modelo, e a validação do Ultralytics usa essa mesma cabeça.
- **Feito para CPU e borda:** a OASIS roda em servidor sem GPU e tem um gateway de edge. A variante **nano** tem 2,4 milhões de parâmetros e 5,3 GFLOPs (log da exportação).
- **Dataset pequeno:** 205 imagens de treino. Um modelo maior tende a sobreajustar e é mais lento em CPU.

### 5.2 Configuração

| Parâmetro | Valor |
| --- | --- |
| Pesos iniciais | **Nenhum (treino do zero, `yolo26n.yaml`).** Os pesos pré-treinados da Ultralytics ficam nos *releases* do GitHub, bloqueados pela rede do ambiente de treino. Fora dele, `python ml/train.py` faz transfer learning a partir de `yolo26n.pt`, o caminho recomendado para datasets pequenos. |
| Épocas | Até 150, com parada antecipada após 40 sem melhora na validação |
| Imagem | 640 × 640 (letterbox) |
| Lote | 16, com `nbs` 16: um passo do otimizador por lote, 13 por época. O padrão `nbs` 64 daria só cerca de 3. |
| Otimizador | Automático do Ultralytics → AdamW (lr 0,002, momento 0,9), LR cosseno, 3 épocas de aquecimento, weight decay 0,0005 |
| Aumentos | HSV (h 0,015 · s 0,5 · v 0,4), rotação ±5°, translação 10%, escala ±50%, espelhamento horizontal 50%, mosaico (desligado nas 10 últimas épocas). Sem espelhamento vertical, porque cachos pendem para baixo, e sem mixup. |
| Reprodutibilidade | Semente 42, `deterministic=True`, cache em disco, versão do dataset fixada |
| Hardware | CPU (4 núcleos), cerca de 65 s por época |

### 5.3 Validação e prevenção de sobreajuste

- **Três conjuntos separados.** O treino ajusta os pesos. A **validação** escolhe a melhor época (aptidão = 0,1·mAP50 + 0,9·mAP50-95), dispara a parada antecipada e escolhe o **limiar de confiança** (maior F1). O **teste** é a divisão oficial do WGISD, não é usado em nenhuma decisão e só serve para relatar as métricas.
- As quase-duplicatas ficam na mesma divisão.
- Aumentos de dados e um modelo pequeno ajudam a conter o sobreajuste.
- As métricas da ficha são medidas com o **mesmo código de inferência da API** (ONNX, `letterbox`, `decode_yolo_output`). O Ultralytics também é rodado no teste, para comparação e gráficos.

### 5.4 Resultados

O treino rodou as 150 épocas (2,7 h em CPU). A melhor época pela validação foi a **127**. O limiar de confiança escolhido na validação, pelo maior F1, foi **0,29**.

**Métricas com o pipeline de inferência da API** (ONNX Runtime, IoU 0,5 para precisão, recall e F1):

| Conjunto | Imagens | Cachos | Precisão | Recall | F1 | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Validação | 37 | 512 | 76,7% | 74,0% | 75,3% | 0,807 | 0,473 |
| **Teste (oficial do WGISD)** | 58 | 850 | **78,6%** | **73,9%** | **76,2%** | **0,830** | **0,481** |

**Matriz de confusão no teste** (uma classe, cacho × fundo, limiar 0,29):

| | Cacho real | Fundo (sem cacho anotado) |
| --- | --- | --- |
| **Previsto como cacho** | 628 verdadeiros positivos | 171 falsos positivos |
| **Não previsto** | 222 falsos negativos (cachos perdidos) | — |

Parte dos "falsos positivos" pode ser de cachos distantes no fundo: o WGISD não anota esses cachos, conforme o datasheet. Os gráficos do Ultralytics (matriz de confusão e curvas P, R, F1 e PR) estão em `ml/reports/oasis-cachos/`.

**Teste por variedade:**

| Variedade | Imagens | Cachos | Precisão | Recall | F1 | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cabernet Franc | 10 | 159 | 79,5% | 75,5% | 77,4% | 0,850 | 0,502 |
| Cabernet Sauvignon | 9 | 111 | 68,0% | 61,3% | 64,5% | 0,681 | 0,323 |
| Chardonnay | 15 | 180 | 80,8% | 79,4% | 80,1% | 0,868 | 0,558 |
| Sauvignon Blanc | 14 | 283 | 80,1% | 75,3% | 77,6% | 0,845 | 0,491 |
| Syrah | 10 | 117 | 80,0% | 71,8% | 75,7% | 0,837 | 0,483 |

Cabernet Sauvignon é a variedade mais difícil para o modelo (mAP50 de 0,68). As demais ficam entre 0,84 e 0,87.

**Comparações e verificações:**
- **Ultralytics, com os pesos PyTorch, no mesmo teste:** precisão 84,9%, recall 74,9%, F1 79,6%, mAP50 0,852 e mAP50-95 0,494. A diferença para o ONNX vem do pré-processamento: o Ultralytics usa letterbox retangular, e a API, entrada quadrada fixa de 640 × 640.
- **Paridade entre ONNX e PyTorch:** 86,2% das caixas do PyTorch têm par no ONNX, com IoU médio de 0,912.
- **Tempo de inferência na API (CPU, 4 núcleos):** mediana de 59 ms e p95 de 73 ms por foto.
- **Sobreajuste:** o desempenho no teste (0,830) ficou próximo do da validação (0,807). Isso indica que o modelo generaliza para fotos novas **do mesmo vinhedo**. A generalização para o Vale do São Francisco ainda precisa ser medida (seção 10).
- **Onde está o modelo:** `models/oasis-cachos.onnx` (9,8 MB) e a ficha `models/oasis-cachos.json`. O relatório completo está em `ml/reports/oasis-cachos/avaliacao.json`.


## 6. Análise de cor medida com dados reais

A análise de cor já existia na plataforma. Até aqui ela tinha sido calibrada só com imagens sintéticas. `ml/calibrate_color.py` a mediu com o WGISD, e o relatório está em `ml/reports/calibracao_cor.json`.

| Medida (fotos e cachos reais do WGISD) | Resultado |
| --- | --- |
| Análise de cor **sozinha**: a caixa do cacho coincide com algum cacho anotado (IoU ≥ 0,5) | **0%** das fotos (IoU mediano de 0,04) |
| Análise de cor sozinha: fotos com alguma anomalia apontada (o WGISD não tem defeitos anotados) | **~90%** |
| Regras originais **dentro das caixas reais**: cachos com "manchas/podridão" | 75% (teste: 76,6%) |
| Regras com limiares calibrados (meta: no máximo 2% nos cachos de calibração) | 2,4% no treino, 1,4% na validação, **1,4% no teste** |
| Máscara de "cor de baga" vs. pixels reais de uva (máscaras do WGISD) | Tintas: 0,7% reconhecidos (cachos verdes). Brancas: 44,5%. |

Conclusões aplicadas:
1. A localização dos cachos passou para o modelo YOLO.
2. A análise de cor só roda **dentro** dos cachos detectados, com os limiares calibrados (manchas a partir de 31% da área do cacho, podridão a partir de 63%, mofo 5%, bagas fora de cor 10%). Esses limiares vão na ficha do modelo.
3. A interface apresenta essa análise como **regra**, não como modelo.

A sensibilidade da regra, isto é, se ela acerta defeitos reais, **não pôde ser medida**: não existem fotos anotadas de cachos com defeito. Em uvas tintas ainda verdes, a maturação fica como "não informada" quando a cor não permite estimar.

## 7. Integração na aplicação (inferência)

### 7.1 Arquitetura

```
Usuário ou sensor ──foto──▶ Frontend (validação, prévia) ──multipart──▶ POST /api/v1/ingest
   ▲                                                                         │ valida tamanho/formato, decodifica,
   │                                                                         │ remove EXIF, tempo limite (30 s)
   │                                                                         ▼
   │                                                       OnnxYoloDetector (ONNX Runtime, CPU)
   │                                                         letterbox 640 → YOLO26n → cachos
   │                                                         + análise de cor em cada cacho (regra)
   │                                                                         ▼
   │                                                       classifier.analyze → qualidade, maturação,
   │                                                       classificação, observações
   │                                                                         ▼
   └──────── resultado (previsão + origem de cada parte) ◀── banco (readings + detections) e imagem
```

**Decisão: o modelo roda dentro da API, não num serviço de inferência separado.**
- Na escala atual (uma propriedade, poucas fotos por hora), a inferência em CPU leva milissegundos (seção 5).
- Rodar no processo evita mais um serviço para implantar e proteger, um salto de rede e mais um ponto de falha.
- O ONNX Runtime mantém a API leve: não há PyTorch, que soma cerca de 3 GB, na imagem da API.
- A separação fica no código e nos artefatos:
  - o treinamento está em `ml/`, com PyTorch;
  - a inferência está em `app/services/detector.py`, com ONNX Runtime;
  - o contrato entre os dois é o par `.onnx` + `.json`;
  - o frontend não conhece o modelo, só o JSON da leitura.
- Se um dia houver muitos sensores ou GPU, um serviço separado entra como outra implementação da interface `Detector`, sem mudar rotas nem frontend.

### 7.2 Troca e atualização do modelo

1. Treine e rode `ml/export_model.py --run <pasta> --version 1.1.0`, que gera `models/oasis-cachos.onnx` e `.json`.
2. A API só carrega o par se o SHA-256 do `.onnx` conferir com a ficha e se as classes forem compatíveis com a tarefa.
3. Modelos genéricos, como os do COCO, são recusados.
4. Com `OASIS_DETECTOR=auto`, qualquer falha volta para a análise de cor e fica registrada no log. Com `yolo`, a API não inicia.
5. Cada leitura grava `model_version` ("YOLO cachos · OASIS Cachos 1.0.0 + OASIS análise de cor v1"). Isso permite saber qual modelo produziu cada resultado do histórico.

### 7.3 Endpoints

**`POST /api/v1/ingest`** recebe `multipart/form-data`. A autenticação é `X-Device-Token` (sensor) ou `Authorization: Bearer` (usuário).

| Campo | Tipo | Obrigatório |
| --- | --- | --- |
| `sensor_id` | texto (até 20) | sim. Define o talhão e a variedade. |
| `image` | arquivo JPEG/PNG/WEBP, até `MAX_UPLOAD_MB` (8 MB) | sim |
| `captured_at` | data e hora ISO 8601 | não. O padrão é o momento do envio. |
| `battery`, `signal`, `firmware`, `temperature_c`, `humidity_pct` | telemetria do dispositivo | não |

Respostas de erro:

| Código | Situação |
| --- | --- |
| 413 | arquivo grande demais |
| 415 | não é uma imagem válida |
| 409 | sensor inativo ou leitura duplicada |
| 422 | data no futuro |
| 504 | tempo limite excedido |
| 503 | falha do detector; nada é gravado |

Resposta **real** (201) ao enviar a foto de teste `tests/fixtures/wgisd/SYH_2017-04-27_1318.jpg` para um sensor de Syrah. O modelo encontrou 7 dos 8 cachos anotados. A lista de detecções foi abreviada aqui:

```json
{
  "id": "OA-00001",
  "sensorId": "S-001",
  "capturedAt": "2026-11-05T12:30:00Z",
  "varietyId": "syrah",
  "quality": "boa",
  "confidence": 0.848,
  "maturation": "nao_informada",
  "visualCondition": "Boa",
  "classification": "APROVADA",
  "observations": "Nenhum sinal visual de dano foi apontado pela análise da imagem.",
  "detections": [
    {
      "kind": "cacho",
      "label": "syrah",
      "confidence": 0.848,
      "box": [
        0.0731,
        0.053,
        0.0723,
        0.2427
      ]
    },
    {
      "kind": "cacho",
      "label": "syrah",
      "confidence": 0.801,
      "box": [
        0.3585,
        0.5914,
        0.0923,
        0.2253
      ]
    },
    …
  ],
  "clustersDetected": 7,
  "imageUrl": "/api/v1/readings/OA-00001/image",
  "source": "upload",
  "modelVersion": "YOLO cachos · OASIS Cachos 1.0.0 + OASIS análise de cor v1",
  "processingMs": 141
}
```


**`GET /api/v1/analysis/model`** (autenticado) devolve:
- o detector em uso;
- a ficha (`card`: arquitetura, dados de treino, métricas de teste e validação, licença);
- `components` (o que decide cada parte: modelo, cadastro ou regra);
- as classes com a origem (`modelo` ou `analise_cor`);
- as limitações;
- os limites do envio.

### 7.4 Boas práticas aplicadas

- Configuração por variáveis de ambiente: `OASIS_DETECTOR`, `OASIS_MODEL_PATH`, `OASIS_MODEL_CONFIDENCE`, `OASIS_ANALYSIS_TIMEOUT_SECONDS`, `MAX_UPLOAD_MB`. Não há URLs sensíveis nem credenciais no código; as da Embrapa ficam no `.env`, fora do git.
- Validação da imagem no navegador (formato, tamanho e decodificação) e no servidor (decodificação real, limite de 40 MP contra imagens-bomba, remoção de EXIF e GPS).
- Tratamento de erros com mensagens em português e logs:
  - o modelo carregado ou o motivo da recusa;
  - o tempo limite e as falhas do detector.
- Testes cobrem:
  - o pré e o pós-processamento do ONNX;
  - a validação da ficha;
  - a calibração;
  - o pipeline de dados;
  - o fluxo de anotação;
  - o modelo publicado com uma foto real do teste do WGISD.

## 8. O que o usuário vê

Na página **Análises → Enviar foto** e na página de cada leitura:

- **Cachos identificados:** caixas sobre a foto, quantidade e confiança do modelo por cacho, com o selo "Modelo YOLO".
- **Variedade:** "Variedade (cadastro do talhão)", porque o modelo não identifica variedades. Aparecem também as características da variedade no catálogo.
- **Possíveis problemas:** manchas, mofo e bagas fora de cor, com o selo "Análise de cor". O índice da regra é mostrado como índice, e não como probabilidade.
- **Avaliação visual:** qualidade (Boa, Atenção ou Necessita atenção), condição visual e maturação, ou "não informada" quando a cor não permite estimar.
- **Recomendações:** respostas da Embrapa (RespondeAgro) para cada anomalia apontada, quando as credenciais da AgroAPI estão configuradas.
- **Transparência:** "Previsão do modelo" junto da confiança. A ficha do modelo traz dados, métricas e limitações, e o aviso de que o resultado não substitui a avaliação de um responsável técnico.

## 9. Como rodar localmente

```bash
# API (o modelo publicado já vem em models/)
cd Vinicola-back
python -m venv .venv && source .venv/bin/activate        # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements-dev.txt                       # inclui onnxruntime
cp .env.example .env                                      # num .env antigo: apague OASIS_MODEL_CONFIDENCE=0.35
python -m uvicorn app.main:app --reload                   # o log mostra "Detector em uso: YOLO cachos · OASIS Cachos …"

# teste rápido de uma foto, com o mesmo código da API
python ml/predict.py tests/fixtures/wgisd/SYH_2017-04-27_1318.jpg --variety syrah

# frontend
cd ../Vinicola-Front && npm install && npm run dev        # http://localhost:5173 → Análises → Enviar foto

# refazer o modelo (opcional): coleta → preparo → treino → exportação
pip install -r requirements-ml.txt
python ml/collect_wgisd.py && python ml/prepare_dataset.py && python ml/calibrate_color.py
python ml/train.py && python ml/export_model.py --run ml/runs/oasis-cachos
```

## 10. O que falta para avaliar qualidade de forma confiável

**O que existe hoje:**
- cachos localizados por um modelo treinado com fotos reais;
- 4.430 caixas, 2.020 máscaras e pontos de bagas no WGISD;
- texto técnico da Embrapa (RespondeAgro);
- a análise de cor calibrada para disparar pouco em cachos sem defeito anotado.

**O que falta:**
- fotos de cachos **com defeitos reais** (podridões, míldio, oídio, rachaduras, desidratação, queimadura) anotadas por um responsável técnico;
- fotos das variedades do catálogo além de Cabernet Sauvignon e Syrah;
- fotos do **Vale do São Francisco**, com a luz, as cultivares e o manejo locais, tiradas pelas câmeras da OASIS (ESP32-CAM), em todas as fases (do cacho verde à colheita);
- rótulos de estágio fenológico.

**Estratégia recomendada:**
1. Instalar as câmeras e coletar continuamente. A OASIS já grava cada foto com sensor, talhão, variedade e data.
2. A cada período, rodar `export-dataset`. Um agrônomo revisa as caixas no Label Studio, marca as anomalias e dá a avaliação geral da imagem.
3. Meta inicial, igual à do `ml/README.md`: 300 ou mais imagens por variedade e 150 ou mais exemplos por anomalia, distribuídos por talhão, data e horário.
4. Dividir por sensor e dia (`import_annotations.py`) e manter um **teste fixo** de talhões e datas que nunca entram no treino.
5. Treinar o modelo completo (`ml/dataset.yaml`, 12 classes) por transfer learning a partir do modelo de cachos ou de `yolo26n.pt`. Comparar com o atual no mesmo teste e publicar só se melhorar; o par `.onnx` + `.json` troca o modelo sem mudar código.
6. Complementos:
   - a **PDDB** (Redape) já foi conferida: tem 83 fotos e 2.330 recortes de videira, rotulados por fitopatologistas, mas quase só de folhas (6 fotos com cachos), e a classe sadia tem 2 fotos. Ela pode embasar um **módulo futuro de doenças em folhas**, desde que se fotografem folhas sadias e doentes nas mesmas condições, para o modelo não aprender o fundo;
   - avaliar um modelo de **segmentação**, para medir a cor só nos pixels das bagas.

> O resultado é uma previsão computacional a partir da imagem. Ele não é um diagnóstico fitossanitário e não substitui a avaliação de um responsável técnico.
