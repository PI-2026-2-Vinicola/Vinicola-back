"""
Criptografia dos dados sensíveis em repouso (campos do banco), com AES-256-GCM.

Formato gravado:  enc:v1:<id da chave>:<nonce base64>:<texto cifrado + tag base64>

- AES-GCM cifra e autentica: qualquer alteração no valor gravado faz a leitura falhar.
- Cada valor usa um nonce aleatório de 96 bits, então o mesmo dado cifrado duas vezes gera valores diferentes.
- O "contexto" entra como dado associado (AAD): um valor cifrado para o e-mail do cliente da organização 3
  não pode ser copiado para outro campo nem para outra organização, porque a decifração falha.
- O id da chave (8 caracteres do SHA-256 da chave) permite trocar a chave: a nova cifra os dados novos e as
  antigas (DATA_ENCRYPTION_OLD_KEYS) continuam lendo os dados já gravados.

A chave fica só em variável de ambiente (DATA_ENCRYPTION_KEY), nunca no código nem no banco.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

PREFIX = "enc:v1:"
# Chave usada só em desenvolvimento e testes, derivada de um texto público: a produção recusa.
DEV_KEY = hashlib.sha256(b"oasis-chave-de-desenvolvimento-nao-use-em-producao").digest()
log = logging.getLogger("oasis.crypto")


class DecryptionError(ValueError):
    """Valor adulterado, de outro contexto ou cifrado com uma chave que não está configurada."""


def _decode_key(value: str) -> bytes:
    raw = value.strip()
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            key = decoder(raw + "=" * (-len(raw) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(key) == 32:
            return key
    raise ValueError("a chave precisa ter 32 bytes em base64 (AES-256)")


def key_problem(value: str) -> str | None:
    """Motivo pelo qual a chave não serve para produção (None = serve)."""
    if not value:
        return "DATA_ENCRYPTION_KEY precisa ser definida (32 bytes em base64) para cifrar os dados sensíveis."
    try:
        key = _decode_key(value)
    except ValueError as exc:
        return f"DATA_ENCRYPTION_KEY inválida: {exc}."
    if key == DEV_KEY:
        return "DATA_ENCRYPTION_KEY não pode ser a chave de desenvolvimento."
    return None


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:8]


@lru_cache
def _keys() -> tuple[bytes, dict[str, bytes]]:
    settings = get_settings()
    if settings.data_encryption_key:
        current = _decode_key(settings.data_encryption_key)
    else:
        log.warning("DATA_ENCRYPTION_KEY vazia: usando a chave de desenvolvimento (inaceitável em produção).")
        current = DEV_KEY
    known = {_key_id(current): current}
    for old in filter(None, (k.strip() for k in settings.data_encryption_old_keys.split(","))):
        key = _decode_key(old)
        known[_key_id(key)] = key
    return current, known


def reset_keys() -> None:
    """Relê as chaves das configurações (testes e troca de chave)."""
    _keys.cache_clear()


def _aad(context: str) -> bytes:
    return f"oasis:{context}".encode()


def encrypt(value: str | None, context: str) -> str | None:
    if value is None or value == "":
        return value
    key, _ = _keys()
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, value.encode("utf-8"), _aad(context))
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"{PREFIX}{_key_id(key)}:{b64(nonce)}:{b64(sealed)}"


def current_key_id() -> str:
    return _key_id(_keys()[0])


def needs_rewrap(value: str | None) -> bool:
    """Valor cifrado com uma chave antiga (deve ser recifrado com a atual antes de a antiga ser removida)."""
    return is_encrypted(value) and value[len(PREFIX):].split(":")[0] != current_key_id()


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)


def decrypt(value: str | None, context: str) -> str | None:
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        raise DecryptionError("valor não está no formato cifrado")
    try:
        kid, nonce_b64, sealed_b64 = value[len(PREFIX):].split(":")
        _, known = _keys()
        key = known[kid]
        unb64 = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        return AESGCM(key).decrypt(unb64(nonce_b64), unb64(sealed_b64), _aad(context)).decode("utf-8")
    except KeyError:
        raise DecryptionError(f"chave {value[len(PREFIX):].split(':')[0]} não configurada") from None
    except (ValueError, InvalidTag, binascii.Error) as exc:
        raise DecryptionError("valor adulterado ou de outro contexto") from exc
