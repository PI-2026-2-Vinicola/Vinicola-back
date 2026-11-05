"""
Ficha do modelo (model card): o arquivo JSON publicado ao lado dos pesos ONNX.

Ela é gerada por ml/export_model.py ao final do treinamento e descreve o que o modelo detecta, com
que dados foi treinado, as métricas medidas no conjunto de teste e as limitações. A API só usa um
modelo cuja ficha confira com o arquivo de pesos (SHA-256): trocar o modelo é publicar o par
`<nome>.onnx` + `<nome>.json` gerado pelo pipeline, sem mudar código.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog import GENERIC_CLUSTER_CLASS, MODEL_CLASSES, MODEL_VARIETY_NAMES

# deteccao_cachos: o modelo localiza cachos (classe "cacho"); variedade e anomalias vêm de outras fontes.
# deteccao_completa: o modelo também identifica variedade e anomalias (classes de app.catalog.MODEL_CLASSES).
# classificacao_variedade: o modelo recebe o recorte de um cacho e diz a variedade.
ModelTask = Literal["deteccao_cachos", "deteccao_completa", "classificacao_variedade"]


class SplitMetrics(BaseModel):
    split: str
    images: int
    instances: int
    precision: float
    recall: float
    f1: float
    map50: float
    map50_95: float
    confidence: float = Field(description="limiar de confiança em que precisão, recall e F1 foram medidos")


class ClassificationMetrics(BaseModel):
    split: str
    images: int  # fotos de onde vieram os recortes
    samples: int  # recortes (um por cacho)
    accuracy: float
    balanced_accuracy: float
    per_class: dict[str, dict[str, float]]
    photo_accuracy: float | None = Field(default=None, description="acerto da variedade por foto, combinando os cachos detectados")


class DatasetInfo(BaseModel):
    name: str
    source_url: str
    version: str
    license: str
    license_url: str | None = None
    institution: str
    description: str
    images: dict[str, int]
    instances: dict[str, int]
    varieties: list[str]
    capture: str


class ColorCalibration(BaseModel):
    """Limiares da análise de cor aplicada dentro de cada cacho detectado (ml/calibrate_color.py)."""

    brown_mild: float
    brown_severe: float
    mold: float
    off_color: float
    target_rate: float = Field(description="fração máxima de cachos reais sem defeito anotado em que cada regra dispara")
    report: str


class ModelCard(BaseModel):
    name: str
    version: str
    task: ModelTask
    architecture: str
    framework: str
    input_size: int
    classes: list[str]
    confidence_threshold: float
    iou_threshold: float
    trained_at: datetime
    training: dict
    metrics: dict[str, SplitMetrics | ClassificationMetrics]
    dataset: DatasetInfo
    sha256: str
    license: str
    limitations: list[str]
    pretrained: bool = False
    end2end: bool | None = None  # saída sem NMS (YOLO26); None = deduzido do formato da saída
    inference_ms_cpu: float | None = None
    color_thresholds: ColorCalibration | None = None


class ModelCardError(ValueError):
    """Ficha ausente, inválida ou que não corresponde ao arquivo de pesos."""


def card_path(model_path: Path) -> Path:
    return model_path.with_suffix(".json")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_model_card(model_path: Path) -> ModelCard:
    path = card_path(model_path)
    if not path.is_file():
        raise ModelCardError(f"Ficha do modelo não encontrada: {path.name} (gerada por ml/export_model.py junto com o .onnx).")
    try:
        card = ModelCard.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        raise ModelCardError(f"Ficha do modelo inválida ({path.name}): {exc}") from exc
    digest = file_sha256(model_path)
    if digest != card.sha256:
        raise ModelCardError(f"O arquivo {model_path.name} não corresponde à ficha {path.name} (SHA-256 diferente): publique o par gerado pelo mesmo treinamento.")
    allowed = {"deteccao_cachos": [GENERIC_CLUSTER_CLASS], "deteccao_completa": MODEL_CLASSES, "classificacao_variedade": list(MODEL_VARIETY_NAMES)}[card.task]
    unknown = [c for c in card.classes if c not in allowed]
    if not card.classes or unknown:
        raise ModelCardError(f"Classes do modelo incompatíveis com a tarefa {card.task}: {unknown or 'nenhuma classe'}.")
    return card
