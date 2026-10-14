"""
Utilitários comuns às integrações com serviços externos (ThingSpeak, Embrapa AgroAPI):
erro padronizado, cache em memória com expiração e limite de requisições por chave.

O cache e o limitador são por processo (como o bloqueio de login em routers/auth.py);
com várias instâncias da API, cada uma mantém o seu.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .. import __version__

USER_AGENT = f"OASIS-API/{__version__} (+https://github.com/PI-2026-2-Vinicola)"


class ExternalServiceError(Exception):
    """Falha ao consultar um serviço externo. `status_code` é o que a API OASIS devolve ao cliente."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class CacheHit:
    value: Any
    stale: bool


class TTLCache:
    """Cache LRU com expiração. Itens vencidos podem ser lidos como "desatualizados" por `stale_for` segundos."""

    def __init__(self, max_items: int = 256):
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()  # chave → (expira_em, valor)
        self._lock = threading.Lock()

    def get(self, key: str, stale_for: float = 0) -> CacheHit | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if now <= expires:
                self._items.move_to_end(key)
                return CacheHit(value, stale=False)
            if stale_for and now - expires <= stale_for:
                return CacheHit(value, stale=True)
            return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class KeyedLocks:
    """Um lock por chave: requisições simultâneas pelo mesmo dado fazem uma única chamada externa."""

    def __init__(self):
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def __call__(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks[key]


class RateLimiter:
    """Janela deslizante: no máximo `limit` eventos por chave a cada `window` segundos."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._events: defaultdict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
