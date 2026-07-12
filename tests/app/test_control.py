from decimal import Decimal

import pytest

from btcbot.app.control import ControlFacade, ControlStatus, TelegramReadOnlyRouter
from btcbot.config.settings import Mode, Settings
from btcbot.domain.models import Position, RoundResult


class Source:
    def __init__(self) -> None:
        self.recent_limit: int | None = None

    async def status(self) -> ControlStatus:
        return ControlStatus(
            "paper", 123, Decimal("501.25"), Decimal("1.25"), 1, "ok", False
        )

    async def positions(self) -> tuple[Position, ...]:
        return (Position(7, "up", Decimal("2"), Decimal("0.96")),)

    async def recent(self, limit: int) -> tuple[RoundResult, ...]:
        self.recent_limit = limit
        return (
            RoundResult(
                7,
                "UP",
                Decimal("0.96"),
                Decimal("2"),
                Decimal("0"),
                Decimal("2"),
                Decimal("0.0786"),
                Decimal("501.25"),
            ),
        )


def _router() -> tuple[TelegramReadOnlyRouter, Source]:
    source = Source()
    settings = Settings(
        mode=Mode.PAPER,
        wallet_private_key="must-not-leak",
        telegram_bot_token="must-not-leak",
        clob_api_secret="must-not-leak",
    )
    facade = ControlFacade(source, settings)
    return TelegramReadOnlyRouter(facade, {123}), source


async def test_unauthorized_chat_receives_nothing() -> None:
    router, _ = _router()
    assert await router.handle(999, "/status") is None
    assert await router.handle_callback(999, "status") is None


async def test_status_and_pnl_commands() -> None:
    router, _ = _router()
    status = await router.handle(123, "/status")
    pnl = await router.handle(123, "/pnl")
    assert status is not None
    assert "mode=paper" in status.text
    assert pnl is not None
    assert "+1.25" in pnl.text


async def test_positions_and_recent_commands() -> None:
    router, source = _router()
    positions = await router.handle(123, "/positions")
    recent = await router.handle(123, "/recent 3")
    assert positions is not None
    assert "#7" in positions.text
    assert recent is not None
    assert "pnl=+0.0786" in recent.text
    assert source.recent_limit == 3


async def test_start_returns_read_only_buttons_only() -> None:
    router, _ = _router()
    reply = await router.handle(123, "/start")
    assert reply is not None
    callbacks = {button.callback_data for row in reply.keyboard for button in row}
    assert callbacks == {"status", "pnl", "positions", "recent"}
    assert "kill" not in callbacks
    assert "pause" not in callbacks


async def test_config_never_exposes_secrets() -> None:
    router, _ = _router()
    reply = await router.handle(123, "/config")
    assert reply is not None
    assert "must-not-leak" not in reply.text
    assert "private" not in reply.text.lower()
    assert "token" not in reply.text.lower()
    assert "api" not in reply.text.lower()


async def test_callback_uses_same_read_only_path() -> None:
    router, _ = _router()
    reply = await router.handle_callback(123, "status")
    assert reply is not None
    assert "mode=paper" in reply.text


@pytest.mark.parametrize("command", ["/recent 0", "/recent 21", "/recent nope", "/recent 1 2"])
async def test_recent_rejects_bad_limits(command: str) -> None:
    router, _ = _router()
    with pytest.raises(ValueError, match="usage"):
        await router.handle(123, command)


async def test_bot_username_suffix_and_unknown_command() -> None:
    router, _ = _router()
    status = await router.handle(123, "/status@mybot")
    unknown = await router.handle(123, "/kill")
    assert status is not None
    assert "mode=paper" in status.text
    assert unknown is not None
    assert "Unknown" in unknown.text
