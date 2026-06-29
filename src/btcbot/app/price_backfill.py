"""app/price_backfill.py — backfill trajektori harga utk ronde lama (Bug B9).

Untuk ronde resolved yang TIDAK punya sampel harga cukup (data lama: ~1 baris
``signals``/ronde), baca riwayat Chainlink **Data Feeds** on-chain
(``getRoundData`` walk via :class:`~btcbot.adapters.chainlink.ChainlinkHistory`)
di jendela ``[window_start, window_end]`` lalu tulis baris harga (``price_now``+``ts``)
ke ``signals`` dengan ``mode='backfill'`` (provenance). Idempoten (skip ronde yang
sudah punya sampel) & best-effort (skip ronde gagal, log). READ-ONLY.

Pakai:
    uv run python -m btcbot.app.price_backfill [--db PATH] [--limit N]

Logika walk (``backfill_round``/``backfill_all``) murni terhadap protokol
:class:`PriceHistory` sehingga dapat diuji tanpa jaringan.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import structlog

from btcbot.config.settings import get_settings
from btcbot.data.store import Store
from btcbot.domain.models import Signal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from btcbot.domain.models import PriceTick, Round

_log = structlog.get_logger("btcbot.price_backfill")
_ZERO = Decimal("0")

# Ronde dianggap "sudah punya trajektori" bila signals >= ambang ini → skip.
_MIN_EXISTING_SAMPLES = 2


class PriceHistory(Protocol):
    """Sumber riwayat harga: yield :class:`PriceTick` di jendela ``[start, end]``."""

    def iter_window(self, start: datetime, end: datetime) -> AsyncIterator[PriceTick]:
        """Iterasi harga historis (kronologis) dalam jendela."""
        ...


def _leader(delta: Decimal) -> str:
    if delta > 0:
        return "UP"
    if delta < 0:
        return "DOWN"
    return ""


async def backfill_round(
    store: Store,
    history: PriceHistory,
    rnd: Round,
    *,
    mode: str = "backfill",
    min_existing: int = _MIN_EXISTING_SAMPLES,
) -> int:
    """Backfill trajektori harga satu ronde. Kembalikan jumlah baris ditulis.

    Idempoten: bila ronde sudah punya ``>= min_existing`` sampel → skip (0).
    """
    existing = await store.get_signals(rnd.round_no)
    if len(existing) >= min_existing:
        return 0
    written = 0
    async for tick in history.iter_window(rnd.window_start, rnd.window_end):
        delta = tick.price - rnd.start_price
        await store.insert_signal(
            Signal(
                round_no=rnd.round_no,
                ts=tick.ts,
                price_now=tick.price,
                delta=delta,
                time_left_sec=(rnd.window_end - tick.ts).total_seconds(),
                p_win=_ZERO,
                leader=_leader(delta),
                ask_win=_ZERO,
                net_edge=_ZERO,
            ),
            mode=mode,
        )
        written += 1
    return written


async def backfill_all(
    store: Store,
    history: PriceHistory,
    *,
    limit: int | None = None,
    mode: str = "backfill",
) -> tuple[int, int]:
    """Backfill semua ronde resolved yang kekurangan sampel. Best-effort.

    Returns:
        ``(rounds_backfilled, samples_written)``.
    """
    rounds = await store.get_resolved_rounds(limit=limit)
    rounds_done = 0
    samples = 0
    for rnd in rounds:
        try:
            n = await backfill_round(store, history, rnd, mode=mode)
        except Exception as exc:
            _log.warning("backfill_round_skip", round_no=rnd.round_no, error=str(exc))
            continue
        if n > 0:
            rounds_done += 1
            samples += n
            _log.info("backfill_round_done", round_no=rnd.round_no, samples=n)
    return rounds_done, samples


async def run_backfill(settings: object, *, limit: int | None = None) -> tuple[int, int]:
    """Wiring nyata: bangun ChainlinkHistory (web3) + Store, lalu backfill_all."""
    # Import berat (web3) on-demand agar modul tetap ringan/diuji tanpa DLL native.
    from btcbot.adapters.chainlink import ChainlinkHistory  # noqa: PLC0415
    from btcbot.config.settings import Settings  # noqa: PLC0415

    assert isinstance(settings, Settings)
    endpoints = settings.rpc_endpoints()
    if not endpoints:
        msg = "POLYGON_RPC_URL/FALLBACKS kosong — tak bisa baca riwayat Chainlink"
        raise ValueError(msg)
    history = ChainlinkHistory(
        endpoints[0],
        settings.chainlink_btcusd_source,
        timeout_sec=settings.polygon_rpc_timeout_seconds,
    )
    store = await Store.open(settings.db_url)
    try:
        return await backfill_all(store, history, limit=limit)
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> int:
    """Entry-point: ``python -m btcbot.app.price_backfill [--db] [--limit]``."""
    parser = argparse.ArgumentParser(
        prog="btcbot-price-backfill",
        description="Backfill trajektori harga BTC/USD (Chainlink Data Feeds) ke signals.",
    )
    parser.add_argument("--db", default=None, help="path/URL DB (default: Settings.db_url)")
    parser.add_argument("--limit", type=int, default=None, help="batasi N ronde")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.db:
        settings = settings.model_copy(update={"db_url": args.db})

    rounds_done, samples = asyncio.run(run_backfill(settings, limit=args.limit))
    _log.info("backfill_complete", rounds=rounds_done, samples=samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
