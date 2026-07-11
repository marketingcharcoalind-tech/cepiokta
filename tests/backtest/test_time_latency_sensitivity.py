"""Focused tests for the read-only time-latency sensitivity CLI."""

from decimal import Decimal

import pytest

from btcbot.backtest.replay import ReplayConfig
from btcbot.backtest.time_latency_sensitivity import (
    DEFAULT_VARIANTS,
    LatencyVariant,
    _percentile,
    build_variant_configs,
)
from btcbot.domain.strategy import StrategyParams
from btcbot.exec.sizing import SizingLimits


def _base_config() -> ReplayConfig:
    return ReplayConfig(
        limits=SizingLimits(
            kelly_fraction=Decimal("0.25"),
            max_notional_round=Decimal("5"),
            max_bankroll_fraction=Decimal("0.02"),
            fill_safety=Decimal("0.8"),
            min_edge=Decimal("0.01"),
            max_price=Decimal("0.99"),
        ),
        params=StrategyParams(
            t_entry_sec=60,
            delta_threshold=Decimal("50"),
            min_price=Decimal("0.96"),
            max_price=Decimal("0.99"),
            min_edge=Decimal("0.01"),
            flip_ratio=Decimal("0.90"),
            hedge_fraction=Decimal("0.5"),
            p_exit=Decimal("0.65"),
        ),
        vol=Decimal("5"),
        starting_balance=Decimal("500"),
    )


def test_default_variants_are_complete_and_ordered():
    assert [variant.name for variant in DEFAULT_VARIANTS] == [
        "tick_0",
        "tick_1",
        "time_50ms",
        "time_100ms",
        "time_250ms",
        "time_500ms",
        "time_1000ms",
    ]


def test_build_variant_configs_isolated_from_baseline():
    base = _base_config()
    built = build_variant_configs(base)

    assert base.latency_mode == "ticks"
    assert base.latency_ticks == 1
    assert built[0][1].latency_mode == "ticks"
    assert built[0][1].latency_ticks == 0
    assert built[1][1].latency_ticks == 1
    assert built[2][1].latency_mode == "time"
    assert built[2][1].latency_ms == 50
    assert built[-1][1].latency_ms == 1000
    assert len({id(config) for _, config in built}) == len(DEFAULT_VARIANTS)


def test_build_variant_configs_rejects_invalid_values():
    base = _base_config()
    with pytest.raises(ValueError, match="Invalid time latency"):
        build_variant_configs(base, (LatencyVariant("bad", "time", milliseconds=-1),))
    with pytest.raises(ValueError, match="Unknown latency mode"):
        build_variant_configs(base, (LatencyVariant("bad", "other"),))


def test_percentile_interpolates_and_handles_empty():
    assert _percentile([], 0.5) == 0.0
    assert _percentile([10.0], 0.95) == 10.0
    assert _percentile([0.0, 100.0], 0.5) == 50.0
    assert _percentile([0.0, 10.0, 20.0, 30.0], 0.95) == pytest.approx(28.5)
