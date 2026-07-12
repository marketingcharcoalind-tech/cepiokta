from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.adapters.clock import SimClock
from btcbot.app.control_actions import (
    ActionReply,
    AuditEvent,
    ControlAction,
    TelegramActionController,
)
from btcbot.risk.manager import CircuitReason, RiskLimits, RiskManager

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        self.events.append(event)


def _controller() -> tuple[TelegramActionController, RiskManager, SimClock, Audit]:
    clock = SimClock(NOW)
    risk = RiskManager(
        RiskLimits(Decimal("5"), Decimal("10"), Decimal("5"), 5, Decimal("50"), 30),
        clock,
    )
    audit = Audit()
    controller = TelegramActionController(
        risk=risk,
        clock=clock,
        audit=audit,
        allowed_chat_ids={123},
        token_factory=lambda: "one-time-token",
    )
    return controller, risk, clock, audit


async def _request(controller: TelegramActionController, command: str) -> ActionReply:
    reply = await controller.command(123, command)
    assert reply is not None
    assert reply.confirm_callback is not None
    return reply


async def test_unauthorized_chat_cannot_request_or_confirm() -> None:
    controller, risk, _clock, _audit = _controller()
    assert await controller.command(999, "/kill") is None
    assert await controller.handle_callback(999, "control:confirm:any") is None
    assert not risk.killed


async def test_pause_requires_second_step_and_is_audited() -> None:
    controller, risk, _clock, audit = _controller()
    reply = await _request(controller, "/pause")
    assert not risk.paused
    result = await controller.handle_callback(123, reply.confirm_callback or "")
    assert result is not None
    assert risk.paused
    assert [event.phase for event in audit.events] == ["requested", "executed"]


async def test_resume_does_not_clear_active_breaker() -> None:
    controller, risk, _clock, _audit = _controller()
    risk.pause()
    risk.on_event(CircuitReason.PRICE_STALE)
    reply = await _request(controller, "/resume")
    await controller.handle_callback(123, reply.confirm_callback or "")
    assert not risk.paused
    assert risk.should_halt()


async def test_kill_is_latched_after_confirmation() -> None:
    controller, risk, _clock, audit = _controller()
    reply = await _request(controller, "/kill")
    assert not risk.killed
    await controller.handle_callback(123, reply.confirm_callback or "")
    assert risk.killed
    assert risk.kill_reason == "telegram:123"
    assert audit.events[-1].action is ControlAction.KILL


async def test_callback_is_single_use_anti_replay() -> None:
    controller, risk, _clock, _audit = _controller()
    reply = await _request(controller, "/pause")
    callback = reply.confirm_callback or ""
    await controller.handle_callback(123, callback)
    second = await controller.handle_callback(123, callback)
    assert risk.paused
    assert second is not None
    assert "already used" in second.text


async def test_expired_confirmation_does_not_execute() -> None:
    controller, risk, clock, audit = _controller()
    reply = await _request(controller, "/kill")
    clock.advance(timedelta(seconds=61))
    result = await controller.handle_callback(123, reply.confirm_callback or "")
    assert result is not None
    assert "expired" in result.text.lower()
    assert not risk.killed
    assert audit.events[-1].phase == "expired"


async def test_cancel_does_not_execute() -> None:
    controller, risk, _clock, audit = _controller()
    reply = await _request(controller, "/pause")
    result = await controller.handle_callback(123, reply.cancel_callback or "")
    assert result is not None
    assert not risk.paused
    assert audit.events[-1].phase == "cancelled"


async def test_unknown_command_cannot_change_risk() -> None:
    controller, risk, _clock, _audit = _controller()
    result = await controller.command(123, "/mode live")
    assert result is not None
    assert "Unknown" in result.text
    assert not risk.should_halt()
