# Modelo YOLO da OASIS

O detector localiza os **cachos** (uma classe por variedade) e as **anomalias visuais** nas imagens enviadas pelos sensores. A API converte as detecções em variedade, qualidade, condição visual, maturação e classificação (`app/services/classifier.py`).

## Classes

| ID | Classe | Tipo | Efeito na classificação |
| --- | --- | --- | --- |
| 0 | `cabernet_sauvignon` | cacho | identifica a variedade |
| 1 | `syrah` | cacho | identifica a variedade |
| 2 | `tempranillo` | cacho | identifica a variedade |
| 3 | `touriga_nacional` | cacho | identifica a variedade |
| 4 | `chenin_blanc` | cacho | identifica a variedade |
| 5 | `moscato_canelli` | cacho | identifica a variedade |
| 6 | `maturacao_desigual` | anomalia leve | **Atenção** |
| 7 | `baga_irregular` | anomalia leve | **Atenção** |
| 8 | `mancha_leve` | anomalia leve | **Atenção** |
| 9 | `podridao` | anomalia grave | **Necessita atenção** |
| 10 | `baga_murcha` | anomalia grave | **Necessita atenção** |
| 11 | `lesao` | anomalia grave | **Necessita atenção** |

Sem anomalias com confiança ≥ 0,5, a leitura é classificada como **Boa** (APROVADA).

## 1. Coletar imagens

- Use as próprias câmeras OASIS (mesma ótica e mesmo enquadramento de produção).
- Varie horário, iluminação, estágio fenológico e talhão.
- Meta inicial: 300 ou mais imagens por variedade e 150 ou mais exemplos de cada anomalia.

## 2. Anotar

Use CVAT, Label Studio ou Roboflow com exportação no formato **YOLO**:

- desenhe uma caixa em cada cacho visível com a classe da variedade;
- desenhe caixas justas nas regiões com anomalias (grupos de bagas afetadas);
- a validação das anomalias deve ser feita por um responsável técnico (agrônomo).

## 3. Organizar

```
ml/datasets/oasis-grapes/
├── images/{train,val,test}/   # 70% / 20% / 10%, separados por talhão e data
└── labels/{train,val,test}/
```

## 4. Treinar

```bash
pip install -r requirements-ml.txt
python ml/train.py --epochs 100 --model yolov8n.pt   # yolov8s.pt para mais precisão
```

Acompanhe **mAP50** e **mAP50-95** por classe. O `yolov8n` roda em CPU; para rodar no próprio edge, exporte para ONNX ou TFLite (`yolo export model=models/oasis-grapes.pt format=onnx`).

## 5. Publicar na API

```bash
OASIS_DETECTOR=yolo OASIS_MODEL_PATH=models/oasis-grapes.pt uvicorn app.main:app
python ml/predict.py exemplo.jpg --variety syrah   # teste rápido
```

O frontend não muda nada: o contrato de `detections` (caixas normalizadas `x, y, largura, altura`) é o mesmo do detector simulado.

> A classificação é baseada na análise computacional da imagem e não substitui a avaliação agronômica profissional.
