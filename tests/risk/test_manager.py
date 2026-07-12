from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.domain.models import OrderRequest
from btcbot.risk.manager import (
    Allow,
    CircuitReason,
    RiskAction,
    RiskLimits,
    RiskManager,
    RiskOrder,
    RiskState,
    Veto,
)

NOW = datetime(2026, 7, 12, 16, 30, tzinfo=UTC)


class NaiveClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12)


def _limits(**overrides: object) -> RiskLimits:
    values: dict[str, object] = {
        "max_notional_round": Decimal("5"),
        "max_open_exposure": Decimal("10"),
        "max_daily_loss_pct": Decimal("5"),
        "max_consec_losses": 5,
        "min_balance": Decimal("50"),
        "max_orders_per_min": 3,
    }
    values.update(overrides)
    return RiskLimits(**values)  # type: ignore[arg-type]


def _state(**overrides: object) -> RiskState:
    values: dict[str, object] = {
        "balance": Decimal("100"),
        "day_start_balance": Decimal("100"),
        "open_exposure": Decimal("0"),
        "round_notional": Decimal("0"),
        "consecutive_losses": 0,
        "recent_order_timestamps": (),
    }
    values.update(overrides)
    return RiskState(**values)  # type: ignore[arg-type]


def _order(
    *,
    price: str = "0.96",
    size: str = "2",
    action: RiskAction = RiskAction.ENTRY,
) -> RiskOrder:
    request = OrderRequest(
        client_id="paper-1",
        token_id="token-up",
        side="BUY",
        price=Decimal(price),
        size=Decimal(size),
        order_type="FOK",
    )
    return RiskOrder(request=request, round_no=1783520100, action=action)


def _manager(limits: RiskLimits | None = None) -> RiskManager:
    return RiskManager(limits or _limits(), SimClock(NOW))


def _reason(decision: Allow | Veto) -> str:
    assert isinstance(decision, Veto)
    return decision.reason


def test_allows_order_inside_every_limit():
    assert isinstance(_manager().check(_order(), _state()), Allow)


def test_vetoes_round_notional_above_limit_but_allows_exact_limit():
    manager = _manager()
    exact = manager.check(_order(price="1", size="1"), _state(round_notional=Decimal("4")))
    above = manager.check(
        _order(price="1", size="1.01"), _state(round_notional=Decimal("4"))
    )
    assert isinstance(exact, Allow)
    assert _reason(above) == "max_notional_round"


def test_vetoes_projected_open_exposure_above_limit():
    decision = _manager().check(
        _order(price="1", size="2"), _state(open_exposure=Decimal("9"))
    )
    assert _reason(decision) == "max_open_exposure"


@pytest.mark.parametrize("size", ["5.0001", "6", "50", "500"])
def test_invariant_order_above_notional_limit_never_passes(size: str):
    decision = _manager().check(_order(price="1", size=size), _state())
    assert isinstance(decision, Veto)
    assert decision.reason == "max_notional_round"


def test_daily_loss_at_limit_latches_automatic_kill():
    manager = _manager()
    decision = manager.check(_order(), _state(balance=Decimal("95")))
    assert _reason(decision) == "kill_switch:max_daily_loss"
    assert manager.killed
    assert manager.should_halt()


def test_balance_below_floor_latches_automatic_kill():
    manager = _manager()
    decision = manager.check(_order(), _state(balance=Decimal("49.99")))
    assert _reason(decision) == "kill_switch:min_balance"
    assert manager.kill_reason == "min_balance"


def test_consecutive_losses_at_limit_latch_automatic_kill():
    manager = _manager()
    decision = manager.check(_order(), _state(consecutive_losses=5))
    assert _reason(decision) == "kill_switch:max_consec_losses"


