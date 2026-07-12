"""Telegram long-poll runtime with a persistent bottom menu for paper control.

Run with ``python -m btcbot.app.telegram_bot``. The process uses Telegram Bot API
long polling, routes read-only menu buttons, and supports confirmed pause/resume/
kill actions. It never submits market orders and never logs the bot token.
"""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass, field
from datetime import UTC
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol

import httpx
import structlog

from btcbot.adapters.clock import SystemClock
from btcbot.app.control import ControlFacade, ControlStatus, MenuButton, TelegramReadOnlyRouter
from btcbot.app.control_actions import ActionReply, AuditEvent, AuditSink, TelegramActionController
from btcbot.config.settings import Mode, Settings, get_settings
from btcbot.domain.models import Position, RoundResult
from btcbot.risk.manager import RiskLimits, RiskManager

_LOG = structlog.get_logger()

_MENU_COMMANDS = {
    "📊 Status": "/status",
    "💰 P&L": "/pnl",
    "📈 Positions": "/positions",
    "🧾 Recent": "/recent 5",
    "⏸ Pause": "/pause",
    "▶️ Resume": "/resume",
    "🛑 KILL": "/kill",
    "⚙️ Config": "/config",
}


class TelegramAPI(Protocol):
    async def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: tuple[tuple[MenuButton, ...], ...] = (),
        *,
        persistent: bool = False,
    ) -> None: ...

    async def answer_callback(self, callback_id: str, text: str) -> None: ...

    async def close(self) -> None: ...


