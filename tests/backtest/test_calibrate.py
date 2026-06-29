"""Task B — vol calibration reliability-curve tool tests (deterministic, no network)."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from btcbot.backtest.calibrate import (
    Calibrator,
    VolReport,
    _bucket_index,
    format_result,
    to_csv,
)
from btcbot.domain.models import Outcome, Round, RoundStatus, Signal

WS = datetime(2026, 6, 26, 13, 15, 0, tzinfo=UTC)
WE = datetime(2026, 6, 26, 13, 20, 0, tzinfo=UTC)
START = Decimal("65000")


def _round(i: int, outcome: Outcome) -> Round:
    return Round(
        condition_id=f"0xc{i}",
        round_no=1782480000 + i * 300,
        token_id_up="u",
        token_id_down="d",
        window_start=WS,
        window_end=WE,
        start_price=START,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status=RoundStatus.RESOLVED,
        resolved_outcome=outcome,
    )


def _signals(prices: list[str], *, n_per: int = 1) -> list[Signal]:
    """Bangun deret signal di seluruh window (ts merata)."""
    out: list[Signal] = []
    total = max(len(prices), 1)
    for k, p in enumerate(prices):
        ts = WS + timedelta(seconds=300 * k / total)
        for _ in range(n_per):
            out.append(
                Signal(
                    round_no=1,
                    ts=ts,
                    price_now=Decimal(p),
                    delta=Decimal(p) - START,
                    time_left_sec=(WE - ts).total_seconds(),
                    p_win=Decimal("0"),
                    leader="UP",
                    ask_win=Decimal("0"),
                    net_edge=Decimal("0"),
                )
            )
    return out


class TestBucketing:
    def test_bucket_index(self) -> None:
        assert _bucket_index(0.50) == 0
        assert _bucket_index(0.59) == 0
        assert _bucket_index(0.60) == 1
        assert _bucket_index(0.95) == 4
        assert _bucket_index(1.0) == 4
        assert _bucket_index(0.40) == 0  # clamp


class TestStubFilter:
    def test_excludes_single_sample_stub(self) -> None:
        cal = Calibrator(min_samples=20)
        rnd = _round(0, Outcome.UP)
        # 1 sampel Δ=0 (stub pra-B9)
        assert cal.feed(rnd, _signals(["65000"])) is False
        res = cal.result()
        assert res.included_rounds == 0
        assert res.excluded_rounds == 1

    def test_excludes_constant_price(self) -> None:
        cal = Calibrator(min_samples=5)
        rnd = _round(0, Outcome.UP)
        # 30 sampel tapi harga konstan → Δ tak bergerak → stub.
        assert cal.feed(rnd, _signals(["65000"] * 30)) is False
        assert cal.result().excluded_rounds == 1

    def test_includes_good_round(self) -> None:
        cal = Calibrator(min_samples=5)
        rnd = _round(0, Outcome.UP)
        prices = [str(65000 + i * 10) for i in range(30)]  # bergerak naik
        assert cal.feed(rnd, _signals(prices)) is True
        assert cal.result().included_rounds == 1


class TestMetricsManual:
    def test_brier_logloss_small_example(self) -> None:
        # 1 ronde, 1 checkpoint, vol tunggal → cek Brier/logloss manual.
        cal = Calibrator(vols=[Decimal("10")], checkpoints=[10.0], min_samples=2)
        rnd = _round(0, Outcome.UP)
        # harga naik → leader UP == resolved UP → outcome 1.
        cal.feed(rnd, _signals(["65000", "65500"]))
        rep = cal.result().reports[0]
        assert rep.n == 1
        # p dihitung engine; Brier=(p-1)^2, logloss=-ln(p) → konsisten.
        p = float(rep.bins[_bucket_index_for(rep)].predicted)
        assert rep.brier == _quant((p - 1.0) ** 2)
        assert rep.logloss == _quant(-math.log(min(max(p, 1e-12), 1 - 1e-12)))


def _bucket_index_for(rep: VolReport) -> int:
    # bin satu-satunya yang berisi (count>0).
    for i, b in enumerate(rep.bins):
        if b.count > 0:
            return i
    return 0


def _quant(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


class TestRecommendation:
    def test_recommends_vol_near_true(self) -> None:
        # Data sintetik berlabel: outcome ditarik dari prob "benar" model vol*=20.
        # Calibrator harus merekomendasikan vol di sekitar 20 (Brier minimum).
        rng = random.Random(12345)
        true_vol = 20.0
        rounds: list[tuple[Round, list[Signal]]] = []
        for i in range(400):
            # Δ acak (gerak harga) → prob menang = Φ(|Δ| / (vol*sqrt(tl))).
            delta = rng.uniform(-300, 300)
            tl = 30.0
            z = abs(delta) / (true_vol * math.sqrt(tl))
            p_true = 0.5 * (1 + math.erf(z / math.sqrt(2)))
            leader_up = delta > 0
            leader_wins = rng.random() < p_true
            # outcome resolved: bila leader menang → resolved = leader side.
            if leader_up:
                resolved = Outcome.UP if leader_wins else Outcome.DOWN
            else:
                resolved = Outcome.DOWN if leader_wins else Outcome.UP
            # >= min_samples sampel bergerak (linear menuju harga checkpoint).
            prices = [str(65000 + delta * k / 10) for k in range(1, 11)]
            rounds.append((_round(i, resolved), _signals(prices)))

        cal = Calibrator(
            vols=[Decimal("5"), Decimal("10"), Decimal("20"), Decimal("40"), Decimal("80")],
            checkpoints=[30.0],
            min_samples=5,
        )
        for rnd, sigs in rounds:
            cal.feed(rnd, sigs)
        res = cal.result()
        assert res.recommended_vol is not None
        # Brier minimum di sekitar true_vol (20) — toleransi 1 langkah grid.
        assert res.recommended_vol in (Decimal("10"), Decimal("20"), Decimal("40"))


class TestDeterminism:
    def _run(self) -> str:
        cal = Calibrator(vols=[Decimal("10"), Decimal("40")], checkpoints=[30.0, 10.0])
        for i in range(10):
            outcome = Outcome.UP if i % 2 == 0 else Outcome.DOWN
            prices = [str(65000 + (i + 1) * 5 * k) for k in range(1, 26)]
            cal.feed(_round(i, outcome), _signals(prices))
        return format_result(cal.result()) + "\n" + to_csv(cal.result())

    def test_two_runs_identical(self) -> None:
        assert self._run() == self._run()


class TestFormatting:
    def test_format_and_csv(self) -> None:
        cal = Calibrator(vols=[Decimal("20")], checkpoints=[30.0], min_samples=5)
        for i in range(40):
            outcome = Outcome.UP if i % 3 else Outcome.DOWN
            prices = [str(65000 + (i + 1) * 7 * k) for k in range(1, 11)]
            cal.feed(_round(i, outcome), _signals(prices))
        res = cal.result()
        text = format_result(res)
        assert "Ronde disertakan" in text
        assert "Reliability" in text
        csv = to_csv(res)
        assert csv.startswith("vol,brier,logloss,ece,n,bin_lo,bin_hi,count,pred,real")

    def test_empty_result_message(self) -> None:
        cal = Calibrator(min_samples=20)
        cal.feed(_round(0, Outcome.UP), _signals(["65000"]))  # stub → excluded
        text = format_result(cal.result())
        assert "tak bisa dihitung" in text
