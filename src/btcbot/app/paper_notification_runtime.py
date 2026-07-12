"""Production wiring for paper P&L/error events to Telegram transport."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from btcbot.adapters.telegram import (
    TelegramHTTPTransport,
    TelegramNotifier,
    TelegramTransport,
)
from btcbot.app.paper_notifications import NotificationPolicy
from btcbot.config.settings import Settings


class PaperNotificationConfig(BaseSettings):
    """Env-backed settings for the T.1 P&L/error notification addendum."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    notify_pnl_per_trade: bool = True
    notify_pnl_wins: bool = True
    notify_pnl_losses: bool = True
    notify_profit_milestone: bool = True
    profit_milestone_step: Decimal = Decimal("50")
    notify_new_equity_high: bool = True
    alert_consec_losses: int = 3
    alert_drawdown_pct: Decimal = Decimal("5")
    notify_errors: bool = True
    notify_action_required: bool = True
    error_dedup_window_sec: int = 300

    @field_validator("profit_milestone_step")
    @classmethod
    def _positive_milestone(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("PROFIT_MILESTONE_STEP must be positive")
        return value

    @field_validator("alert_consec_losses", "error_dedup_window_sec")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("notification integer thresholds must be positive")
        return value

    @field_validator("alert_drawdown_pct")
    @classmethod
    def _non_negative_drawdown(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("ALERT_DRAWDOWN_PCT must be non-negative")
        return value

    def policy(self) -> NotificationPolicy:
        """Translate env settings into the transport-neutral tracker policy."""
        per_trade = self.notify_pnl_per_trade
        return NotificationPolicy(
            notify_wins=per_trade and self.notify_pnl_wins,
            notify_losses=self.notify_pnl_losses,
            notify_equity_high=self.notify_new_equity_high,
            notify_profit_milestone=self.notify_profit_milestone,
            profit_milestone_step=self.profit_milestone_step,
            alert_consecutive_losses=self.alert_consec_losses,
            alert_drawdown_pct=self.alert_drawdown_pct,
            error_dedup_window=timedelta(seconds=self.error_dedup_window_sec),
        )


def build_paper_notifier(
    settings: Settings,
    *,
    transport: TelegramTransport | None = None,
) -> TelegramNotifier:
    """Build the real best-effort notifier without starting network work."""
    if not settings.telegram_enabled:
        raise RuntimeError("TELEGRAM_ENABLED must be true")
    selected = transport or TelegramHTTPTransport(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_notify_chat_id,
    )
    return TelegramNotifier(selected)
