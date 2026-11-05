"""
Fontes de dados usadas no treinamento — somente dados reais e oficiais.

Embrapa WGISD (Wine Grape Instance Segmentation Dataset), da Embrapa Informática Agropecuária:
300 fotografias de campo com 4.431 cachos anotados por caixas (formato YOLO) e 2.020 com máscaras.
As informações abaixo foram conferidas no próprio repositório (README/datasheet e EXIF das imagens
originais) na versão fixada em WGISD_COMMIT.
"""

WGISD_REPO = "https://github.com/thsant/wgisd"
# Versão usada no treinamento (último commit do repositório, 23/08/2022). Fixar a versão torna o dataset reproduzível.
WGISD_COMMIT = "6910edc5ae3aae8c20062941b1641821f0c30127"
# O GitHub declara CC BY-NC 4.0; o registro oficial no Zenodo (DOI 10.5281/zenodo.3361736), CC BY-NC-SA 4.0.
# Vale a mais restritiva: uso não comercial, com atribuição e compartilhamento pela mesma licença.
WGISD_LICENSE = "CC BY-NC-SA 4.0"
WGISD_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
WGISD_DOI = "10.5281/zenodo.3361736"
WGISD_INSTITUTION = "Embrapa Informática Agropecuária"
WGISD_AUTHORS = "T. T. Santos, L. L. de Souza, A. A. Santos e S. Avila"
WGISD_PAPER = "Santos et al., Grape detection, segmentation and tracking using deep neural networks and three-dimensional association (arXiv:1907.11819)"
WGISD_PAPER_URL = "https://arxiv.org/abs/1907.11819"
WGISD_SITE = {
    "local": "Vinícola Guaspari",
    "municipio": "Espírito Santo do Pinhal",
    "uf": "SP",
    "latitude": -22.181018,
    "longitude": -46.741618,
}
# Prefixo do arquivo → variedade. `oasis_id` liga a variedade ao catálogo da OASIS (app/catalog.py), quando existe.
WGISD_VARIETIES = {
    "CDY": {"name": "Chardonnay", "type": "Branca", "oasis_id": None},
    "CFR": {"name": "Cabernet Franc", "type": "Tinta", "oasis_id": None},
    "CSV": {"name": "Cabernet Sauvignon", "type": "Tinta", "oasis_id": "cabernet-sauvignon"},
    "SVB": {"name": "Sauvignon Blanc", "type": "Branca", "oasis_id": None},
    "SYH": {"name": "Syrah", "type": "Tinta", "oasis_id": "syrah"},
}
# Totais publicados no datasheet do WGISD (conferidos pelo preparo do dataset).
WGISD_EXPECTED = {"images": 300, "train_images": 242, "test_images": 58}


def wgisd_file_url(relative_path: str) -> str:
    """Endereço permanente de um arquivo do WGISD na versão fixada."""
    return f"{WGISD_REPO}/blob/{WGISD_COMMIT}/{relative_path}"
