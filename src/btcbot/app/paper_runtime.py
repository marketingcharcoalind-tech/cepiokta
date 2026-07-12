"""Shared composition root for the operational paper process.

This module creates exactly one RiskManager and one paper ledger for OMS,
reconciliation, Telegram control, and P&L/error notifications. It is deliberately
paper-only and contains no signer, credential, CLOB REST order, or live path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic

from btcbot.adapters.clock import Clock
from btcbot.adapters.telegram import BotEvent, Severity
from btcbot.app.control import ControlFacade, ControlStatus, TelegramReadOnlyRouter
from btcbot.app.control_actions import TelegramActionController
from btcbot.app.paper import PaperLedger, PaperRunner, PaperTickResult
from btcbot.app.paper_notifications import NotificationPolicy, PaperNotificationTracker
from btcbot.app.reconcile import PaperReconciler, ReconciliationReport, ReconciliationSnapshot
from btcbot.app.telegram_bot import LogAuditSink, TelegramAPI, TelegramPollingRuntime
from btcbot.config.settings import Mode, Settings
from btcbot.data.store import Store
from btcbot.domain.fees import CryptoFeesV2
from btcbot.domain.models import Position, Round, RoundResult, Signal
from btcbot.domain.strategy import MarketBook, Strategy, StrategyParams
from btcbot.exec.oms import BookProvider, PaperOMS, PaperOMSConfig
from btcbot.exec.sizing import SizingLimits
from btcbot.risk.manager import CircuitReason, RiskLimits, RiskManager

_ZERO = Decimal("0")


@dataclass(slots=True)
class RuntimeEventBuffer:
    """In-process event sink; production can inject TelegramNotifier instead."""

    events: list[BotEvent] = field(default_factory=list)

    async def emit(self, event: BotEvent) -> None:
        self.events.append(event)


class PaperControlSource:
    """Live read-only view over the same ledger and risk gate used by OMS."""

    def __init__(self, ledger: PaperLedger, risk: RiskManager) -> None:
        self._ledger = ledger
        self._risk = risk
        self._started_at = monotonic()
        self._results: list[RoundResult] = []
        self._wss_status = "not_started"

    def record_result(self, result: RoundResult) -> None:
        self._results.append(result)

    def set_wss_status(self, status: str) -> None:
        self._wss_status = status

    async def status(self) -> ControlStatus:
        pnl_today = sum((result.pnl for result in self._results), _ZERO)
        return ControlStatus(
            mode="paper",
            uptime_seconds=int(monotonic() - self._started_at),
            balance=self._ledger.balance,
            pnl_today=pnl_today,
            open_positions=len(self._ledger.positions()),
            wss_status=self._wss_status,
            halted=self._risk.should_halt(),
        )

    async def positions(self) -> tuple[Position, ...]:
        return self._ledger.positions()

    async def recent(self, limit: int) -> tuple[RoundResult, ...]:
        return tuple(self._results[-limit:])


class OperationalPaperRuntime:
    """Paper core whose OMS, controls, reconciliation, and alerts share state."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        settings: Settings,
        clock: Clock,
        risk: RiskManager,
        ledger: PaperLedger,
        runner: PaperRunner,
        source: PaperControlSource,
        reconciler: PaperReconciler,
        notifications: PaperNotificationTracker,
        event_sink: RuntimeEventBuffer,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.risk = risk
        self.ledger = ledger
        self.runner = runner
        self.source = source
        self.reconciler = reconciler
        self.notifications = notifications
        self.event_sink = event_sink

    async def on_tick(self, rnd: Round, signal: Signal, books: MarketBook) -> PaperTickResult:
        return await self.runner.on_tick(rnd, signal, books)

    async def settle(self, rnd: Round) -> RoundResult:
        result = await self.runner.settle(rnd)
        self.source.record_result(result)
        await self.notifications.on_result(result)
        return result

    async def reconcile(self, snapshot: ReconciliationSnapshot) -> ReconciliationReport:
        return await self.reconciler.reconcile(snapshot)

    async def report_error(
        self,
        *,
        kind: str,
        detail: str,
        remediation: str,
        severity: Severity = Severity.CRITICAL,
        action_required: bool = True,
    ) -> bool:
        """Emit a deduplicated actionable operator error via the shared sink."""
        return await self.notifications.error(
            kind=kind,
            detail=detail,
            remediation=remediation,
            severity=severity,
            action_required=action_required,
        )

    def set_wss_status(self, status: str) -> None:
        """Update operator state and the shared WSS circuit breaker."""
        normalized = status.strip().lower()
        self.source.set_wss_status(normalized)
        disconnected = normalized in {"disconnected", "stale", "gave_up"}
        reconnecting = normalized == "reconnecting"
        self.risk.on_event(CircuitReason.WSS_DISCONNECTED, active=disconnected)
        self.risk.on_event(CircuitReason.WSS_RECONNECTING, active=reconnecting)
        if normalized in {"connected", "reconnected"}:
            self.risk.on_event(CircuitReason.WSS_DISCONNECTED, active=False)
            self.risk.on_event(CircuitReason.WSS_RECONNECTING, active=False)

    def build_telegram(self, api: TelegramAPI) -> TelegramPollingRuntime:
        """Build Telegram controls against this runtime's real shared state."""
        allowed = set(self.settings.allowed_chat_ids())
        if not allowed:
            raise RuntimeError("Telegram whitelist must not be empty")
        facade = ControlFacade(self.source, self.settings)
        readonly = TelegramReadOnlyRouter(facade, allowed)
        actions = TelegramActionController(
            risk=self.risk,
            clock=self.clock,
            audit=LogAuditSink(),
            allowed_chat_ids=allowed,
        )
        return TelegramPollingRuntime(api, readonly, actions, allowed)


