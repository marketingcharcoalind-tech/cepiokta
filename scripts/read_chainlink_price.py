"""Skrip manual: baca SATU KALI harga BTC/USD dari Chainlink Data Feeds.

Verifikasi cepat bahwa CHAINLINK_BTCUSD_SOURCE + RPC (primary+fallbacks) benar.
READ-ONLY (eth_call): TIDAK menulis apa pun ke chain, tanpa private key, tanpa order.

Pakai:
    uv run python scripts/read_chainlink_price.py

Konfigurasi via env (.env): POLYGON_RPC_URL, POLYGON_RPC_FALLBACKS,
POLYGON_RPC_TIMEOUT_SECONDS, CHAINLINK_BTCUSD_SOURCE, CHAINLINK_FEED_TYPE,
CHAINLINK_MAX_STALENESS_SEC.
"""

from __future__ import annotations

import asyncio

from btcbot.adapters.chainlink import FailoverPriceSource, PriceUnavailableError
from btcbot.adapters.clock import SystemClock
from btcbot.config.settings import get_settings


async def main() -> int:
    settings = get_settings()
    endpoints = settings.rpc_endpoints()
    print(f"RPC     : {endpoints or '(kosong!)'}")
    print(f"Feed    : {settings.chainlink_btcusd_source or '(kosong!)'}")
    print(f"Type    : {settings.chainlink_feed_type}")

    if not endpoints:
        print("GAGAL  : tidak ada RPC (set POLYGON_RPC_URL / POLYGON_RPC_FALLBACKS)")
        return 1

    feed = FailoverPriceSource.from_endpoints(
        rpc_urls=endpoints,
        address=settings.chainlink_btcusd_source,
        clock=SystemClock(),
        source_label=f"chainlink:{settings.chainlink_feed_type}",
        timeout_sec=settings.polygon_rpc_timeout_seconds,
        max_staleness_sec=settings.chainlink_max_staleness_sec,
    )

    try:
        tick = await feed.price_now()
    except PriceUnavailableError as exc:
        print(f"GAGAL  : {exc}")
        return 1

    print(f"PRICE  : ${tick.price}")
    print(f"ts     : {tick.ts.isoformat()}")
    print(f"source : {tick.source}")
    print(f"roundId: {tick.round_id}")
    print(f"stale  : {tick.stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