def test_rate_limit_uses_rolling_utc_minute():
    timestamps = (
        NOW - timedelta(seconds=59),
        NOW - timedelta(seconds=30),
        NOW - timedelta(seconds=1),
    )
    decision = _manager().check(_order(), _state(recent_order_timestamps=timestamps))
    assert _reason(decision) == "max_orders_per_min"


def test_order_exactly_sixty_seconds_old_is_outside_rate_window():
    timestamps = (
        NOW - timedelta(seconds=60),
        NOW - timedelta(seconds=30),
        NOW - timedelta(seconds=1),
    )
    assert isinstance(
        _manager().check(_order(), _state(recent_order_timestamps=timestamps)), Allow
    )


def test_pause_blocks_entry_but_allows_exit():
    manager = _manager()
    manager.pause()
    assert _reason(manager.check(_order(), _state())) == "paused"
    assert isinstance(manager.check(_order(action=RiskAction.EXIT), _state()), Allow)


def test_resume_only_clears_manual_pause():
    manager = _manager()
    manager.pause()
    manager.on_event(CircuitReason.PRICE_STALE)
    manager.resume()
    assert not manager.paused
    assert _reason(manager.check(_order(), _state())) == "circuit_breaker:price_stale"


@pytest.mark.parametrize(
    "reason",
    [
        CircuitReason.WSS_DISCONNECTED,
        CircuitReason.WSS_RECONNECTING,
        CircuitReason.PRICE_STALE,
        CircuitReason.CLOCK_DRIFT,
        CircuitReason.ABNORMAL_SPREAD,
        CircuitReason.LOW_LIQUIDITY,
        CircuitReason.LATENCY_BREACH,
    ],
)
def test_each_circuit_breaker_blocks_entry_and_can_clear(reason: CircuitReason):
    manager = _manager()
    manager.on_event(reason)
    assert isinstance(manager.check(_order(), _state()), Veto)
    assert isinstance(manager.check(_order(action=RiskAction.EXIT), _state()), Allow)
    manager.on_event(reason, active=False)
    assert isinstance(manager.check(_order(), _state()), Allow)


def test_reconciliation_mismatch_is_fatal_and_cannot_be_resumed():
    manager = _manager()
    manager.on_event(CircuitReason.RECONCILIATION_MISMATCH)
    manager.resume()
    manager.on_event(CircuitReason.RECONCILIATION_MISMATCH, active=False)
    assert _reason(manager.check(_order(action=RiskAction.EXIT), _state())) == (
        "kill_switch:reconciliation_mismatch"
    )


def test_manual_kill_blocks_all_actions():
    manager = _manager()
    manager.kill("operator_request")
    assert _reason(manager.check(_order(), _state())) == "kill_switch:operator_request"
    assert _reason(manager.check(_order(action=RiskAction.EXIT), _state())) == (
        "kill_switch:operator_request"
    )


def test_invalid_order_and_state_fail_closed():
    assert _reason(_manager().check(_order(size="0"), _state())) == "invalid_order_value"
    assert _reason(
        _manager().check(_order(), _state(open_exposure=Decimal("-1")))
    ) == "invalid_risk_state"


def test_naive_or_future_rate_limit_timestamp_fails_closed():
    naive = datetime(2026, 7, 12, 16, 29)
    assert _reason(
        _manager().check(_order(), _state(recent_order_timestamps=(naive,)))
    ) == "invalid_order_timestamp"
    assert _reason(
        _manager().check(
            _order(), _state(recent_order_timestamps=(NOW + timedelta(seconds=1),))
        )
    ) == "future_order_timestamp"


def test_naive_clock_is_rejected():
    manager = RiskManager(_limits(), NaiveClock())
    with pytest.raises(ValueError, match="timezone-aware"):
        manager.check(_order(), _state())


def test_invalid_limits_fail_fast():
    with pytest.raises(ValueError, match="non-negative"):
        _limits(max_open_exposure=Decimal("-1"))
    with pytest.raises(ValueError, match="positive"):
        _limits(max_orders_per_min=0)
