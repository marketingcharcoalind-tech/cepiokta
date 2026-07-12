from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.adapters.clock import SimClock
from btcbot.adapters.telegram import BotEvent, Severity
from btcbot.app.paper_notifications import NotificationPolicy, PaperNotificationTracker
from btcbot.domain.models import RoundResult

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Sink:
    def __init__(self) -> None:
        self.events: list[BotEvent] = []

    async def emit(self, event: BotEvent) -> None:
        self.events.append(event)


def _result(round_no: int, pnl: str, balance: str) -> RoundResult:
    return RoundResult(
        round_no=round_no,
        side_taken="UP",
        entry_price=Decimal("0.96"),
        size=Decimal("5"),
        hedge_cost=Decimal("0"),
        settled=Decimal("5"),
        pnl=Decimal(pnl),
        balance_after=Decimal(balance),
    )


async def test_win_emits_trade_and_equity_high() -> None:
    sink = Sink()
    tracker = PaperNotificationTracker(
        starting_balance=Decimal("500"), sink=sink, clock=SimClock(NOW)
    )
    await tracker.on_result(_result(1, "1", "501"))
    assert [event.kind for event in sink.events] == ["trade_win", "equity_high"]


async def test_loss_streak_and_drawdown_alert_once() -> None:
    sink = Sink()
    tracker = PaperNotificationTracker(
        starting_balance=Decimal("500"),
        sink=sink,
        clock=SimClock(NOW),
        policy=NotificationPolicy(
            alert_consecutive_losses=2,
            alert_drawdown_pct=Decimal("1"),
        ),
    )
    await tracker.on_result(_result(1, "-3", "497"))
    await tracker.on_result(_result(2, "-3", "494"))
    kinds = [event.kind for event in sink.events]
    assert kinds.count("consec_loss") == 1
    assert kinds.count("drawdown") == 1


async def test_profit_milestone_is_not_repeated() -> None:
    sink = Sink()
    tracker = PaperNotificationTracker(
        starting_balance=Decimal("500"),
        sink=sink,
        clock=SimClock(NOW),
        policy=NotificationPolicy(profit_milestone_step=Decimal("10")),
    )
    await tracker.on_result(_result(1, "11", "511"))
    await tracker.on_result(_result(2, "1", "512"))
    assert [event.kind for event in sink.events].count("profit_milestone") == 1


async def test_actionable_error_is_deduplicated_then_released() -> None:
    sink = Sink()
    clock = SimClock(NOW)
    tracker = PaperNotificationTracker(
        starting_balance=Decimal("500"),
        sink=sink,
        clock=clock,
        policy=NotificationPolicy(error_dedup_window=timedelta(seconds=60)),
    )
    first = await tracker.error(
        kind="WSS disconnected",
        detail="reconnect failed",
        remediation="check CLOB_WSS_URL and connectivity",
        severity=Severity.CRITICAL,
    )
    duplicate = await tracker.error(
        kind="WSS disconnected",
        detail="reconnect failed",
        remediation="check CLOB_WSS_URL and connectivity",
    )
    clock.advance(timedelta(seconds=61))
    released = await tracker.error(
        kind="WSS disconnected",
        detail="reconnect failed",
        remediation="check CLOB_WSS_URL and connectivity",
    )
    assert (first, duplicate, released) == (True, False, True)
    assert len(sink.events) == 2
    assert "ACTION REQUIRED" in sink.events[0].text
