from decimal import Decimal

import pytest

from btcbot.app.operational_paper_entrypoint import (
    _assert_execution_opt_in,
    _assert_smoke_safe,
)
from btcbot.config.settings import Mode, Settings


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
    settings = _safe_settings().model_copy({"db_url": "sqlite+aiosqlite:///./btcbot.db"})
    with pytest.raises(RuntimeError, match=r"paper\.db"):
        _assert_smoke_safe(settings)


def test_execution_is_off_without_opt_in() -> None:
    _assert_execution_opt_in(enabled=False, confirmation="", max_start_lag_seconds=300)


def test_execution_requires_exact_confirmation() -> None:
    with pytest.raises(RuntimeError, match="PAPER_ONLY"):
        _assert_execution_opt_in(
            enabled=True,
            confirmation="wrong",
            max_start_lag_seconds=2,
        )


def test_execution_rejects_late_start_allowance() -> None:
    with pytest.raises(RuntimeError, match="<= 2"):
        _assert_execution_opt_in(
            enabled=True,
            confirmation="PAPER_ONLY",
            max_start_lag_seconds=3,
        )


def test_execution_accepts_double_opt_in_at_strict_start() -> None:
    _assert_execution_opt_in(
        enabled=True,
        confirmation="PAPER_ONLY",
        max_start_lag_seconds=2,
    )
