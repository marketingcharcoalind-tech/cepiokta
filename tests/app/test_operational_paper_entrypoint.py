from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.app.operational_paper_entrypoint import (
    _assert_execution_opt_in,
    _assert_smoke_safe,
    _select_upcoming_round,
)
from btcbot.config.settings import Mode, Settings
from btcbot.domain.models import MarketStatus, RoundMeta

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _safe_settings() -> Settings:
    return Settings(
        mode=Mode.PAPER,
        live_confirmed="no",
        db_url="sqlite+aiosqlite:///./paper.db",
        delta_threshold="50",
        telegram_enabled=True,
        telegram_bot_token="test-token",
        telegram_notify_chat_id="123",
        paper_starting_balance=Decimal("500"),
    )


def _meta(start: datetime) -> RoundMeta:
    return RoundMeta(
        "market",
        "condition",
        "btc-updown-5m-1",
        "up",
        "down",
        start,
        start + timedelta(minutes=5),
        Decimal("0.01"),
        Decimal("1"),
        MarketStatus.OPEN,
    )


def test_smoke_safety_accepts_paper_without_credentials() -> None:
    _assert_smoke_safe(_safe_settings())


def test_smoke_safety_rejects_non_paper_mode() -> None:
    settings = _safe_settings().model_copy(update={"mode": Mode.READONLY})
    with pytest.raises(RuntimeError, match="MODE=paper"):
        _assert_smoke_safe(settings)


def test_smoke_safety_rejects_live_confirmation() -> None:
    settings = _safe_settings().model_copy(update={"live_confirmed": "yes"})
    with pytest.raises(RuntimeError, match="LIVE_CONFIRMED=no"):
        _assert_smoke_safe(settings)


def test_smoke_safety_rejects_any_private_credentials() -> None:
    settings = _safe_settings().model_copy(update={"clob_api_key": "forbidden"})
    with pytest.raises(RuntimeError, match="credentials"):
        _assert_smoke_safe(settings)


def test_smoke_safety_rejects_non_paper_database() -> None:
    settings = _safe_settings().model_copy(update={"db_url": "sqlite+aiosqlite:///./btcbot.db"})
    with pytest.raises(RuntimeError, match=r"paper\.db"):
        _assert_smoke_safe(settings)


def test_execution_is_off_without_opt_in() -> None:
    _assert_execution_opt_in(
        enabled=False,
        confirmation="",
        max_start_lag_seconds=300,
        full_round=False,
    )


def test_execution_requires_exact_confirmation() -> None:
    with pytest.raises(RuntimeError, match="PAPER_ONLY"):
        _assert_execution_opt_in(
            enabled=True,
            confirmation="wrong",
            max_start_lag_seconds=2,
            full_round=True,
        )


def test_execution_rejects_late_start_allowance() -> None:
    with pytest.raises(RuntimeError, match="<= 2"):
        _assert_execution_opt_in(
            enabled=True,
            confirmation="PAPER_ONLY",
            max_start_lag_seconds=3,
            full_round=True,
        )


def test_execution_requires_full_round() -> None:
    with pytest.raises(RuntimeError, match="full-round"):
        _assert_execution_opt_in(
            enabled=True,
            confirmation="PAPER_ONLY",
            max_start_lag_seconds=2,
            full_round=False,
        )


def test_execution_accepts_triple_opt_in() -> None:
    _assert_execution_opt_in(
        enabled=True,
        confirmation="PAPER_ONLY",
        max_start_lag_seconds=2,
        full_round=True,
    )


def test_select_upcoming_round_chooses_nearest_future() -> None:
    selected = _select_upcoming_round(
        [_meta(NOW + timedelta(minutes=10)), _meta(NOW + timedelta(minutes=5))],
        NOW,
    )
    assert selected.start_time == NOW + timedelta(minutes=5)


def test_select_upcoming_round_rejects_only_active_round() -> None:
    with pytest.raises(RuntimeError, match="upcoming"):
        _select_upcoming_round([_meta(NOW - timedelta(minutes=1))], NOW)
