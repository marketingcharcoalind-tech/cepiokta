"""Bounded real-adapter smoke entrypoint for the operational paper pipeline.

The command connects only to public/read-only Gamma, CLOB market WebSocket, and
Chainlink sources. Paper execution is off by default and requires an explicit
flag plus a confirmation phrase. No signer, private API, CLOB REST order, or
live path exists here.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from btcbot.adapters.chainlink import FailoverPriceSource
from btcbot.adapters.clob_ws import HttpClobWS
from btcbot.adapters.clock import SystemClock
from btcbot.adapters.gamma import HttpGammaClient
from btcbot.app.cli import configure_logging
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

_EXECUTION_CONFIRMATION = "PAPER_ONLY"
_MAX_EXECUTION_START_LAG_SECONDS = 2.0


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


def _assert_execution_opt_in(
    *,
    enabled: bool,
    confirmation: str,
    max_start_lag_seconds: float,
) -> None:
    """Require deliberate paper-only opt-in and a trustworthy start-price window."""
    if not enabled:
        return
    if confirmation != _EXECUTION_CONFIRMATION:
        raise RuntimeError("paper execution requires confirmation PAPER_ONLY")
    if max_start_lag_seconds > _MAX_EXECUTION_START_LAG_SECONDS:
        raise RuntimeError("paper execution requires max start lag <= 2 seconds")


async def run_bounded_smoke(
    settings: Settings,
    *,
    max_ticks: int,
    max_start_lag_seconds: float = 2.0,
    paper_execution_enabled: bool = False,
    execution_confirmation: str = "",
) -> tuple[int, int, bool, str | None]:
    """Run one bounded market observation and return report fields."""
    _assert_smoke_safe(settings)
    _assert_execution_opt_in(
        enabled=paper_execution_enabled,
        confirmation=execution_confirmation,
        max_start_lag_seconds=max_start_lag_seconds,
    )
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
            paper_execution_enabled=paper_execution_enabled,
        ),
    )
    await service.start()
    try:
        report = await loop.run_round(meta, max_ticks=max_ticks)
        return report.round_no, report.ticks, report.settled, report.skipped_reason
    finally:
        await resources.close()


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded operational paper smoke")
    parser.add_argument("--max-ticks", type=int, default=3)
    parser.add_argument("--max-start-lag-seconds", type=float, default=2.0)
    parser.add_argument("--enable-paper-execution", action="store_true")
    parser.add_argument("--confirm-paper-execution", default="")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    result = await run_bounded_smoke(
        settings,
        max_ticks=args.max_ticks,
        max_start_lag_seconds=args.max_start_lag_seconds,
        paper_execution_enabled=args.enable_paper_execution,
        execution_confirmation=args.confirm_paper_execution,
    )
    round_no, ticks, settled, reason = result
    execution = "enabled" if args.enable_paper_execution else "disabled"
    print(  # noqa: T201 - intentional CLI smoke summary
        f"SMOKE round={round_no} ticks={ticks} settled={settled} "
        f"reason={reason or 'none'} execution={execution}"
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
