"""Skrip manual: baca SATU KALI harga BTC/USD dari Chainlink Data Feeds.

Verifikasi cepat bahwa CHAINLINK_BTCUSD_SOURCE + POLYGON_RPC_URL benar.
READ-ONLY (eth_call): TIDAK menulis apa pun ke chain, tanpa private key, tanpa order.

Pakai:
    uv run python scripts/read_chainlink_price.py

Konfigurasi via env (.env): POLYGON_RPC_URL, CHAINLINK_BTCUSD_SOURCE,
CHAINLINK_FEED_TYPE, CHAINLINK_MAX_STALENESS_SEC.
"""

from __future__ import annotations

import asyncio

from btcbot.adapters.chainlink import (
    ChainlinkDataFeed,
    PriceUnavailableError,
    Web3AggregatorReader,
)
from btcbot.adapters.clock import SystemClock
from btcbot.config.settings import get_settings


async def main() -> int:
    settings = get_settings()
    print(f"RPC     : {settings.polygon_rpc_url or '(kosong!)'}")
    print(f"Feed    : {settings.chainlink_btcusd_source or '(kosong!)'}")
    print(f"Type    : {settings.chainlink_feed_type}")

    reader = Web3AggregatorReader(
        rpc_url=settings.polygon_rpc_url,
        address=settings.chainlink_btcusd_source,
    )
    feed = ChainlinkDataFeed(
        reader=reader,
        clock=SystemClock(),
        source=f"chainlink:{settings.chainlink_feed_type}:{settings.chainlink_btcusd_source}",
        max_staleness_sec=settings.chainlink_max_staleness_sec,
    )

    try:
        tick = await feed.price_now()
    except PriceUnavailableError as exc:
        print(f"GAGAL  : {exc}")
        return 1

    print(f"PRICE  : ${tick.price}")
    print(f"ts     : {tick.ts.isoformat()}")
    print(f"roundId: {tick.round_id}")
    print(f"stale  : {tick.stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
