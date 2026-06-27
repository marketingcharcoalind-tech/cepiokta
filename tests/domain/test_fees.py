"""Unit tests for btcbot.domain.fees (crypto_fees_v2: rate*min(p,1-p)^exponent)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from btcbot.domain.fees import (
    DEFAULT_FEE_EXPONENT,
    DEFAULT_FEE_RATE,
    CryptoFeesV2,
    FeeModel,
    ProportionalTakerFee,
    ZeroFee,
    estimate_fee,
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "gamma_fee_schedule.json"


class TestEstimateFee:
    def test_near_up_extreme(self) -> None:
        # p=0.92 → min(0.92,0.08)=0.08; size 1 * 0.07 * 0.08 = 0.0056
        assert estimate_fee(Decimal("0.92"), Decimal("1")) == Decimal("0.0056")

    def test_max_at_half(self) -> None:
        # p=0.50 → 0.07 * 0.50 = 0.035 (maksimum)
        assert estimate_fee(Decimal("0.50"), Decimal("1")) == Decimal("0.035")

    def test_symmetric(self) -> None:
        # p=0.08 simetris dengan p=0.92 → 0.0056
        assert estimate_fee(Decimal("0.08"), Decimal("1")) == Decimal("0.0056")
        assert estimate_fee(Decimal("0.08"), Decimal("1")) == estimate_fee(
            Decimal("0.92"), Decimal("1")
        )

    def test_linear_in_size(self) -> None:
        one = estimate_fee(Decimal("0.92"), Decimal("1"))
        ten = estimate_fee(Decimal("0.92"), Decimal("10"))
        assert ten == one * Decimal("10")
        assert estimate_fee(Decimal("0.92"), Decimal("0")) == Decimal("0")

    def test_exponent_two_lowers_fee_at_extreme(self) -> None:
        # exponent=2 menekan fee lebih tajam di ekstrem (0.08^2 < 0.08).
        exp1 = estimate_fee(Decimal("0.92"), Decimal("1"), exponent=1)
        exp2 = estimate_fee(Decimal("0.92"), Decimal("1"), exponent=2)
        assert exp2 < exp1
        assert exp2 == Decimal("0.07") * (Decimal("0.08") ** 2)  # 0.000448

    def test_custom_rate(self) -> None:
        assert estimate_fee(Decimal("0.50"), Decimal("1"), rate=Decimal("0.10")) == Decimal("0.05")


class TestCryptoFeesV2:
    def test_defaults_verified(self) -> None:
        fee = CryptoFeesV2()
        assert fee.rate == DEFAULT_FEE_RATE == Decimal("0.07")
        assert fee.exponent == DEFAULT_FEE_EXPONENT == 1

    def test_fee_per_share_matches_estimate(self) -> None:
        fee = CryptoFeesV2()
        assert fee.fee_per_share(Decimal("0.92")) == Decimal("0.0056")
        assert fee.fee_per_share(Decimal("0.50")) == Decimal("0.035")

    def test_exponent_field(self) -> None:
        fee = CryptoFeesV2(exponent=2)
        assert fee.fee_per_share(Decimal("0.92")) == Decimal("0.07") * (Decimal("0.08") ** 2)

    def test_satisfies_protocol(self) -> None:
        assert isinstance(CryptoFeesV2(), FeeModel)

    def test_legacy_alias(self) -> None:
        # Nama lama tetap valid (kompatibilitas engine/replay/report).
        assert ProportionalTakerFee is CryptoFeesV2
        assert ProportionalTakerFee(Decimal("0.07"), 1).fee_per_share(Decimal("0.9")) == Decimal(
            "0.007"
        )

    def test_rate_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            CryptoFeesV2(Decimal("1"))

    def test_exponent_below_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="exponent"):
            CryptoFeesV2(exponent=0)


class TestZeroFee:
    def test_always_zero(self) -> None:
        fee = ZeroFee()
        assert fee.fee_per_share(Decimal("0.99")) == Decimal("0")
        assert fee.fee_per_share(Decimal("0.50")) == Decimal("0")

    def test_satisfies_protocol(self) -> None:
        assert isinstance(ZeroFee(), FeeModel)


class TestFeeScheduleFixture:
    def test_fixture_matches_model_defaults(self) -> None:
        raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        sched = raw["feeSchedule"]
        assert raw["feeType"] == "crypto_fees_v2"
        assert sched["takerOnly"] is True
        # Model default mencerminkan feeSchedule live.
        fee = CryptoFeesV2(Decimal(str(sched["rate"])), int(sched["exponent"]))
        assert fee.rate == Decimal("0.07")
        assert fee.exponent == 1
        # taker fee di p=0.92 sesuai formula terverifikasi.
        assert fee.fee_per_share(Decimal("0.92")) == Decimal("0.0056")
