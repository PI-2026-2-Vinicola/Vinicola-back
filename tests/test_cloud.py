"""Publicação na nuvem: fotos no S3 (Supabase Storage), fila de análise, IP atrás do proxy, URL do banco e gateway."""

import asyncio
import hashlib
import io
import re
import threading
import time
import xml.sax.saxutils as su
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from PIL import Image
from starlette.requests import Request

from app import main as main_module
from app.cli import download_photos
from app.config import Settings
from app.deps import client_ip
from app.services import storage as storage_module
from app.services.analysis_queue import AnalysisBusy, run_limited
from app.services.s3 import EMPTY_SHA256, S3Client, S3Error, sign_v4
from app.services.storage import LocalStorage, S3Storage, StorageError, load_image

from .conftest import API
from .images import grape_cluster, jpeg

AWS_EXAMPLE = {"access_key": "AKIAIOSFODNN7EXAMPLE", "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
               "region": "us-east-1", "now": datetime(2013, 5, 24, tzinfo=timezone.utc), "payload_sha256": EMPTY_SHA256}


# ------------------------------------------------------------------ assinatura e cliente S3
def test_signature_matches_the_aws_documentation_examples():
    get = sign_v4("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, **AWS_EXAMPLE)
    assert get["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    listing = sign_v4("GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J", {}, **AWS_EXAMPLE)
    assert listing["authorization"].endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


class FakeS3:
    """Bucket em memória que responde como o S3 (e confere o hash do corpo assinado)."""

    def __init__(self, bucket="oasis-fotos", page_size=1000, fail_with: int | None = None):
        self.bucket, self.page_size, self.fail_with = bucket, page_size, fail_with
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=chave/")
        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        if self.fail_with:
            return httpx.Response(self.fail_with, content=b"<Error><Code>AccessDenied</Code><Message>negado</Message></Error>")
        path = unquote(urlsplit(str(request.url)).path)
        prefix = f"/storage/v1/s3/{self.bucket}"
        assert path.startswith(prefix)
        key = path[len(prefix):].lstrip("/")
        if request.method == "PUT":
            self.objects[key] = request.content
            return httpx.Response(200)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        if key:
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>")
        params = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        keys = sorted(k for k in self.objects if k.startswith(params.get("prefix", "")))
        start = int(params.get("continuation-token", "0"))
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        items = "".join(f"<Contents><Key>{su.escape(k)}</Key><Size>{len(self.objects[k])}</Size></Contents>" for k in page)
        token = f"<NextContinuationToken>{start + self.page_size}</NextContinuationToken>" if more else ""
        body = f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{str(more).lower()}</IsTruncated>{items}{token}</ListBucketResult>'
        return httpx.Response(200, content=body.encode())


def _client(fake: FakeS3) -> S3Client:
    return S3Client("https://projeto.storage.supabase.co/storage/v1/s3", "sa-east-1", fake.bucket, "chave", "segredo", transport=httpx.MockTransport(fake))


def test_s3_client_puts_gets_lists_with_pagination_and_deletes():
    fake = FakeS3(page_size=2)
    c = _client(fake)
    for name in ("2026/11/22/a.jpg", "2026/11/22/a_thumb.jpg", "2026/11/23/b c+ç.jpg"):
        c.put(name, name.encode(), "image/jpeg")
    assert fake.requests[0].headers["content-type"] == "image/jpeg"
    assert c.get("2026/11/23/b c+ç.jpg") == "2026/11/23/b c+ç.jpg".encode()
    assert [k for k, _ in c.list()] == sorted(fake.objects)  # duas páginas
    assert [k for k, _ in c.list("2026/11/22/")] == ["2026/11/22/a.jpg", "2026/11/22/a_thumb.jpg"]
    c.delete("2026/11/22/a.jpg")
    c.delete("nao/existe.jpg")
    with pytest.raises(FileNotFoundError):
        c.get("2026/11/22/a.jpg")


def test_s3_errors_become_storage_errors():
    storage = S3Storage(_client(FakeS3(fail_with=403)))
    with pytest.raises(StorageError, match="AccessDenied"):
        storage.put("x.jpg", b"1")

    def offline(_):
        raise httpx.ConnectError("sem rede")

    storage = S3Storage(S3Client("https://h/s3", "r", "b", "k", "s", transport=httpx.MockTransport(offline)))
    with pytest.raises(StorageError):
        storage.get("x.jpg")
    with pytest.raises(S3Error):
        _client(FakeS3(fail_with=500)).put("x.jpg", b"1")


# ------------------------------------------------------------------ fotos guardadas no bucket
@pytest.fixture
def bucket(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage_module, "get_storage", lambda: S3Storage(_client(fake)))
    return fake


def _sensor(client, admin_headers, sensor_id):
    body = {"id": sensor_id, "name": f"Câmera {sensor_id}", "block": "Talhão N", "location": "Fileira 1", "varietyId": "syrah", "device": "ESP32-CAM", "captureIntervalMin": 60}
    res = client.post(f"{API}/sensors", json=body, headers=admin_headers)
    assert res.status_code == 201, res.text


def test_photos_go_to_the_bucket_and_are_served_and_deleted_from_it(client, admin_headers, bucket):
    _sensor(client, admin_headers, "NV-01")
    res = client.post(f"{API}/ingest", data={"sensor_id": "NV-01"}, files={"image": ("c.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=admin_headers)
    assert res.status_code == 201, res.text
    code = res.json()["id"]
    assert len(bucket.objects) == 2 and any(k.endswith("_thumb.jpg") for k in bucket.objects)
    img = client.get(f"{API}/readings/{code}/image", headers=admin_headers)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content in bucket.objects.values()
    assert client.get(f"{API}/system/status", headers=admin_headers).json()["storageBackend"] == "local"  # configuração da suíte
    assert client.delete(f"{API}/readings/{code}", headers=admin_headers).status_code == 204
    assert bucket.objects == {}


def test_bucket_outage_answers_503_so_the_gateway_retries(client, admin_headers, monkeypatch):
    _sensor(client, admin_headers, "NV-02")
    monkeypatch.setattr(storage_module, "get_storage", lambda: S3Storage(_client(FakeS3(fail_with=503))))
    res = client.post(f"{API}/ingest", data={"sensor_id": "NV-02"}, files={"image": ("c.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=admin_headers)
    assert res.status_code == 503 and res.headers["retry-after"] == "60"


def test_download_photos_copies_only_what_is_missing(tmp_path, monkeypatch, capsys):
    src = LocalStorage(tmp_path / "origem")
    src.put("2026/11/22/a.jpg", b"aaa")
    src.put("2026/11/22/a_thumb.jpg", b"a")
    monkeypatch.setattr(storage_module, "get_storage", lambda: src)
    out = tmp_path / "copia"
    assert download_photos(out) == 0 and (out / "2026/11/22/a.jpg").read_bytes() == b"aaa"
    assert download_photos(out) == 0 and "0 copiadas, 2 já existiam" in capsys.readouterr().out


def test_local_storage_refuses_keys_outside_its_folder(tmp_path):
    s = LocalStorage(tmp_path / "fotos")
    with pytest.raises(FileNotFoundError):
        s.get("../../etc/passwd")
    s.delete("../fora.jpg")  # ignorado


# ------------------------------------------------------------------ memória: foto reduzida e fila de análise
def _photo(size, fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (120, 40, 90)).save(out, fmt)
    return out.getvalue()


@pytest.mark.parametrize(("size", "fmt", "expected"), [((4000, 3000), "JPEG", (1600, 1200)), ((3000, 4000), "PNG", (1200, 1600)), ((800, 600), "JPEG", (800, 600))])
def test_photos_are_opened_already_reduced(size, fmt, expected):
    assert load_image(_photo(size, fmt)).size == expected


def test_analysis_queue_limits_concurrency_and_does_not_count_waiting_time():
    running, peak = 0, 0
    lock = threading.Lock()

    def work():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with lock:
            running -= 1
        return "ok"

    async def scenario():
        jobs = [run_limited(work, limit=1, timeout=0.3, queue_timeout=2) for _ in range(3)]
        return await asyncio.gather(*jobs)

    assert asyncio.run(scenario()) == ["ok", "ok", "ok"] and peak == 1  # 3 × 0,15 s > tempo limite, mas a espera não conta


def test_analysis_queue_rejects_when_the_wait_is_too_long_and_frees_the_slot_only_when_work_ends():
    async def scenario():
        slow = asyncio.ensure_future(run_limited(lambda: time.sleep(0.3), limit=1, timeout=0.05, queue_timeout=1))
        await asyncio.sleep(0.01)
        with pytest.raises(AnalysisBusy):
            await run_limited(lambda: None, limit=1, timeout=1, queue_timeout=0.05)
        with pytest.raises(TimeoutError):
            await slow
        started = time.perf_counter()
        await run_limited(lambda: None, limit=1, timeout=1, queue_timeout=1)  # espera a análise lenta terminar de fato
        return time.perf_counter() - started

    assert asyncio.run(scenario()) > 0.1


# ------------------------------------------------------------------ IP atrás do proxy do Render
def _request(headers: dict[str, str], peer="10.0.0.7") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (peer, 1234), "query_string": b""})


def test_client_ip_comes_from_the_configured_proxy_header(monkeypatch):
    settings = Settings(_env_file=None, client_ip_header="true-client-ip,cf-connecting-ip")
    monkeypatch.setattr("app.deps.get_settings", lambda: settings)
    assert client_ip(_request({"True-Client-IP": "177.10.20.30", "X-Forwarded-For": "1.2.3.4"})) == "177.10.20.30"
    assert client_ip(_request({"CF-Connecting-IP": "2804:14c::1"})) == "2804:14c::1"
    assert client_ip(_request({"True-Client-IP": "não é ip"})) == "10.0.0.7"  # valor inválido: IP da conexão
    monkeypatch.setattr("app.deps.get_settings", lambda: Settings(_env_file=None))
    assert client_ip(_request({"True-Client-IP": "177.10.20.30"})) == "10.0.0.7"  # sem configuração, cabeçalho ignorado


# ------------------------------------------------------------------ configuração
@pytest.mark.parametrize("url", ["postgres://u:s@h:5432/db", "postgresql://u:s@h:5432/db", "postgresql+psycopg://u:s@h:5432/db"])
def test_database_url_from_render_or_supabase_uses_psycopg(url):
    assert Settings(_env_file=None, database_url=url).database_url == "postgresql+psycopg://u:s@h:5432/db"


def test_production_requires_complete_bucket_settings():
    base = {"environment": "production", "jwt_secret": "s" * 40, "storage_backend": "s3", "s3_bucket": "oasis-fotos"}
    problems = Settings(_env_file=None, **base).validate_for_production()
    assert any(re.search("S3_ENDPOINT_URL.*S3_SECRET_ACCESS_KEY", p) for p in problems)
    with pytest.raises(ValueError):
        Settings(_env_file=None, storage_backend="ftp")


def test_health_reports_the_deployed_commit(client, monkeypatch):
    monkeypatch.setattr(main_module, "DEPLOYED_COMMIT", "abc1234")
    assert client.get("/health").json()["commit"] == "abc1234"


# ------------------------------------------------------------------ gateway de edge
def test_gateway_queues_the_photo_when_the_api_answers_5xx(tmp_path, monkeypatch):
    from edge import gateway

    monkeypatch.setattr(gateway, "QUEUE_DIR", tmp_path)
    monkeypatch.setattr(gateway, "forward", lambda meta, image: httpx.Response(503, json={"detail": "ocupado"}))
    good = _textured_jpeg()
    status, body = gateway.handle_capture({"sensor_id": "S-1", "token": "t"}, good)
    assert status == 202 and body["queued"] and body["apiStatus"] == 503
    assert len(list(tmp_path.glob("*.json"))) == 1


def _textured_jpeg() -> bytes:
    import numpy as np

    rng = np.random.default_rng(1)
    arr = (rng.random((900, 1200, 3)) * 160 + 60).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(arr).save(out, "JPEG", quality=90)
    return out.getvalue()
