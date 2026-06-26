"""Boot sequence & readonly runner (docs/08 §8.14).

Menampilkan boot sequence bergaya referensi lalu menjalankan loop **readonly**:
menemukan ronde aktif, merekam orderbook + harga + resolusi ke DB, dan mencetak
log per-ronde (JSON via structlog). **TIDAK PERNAH** mengirim order — apa pun
MODE-nya, runner ini hanya merekam (fase pra-live).

Entrypoint: ``python -m btcbot.app.cli`` (lihat :func:`main`). Gunakan
``--demo`` untuk menjalankan end-to-end terhadap adapter fixture (tanpa jaringan
& tanpa order), berguna untuk ``make run-readonly``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import TYPE_CHECKING

import structlog

from btcbot import __version__
from btcbot.adapters.chainlink import FailoverPriceSource, PriceUnavailableError
from btcbot.adapters.clob_ws import HttpClobWS
from btcbot.adapters.clock import SystemClock
from btcbot.adapters.gamma import HttpGammaClient
from btcbot.app.demo import build_demo_runtime
from btcbot.config.settings import Settings, get_settings
from btcbot.data.recorder import Recorder
from btcbot.data.store import Store
from btcbot.domain.models import round_from_meta

if TYPE_CHECKING:
    from collections.abc import Callable

    from btcbot.adapters.gamma import GammaClient

# Langkah boot bergaya referensi (docs/08 §8.14).
_BOOT_STEPS: tuple[str, ...] = (
    "connecting to Polymarket",
    "authenticating wallet",
    "opening websocket feed (BTC 5m)",
    "loading interval-loader module",
    "loading trend module",
    "loading hedging module",
)


def configure_logging(level: str = "INFO") -> None:
    """Konfigurasi structlog untuk output JSON terstruktur."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def run_boot_sequence(out: Callable[[str], None] = print, version: str = __version__) -> None:
    """Cetak boot sequence bergaya referensi diakhiri 'all systems go.'."""
    out(f"5min-btc-polymarket v{version}")
    for step in _BOOT_STEPS:
        out(f"  {step} ...".ljust(40) + "[ ok ]")
    out("all systems go.")


async def run_readonly(  # noqa: PLR0913
    *,
    settings: Settings,
    gamma: GammaClient,
    recorder: Recorder,
    max_rounds: int | None = None,
    updates_per_round: int | None = None,
    shutdown: asyncio.Event | None = None,
    logger: structlog.typing.FilteringBoundLogger | None = None,
) -> int:
    """Loop readonly: rekam ronde demi ronde. Kembalikan jumlah ronde diproses.

    Tidak mengirim order. Berhenti bila ``max_rounds`` tercapai atau
    ``shutdown`` di-set (mis. SIGINT).
    """
    log = logger or structlog.get_logger()
    balance = settings.paper_starting_balance  # saldo simulasi tetap (readonly)
    processed = 0

    while max_rounds is None or processed < max_rounds:
        if shutdown is not None and shutdown.is_set():
            log.info("shutdown_requested", processed=processed)
            break

        meta = await gamma.discover_active_round()

        # start_price = harga BTC saat ronde ditemukan (Chainlink). Bila harga
        # tidak tersedia, JANGAN mengarang: lewati ronde ini (akan dicoba lagi).
        try:
            tick = await recorder.sample_price()
        except PriceUnavailableError as exc:
            log.warning("price_unavailable_skip_round", market_id=meta.market_id, error=str(exc))
            processed += 1
            continue

        round_no = int(meta.start_time.timestamp())
        rnd = round_from_meta(meta, round_no=round_no, start_price=tick.price)
        await recorder.record_round(rnd)

        # Δ = price_now - start_price (price truth Chainlink) → direkam ke signals.
        delta_str = "0"
        tick2 = await recorder.record_price_tick(rnd)
        delta_str = str(tick2.price - rnd.start_price)
        if tick2.stale:
            log.warning("price_stale", round_no=round_no, ts=tick2.ts.isoformat())

        await recorder.consume_market(
            round_no,
            [meta.token_id_up, meta.token_id_down],
            limit=updates_per_round,
        )

        # Rekonsiliasi resolusi bila sudah tersedia.
        resolved = await gamma.get_market(meta.condition_id)
        if resolved.outcome is not None:
            await recorder.record_resolution(round_no, resolved.outcome)

        processed += 1
        log.info(
            "round_recorded",
            round_no=round_no,
            market_id=meta.market_id,
            delta=delta_str,
            balance=str(balance),
            mode=str(settings.mode),
            resolved=(None if resolved.outcome is None else str(resolved.outcome)),
        )

    return processed


