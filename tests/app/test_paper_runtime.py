from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from btcbot.adapters.clock import SimClock
from btcbot.app.control import MenuButton
from btcbot.app.paper_runtime import build_operational_paper_runtime
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round, RoundStatus, Signal
from btcbot.domain.strategy import MarketBook

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Books:
    def __init__(self, market: MarketBook) -> None:
        self.market = market

    async def get_orderbook(self, token_id: str) -> OrderBook:
        return self.market.up if token_id == "up" else self.market.down


class API:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, tuple[tuple[MenuButton, ...], ...], bool]] = []

    async def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        return []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: tuple[tuple[MenuButton, ...], ...] = (),
        *,
        persistent: bool = False,
    ) -> None:
        self.messages.append((chat_id, text, keyboard, persistent))

    async def answer_callback(self, callback_id: str, text: str) -> None:
        return None

    async def close(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        mode=Mode.PAPER,
        live_confirmed="no",
        delta_threshold="50",
        t_entry_sec=60,
        min_price=Decimal("0.96"),
        max_price=Decimal("0.99"),
        paper_starting_balance=Decimal("500"),
        telegram_enabled=True,
        telegram_bot_token="test-token",
        telegram_notify_chat_id="123",
        telegram_allowed_chat_ids="123",
    )


def _market() -> MarketBook:
    return MarketBook(
        up=OrderBook("up", NOW, [], [BookLevel(Decimal("0.96"), Decimal("100"))]),
        down=OrderBook("down", NOW, [], [BookLevel(Decimal("0.04"), Decimal("100"))]),
    )


def _round(outcome: Outcome | None = None) -> Round:
    return Round(
        "condition",
        1,
        "up",
        "down",
        NOW - timedelta(minutes=5),
        NOW,
        Decimal("100000"),
        Decimal("0.01"),
        Decimal("1"),
        RoundStatus.RESOLVED if outcome else RoundStatus.ACTIVE,
        outcome,
    )


def _signal() -> Signal:
    return Signal(
        1,
        NOW,
        Decimal("100100"),
        Decimal("100"),
        30.0,
        Decimal("0.999"),
        "UP",
        Decimal("0.96"),
        Decimal("0.03"),
    )


async def test_shared_risk_pauses_the_oms(tmp_path: Path) -> None:
    store = await Store.open(str(tmp_path / "paper.db"))
    market = _market()
    runtime = build_operational_paper_runtime(
        settings=_settings(),
        store=store,
        books=Books(market),
        clock=SimClock(NOW),
    )
    try:
        runtime.risk.pause()
        tick = await runtime.on_tick(_round(), _signal(), market)
        assert tick.execution is not None
        assert tick.execution.reason == "risk:paused"
        assert runtime.ledger.positions() == ()
    finally:
        await store.close()


async def test_status_reads_real_ledger_and_settlement(tmp_path: Path) -> None:
    store = await Store.open(str(tmp_path / "paper.db"))
    market = _market()
    runtime = build_operational_paper_runtime(
        settings=_settings(),
        store=store,
        books=Books(market),
        clock=SimClock(NOW),
    )
    try:
        await runtime.on_tick(_round(), _signal(), market)
        open_status = await runtime.source.status()
        assert open_status.open_positions == 1
        assert open_status.balance < Decimal("500")

        result = await runtime.settle(_round(Outcome.UP))
        settled_status = await runtime.source.status()
        assert settled_status.balance == result.balance_after
        assert settled_status.open_positions == 0
        assert await runtime.source.recent(5) == (result,)
    finally:
        await store.close()


async def test_telegram_controls_shared_risk(tmp_path: Path) -> None:
    store = await Store.open(str(tmp_path / "paper.db"))
    market = _market()
    runtime = build_operational_paper_runtime(
        settings=_settings(),
        store=store,
        books=Books(market),
        clock=SimClock(NOW),
    )
    api = API()
    telegram = runtime.build_telegram(api)
    try:
        await telegram.handle_update(
            {"update_id": 1, "message": {"chat": {"id": 123}, "text": "⏸ Pause"}}
        )
        callback = api.messages[0][2][0][0].callback_data
        await telegram.handle_update(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cb",
                    "data": callback,
                    "message": {"chat": {"id": 123}},
                },
            }
        )
        assert runtime.risk.paused is True
        assert (await runtime.source.status()).halted is True
    finally:
        await telegram.close()
        await store.close()
