"""Dataset próprio: exportação das fotos com pré-anotações e importação das anotações revisadas."""

import json
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from app.annotation_export import export_dataset
from app.catalog import MODEL_CLASSES
from app.database import SessionLocal
from ml import import_annotations

from .conftest import API
from .images import grape_cluster, jpeg


def _ingest(client, token, sensor_id, at, **kw):
    res = client.post(f"{API}/ingest", data={"sensor_id": sensor_id, "captured_at": at}, files={"image": ("c.jpg", jpeg(grape_cluster(**kw)), "image/jpeg")}, headers={"X-Device-Token": token})
    assert res.status_code == 201, res.text
    return res.json()


def test_export_then_import_reviewed_annotations(client, admin_headers, tmp_path):
    body = {"id": "AN-01", "name": "Câmera Anotação", "block": "Talhão A", "location": "Fileira 1", "varietyId": "syrah", "device": "ESP32-CAM", "captureIntervalMin": 60}
    res = client.post(f"{API}/sensors", json=body, headers=admin_headers)
    assert res.status_code == 201, res.text
    token = res.json()["deviceToken"]
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    codes = [_ingest(client, token, "AN-01", (base + timedelta(hours=h)).isoformat(), rot=0.3 if h == 1 else 0.0, seed=h)["id"] for h in range(3)]

    out = tmp_path / "oasis-campo"
    with SessionLocal() as db:
        result = export_dataset(db, out, sensor_id="AN-01")
    assert result["images"] == 3 and result["missing_files"] == 0 and result["pre_annotations"] >= 3
    for code in codes:
        assert (out / "images" / "train" / f"{code}.jpg").exists()
        first = (out / "labels" / "train" / f"{code}.txt").read_text().splitlines()[0].split()
        assert MODEL_CLASSES[int(first[0])] == "syrah" and all(0 <= float(v) <= 1 for v in first[1:])  # cacho com a variedade do talhão
    tasks = json.loads((out / "label_studio" / "tasks.json").read_text())
    rect = tasks[0]["predictions"][0]["result"][0]
    assert rect["type"] == "rectanglelabels" and rect["value"]["rectanglelabels"] == ["syrah"] and 0 <= rect["value"]["x"] <= 100
    config = ET.fromstring((out / "label_studio" / "config.xml").read_text())
    assert [lb.get("value") for lb in config.iter("Label")] == MODEL_CLASSES
    assert "leitura" in (out / "metadata.csv").read_text().splitlines()[0]

    # Simula a exportação da ferramenta de anotação: outra numeração de classes (ordem alfabética).
    tool = tmp_path / "exportacao-ferramenta"
    shutil.copytree(out / "images" / "train", tool / "images")
    tool_names = sorted(MODEL_CLASSES)
    (tool / "classes.txt").write_text("\n".join(tool_names))
    (tool / "labels").mkdir()
    for label in (out / "labels" / "train").glob("*.txt"):
        lines = [f"{tool_names.index(MODEL_CLASSES[int(ln.split()[0])])} {' '.join(ln.split()[1:])}" for ln in label.read_text().splitlines()]
        (tool / "labels" / label.name).write_text("\n".join(lines) + "\n")

    dataset = tmp_path / "anotado"
    report = import_annotations.main(["--annotations", str(tool), "--metadata", str(out / "metadata.csv"), "--out", str(dataset), "--val", "0", "--test", "0"])
    assert report["imagens"] == 3 and report["grupos_sensor_dia"] <= 2 and report["caixas_por_classe"]["syrah"] == 3
    imported = next((dataset / "labels" / "train").glob(f"{codes[0]}.txt")).read_text().splitlines()[0].split()
    assert MODEL_CLASSES[int(imported[0])] == "syrah"  # remapeado de volta para a ordem da OASIS
    assert "names:" in (dataset / "data.yaml").read_text()