async def build_runtime(settings: Settings) -> tuple[Store, GammaClient, Recorder]:
    """Bangun store + adapter nyata + recorder (readonly, tanpa order).

    Catatan: ``ChainlinkDataFeed`` membaca on-chain via RPC. Bila harga gagal
    dibaca/anomali, ``record_price_tick`` melempar ``PriceUnavailableError``
    yang ditangani di loop (Δ dilewati & di-log).
    """
    store = await Store.open(settings.db_url)
    clock = SystemClock()
    gamma = HttpGammaClient(settings.gamma_base_url)
    ws = HttpClobWS(
        settings.clob_wss_url,
        clock=clock,
        stale_ms=settings.ws_stale_seconds * 1000,
        app_ping_seconds=settings.ws_app_ping_seconds,
    )
    reader_endpoints = settings.rpc_endpoints()
    price_source = FailoverPriceSource.from_endpoints(
        rpc_urls=reader_endpoints,
        address=settings.chainlink_btcusd_source,
        clock=clock,
        source_label=f"chainlink:{settings.chainlink_feed_type}",
        timeout_sec=settings.polygon_rpc_timeout_seconds,
        max_staleness_sec=settings.chainlink_max_staleness_sec,
    )
    recorder = Recorder(store, ws, price_source, clock, mode=str(settings.mode))
    ws.set_event_sink(recorder.on_circuit_event)
    return store, gamma, recorder


async def main_async(
    settings: Settings,
    *,
    demo: bool = False,
    max_rounds: int | None = None,
    updates_per_round: int | None = None,
) -> int:
    """Wiring penuh + boot + loop readonly. Kembalikan jumlah ronde diproses."""
    run_boot_sequence()
    log = structlog.get_logger()
    log.info("boot_complete", mode=str(settings.mode), demo=demo)

    if str(settings.mode) != "readonly":
        # Fase pra-live: apa pun MODE-nya, runner ini TIDAK mengirim order.
        log.warning("non_readonly_mode_recording_only", mode=str(settings.mode))

    store: Store
    gamma: GammaClient
    recorder: Recorder
    if demo:
        store, gamma, recorder = await build_demo_runtime(settings)
    else:
        store, gamma, recorder = await build_runtime(settings)

    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown, log)

    try:
        return await run_readonly(
            settings=settings,
            gamma=gamma,
            recorder=recorder,
            max_rounds=max_rounds,
            updates_per_round=updates_per_round,
            shutdown=shutdown,
            logger=log,
        )
    finally:
        await store.close()
        log.info("store_closed")


def _install_signal_handlers(
    shutdown: asyncio.Event,
    logger: structlog.typing.FilteringBoundLogger,
) -> None:
    """Pasang handler SIGINT untuk graceful shutdown (best-effort)."""
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
    except (NotImplementedError, RuntimeError):
        # add_signal_handler tidak tersedia (mis. Windows) — andalkan
        # KeyboardInterrupt yang ditangani di main().
        logger.debug("signal_handler_unavailable")


def main(argv: list[str] | None = None) -> int:
    """Entrypoint sinkron: parse args, konfigurasi log, jalankan loop."""
    parser = argparse.ArgumentParser(
        prog="btcbot",
        description="5min-btc-polymarket readonly runner",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="jalankan dengan adapter fixture (tanpa jaringan)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="berhenti setelah N ronde",
    )
    parser.add_argument(
        "--updates-per-round", type=int, default=None, help="batas snapshot orderbook per ronde"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        asyncio.run(
            main_async(
                settings,
                demo=args.demo,
                max_rounds=args.max_rounds,
                updates_per_round=args.updates_per_round,
            )
        )
    except KeyboardInterrupt:
        structlog.get_logger().info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
