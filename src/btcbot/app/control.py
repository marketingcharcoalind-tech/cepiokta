"""Read-only control facade and Telegram command router for T.2.

This layer exposes paper status, PnL, positions, recent results, and a safe config
view. It cannot pause, resume, kill, change MODE, alter limits, or access secrets.
Telegram transport is separate and auxiliary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from btcbot.config.settings import Settings
from btcbot.domain.models import Position, RoundResult


@dataclass(frozen=True, slots=True)
class ControlStatus:
    """Safe operator-facing paper status snapshot."""

    mode: str
    uptime_seconds: int
    balance: Decimal
    pnl_today: Decimal
    open_positions: int
    wss_status: str
    halted: bool


class ControlDataSource(Protocol):
    """Read-only source implemented by the paper runtime."""

    async def status(self) -> ControlStatus:
        ...

    async def positions(self) -> tuple[Position, ...]:
        ...

    async def recent(self, limit: int) -> tuple[RoundResult, ...]:
        ...


@dataclass(frozen=True, slots=True)
class MenuButton:
    """Transport-neutral inline button description."""

    text: str
    callback_data: str


@dataclass(frozen=True, slots=True)
class CommandReply:
    """Text plus optional rows of inline buttons."""

    text: str
    keyboard: tuple[tuple[MenuButton, ...], ...] = ()


class ControlFacade:
    """Single read-only control entry point shared by Telegram and future CLI."""

    def __init__(self, source: ControlDataSource, settings: Settings) -> None:
        self._source = source
        self._settings = settings

    async def status(self) -> ControlStatus:
        return await self._source.status()

    async def positions(self) -> tuple[Position, ...]:
        return await self._source.positions()

    async def recent(self, limit: int = 5) -> tuple[RoundResult, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("recent limit must be between 1 and 20")
        return await self._source.recent(limit)

    async def pnl(self) -> tuple[Decimal, Decimal]:
        status = await self.status()
        return status.pnl_today, status.balance

    def safe_config(self) -> dict[str, str]:
        """Return an explicit allowlist, never secrets/endpoints/credentials."""
        settings = self._settings
        return {
            "mode": settings.mode.value,
            "t_entry_sec": str(settings.t_entry_sec),
            "delta_threshold": settings.delta_threshold,
            "min_price": str(settings.min_price),
            "max_price": str(settings.max_price),
            "min_edge": str(settings.min_edge),
            "max_notional_round": str(settings.max_notional_round),
            "max_open_exposure": str(settings.max_open_exposure),
            "max_daily_loss_pct": str(settings.max_daily_loss_pct),
            "max_consec_losses": str(settings.max_consec_losses),
            "paper_starting_balance": str(settings.paper_starting_balance),
        }


class TelegramReadOnlyRouter:
    """Whitelist-guarded command parser for T.2, with no control actions."""

    def __init__(self, facade: ControlFacade, allowed_chat_ids: set[int]) -> None:
        self._facade = facade
        self._allowed = frozenset(allowed_chat_ids)

    async def handle(self, chat_id: int, text: str) -> CommandReply | None:
        """Return None for unauthorized chats; never reveal workspace state."""
        if chat_id not in self._allowed:
            return None
        parts = text.strip().split()
        if not parts:
            return CommandReply("Unknown command. Use /help.")
        command = parts[0].split("@", 1)[0].lower()
        if command in {"/start", "/help"}:
            return CommandReply("Paper bot control (read-only)", self.main_menu())
        if command == "/status":
            return CommandReply(await self._format_status())
        if command == "/balance":
            status = await self._facade.status()
            return CommandReply(f"Paper balance: ${status.balance}")
        if command == "/pnl":
            pnl, balance = await self._facade.pnl()
            return CommandReply(f"Paper PnL today: {pnl:+} | balance: ${balance}")
        if command == "/positions":
            return CommandReply(self._format_positions(await self._facade.positions()))
        if command == "/recent":
            limit = self._parse_recent_limit(parts)
            return CommandReply(self._format_recent(await self._facade.recent(limit)))
        if command == "/config":
            config = self._facade.safe_config()
            return CommandReply("\n".join(f"{key}: {value}" for key, value in config.items()))
        return CommandReply("Unknown command. Use /help.")

    async def handle_callback(self, chat_id: int, callback_data: str) -> CommandReply | None:
        """Map safe menu callbacks to the same read-only command paths."""
        mapping = {
            "status": "/status",
            "pnl": "/pnl",
            "positions": "/positions",
            "recent": "/recent 5",
        }
        command = mapping.get(callback_data)
        if command is None:
            return None if chat_id not in self._allowed else CommandReply("Unknown action.")
        return await self.handle(chat_id, command)

    @staticmethod
    def main_menu() -> tuple[tuple[MenuButton, ...], ...]:
        return (
            (MenuButton("📊 Status", "status"), MenuButton("💰 PnL", "pnl")),
            (MenuButton("📈 Positions", "positions"), MenuButton("🧾 Recent", "recent")),
        )

    async def _format_status(self) -> str:
        status = await self._facade.status()
        return (
            f"mode={status.mode} | uptime={status.uptime_seconds}s | "
            f"balance=${status.balance} | pnl={status.pnl_today:+} | "
            f"positions={status.open_positions} | wss={status.wss_status} | "
            f"halted={status.halted}"
        )

    @staticmethod
    def _format_positions(positions: tuple[Position, ...]) -> str:
        if not positions:
            return "No open paper positions."
        return "\n".join(
            f"#{position.round_no} {position.token_id}: {position.size} @ {position.avg_price}"
            for position in positions
        )

    @staticmethod
    def _format_recent(results: tuple[RoundResult, ...]) -> str:
        if not results:
            return "No settled paper rounds yet."
        return "\n".join(
            f"#{result.round_no} {result.side_taken} pnl={result.pnl:+} balance={result.balance_after}"
            for result in results
        )

    @staticmethod
    def _parse_recent_limit(parts: list[str]) -> int:
        if len(parts) == 1:
            return 5
        if len(parts) != 2:
            raise ValueError("usage: /recent [1-20]")
        try:
            limit = int(parts[1])
        except ValueError as exc:
            raise ValueError("usage: /recent [1-20]") from exc
        if not 1 <= limit <= 20:
            raise ValueError("usage: /recent [1-20]")
        return limit
