"""Bounded real-adapter smoke entrypoint for the operational paper pipeline.

The command connects only to public/read-only Gamma, CLOB market WebSocket, and
Chainlink sources. Smoke mode persists round/signal observations to ``paper.db``
but deliberately does not call PaperRunner decisions, so it creates no paper
orders or fills. There is no signer, private API, CLOB REST order, or live path.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from btcbot.adapters.chainlink import FailoverPriceSource
from btcbot.adapters.clob_ws import HttpClobWS
from btcbot.adapters.clock import SystemClock
from btcbot.adapters.gamma import HttpGammaClient
from btcbot.app.operational_paper import (
    LiveBookCache,
    OperationalLoopConfig,
    OperationalPaperLoop,
)
from btcbot.app.paper_notification_runtime import (
    NotifiedPaperRuntime,
    PaperNotificationConfig,
    build_notified_paper_runtime,
)
from btcbot.config.settings import Mode, Settings, get_settings
from btcbot.data.store import Store


@dataclass(slots=True)
class SmokeResources:
    """Owned resources for one bounded smoke run."""

    store: Store
    gamma: HttpGammaClient
    stream: HttpClobWS
    service: NotifiedPaperRuntime

    async def close(self) -> None:
        self.stream.close()
        await self.service.stop(drain=True)
        await self.gamma.aclose()
        await self.store.close()


def _assert_smoke_safe(settings: Settings) -> None:
    if settings.mode is not Mode.PAPER:
        raise RuntimeError("bounded smoke requires MODE=paper")
    if settings.live_confirmed == "yes":
        raise RuntimeError("bounded smoke requires LIVE_CONFIRMED=no")
    if any(
        value.strip()
        for value in (
            settings.wallet_private_key,
            settings.clob_api_key,
            settings.clob_api_secret,
            settings.clob_api_passphrase,
        )
    ):
        raise RuntimeError("bounded smoke refuses configured wallet/CLOB credentials")
    if "paper.db" not in settings.db_url:
        raise RuntimeError("bounded smoke requires DB_URL pointing to paper.db")


async def run_bounded_smoke(
    settings: Settings,
    *,
    max_ticks: int,
    max_start_lag_seconds: float = 2.0,
) -> tuple[int, int, bool, str | None]:
    """Run one bounded read-only market observation and return report fields."""
    _assert_smoke_safe(settings)
    if max_ticks <= 0:
        raise ValueError("max_ticks must be positive")

    clock = SystemClock()
    store = await Store.open(settings.db_url)
    gamma = HttpGammaClient(settings.gamma_base_url, clock=clock)
    meta = await gamma.discover_active_round()
    cache = LiveBookCache(meta.token_id_up, meta.token_id_down)
    stream = HttpClobWS(
        settings.clob_wss_url,
        clock=clock,
        stale_ms=settings.ws_stale_seconds * 1000,
        app_ping_seconds=settings.ws_app_ping_seconds,
    )
    price_source = FailoverPriceSource.from_endpoints(
        rpc_urls=settings.rpc_endpoints(),
        address=settings.chainlink_btcusd_source,
        clock=clock,
        source_label=f"chainlink:{settings.chainlink_feed_type}",
        timeout_sec=settings.polygon_rpc_timeout_seconds,
        max_staleness_sec=settings.chainlink_max_staleness_sec,
    )
    service = build_notified_paper_runtime(
        settings=settings,
        store=store,
        books=cache,
        clock=clock,
        notification_config=PaperNotificationConfig(),
    )
    resources = SmokeResources(store, gamma, stream, service)
    loop = OperationalPaperLoop(
        settings=settings,
        gamma=gamma,
        stream=stream,
        price_source=price_source,
        store=store,
        clock=clock,
        runtime=service.core,
        books=cache,
        config=OperationalLoopConfig(
            max_start_lag_seconds=max_start_lag_seconds,
            paper_execution_enabled=False,
        ),
    )
    await service.start()
    try:
        report = await loop.run_round(meta, max_ticks=max_ticks)
        return report.round_no, report.ticks, report.settled, report.skipped_reason
    finally:
        await resources.close()


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded paper market-data smoke")
    parser.add_argument("--max-ticks", type=int, default=3)
    parser.add_argument("--max-start-lag-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    result = await run_bounded_smoke(
        get_settings(),
        max_ticks=args.max_ticks,
        max_start_lag_seconds=args.max_start_lag_seconds,
    )
    round_no, ticks, settled, reason = result
    print(  # noqa: T201 - intentional CLI smoke summary
        f"SMOKE round={round_no} ticks={ticks} settled={settled} "
        f"reason={reason or 'none'} execution=disabled"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
