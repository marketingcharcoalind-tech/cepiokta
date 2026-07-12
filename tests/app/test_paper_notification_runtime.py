from decimal import Decimal
from pathlib import Path

import pytest

from btcbot.adapters.telegram import TelegramNotifier
from btcbot.app.paper_notification_runtime import (
    PaperNotificationConfig,
    build_paper_notifier,
)
from btcbot.config.settings import Settings


class Transport:
    async def send(self, text: str) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in (
        "NOTIFY_PNL_PER_TRADE",
        "NOTIFY_PNL_WINS",
        "NOTIFY_PNL_LOSSES",
        "NOTIFY_PROFIT_MILESTONE",
        "PROFIT_MILESTONE_STEP",
        "NOTIFY_NEW_EQUITY_HIGH",
        "ALERT_CONSEC_LOSSES",
        "ALERT_DRAWDOWN_PCT",
        "NOTIFY_ERRORS",
        "NOTIFY_ACTION_REQUIRED",
        "ERROR_DEDUP_WINDOW_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def test_config_maps_to_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFY_PNL_PER_TRADE", "false")
    monkeypatch.setenv("NOTIFY_PNL_LOSSES", "true")
    monkeypatch.setenv("PROFIT_MILESTONE_STEP", "25")
    monkeypatch.setenv("ERROR_DEDUP_WINDOW_SEC", "90")
    config = PaperNotificationConfig()
    policy = config.policy()
    assert policy.notify_wins is False
    assert policy.notify_losses is True
    assert policy.profit_milestone_step == Decimal("25")
    assert policy.error_dedup_window.total_seconds() == 90


def test_build_notifier_requires_enabled_telegram() -> None:
    with pytest.raises(RuntimeError, match="TELEGRAM_ENABLED"):
        build_paper_notifier(Settings(), transport=Transport())


def test_build_notifier_accepts_injected_transport() -> None:
    settings = Settings(
        telegram_enabled=True,
        telegram_bot_token="test-token",
        telegram_notify_chat_id="123",
    )
    notifier = build_paper_notifier(settings, transport=Transport())
    assert isinstance(notifier, TelegramNotifier)
