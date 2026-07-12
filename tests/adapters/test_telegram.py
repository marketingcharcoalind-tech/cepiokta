import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from btcbot.adapters.telegram import (
    BotEvent,
    Severity,
    TelegramHTTPTransport,
    TelegramNotifier,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


class FakeTransport:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.messages: list[str] = []
        self.closed = False

    async def send(self, text: str) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("telegram unavailable")
        self.messages.append(text)

    async def close(self) -> None:
        self.closed = True


def _event(kind: str = "paper_fill", severity: Severity = Severity.INFO) -> BotEvent:
    return BotEvent(kind, "#1 UP @0.96 x2", severity, NOW)


def test_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        BotEvent("x", "text", Severity.INFO, datetime(2026, 7, 13))  # noqa: DTZ001


async def test_emit_is_nonblocking_and_worker_sends() -> None:
    transport = FakeTransport()
    notifier = TelegramNotifier(transport, retry_delay_seconds=0)
    await notifier.start()
    await notifier.emit(_event())
    await notifier.stop(drain=True)
    assert notifier.sent == 1
    assert "#1 UP" in transport.messages[0]
    assert transport.closed


async def test_transport_failure_never_escapes_producer() -> None:
    transport = FakeTransport(failures=10)
    notifier = TelegramNotifier(transport, retry_attempts=1, retry_delay_seconds=0)
    await notifier.start()
    await notifier.emit(_event(severity=Severity.CRITICAL))
    await notifier.stop(drain=True)
    assert notifier.failed == 1
    assert transport.messages == []


async def test_retry_recovers() -> None:
    transport = FakeTransport(failures=1)
    notifier = TelegramNotifier(transport, retry_attempts=2, retry_delay_seconds=0)
    await notifier.start()
    await notifier.emit(_event())
    await notifier.stop(drain=True)
    assert notifier.sent == 1
    assert notifier.failed == 0


async def test_full_queue_preserves_critical_over_info() -> None:
    transport = FakeTransport()
    notifier = TelegramNotifier(transport, queue_size=1, retry_delay_seconds=0)
    await notifier.emit(_event("heartbeat", Severity.INFO))
    await notifier.emit(_event("kill", Severity.CRITICAL))
    await notifier.start()
    await notifier.stop(drain=True)
    assert notifier.dropped == 1
    assert "CRITICAL" in transport.messages[0]


async def test_full_critical_queue_drops_new_info() -> None:
    transport = FakeTransport()
    notifier = TelegramNotifier(transport, queue_size=1)
    await notifier.emit(_event("kill", Severity.CRITICAL))
    await notifier.emit(_event("heartbeat", Severity.INFO))
    assert notifier.dropped == 1
    await notifier.start()
    await notifier.stop(drain=True)


async def test_stop_without_drain_discards_safely() -> None:
    transport = FakeTransport()
    notifier = TelegramNotifier(transport)
    await notifier.emit(_event())
    await notifier.start()
    await asyncio.sleep(0)
    await notifier.stop(drain=False)
    assert transport.closed


async def test_http_transport_posts_expected_payload_without_real_network() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = TelegramHTTPTransport(token="secret-token", chat_id="123", client=client)
    try:
        await transport.send("hello")
    finally:
        await client.aclose()
    assert captured[0].url.path.endswith("/botsecret-token/sendMessage")
    assert b'"chat_id":"123"' in captured[0].content


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="required"):
        TelegramHTTPTransport(token="", chat_id="123")
    with pytest.raises(ValueError, match="invalid"):
        TelegramNotifier(FakeTransport(), queue_size=0)
