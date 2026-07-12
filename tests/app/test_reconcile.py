from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from btcbot.adapters.clock import SimClock
from btcbot.adapters.telegram import BotEvent
from btcbot.app.reconcile import (
    PaperOrderRecord,
    PaperReconciler,
    PositionSnapshot,
    ReconciliationSnapshot,
)
from btcbot.domain.models import Fill, OrderRequest, Outcome, RoundResult
from btcbot.risk.manager import RiskLimits, RiskManager

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Events:
    def __init__(self) -> None:
        self.events: list[BotEvent] = []

    async def emit(self, event: BotEvent) -> None:
        self.events.append(event)


def _risk() -> RiskManager:
    limits = RiskLimits(Decimal("5"), Decimal("10"), Decimal("5"), 5, Decimal("50"), 30)
    return RiskManager(limits, SimClock(NOW))


def _snapshot() -> ReconciliationSnapshot:
    request = OrderRequest("paper-1", "up", "BUY", Decimal("0.96"), Decimal("2"), "FOK")
    fill = Fill("paper:paper-1", "up", Decimal("0.96"), Decimal("2"), NOW)
    result = RoundResult(
        round_no=1,
        side_taken="UP",
        entry_price=Decimal("0.9607"),
        size=Decimal("2"),
        hedge_cost=Decimal("0"),
        settled=Decimal("2"),
        pnl=Decimal("0.0786"),
        balance_after=Decimal("500.0786"),
    )
    return ReconciliationSnapshot(
        round_no=1,
        resolved_outcome=Outcome.UP,
        orders=(PaperOrderRecord(request, "paper:paper-1", "FILLED", (fill,)),),
        positions=(PositionSnapshot("up", Outcome.UP, Decimal("2")),),
        result=result,
        round_start_balance=Decimal("500"),
        actual_balance=Decimal("500.0786"),
        ts=NOW,
    )


async def test_clean_round_reconciles_without_alert_or_freeze() -> None:
    risk = _risk()
    events = Events()
    report = await PaperReconciler(risk, events).reconcile(_snapshot())
    assert report.ok
    assert report.mismatches == ()
    assert not risk.killed
    assert events.events == []


async def test_balance_mismatch_freezes_and_alerts_critical() -> None:
    risk = _risk()
    events = Events()
    snapshot = replace(_snapshot(), actual_balance=Decimal("499"))
    report = await PaperReconciler(risk, events).reconcile(snapshot)
    assert not report.ok
    assert "balance_after" in report.mismatches
    assert "round_pnl" in report.mismatches
    assert risk.killed
    assert risk.kill_reason == "reconciliation_mismatch"
    assert len(events.events) == 1
    assert events.events[0].severity.value == "critical"


async def test_unknown_order_fill_is_detected() -> None:
    risk = _risk()
    events = Events()
    snapshot = _snapshot()
    bad_fill = replace(snapshot.orders[0].fills[0], order_id="unknown")
    bad_order = replace(snapshot.orders[0], fills=(bad_fill,))
    report = await PaperReconciler(risk, events).reconcile(replace(snapshot, orders=(bad_order,)))
    assert any(reason.startswith("unknown_order_fill") for reason in report.mismatches)


async def test_overfill_and_position_mismatch_are_detected() -> None:
    snapshot = _snapshot()
    overfill = replace(snapshot.orders[0].fills[0], size=Decimal("3"))
    bad_order = replace(snapshot.orders[0], fills=(overfill,))
    report = await PaperReconciler(_risk(), Events()).reconcile(
        replace(snapshot, orders=(bad_order,))
    )
    assert any(reason.startswith("overfill") for reason in report.mismatches)
    assert any(reason.startswith("position_size") for reason in report.mismatches)


async def test_settlement_uses_gamma_winning_outcome() -> None:
    snapshot = replace(_snapshot(), resolved_outcome=Outcome.DOWN)
    report = await PaperReconciler(_risk(), Events()).reconcile(snapshot)
    assert "settlement_payout" in report.mismatches


async def test_fill_token_mismatch_is_detected() -> None:
    snapshot = _snapshot()
    wrong_fill = replace(snapshot.orders[0].fills[0], token_id="down")
    wrong_order = replace(snapshot.orders[0], fills=(wrong_fill,))
    report = await PaperReconciler(_risk(), Events()).reconcile(
        replace(snapshot, orders=(wrong_order,))
    )
    assert any(reason.startswith("fill_token") for reason in report.mismatches)
