"""
Gateway de edge da OASIS.

Recebe as capturas dos sensores na rede local (HTTP em /capture ou MQTT), faz o
processamento inicial (edge/preprocess.py) e encaminha a imagem preparada para a
API na nuvem (POST /api/v1/ingest). Sem internet, as imagens ficam em uma fila
local e são reenviadas automaticamente.

Execução:
    OASIS_API_URL=http://localhost:8000 uvicorn edge.gateway:app --port 8081
    # opcional: OASIS_MQTT_HOST=localhost para assinar oasis/sensores/+/captura
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from .preprocess import preprocess

API_URL = os.getenv("OASIS_API_URL", "http://localhost:8000").rstrip("/")
QUEUE_DIR = Path(os.getenv("OASIS_EDGE_QUEUE", Path(__file__).parent / "queue"))
RETRY_SECONDS = int(os.getenv("OASIS_EDGE_RETRY_SECONDS", "60"))
MQTT_HOST = os.getenv("OASIS_MQTT_HOST")
MQTT_PORT = int(os.getenv("OASIS_MQTT_PORT", "1883"))

FORWARDED = ("sensor_id", "captured_at", "battery", "signal", "firmware", "temperature_c", "humidity_pct")

QUEUE_DIR.mkdir(parents=True, exist_ok=True)
stats = {"received": 0, "discarded": 0, "forwarded": 0, "queued": 0}


def forward(meta: dict, image: bytes, timeout: float = 20.0) -> httpx.Response:
    data = {k: str(v) for k, v in meta.items() if k in FORWARDED and v not in (None, "")}
    headers = {"X-Device-Token": meta.get("token") or ""}
    return httpx.post(f"{API_URL}/api/v1/ingest", data=data, files={"image": ("captura.jpg", image, "image/jpeg")}, headers=headers, timeout=timeout)


def enqueue(meta: dict, image: bytes) -> str:
    key = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    (QUEUE_DIR / f"{key}.jpg").write_bytes(image)
    (QUEUE_DIR / f"{key}.json").write_text(json.dumps(meta))
    stats["queued"] += 1
    return key


def flush_queue() -> int:
    sent = 0
    for meta_file in sorted(QUEUE_DIR.glob("*.json")):
        img_file = meta_file.with_suffix(".jpg")
        try:
            res = forward(json.loads(meta_file.read_text()), img_file.read_bytes())
        except httpx.HTTPError:
            break  # ainda sem conexão
        if res.status_code < 500:  # 2xx enviado; 4xx não adianta reenviar
            meta_file.unlink(missing_ok=True)
            img_file.unlink(missing_ok=True)
            sent += 1
    return sent


def handle_capture(meta: dict, raw: bytes) -> tuple[int, dict]:
    stats["received"] += 1
    result = preprocess(raw)
    info = {"brightness": result.brightness, "sharpness": result.sharpness, "size": [result.width, result.height]}
    if not result.accepted or result.image is None:
        stats["discarded"] += 1
        return 422, {"accepted": False, "reason": result.reason, **info}
    meta.setdefault("captured_at", datetime.now(timezone.utc).isoformat())
    try:
        res = forward(meta, result.image)
        stats["forwarded"] += 1
        return res.status_code, res.json() if res.headers.get("content-type", "").startswith("application/json") else {"status": res.status_code}
    except httpx.HTTPError:
        key = enqueue(meta, result.image)
        return 202, {"accepted": True, "queued": key, **info}


def retry_loop(stop: threading.Event) -> None:
    while not stop.wait(RETRY_SECONDS):
        flush_queue()


def start_mqtt(stop: threading.Event) -> None:  # pragma: no cover - requer broker
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[edge] paho-mqtt não instalado — MQTT desativado")
        return
    pending_meta: dict[str, dict] = {}

    def on_message(_c, _u, msg):
        parts = msg.topic.split("/")  # oasis/sensores/{id}/{captura|meta}
        sensor_id, kind = parts[2], parts[3]
        if kind == "meta":
            pending_meta[sensor_id] = json.loads(msg.payload or b"{}")
            return
        meta = pending_meta.pop(sensor_id, {})
        handle_capture(
            {
                "sensor_id": sensor_id,
                "token": os.getenv(f"OASIS_TOKEN_{sensor_id.replace('-', '_')}", ""),
                "battery": meta.get("battery"),
                "signal": meta.get("signal"),
                "firmware": meta.get("firmware"),
                "temperature_c": meta.get("temperatureC"),
                "humidity_pct": meta.get("humidityPct"),
                "captured_at": meta.get("capturedAt"),
            },
            msg.payload,
        )

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("oasis/sensores/+/captura")
    client.subscribe("oasis/sensores/+/meta")
    client.loop_start()
    stop.wait()
    client.loop_stop()


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=retry_loop, args=(stop,), daemon=True).start()
    if MQTT_HOST:
        threading.Thread(target=start_mqtt, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="OASIS Edge Gateway", lifespan=lifespan)


@app.post("/capture")
def capture(
    sensor_id: str = Form(...),
    image: UploadFile = File(...),
    captured_at: str | None = Form(default=None),
    battery: int | None = Form(default=None),
    signal: int | None = Form(default=None),
    firmware: str | None = Form(default=None),
    temperature_c: float | None = Form(default=None),
    humidity_pct: float | None = Form(default=None),
    x_device_token: str | None = Header(default=None),
):
    meta = {
        "sensor_id": sensor_id, "token": x_device_token, "captured_at": captured_at, "battery": battery, "signal": signal,
        "firmware": firmware, "temperature_c": temperature_c, "humidity_pct": humidity_pct,
    }
    code, body = handle_capture(meta, image.file.read())
    return JSONResponse(body, status_code=code)


@app.get("/status")
def status():
    return {**stats, "pending": len(list(QUEUE_DIR.glob("*.json"))), "api": API_URL, "time": time.time()}