class TelegramBotAPI:
    """Small async Bot API client; credentials remain private in the URL field."""

    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        if not token.strip():
            raise ValueError("Telegram token is required")
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=40)
        self._owns_client = client is None

    async def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        response = await self._client.post(
            f"{self._base}/getUpdates",
            json={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram getUpdates returned not-ok")
        return list(payload.get("result", []))

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: tuple[tuple[MenuButton, ...], ...] = (),
        *,
        persistent: bool = False,
    ) -> None:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if keyboard and persistent:
            body["reply_markup"] = {
                "keyboard": [[{"text": button.text} for button in row] for row in keyboard],
                "resize_keyboard": True,
                "is_persistent": True,
                "one_time_keyboard": False,
                "input_field_placeholder": "Pilih menu paper bot...",
            }
        elif keyboard:
            body["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": button.text, "callback_data": button.callback_data}
                        for button in row
                    ]
                    for row in keyboard
                ]
            }
        response = await self._client.post(f"{self._base}/sendMessage", json=body)
        response.raise_for_status()

    async def answer_callback(self, callback_id: str, text: str) -> None:
        response = await self._client.post(
            f"{self._base}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(slots=True)
class RuntimeState:
    """Minimal paper state view until realtime trading owns this process."""

    started_at: float = field(default_factory=monotonic)
    balance: Decimal = Decimal("0")
    pnl_today: Decimal = Decimal("0")
    positions: tuple[Position, ...] = ()
    results: tuple[RoundResult, ...] = ()
    wss_status: str = "not_started"


class RuntimeControlSource:
    def __init__(self, state: RuntimeState, risk: RiskManager) -> None:
        self._state = state
        self._risk = risk

    async def status(self) -> ControlStatus:
        return ControlStatus(
            mode="paper",
            uptime_seconds=int(monotonic() - self._state.started_at),
            balance=self._state.balance,
            pnl_today=self._state.pnl_today,
            open_positions=len(self._state.positions),
            wss_status=self._state.wss_status,
            halted=self._risk.should_halt(),
        )

    async def positions(self) -> tuple[Position, ...]:
        return self._state.positions

    async def recent(self, limit: int) -> tuple[RoundResult, ...]:
        return self._state.results[-limit:]


class LogAuditSink(AuditSink):
    async def write(self, event: AuditEvent) -> None:
        _LOG.info(
            "telegram_control_audit",
            chat_id=event.chat_id,
            action=event.action.value,
            phase=event.phase,
            detail=event.detail,
            ts=event.ts.astimezone(UTC).isoformat(),
        )


class TelegramPollingRuntime:
    """Route Telegram updates with failure isolation and no credential logging."""

    def __init__(
        self,
        api: TelegramAPI,
        readonly: TelegramReadOnlyRouter,
        actions: TelegramActionController,
        allowed_chat_ids: set[int],
    ) -> None:
        self._api = api
        self._readonly = readonly
        self._actions = actions
        self._allowed = frozenset(allowed_chat_ids)
        self._offset = 0

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                updates = await self._api.get_updates(self._offset, 25)
                for update in updates:
                    self._offset = max(self._offset, int(update["update_id"]) + 1)
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOG.warning("telegram_poll_error", error_type=type(exc).__name__)
                await asyncio.sleep(2)

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            await self._handle_message(update["message"])
        elif "callback_query" in update:
            await self._handle_callback(update["callback_query"])

    async def _handle_message(self, message: dict[str, Any]) -> None:
        chat_id = int(message.get("chat", {}).get("id", 0))
        raw_text = str(message.get("text", ""))
        text = _MENU_COMMANDS.get(raw_text, raw_text)
        if chat_id not in self._allowed:
            _LOG.warning("telegram_unauthorized", chat_id=chat_id)
            return
        if text in {"/start", "/help"}:
            reply = await self._readonly.handle(chat_id, text)
            if reply is not None:
                await self._api.send_message(
                    chat_id,
                    "🤖 BTC Paper Bot\nMode simulasi, tidak memakai uang nyata.\nPilih menu:",
                    self.persistent_menu(),
                    persistent=True,
                )
            return
        action_reply = await self._actions.command(chat_id, text)
        if action_reply is not None and not action_reply.text.startswith("Unknown"):
            await self._send_action_reply(chat_id, action_reply)
            return
        reply = await self._readonly.handle(chat_id, text)
        if reply is not None:
            await self._api.send_message(chat_id, reply.text)

    async def _handle_callback(self, query: dict[str, Any]) -> None:
        callback_id = str(query.get("id", ""))
        chat_id = int(query.get("message", {}).get("chat", {}).get("id", 0))
        data = str(query.get("data", ""))
        if chat_id not in self._allowed:
            _LOG.warning("telegram_unauthorized_callback", chat_id=chat_id)
            return
        if data.startswith("control:"):
            action_reply = await self._actions.handle_callback(chat_id, data)
            if action_reply is not None:
                await self._api.answer_callback(callback_id, action_reply.text)
                await self._api.send_message(chat_id, action_reply.text)
            return
        reply = await self._readonly.handle_callback(chat_id, data)
        if reply is not None:
            await self._api.answer_callback(callback_id, "OK")
            await self._api.send_message(chat_id, reply.text)

    @staticmethod
    def persistent_menu() -> tuple[tuple[MenuButton, ...], ...]:
        return (
            (MenuButton("📊 Status", ""), MenuButton("💰 P&L", "")),
            (MenuButton("📈 Positions", ""), MenuButton("🧾 Recent", "")),
            (MenuButton("⏸ Pause", ""), MenuButton("▶️ Resume", "")),
            (MenuButton("⚙️ Config", ""), MenuButton("🛑 KILL", "")),
        )

    async def _send_action_reply(self, chat_id: int, reply: ActionReply) -> None:
        keyboard: tuple[tuple[MenuButton, ...], ...] = ()
        if reply.confirm_callback and reply.cancel_callback:
            keyboard = (
                (
                    MenuButton("✅ Confirm", reply.confirm_callback),
                    MenuButton("❌ Cancel", reply.cancel_callback),
                ),
            )
        await self._api.send_message(chat_id, reply.text, keyboard)

    async def close(self) -> None:
        await self._api.close()


def build_runtime(settings: Settings, api: TelegramAPI | None = None) -> TelegramPollingRuntime:
    if settings.mode is not Mode.PAPER:
        raise RuntimeError("Telegram paper control requires MODE=paper")
    if not settings.telegram_enabled:
        raise RuntimeError("TELEGRAM_ENABLED must be true")
    allowed = set(settings.allowed_chat_ids())
    if not allowed:
        raise RuntimeError("Telegram whitelist must not be empty")
    clock = SystemClock()
    risk = RiskManager(RiskLimits.from_settings(settings), clock)
    state = RuntimeState(balance=settings.paper_starting_balance)
    facade = ControlFacade(RuntimeControlSource(state, risk), settings)
    readonly = TelegramReadOnlyRouter(facade, allowed)
    actions = TelegramActionController(
        risk=risk,
        clock=clock,
        audit=LogAuditSink(),
        allowed_chat_ids=allowed,
    )
    return TelegramPollingRuntime(
        api or TelegramBotAPI(settings.telegram_bot_token), readonly, actions, allowed
    )


async def main_async() -> int:
    settings = get_settings()
    runtime = build_runtime(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    _LOG.info("telegram_polling_started", allowed_count=len(settings.allowed_chat_ids()))
    try:
        await runtime.run(stop)
    finally:
        await runtime.close()
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
