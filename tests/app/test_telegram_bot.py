from decimal import Decimal
from typing import Any

from btcbot.app.control import MenuButton
from btcbot.app.telegram_bot import TelegramPollingRuntime, build_runtime
from btcbot.config.settings import Mode, Settings


class API:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, tuple[tuple[MenuButton, ...], ...], bool]] = []
        self.answers: list[tuple[str, str]] = []
        self.closed = False

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
        self.answers.append((callback_id, text))

    async def close(self) -> None:
        self.closed = True


def _runtime() -> tuple[TelegramPollingRuntime, API]:
    api = API()
    settings = Settings(
        mode=Mode.PAPER,
        telegram_enabled=True,
        telegram_bot_token="test-token",
        telegram_notify_chat_id="123",
        telegram_allowed_chat_ids="123",
        paper_starting_balance=Decimal("500"),
    )
    return build_runtime(settings, api), api


async def test_start_sends_persistent_bottom_menu() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/start"}}
    )
    assert len(api.messages) == 1
    assert "BTC Paper Bot" in api.messages[0][1]
    assert api.messages[0][3] is True
    labels = {button.text for row in api.messages[0][2] for button in row}
    assert labels == {
        "📊 Status",
        "💰 P&L",
        "📈 Positions",
        "🧾 Recent",
        "⏸ Pause",
        "▶️ Resume",
        "⚙️ Config",
        "🛑 KILL",
    }


async def test_persistent_status_button_routes_to_status() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 2, "message": {"chat": {"id": 123}, "text": "📊 Status"}}
    )
    assert "mode=paper" in api.messages[0][1]
    assert api.messages[0][3] is False


async def test_persistent_kill_button_only_shows_inline_confirmation() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 3, "message": {"chat": {"id": 123}, "text": "🛑 KILL"}}
    )
    assert "Confirm kill" in api.messages[0][1]
    assert api.messages[0][2]
    assert api.messages[0][3] is False


async def test_readonly_callback_is_still_answered() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {
            "update_id": 4,
            "callback_query": {
                "id": "cb1",
                "data": "status",
                "message": {"chat": {"id": 123}},
            },
        }
    )
    assert api.answers == [("cb1", "OK")]
    assert "mode=paper" in api.messages[0][1]


async def test_unauthorized_chat_gets_no_response() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 5, "message": {"chat": {"id": 999}, "text": "/start"}}
    )
    assert api.messages == []


async def test_close_closes_api() -> None:
    runtime, api = _runtime()
    await runtime.close()
    assert api.closed
