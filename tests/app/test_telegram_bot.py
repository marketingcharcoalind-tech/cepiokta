from decimal import Decimal
from typing import Any

from btcbot.app.telegram_bot import TelegramPollingRuntime, build_runtime
from btcbot.config.settings import Mode, Settings


class API:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, object]] = []
        self.answers: list[tuple[str, str]] = []
        self.closed = False

    async def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        return []

    async def send_message(self, chat_id: int, text: str, keyboard: object = ()) -> None:
        self.messages.append((chat_id, text, keyboard))

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


async def test_start_sends_real_inline_keyboard_payload() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/start"}}
    )
    assert len(api.messages) == 1
    assert "read-only" in api.messages[0][1]
    assert api.messages[0][2]


async def test_readonly_button_callback_is_answered() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {
            "update_id": 2,
            "callback_query": {
                "id": "cb1",
                "data": "status",
                "message": {"chat": {"id": 123}},
            },
        }
    )
    assert api.answers == [("cb1", "OK")]
    assert "mode=paper" in api.messages[0][1]


async def test_kill_request_only_shows_confirmation() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 3, "message": {"chat": {"id": 123}, "text": "/kill"}}
    )
    assert "Confirm kill" in api.messages[0][1]
    assert api.messages[0][2]


async def test_unauthorized_chat_gets_no_response() -> None:
    runtime, api = _runtime()
    await runtime.handle_update(
        {"update_id": 4, "message": {"chat": {"id": 999}, "text": "/start"}}
    )
    assert api.messages == []


async def test_close_closes_api() -> None:
    runtime, api = _runtime()
    await runtime.close()
    assert api.closed
