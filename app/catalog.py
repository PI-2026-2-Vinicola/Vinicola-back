"""Catálogo de domínio compartilhado com o frontend (mesmos identificadores)."""

QUALITIES = ("boa", "atencao", "critica")
QUALITY_LABEL = {"boa": "Boa", "atencao": "Atenção", "critica": "Necessita atenção"}
CLASSIFICATION_BY_QUALITY = {"boa": "APROVADA", "atencao": "EM OBSERVAÇÃO", "critica": "REVISÃO NECESSÁRIA"}
MATURATIONS = ("desenvolvimento", "pintor", "maturacao", "adequada", "sobrematuracao")
SENSOR_STATUSES = ("online", "atencao", "offline")
ROLES = ("admin", "gestor", "operador")
STAGES = ("recebida", "processando", "analisando", "concluida")

VARIETIES = [
    {"id": "cabernet-sauvignon", "name": "Cabernet Sauvignon", "type": "Tinta", "color": "Negro-azulada", "maturation_cycle": "Tardia (ciclo longo)", "origin": "Bordeaux, França"},
    {"id": "syrah", "name": "Syrah", "type": "Tinta", "color": "Preto-violácea", "maturation_cycle": "Média", "origin": "Vale do Rhône, França"},
    {"id": "tempranillo", "name": "Tempranillo", "type": "Tinta", "color": "Negro-azulada com reflexos rubi", "maturation_cycle": "Precoce", "origin": "Rioja e Ribera del Duero, Espanha"},
    {"id": "touriga-nacional", "name": "Touriga Nacional", "type": "Tinta", "color": "Azul-escura", "maturation_cycle": "Média", "origin": "Douro e Dão, Portugal"},
    {"id": "chenin-blanc", "name": "Chenin Blanc", "type": "Branca", "color": "Verde-amarelada", "maturation_cycle": "Média a tardia", "origin": "Vale do Loire, França"},
    {"id": "moscato-canelli", "name": "Moscato Canelli", "type": "Branca", "color": "Amarelo-dourada", "maturation_cycle": "Precoce", "origin": "Piemonte, Itália"},
]
VARIETY_IDS = tuple(v["id"] for v in VARIETIES)


def yolo_class(variety_id: str) -> str:
    """Nome da classe no dataset YOLO (snake_case)."""
    return variety_id.replace("-", "_")


def variety_from_class(label: str) -> str | None:
    vid = label.replace("_", "-")
    return vid if vid in VARIETY_IDS else None


# Classes de anomalia do modelo e o texto apresentado ao usuário.
MILD_ANOMALIES = ("maturacao_desigual", "baga_irregular", "mancha_leve")
SEVERE_ANOMALIES = ("podridao", "baga_murcha", "lesao")
ANOMALY_TEXT = {
    "maturacao_desigual": ("Maturação desigual", "Coloração irregular em parte das bagas — acompanhar a evolução da maturação no talhão."),
    "baga_irregular": ("Bagas irregulares", "Presença de bagas menores que o padrão (possível desavinho). Recomenda-se monitorar nas próximas leituras."),
    "mancha_leve": ("Manchas leves", "Pequenas manchas superficiais em algumas bagas. Situação a ser acompanhada."),
    "podridao": ("Sinais de podridão", "Bagas com aspecto visual compatível com podridão. Recomenda-se inspeção em campo por um responsável técnico."),
    "baga_murcha": ("Bagas desidratadas", "Bagas murchas/desidratadas em região do cacho. Verificar irrigação e exposição solar."),
    "lesao": ("Lesões visíveis", "Manchas escuras e rachaduras em várias bagas — possível doença fúngica ou dano mecânico. Avaliação agronômica recomendada."),
}
GOOD_OBSERVATIONS = (
    "Cacho uniforme, coloração compatível com a variedade e sem sinais visuais de dano.",
    "Bagas íntegras e maturação homogênea no cacho.",
    "Características visuais dentro dos parâmetros esperados para a variedade.",
    "Cacho bem formado, bagas túrgidas e sem manchas aparentes.",
)

# Todas as classes do modelo YOLO, na ordem do dataset (ml/dataset.yaml).
MODEL_CLASSES = [yolo_class(v) for v in VARIETY_IDS] + list(MILD_ANOMALIES) + list(SEVERE_ANOMALIES)
