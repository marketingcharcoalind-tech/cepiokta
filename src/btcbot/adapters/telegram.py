"""Best-effort Telegram notifier for T.1.

Telegram is auxiliary: producers only enqueue events and never wait for network
I/O. Transport failures are contained, critical events are preserved ahead of
routine info where possible, and bot tokens are never included in logs/events.
No command/control handler exists in T.1.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import httpx
import structlog

_LOG = structlog.get_logger()


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class BotEvent:
    """One notification emitted by paper core or risk infrastructure."""

    kind: str
    text: str
    severity: Severity
    ts: datetime

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.text.strip():
            raise ValueError("event kind and text must not be empty")
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")


class TelegramTransport(Protocol):
    """Injectable outbound transport for tests and production HTTP."""

    async def send(self, text: str) -> None:
        """Send one already-formatted message."""
        ...

    async def close(self) -> None:
        """Release transport resources."""
        ...


class TelegramHTTPTransport:
    """Minimal async Telegram Bot API transport using existing httpx dependency."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token.strip() or not chat_id.strip():
            raise ValueError("Telegram token and chat_id are required")
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def send(self, text: str) -> None:
        response = await self._client.post(
            self._url,
            json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class TelegramNotifier:
    """Non-blocking bounded-queue notifier with isolated sender task."""

    def __init__(
        self,
        transport: TelegramTransport,
        *,
        queue_size: int = 100,
        retry_attempts: int = 2,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        if queue_size <= 0 or retry_attempts < 0 or retry_delay_seconds < 0:
            raise ValueError("invalid notifier queue/retry configuration")
        self._transport = transport
        self._queue: asyncio.Queue[BotEvent] = asyncio.Queue(maxsize=queue_size)
        self._retry_attempts = retry_attempts
        self._retry_delay = retry_delay_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.sent = 0
        self.dropped = 0
        self.failed = 0

    async def start(self) -> None:
        """Start sender worker once; safe to call repeatedly."""
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._worker(), name="telegram-notifier")

    async def emit(self, event: BotEvent) -> None:
        """Enqueue immediately; never perform network I/O in caller path."""
        if self._stopping:
            self.dropped += 1
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if event.severity is Severity.CRITICAL and self._drop_one_noncritical():
                self._queue.put_nowait(event)
            else:
                self.dropped += 1

    async def stop(self, *, drain: bool = True) -> None:
        """Optionally drain queued events, stop worker, and close transport."""
        self._stopping = True
        if drain and self._task is not None and not self._task.done():
            await self._queue.join()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._transport.close()

    def _drop_one_noncritical(self) -> bool:
        buffered: list[BotEvent] = []
        removed = False
        while not self._queue.empty():
            queued = self._queue.get_nowait()
            self._queue.task_done()
            if not removed and queued.severity is not Severity.CRITICAL:
                removed = True
                self.dropped += 1
                continue
            buffered.append(queued)
        for queued in buffered:
            self._queue.put_nowait(queued)
        return removed

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._send_with_retry(event)
            finally:
                self._queue.task_done()

    async def _send_with_retry(self, event: BotEvent) -> None:
        message = self._format(event)
        for attempt in range(self._retry_attempts + 1):
            try:
                await self._transport.send(message)
                self.sent += 1
                return
            except Exception as exc:
                if attempt >= self._retry_attempts:
                    self.failed += 1
                    _LOG.warning(
                        "telegram_notification_dropped",
                        kind=event.kind,
                        severity=event.severity.value,
                        error_type=type(exc).__name__,
                    )
                    return
                await asyncio.sleep(self._retry_delay)

    @staticmethod
    def _format(event: BotEvent) -> str:
        timestamp = event.ts.astimezone(UTC).isoformat()
        return f"[{event.severity.value.upper()}] {event.text}\n{timestamp}"
