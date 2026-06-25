"""Unit tests for btcbot.adapters.chainlink (Data Feeds reader, RPC mocked)."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btcbot.adapters.chainlink import (
    AggregatorRoundData,
    ChainlinkDataFeed,
    FakePriceSource,
    PriceUnavailableError,
)
from btcbot.adapters.clock import SimClock
from btcbot.domain.models import PriceSource, PriceTick

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


async def _no_sleep(_seconds: float) -> None:
    return None


class FakeReader:
    """AggregatorReader mock dengan kontrol kegagalan & nilai."""

    def __init__(
        self,
        *,
        decimals_val: int = 8,
        data: AggregatorRoundData | None = None,
        fail_data_times: int = 0,
        always_fail: bool = False,
        hang: bool = False,
    ) -> None:
        self._decimals_val = decimals_val
        self._data = data
        self._fail_data_times = fail_data_times
        self._always_fail = always_fail
        self._hang = hang
        self.data_calls = 0

    async def decimals(self) -> int:
        if self._always_fail:
            raise ConnectionError("RPC down")
        return self._decimals_val

    async def latest_round_data(self) -> AggregatorRoundData:
        self.data_calls += 1
        if self._hang:
            await asyncio.sleep(10)  # akan time out
        if self._always_fail:
            raise ConnectionError("RPC down")
        if self.data_calls <= self._fail_data_times:
            raise ConnectionError("transient RPC error")
        assert self._data is not None
        return self._data


def _data(
    answer: int, *, updated_at: datetime | None = None, round_id: int = 100
) -> AggregatorRoundData:
    ts = updated_at or NOW
    return AggregatorRoundData(
        round_id=round_id,
        answer=answer,
        started_at=_epoch(ts),
        updated_at=_epoch(ts),
        answered_in_round=round_id,
    )


def _feed(reader: FakeReader, **kw: object) -> ChainlinkDataFeed:
    return ChainlinkDataFeed(
        reader=reader,
        clock=SimClock(NOW),
        source="chainlink:data_feed:0xfeed",
        sleep=_no_sleep,
        **kw,  # type: ignore[arg-type]
    )


class TestChainlinkDataFeed:
    async def test_price_normalized_8_decimals(self) -> None:
        # answer 6425012345678 dengan 8 desimal = 64250.12345678
        reader = FakeReader(decimals_val=8, data=_data(6425012345678))
        feed = _feed(reader)
        tick = await feed.price_now()
        assert isinstance(tick, PriceTick)
        assert tick.price == Decimal("64250.12345678")
        assert tick.round_id == 100
        assert tick.source == "chainlink:data_feed:0xfeed"
        assert tick.stale is False

    async def test_satisfies_price_source_protocol(self) -> None:
        feed = _feed(FakeReader(data=_data(6425000000000)))
        assert isinstance(feed, PriceSource)

    async def test_staleness_detected(self) -> None:
        old = NOW - timedelta(seconds=200)  # > 120s default
        reader = FakeReader(decimals_val=8, data=_data(6425000000000, updated_at=old))
        feed = _feed(reader)
        tick = await feed.price_now()
        assert tick.stale is True

    async def test_fresh_not_stale(self) -> None:
        recent = NOW - timedelta(seconds=30)
        reader = FakeReader(decimals_val=8, data=_data(6425000000000, updated_at=recent))
        feed = _feed(reader)
        tick = await feed.price_now()
        assert tick.stale is False

    async def test_answer_zero_rejected(self) -> None:
        feed = _feed(FakeReader(decimals_val=8, data=_data(0)))
        with pytest.raises(PriceUnavailableError, match="answer"):
            await feed.price_now()

    async def test_answer_negative_rejected(self) -> None:
        feed = _feed(FakeReader(decimals_val=8, data=_data(-1)))
        with pytest.raises(PriceUnavailableError, match="answer"):
            await feed.price_now()

    async def test_sanity_range_too_low_rejected(self) -> None:
        # 999 * 1e8 → price 999 < 1000
        feed = _feed(FakeReader(decimals_val=8, data=_data(99900000000)))
        with pytest.raises(PriceUnavailableError, match="sanity"):
            await feed.price_now()

    async def test_sanity_range_too_high_rejected(self) -> None:
        # 2_000_000 * 1e8 → price 2e6 > 1e6
        feed = _feed(FakeReader(decimals_val=8, data=_data(200000000000000)))
        with pytest.raises(PriceUnavailableError, match="sanity"):
            await feed.price_now()

    async def test_retry_then_success(self) -> None:
        reader = FakeReader(decimals_val=8, data=_data(6425000000000), fail_data_times=2)
        feed = _feed(reader, retries=3)
        tick = await feed.price_now()
        assert tick.price == Decimal("64250")
        assert reader.data_calls == 3  # 2 gagal + 1 sukses

    async def test_price_unavailable_on_rpc_failure(self) -> None:
        feed = _feed(FakeReader(always_fail=True), retries=2)
        with pytest.raises(PriceUnavailableError):
            await feed.price_now()

    async def test_timeout_raises_price_unavailable(self) -> None:
        reader = FakeReader(decimals_val=8, data=_data(6425000000000), hang=True)
        feed = _feed(reader, retries=2, timeout_sec=0.01)
        with pytest.raises(PriceUnavailableError):
            await feed.price_now()


class TestFakePriceSource:
    async def test_returns_tick(self) -> None:
        src = FakePriceSource(Decimal("64091"), ts=NOW, source="demo", round_id=7)
        tick = await src.price_now()
        assert tick.price == Decimal("64091")
        assert tick.ts == NOW
        assert tick.source == "demo"
        assert tick.round_id == 7
        assert tick.stale is False

    async def test_satisfies_protocol(self) -> None:
        assert isinstance(FakePriceSource(Decimal("64000")), PriceSource)

    async def test_set_price(self) -> None:
        src = FakePriceSource(Decimal("64000"))
        src.set_price(Decimal("64500"))
        assert (await src.price_now()).price == Decimal("64500")

    async def test_error_raised(self) -> None:
        src = FakePriceSource(Decimal("64000"), error=PriceUnavailableError("boom"))
        with pytest.raises(PriceUnavailableError, match="boom"):
            await src.price_now()
