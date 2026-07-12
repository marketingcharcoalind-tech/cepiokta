from decimal import Decimal

import pytest

from btcbot.app.operational_paper_entrypoint import _assert_smoke_safe
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
    settings = _safe_settings().model_copy(update={"db_url": "sqlite+aiosqlite:///./btcbot.db"})
    with pytest.raises(RuntimeError, match=r"paper\.db"):
        _assert_smoke_safe(settings)
