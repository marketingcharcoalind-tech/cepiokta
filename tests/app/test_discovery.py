"""Unit tests for btcbot.app.discovery (resilient Gamma discovery — Bug B4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.adapters.gamma import GammaError
from btcbot.app.discovery import discover_with_retry, discovery_backoff
from btcbot.config.settings import Settings
from btcbot.domain.models import MarketStatus, RoundMeta


def _meta() -> RoundMeta:
    return RoundMeta(
        market_id="m1",
        condition_id="0xabc",
        slug="btc-updown-5m-1782480000",
        token_id_up="up",
        token_id_down="down",
        start_time=datetime(2026, 6, 26, 13, 15, tzinfo=UTC),
        end_time=datetime(2026, 6, 26, 13, 20, tzinfo=UTC),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        status=MarketStatus.OPEN,
        asset="btc",
        timeframe="5m",
    )


class _FatalAuthError(Exception):
    """Bukan GammaError → fatal (mensimulasikan auth/config error)."""


class FakeGamma:
    """Gamma palsu: skrip perilaku discover_active_round per panggilan."""

    def __init__(self, behaviors: list[object]) -> None:
        self._behaviors = list(behaviors)
        self.calls = 0

    async def discover_active_round(self) -> RoundMeta:
        self.calls += 1
        item = self._behaviors[min(self.calls - 1, len(self._behaviors) - 1)]
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, RoundMeta)
        return item

    async def discover_rounds(self) -> list[RoundMeta]:  # pragma: no cover - tak dipakai
        return []

    async def get_market(self, condition_id: str) -> RoundMeta:  # pragma: no cover
        raise NotImplementedError


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "gamma_discovery_retry": True,
        "gamma_discovery_max_backoff_seconds": 15,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class TestDiscoveryBackoff:
    def test_schedule_then_cap(self) -> None:
        assert discovery_backoff(1, 15.0) == 1.0
        assert discovery_backoff(2, 15.0) == 2.0
        assert discovery_backoff(3, 15.0) == 5.0
        assert discovery_backoff(4, 15.0) == 15.0  # cap
        assert discovery_backoff(99, 15.0) == 15.0

    def test_never_exceeds_cap(self) -> None:
        for attempt in range(1, 10):
            assert discovery_backoff(attempt, 3.0) <= 3.0


class TestDiscoverWithRetry:
    async def test_transient_failures_then_success(self) -> None:
        # 3x GammaError lalu sukses → loop TIDAK mati, akhirnya dapat ronde.
        gamma = FakeGamma(
            [GammaError("tidak ada ronde"), GammaError("5xx"), GammaError("timeout"), _meta()]
        )
        sleep = _RecordingSleep()
        meta = await discover_with_retry(gamma, settings=_settings(), sleep=sleep)
        assert meta is not None
        assert meta.slug == "btc-updown-5m-1782480000"
        assert gamma.calls == 4
        assert sleep.delays == [1.0, 2.0, 5.0]  # backoff meningkat

    async def test_empty_results_retry_backoff_increasing_to_cap(self) -> None:
        # GammaError "tidak ada ronde" berturut → backoff naik sampai cap.
        gamma = FakeGamma([GammaError("tidak ada ronde")] * 6 + [_meta()])
        sleep = _RecordingSleep()
        meta = await discover_with_retry(
            gamma, settings=_settings(gamma_discovery_max_backoff_seconds=15), sleep=sleep
        )
        assert meta is not None
        assert sleep.delays == [1.0, 2.0, 5.0, 15.0, 15.0, 15.0]
        # monoton tak menurun & tak melebihi cap
        assert sleep.delays == sorted(sleep.delays)
        assert max(sleep.delays) <= 15.0

    async def test_fatal_error_propagates(self) -> None:
        # Error fatal (auth) → tetap raise (tidak di-retry selamanya).
        gamma = FakeGamma([_FatalAuthError("401 unauthorized")])
        sleep = _RecordingSleep()
        with pytest.raises(_FatalAuthError):
            await discover_with_retry(gamma, settings=_settings(), sleep=sleep)
        assert gamma.calls == 1
        assert sleep.delays == []  # tak ada retry untuk fatal

    async def test_retry_disabled_propagates_gamma_error(self) -> None:
        gamma = FakeGamma([GammaError("tidak ada ronde")])
        with pytest.raises(GammaError):
            await discover_with_retry(gamma, settings=_settings(gamma_discovery_retry=False))
        assert gamma.calls == 1

    async def test_shutdown_during_wait_returns_none(self) -> None:
        gamma = FakeGamma([GammaError("tidak ada ronde")])
        shutdown = asyncio.Event()

        async def _sleep(_delay: float) -> None:  # tak dipakai (ada shutdown)
            return None

        # Set shutdown agar wait_for(shutdown.wait()) langsung selesai → return None.
        shutdown.set()
        meta = await discover_with_retry(
            gamma, settings=_settings(), shutdown=shutdown, sleep=_sleep
        )
        assert meta is None

    async def test_cap_respected_with_small_cap(self) -> None:
        gamma = FakeGamma([GammaError("x"), GammaError("x"), GammaError("x"), _meta()])
        sleep = _RecordingSleep()
        await discover_with_retry(
            gamma, settings=_settings(gamma_discovery_max_backoff_seconds=3), sleep=sleep
        )
        assert all(d <= 3.0 for d in sleep.delays)
        assert sleep.delays == [1.0, 2.0, 3.0]  # 5s di-clamp ke cap 3
