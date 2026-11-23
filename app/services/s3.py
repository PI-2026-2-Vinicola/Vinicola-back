"""
Cliente S3 mínimo (assinatura AWS Signature Version 4) sobre httpx.

Usado para guardar as fotos fora do servidor (Supabase Storage, Cloudflare R2, Backblaze B2, MinIO ou AWS S3)
sem trazer o boto3 para a API: o plano gratuito do Render tem 512 MB de memória. Endereçamento por caminho
(`<endpoint>/<bucket>/<chave>`), como o Supabase Storage exige.
"""

from __future__ import annotations

import hashlib
import hmac
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

import httpx

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class S3Error(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"S3 respondeu {status} {code}: {message}")
        self.status, self.code = status, code


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_v4(method: str, url: str, headers: dict[str, str], *, payload_sha256: str, access_key: str, secret_key: str,
            region: str, now: datetime, service: str = "s3") -> dict[str, str]:
    """
    Devolve os cabeçalhos assinados (inclui Host, X-Amz-Date, X-Amz-Content-Sha256 e Authorization).
    `headers` entram na assinatura; o caminho da URL já deve estar codificado.
    """
    parts = urlsplit(url)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    signed = {k.lower(): str(v).strip() for k, v in headers.items()}
    signed.update({"host": parts.netloc, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256})
    query = sorted(
        (_uri_encode(unquote(k), keep_slash=False), _uri_encode(unquote(v), keep_slash=False))
        for k, _, v in (item.partition("=") for item in parts.query.split("&") if item)
    )
    names = sorted(signed)
    canonical = "\n".join([
        method.upper(),
        parts.path or "/",
        "&".join(f"{k}={v}" for k, v in query),
        "".join(f"{n}:{signed[n]}\n" for n in names),
        ";".join(names),
        payload_sha256,
    ])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in signed.items() if k != "host"}
    out["authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    return out


class S3Client:
    """Operações de objeto usadas pela OASIS: gravar, ler, apagar e listar."""

    def __init__(self, endpoint_url: str, region: str, bucket: str, access_key: str, secret_key: str, *,
                 timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.base = endpoint_url.rstrip("/")
        self.region, self.bucket = region, bucket
        self._access_key, self._secret_key = access_key, secret_key
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _url(self, key: str = "", query: str = "") -> str:
        path = _uri_encode(self.bucket, keep_slash=False) + ("/" + _uri_encode(key, keep_slash=True) if key else "")
        return f"{self.base}/{path}" + (f"?{query}" if query else "")

    def _request(self, method: str, url: str, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
        payload = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        signed = sign_v4(method, url, headers or {}, payload_sha256=payload, access_key=self._access_key,
                         secret_key=self._secret_key, region=self.region, now=datetime.now(timezone.utc))
        return self._http.request(method, url, content=body or None, headers=signed)

    @staticmethod
    def _error(res: httpx.Response) -> S3Error:
        code, message = "", res.text[:200]
        try:
            root = ET.fromstring(res.content)
            code = root.findtext("Code") or root.findtext(f"{_NS}Code") or ""
            message = root.findtext("Message") or root.findtext(f"{_NS}Message") or message
        except ET.ParseError:
            pass
        return S3Error(res.status_code, code, message)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        res = self._request("PUT", self._url(key), data, {"content-type": content_type})
        if res.status_code not in (200, 201):
            raise self._error(res)

    def get(self, key: str) -> bytes:
        res = self._request("GET", self._url(key))
        if res.status_code == 404:
            raise FileNotFoundError(key)
        if res.status_code != 200:
            raise self._error(res)
        return res.content

    def delete(self, key: str) -> None:
        res = self._request("DELETE", self._url(key))
        if res.status_code not in (200, 204, 404):
            raise self._error(res)

    def list(self, prefix: str = "") -> Iterator[tuple[str, int]]:
        """(chave, tamanho em bytes) de cada objeto, paginando com ListObjectsV2."""
        token = ""
        while True:
            params = {"list-type": "2", "max-keys": "1000"}
            if prefix:
                params["prefix"] = prefix
            if token:
                params["continuation-token"] = token
            query = "&".join(f"{_uri_encode(k, keep_slash=False)}={_uri_encode(v, keep_slash=False)}" for k, v in sorted(params.items()))
            res = self._request("GET", self._url(query=query))
            if res.status_code != 200:
                raise self._error(res)
            root = ET.fromstring(res.content)
            for item in root.iter(f"{_NS}Contents"):
                yield item.findtext(f"{_NS}Key") or "", int(item.findtext(f"{_NS}Size") or 0)
            token = root.findtext(f"{_NS}NextContinuationToken") or ""
            if (root.findtext(f"{_NS}IsTruncated") or "false").lower() != "true" or not token:
                return
