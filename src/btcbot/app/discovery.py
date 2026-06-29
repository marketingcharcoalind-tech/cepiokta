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

import httpx
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

# Error FATAL default untuk supervisor: auth/client (4xx) → JANGAN ditelan.
# (5xx/timeout/transport dibungkus GammaError = transient → di-retry.)
_DEFAULT_FATAL_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPStatusError,)


def discovery_backoff(attempt: int, cap: float) -> float:
    """Delay backoff untuk ``attempt`` (mulai 1), tidak pernah melebihi ``cap``.

    ``1s → 2s → 5s → cap`` (tiap nilai juga di-clamp ke ``cap``).
    """
    base = _BACKOFF_SCHEDULE[attempt - 1] if attempt <= len(_BACKOFF_SCHEDULE) else cap
    return min(base, cap)


async def wait_or_shutdown(
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
            if await wait_or_shutdown(delay, shutdown, sleep):
                return None


async def run_supervised(  # noqa: PLR0913 - parameter eksplisit (keyword-only)
    loop_body: Callable[[], Awaitable[int]],
    *,
    settings: Settings,
    shutdown: asyncio.Event | None = None,
    logger: structlog.typing.FilteringBoundLogger | None = None,
    sleep: SleepFunc = asyncio.sleep,
    fatal_errors: tuple[type[BaseException], ...] = _DEFAULT_FATAL_ERRORS,
) -> int:
    """Jaring pengaman global (Bug B5): jalankan ``loop_body`` & pulih dari error.

    Filosofi: satu-satunya cara proses berhenti = **shutdown sengaja** atau error
    **FATAL** (config/auth). Hiccup jaringan / exception tak terduga apa pun →
    log ``loop_supervisor_restart`` + backoff ter-cap (pola ``discovery_backoff``)
    → MULAI ULANG ``loop_body`` (yang me-rediscover ronde aktif). Tidak pernah exit
    diam-diam.

    Args:
        loop_body: Coroutine factory loop utama (mis. ``lambda: run_readonly(...)``).
            Kembalikan int (mis. jumlah ronde diproses) saat selesai normal.
        settings: Konfigurasi (cap backoff dari ``gamma_discovery_max_backoff_seconds``).
        shutdown: Event shutdown — bila ter-set, berhenti rapi (tidak restart).
        logger: Logger structlog.
        sleep: Fungsi tidur (injectable untuk test).
        fatal_errors: Kelas error yang DI-propagate (default auth/4xx httpx).

    Returns:
        Nilai dari ``loop_body`` saat selesai normal; ``restart_count`` bila
        berhenti karena shutdown.

    Raises:
        BaseException: ``KeyboardInterrupt``/``SystemExit``/``CancelledError`` dan
            error di ``fatal_errors`` di-propagate (tidak ditelan).
    """
    log = logger or structlog.get_logger()
    cap = float(settings.gamma_discovery_max_backoff_seconds)
    restart_count = 0
    while True:
        if shutdown is not None and shutdown.is_set():
            return restart_count
        try:
            return await loop_body()
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except fatal_errors as exc:
            log.error("loop_fatal", error=str(exc), error_type=type(exc).__name__)
            raise
        except Exception as exc:
            restart_count += 1
            delay = discovery_backoff(restart_count, cap)
            log.error(
                "loop_supervisor_restart",
                error=str(exc),
                error_type=type(exc).__name__,
                restart_count=restart_count,
                backoff=delay,
            )
            if await wait_or_shutdown(delay, shutdown, sleep):
                return restart_count
