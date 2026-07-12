from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from btcbot.adapters.clock import SimClock
from btcbot.adapters.telegram import TelegramNotifier
from btcbot.app.paper_notification_runtime import (
    PaperNotificationConfig,
    build_notified_paper_runtime,
    build_paper_notifier,
)
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.models import OrderBook

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Transport:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True


class Books:
    async def get_orderbook(self, token_id: str) -> OrderBook:
        return OrderBook(token_id, NOW, [], [])


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


def _paper_settings() -> Settings:
    return Settings(
        mode=Mode.PAPER,
        live_confirmed="no",
        delta_threshold="50",
        telegram_enabled=True,
        telegram_bot_token="test-token",
        telegram_notify_chat_id="123",
    )


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
    notifier = build_paper_notifier(_paper_settings(), transport=Transport())
    assert isinstance(notifier, TelegramNotifier)


async def test_notified_runtime_routes_actionable_error_to_transport(tmp_path: Path) -> None:
    store = await Store.open(str(tmp_path / "paper.db"))
    transport = Transport()
    service = build_notified_paper_runtime(
        settings=_paper_settings(),
        store=store,
        books=Books(),
        clock=SimClock(NOW),
        transport=transport,
    )
    try:
        await service.start()
        emitted = await service.core.report_error(
            kind="WSS disconnected",
            detail="reconnect failed",
            remediation="check CLOB_WSS_URL and connectivity",
        )
        await service.stop(drain=True)
        assert emitted is True
        assert len(transport.sent) == 1
        assert "ACTION REQUIRED" in transport.sent[0]
        assert transport.closed is True
    finally:
        await store.close()
