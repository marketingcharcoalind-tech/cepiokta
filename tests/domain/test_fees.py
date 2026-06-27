"""Unit tests for btcbot.domain.fees."""

from __future__ import annotations

from decimal import Decimal

import pytest

from btcbot.domain.fees import (
    DEFAULT_FEE_RATE,
    FeeModel,
    ProportionalTakerFee,
    ZeroFee,
)


class TestProportionalTakerFee:
    def test_default_rate_is_seven_percent(self) -> None:
        assert ProportionalTakerFee().rate == DEFAULT_FEE_RATE == Decimal("0.07")

    def test_fee_is_rate_times_min_p(self) -> None:
        fee = ProportionalTakerFee(Decimal("0.07"))
        # fee = rate * min(price, 1-price); price 0.90 → 0.07 * 0.10 = 0.007
        assert fee.fee_per_share(Decimal("0.90")) == Decimal("0.007")

    def test_fee_symmetric_max_at_half(self) -> None:
        fee = ProportionalTakerFee(Decimal("0.07"))
        assert fee.fee_per_share(Decimal("0.50")) == Decimal("0.035")  # rate/2 (maks)
        assert fee.fee_per_share(Decimal("0.20")) == fee.fee_per_share(Decimal("0.80"))

    def test_fee_small_near_extreme(self) -> None:
        fee = ProportionalTakerFee(Decimal("0.07"))
        assert fee.fee_per_share(Decimal("0.99")) == Decimal("0.0007")  # 0.07 * 0.01

    def test_fee_never_negative(self) -> None:
        fee = ProportionalTakerFee()
        assert fee.fee_per_share(Decimal("0")) == Decimal("0")

    def test_satisfies_protocol(self) -> None:
        assert isinstance(ProportionalTakerFee(), FeeModel)

    def test_rate_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            ProportionalTakerFee(Decimal("1"))
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            ProportionalTakerFee(Decimal("-0.01"))

    def test_zero_rate_allowed_for_ablation(self) -> None:
        assert ProportionalTakerFee(Decimal("0")).fee_per_share(Decimal("0.9")) == Decimal("0")


class TestZeroFee:
    def test_always_zero(self) -> None:
        fee = ZeroFee()
        assert fee.fee_per_share(Decimal("0.99")) == Decimal("0")
        assert fee.fee_per_share(Decimal("0.50")) == Decimal("0")

    def test_satisfies_protocol(self) -> None:
        assert isinstance(ZeroFee(), FeeModel)
