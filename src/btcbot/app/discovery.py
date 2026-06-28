"""app/discovery.py — discovery Gamma yang RESILIEN (Bug B4).

Gap transien di batas window / hiccup Gamma membuat ronde 5m sesaat tak terlihat
→ ``discover_active_round()`` raise ``GammaError``. Satu kegagalan transient
**TIDAK BOLEH** mematikan soak yang jalan berhari-hari (filosofi sama dengan fix
freeze recorder: jangan mati/diam — selalu log + retry + tetap hidup).

:func:`discover_with_retry` membungkus ``discover_active_round`` dengan retry
**tak terbatas** (ini collector long-running) + backoff bertingkat berbatas cap:
``1s → 2s → 5s → cap`` (``GAMMA_DISCOVERY_MAX_BACKOFF_SECONDS``, default 15s).
Tiap percobaan di-log (``event="discover_retry"``) agar tak senyap.

Pemisahan error:
- **Transient** (``GammaError``: tidak ada ronde / 429 / 5xx / transport) → RETRY.
- **Fatal** (config/auth, mis. ``httpx.HTTPStatusError`` 401/403, ``ValueError``)
  → di-propagate (JANGAN ditelan).

Modul ini sengaja **tidak** mengimpor adapter on-chain (chainlink/web3) agar
ringan & dapat diuji tanpa jaringan/DLL native.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from btcbot.adapters.gamma import GammaError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from btcbot.adapters.gamma import GammaClient
    from btcbot.config.settings import Settings
    from btcbot.domain.models import RoundMeta

    SleepFunc = Callable[[float], Awaitable[None]]

# Backoff bertingkat sebelum cap (detik).
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 5.0)


def discovery_backoff(attempt: int, cap: float) -> float:
    """Delay backoff untuk ``attempt`` (mulai 1), tidak pernah melebihi ``cap``.

    ``1s → 2s → 5s → cap`` (tiap nilai juga di-clamp ke ``cap``).
    """
    base = _BACKOFF_SCHEDULE[attempt - 1] if attempt <= len(_BACKOFF_SCHEDULE) else cap
    return min(base, cap)


async def _wait_or_shutdown(
    delay: float,
    shutdown: asyncio.Event | None,
    sleep: SleepFunc,
) -> bool:
    """Tunggu ``delay`` detik; kembalikan True bila ``shutdown`` ter-set saat tunggu."""
    if shutdown is None:
        await sleep(delay)
        return False
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=delay)
    except TimeoutError:
        return False
    return True


async def discover_with_retry(
    gamma: GammaClient,
    *,
    settings: Settings,
    shutdown: asyncio.Event | None = None,
    logger: structlog.typing.FilteringBoundLogger | None = None,
    sleep: SleepFunc = asyncio.sleep,
) -> RoundMeta | None:
    """Discover ronde aktif dengan retry resilien terhadap kegagalan transient.

    Args:
        gamma: Klien discovery Gamma.
        settings: Konfigurasi (retry on/off + cap backoff).
        shutdown: Event shutdown (opsional) — bila ter-set saat menunggu/awal,
            kembalikan ``None`` (loop pemanggil berhenti graceful).
        logger: Logger structlog (default global).
        sleep: Fungsi tidur (injectable untuk test).

    Returns:
        :class:`RoundMeta` ronde aktif/terdekat, atau ``None`` bila shutdown.

    Raises:
        Exception: error FATAL (config/auth) di-propagate; ``GammaError``
            transient TIDAK di-propagate (di-retry tak terbatas).
    """
    log = logger or structlog.get_logger()

    # Fail-fast bila retry dimatikan (perilaku lama).
    if not settings.gamma_discovery_retry:
        return await gamma.discover_active_round()

    cap = float(settings.gamma_discovery_max_backoff_seconds)
    attempt = 0
    while True:
        if shutdown is not None and shutdown.is_set():
            return None
        try:
            return await gamma.discover_active_round()
        except GammaError as exc:
            attempt += 1
            delay = discovery_backoff(attempt, cap)
            log.warning(
                "discover_retry",
                attempt=attempt,
                sleep=delay,
                error=str(exc),
            )
            if await _wait_or_shutdown(delay, shutdown, sleep):
                return None