def _resolve_delta_threshold(settings: Settings) -> Decimal:
    raw = settings.delta_threshold.strip().lower()
    if raw == "auto":
        raise RuntimeError("operational paper runtime requires numeric DELTA_THRESHOLD")
    return Decimal(raw)


def build_operational_paper_runtime(
    *,
    settings: Settings,
    store: Store,
    books: BookProvider,
    clock: Clock,
    event_buffer: RuntimeEventBuffer | None = None,
    notification_policy: NotificationPolicy | None = None,
) -> OperationalPaperRuntime:
    """Build shared paper-only core; an external market loop calls ``on_tick``."""
    if settings.mode is not Mode.PAPER:
        raise RuntimeError("operational runtime requires MODE=paper")
    if settings.live_confirmed == "yes":
        raise RuntimeError("LIVE_CONFIRMED must remain no for paper runtime")

    risk = RiskManager(RiskLimits.from_settings(settings), clock)
    fee = CryptoFeesV2(rate=settings.fee_rate, exponent=settings.fee_exponent)
    ledger = PaperLedger(settings.paper_starting_balance, fee)
    oms = PaperOMS(
        mode=Mode.PAPER,
        risk_manager=risk,
        books=books,
        clock=clock,
        config=PaperOMSConfig(
            latency_ms=100,
            competition_fraction=settings.backtest_competition_fraction,
        ),
    )
    strategy = Strategy(
        StrategyParams.from_settings(
            settings,
            delta_threshold=_resolve_delta_threshold(settings),
        )
    )
    runner = PaperRunner(
        strategy=strategy,
        limits=SizingLimits.from_settings(settings),
        oms=oms,
        ledger=ledger,
        store=store,
        clock=clock,
    )
    source = PaperControlSource(ledger, risk)
    sink = event_buffer or RuntimeEventBuffer()
    reconciler = PaperReconciler(risk, sink)
    notifications = PaperNotificationTracker(
        starting_balance=settings.paper_starting_balance,
        sink=sink,
        clock=clock,
        policy=notification_policy,
    )
    return OperationalPaperRuntime(
        settings=settings,
        clock=clock,
        risk=risk,
        ledger=ledger,
        runner=runner,
        source=source,
        reconciler=reconciler,
        notifications=notifications,
        event_sink=sink,
    )
