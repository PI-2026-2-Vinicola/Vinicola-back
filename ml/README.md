# Visão computacional da OASIS — dados, treinamento e modelo publicado

O pipeline tem **três etapas separadas**. As duas primeiras rodam aqui em `ml/`, com PyTorch e Ultralytics. A terceira roda na API, com ONNX Runtime e sem PyTorch.

```
ETAPA 1 · COLETA            ETAPA 2 · TREINAMENTO                         ETAPA 3 · INFERÊNCIA (API)
Embrapa WGISD (fotos   →    preparo → YOLO26n → avaliação → ONNX    →     foto do usuário/sensor → /ingest →
reais + caixas)             (dataset)  (train)   (val/teste)  + ficha      YOLO (cachos) → análise de cor → resultado
collect_wgisd.py            prepare_dataset.py · train.py · export_model.py   app/services/detector.py
```

A documentação completa está em [`docs/yolo-embrapa.md`](../docs/yolo-embrapa.md). Ela cobre as fontes da Embrapa, o dataset, a escolha das classes, a anotação, o treinamento, as métricas, a integração e as limitações.

## O que o modelo publicado faz

| | |
| --- | --- |
| Modelo | `models/oasis-cachos.onnx` + ficha `models/oasis-cachos.json` (YOLO26n, entrada 640 × 640) |
| Detecta | **cachos de uva** (classe única `cacho`) |
| Dados | 300 fotos reais de campo do **Embrapa WGISD** (Embrapa Informática Agropecuária), 4.430 cachos anotados manualmente |
| Não faz | não identifica a variedade, não diagnostica doença ou qualidade e não estima maturação. A variedade vem do cadastro do talhão. Anomalias e maturação vêm da análise de cor dentro de cada cacho detectado. |
| Métricas (teste oficial do WGISD, código da API) | precisão 78,6% · recall 73,9% · F1 76,2% · **mAP50 0,830** · mAP50-95 0,481 · cerca de 59 ms por foto em CPU (`ml/reports/oasis-cachos/avaliacao.json`) |
| Licença | uso **não comercial**, com atribuição. Os pesos derivam do WGISD, que é CC BY-NC 4.0. |

## Ambiente

```bash
python -m venv .venv-ml && source .venv-ml/bin/activate   # Windows (Git Bash): source .venv-ml/Scripts/activate
# opcional, em máquina sem GPU (download bem menor):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt
```

## Etapa 1 — coleta e preparo

```bash
python ml/collect_wgisd.py              # baixa o WGISD na versão fixada (≈2,7 GB) e extrai os metadados
python ml/prepare_dataset.py            # valida, deduplica, mede qualidade, redimensiona e divide
python ml/calibrate_color.py            # calibra a análise de cor com os cachos reais (ml/reports/calibracao_cor.json)
```

Resultado em `ml/datasets/wgisd-cachos/`, uma pasta ignorada pelo git:

```
images/{train,val,test}/*.jpg     205 / 37 / 58 fotos (teste = divisão oficial do WGISD)
labels/{train,val,test}/*.txt     formato YOLO: classe cx cy largura altura (normalizados)
data.yaml                         classes e caminhos para o Ultralytics
metadata.csv                      origem, variedade, câmera, data, URL, licença, hash, qualidade… por imagem
preparo.json                      relatório: contagens, exclusões, caixas recortadas, quase-duplicatas
```

## Etapa 2 — treinamento, avaliação e publicação

```bash
python ml/train.py                                            # transfer learning a partir de yolo26n.pt (recomendado)
python ml/train.py --model yolo26n.yaml --epochs 150 --nbs 16 # do zero (como o modelo publicado)
python ml/export_model.py --run ml/runs/oasis-cachos          # ONNX + ficha + relatórios em ml/reports/oasis-cachos/
```

O `export_model.py` faz quatro coisas:
- escolhe o limiar de confiança na **validação** (maior F1);
- mede o **teste** com o pipeline de inferência da API;
- roda a validação do Ultralytics, que gera a matriz de confusão e as curvas P/R/F1/PR;
- confere a paridade entre o ONNX e o PyTorch.

Para trocar o modelo em uso, basta publicar um novo par `.onnx` + `.json`, sem mudar código. A API recusa:
- um par cujo SHA-256 não confere;
- classes incompatíveis;
- modelos genéricos, como os do COCO.

```bash
python ml/predict.py foto.jpg --variety syrah           # mesmo resultado que a API produziria
python ml/predict.py foto.jpg --variety syrah --color   # só a análise de cor, para comparação
```

## Dataset próprio (qualidade, doenças e variedades do Vale)

O WGISD não tem rótulos de qualidade, doença ou dano. Também só cobre duas das seis variedades do catálogo (Cabernet Sauvignon e Syrah). Para treinar o modelo completo de `ml/dataset.yaml` (12 classes), o fluxo é este:

```bash
python -m app.cli export-dataset --out ml/datasets/oasis-campo   # fotos da OASIS + pré-anotações + tarefas do Label Studio
# revisão no Label Studio (label_studio/config.xml) ou no CVAT ("Ultralytics YOLO") por um responsável técnico
python ml/import_annotations.py --annotations <exportação YOLO da ferramenta>
python ml/train.py --data ml/datasets/oasis-campo-anotado/data.yaml
python ml/export_model.py --run ml/runs/oasis-cachos --name oasis-completo
```

As classes do modelo completo ficam em `ml/dataset.yaml`, na ordem de `app/catalog.py → MODEL_CLASSES`. O `import_annotations.py` remapeia a numeração de cada ferramenta pelo **nome** da classe. A divisão é feita por sensor e dia, para que fotos quase iguais não caiam em treino e teste ao mesmo tempo.

> O resultado é uma previsão computacional a partir da imagem. Ele não é um diagnóstico fitossanitário e não substitui a avaliação de um responsável técnico.
