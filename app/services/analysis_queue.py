"""
Fila das análises de imagem.

Cada análise usa algumas centenas de MB de memória. Sem limite, várias fotos chegando juntas derrubam uma
instância pequena (512 MB no plano gratuito do Render). Aqui, no máximo OASIS_MAX_CONCURRENT_ANALYSES rodam ao
mesmo tempo; as outras esperam a vez. O tempo de espera na fila não conta no tempo limite da análise.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


class AnalysisBusy(RuntimeError):
    """A fila não andou dentro do tempo de espera permitido."""


_state: tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore] | None = None


def _semaphore(limit: int) -> asyncio.Semaphore:
    global _state
    loop = asyncio.get_running_loop()
    if _state is None or _state[0] is not loop or _state[1] != limit:
        _state = (loop, limit, asyncio.Semaphore(limit))
    return _state[2]


async def run_limited(fn: Callable[[], T], *, limit: int, timeout: float, queue_timeout: float) -> T:
    """
    Roda `fn` numa thread quando houver vaga. AnalysisBusy se a vaga não vier em `queue_timeout` segundos;
    TimeoutError se a análise passar de `timeout`. A vaga só é liberada quando a thread termina de fato,
    mesmo que a requisição já tenha desistido por tempo, para a memória nunca passar do limite.
    """
    sem = _semaphore(max(1, limit))
    try:
        await asyncio.wait_for(sem.acquire(), queue_timeout)
    except TimeoutError:
        raise AnalysisBusy from None

    task = asyncio.ensure_future(run_in_threadpool(fn))

    def _done(t: asyncio.Future) -> None:
        sem.release()
        if not t.cancelled():
            t.exception()  # marca o erro como lido: a requisição pode já ter respondido por tempo limite

    task.add_done_callback(_done)
    return await asyncio.wait_for(asyncio.shield(task), timeout)
