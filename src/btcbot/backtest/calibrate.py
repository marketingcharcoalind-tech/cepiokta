"""backtest/calibrate.py — kalibrasi volatilitas via reliability curve (G1 prereq).

``backtest_vol_per_sqrt_sec`` mencemari ``p_win``/``net_edge``/``delta_threshold(auto)``
bila belum dikalibrasi (model overconfident). Tool ini, untuk grid kandidat vol,
membangun **reliability curve** (prediksi ``p_win`` vs realized win-rate leader)
atas ronde resolved **berdata-bagus**, lalu merekomendasikan vol dengan kalibrasi
terbaik (**Brier minimum**, tie-break **ECE** terkecil).

Asumsi & batasan kalibrasi:
- Sampel = (ronde x checkpoint ``time_left``). Untuk tiap checkpoint dipilih tick
  spot terdekat; ``leader`` = arah Δ saat itu; ``outcome`` = ``leader == resolved``.
- ``p_win`` dihitung memakai :meth:`SignalEngine.compute` (TIDAK mengubah logikanya —
  hanya memanggil dengan ``vol`` berbeda).
- Ronde **stub** (pra-fix B9: 1 sampel / Δ konstan) DIKECUALIKAN (racun kalibrasi).
- Hanya MEMBACA data historis (streaming, memory-safe pola B7); TIDAK menulis DB /
  settings / jalur trading. Rekomendasi vol = keputusan **manual** operator.

Deterministik (tanpa randomness): run identik → hasil identik.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.config.settings import get_settings
from btcbot.data.store import Store
from btcbot.domain.signal import SignalEngine

if TYPE_CHECKING:
    from collections.abc import Sequence

    from btcbot.config.settings import Settings
    from btcbot.domain.models import Round, Signal

# Default grid & checkpoint (dapat di-override via CLI).
_DEFAULT_VOLS: tuple[Decimal, ...] = (
    Decimal("5"),
    Decimal("10"),
    Decimal("20"),
    Decimal("40"),
    Decimal("80"),
    Decimal("160"),
)
_DEFAULT_CHECKPOINTS: tuple[float, ...] = (60.0, 45.0, 30.0, 20.0, 10.0, 5.0)
_DEFAULT_MIN_SAMPLES = 20
_UNDERPOPULATED = 30  # bin dgn 0<count<ini → peringatan "data belum cukup"
_OVERCONFIDENT_ECE = 0.15  # ECE >= ini → tandai vol OVERCONFIDENT
_BINS = 5  # [0.5,0.6),[0.6,0.7),[0.7,0.8),[0.8,0.9),[0.9,1.0]
_LOGLOSS_EPS = 1e-12
_BUCKET_EPS = 1e-9  # hindari salah-bin di batas (mis. 0.60 jatuh ke bin 0 karena float)


def _bucket_index(p: float) -> int:
    """Indeks bin reliability untuk ``p`` (clamp ke [0, 4]); bin selebar 0.1 dari 0.5."""
    idx = int((p - 0.5) / 0.1 + _BUCKET_EPS)
    return max(0, min(idx, _BINS - 1))


def _bin_bounds(idx: int) -> tuple[Decimal, Decimal]:
    lo = Decimal("0.5") + Decimal("0.1") * idx
    return lo, lo + Decimal("0.1")


@dataclass
class _BinAcc:
    count: int = 0
    pred_sum: float = 0.0
    win_sum: int = 0


@dataclass
class _VolAcc:
    """Akumulator inkremental satu kandidat vol (memory-safe)."""

    vol: Decimal
    brier_sum: float = 0.0
    logloss_sum: float = 0.0
    n: int = 0
    bins: list[_BinAcc] = field(default_factory=lambda: [_BinAcc() for _ in range(_BINS)])

    def add(self, p: float, outcome: int) -> None:
        self.n += 1
        self.brier_sum += (p - outcome) ** 2
        pc = min(max(p, _LOGLOSS_EPS), 1.0 - _LOGLOSS_EPS)
        self.logloss_sum += -(outcome * math.log(pc) + (1 - outcome) * math.log(1.0 - pc))
        b = self.bins[_bucket_index(p)]
        b.count += 1
        b.pred_sum += p
        b.win_sum += outcome


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lo: Decimal
    hi: Decimal
    count: int
    predicted: Decimal
    realized: Decimal


@dataclass(frozen=True, slots=True)
class VolReport:
    vol: Decimal
    brier: Decimal
    logloss: Decimal
    ece: Decimal
    n: int
    bins: tuple[ReliabilityBin, ...]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    included_rounds: int
    excluded_rounds: int
    checkpoints: tuple[float, ...]
    reports: tuple[VolReport, ...]
    recommended_vol: Decimal | None


def _q(value: float, places: str = "0.0001") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places))


def _is_stub(signals: Sequence[Signal], min_samples: int) -> bool:
    """Ronde stub bila < ``min_samples`` sampel ATAU harga konstan (Δ tak bergerak)."""
    if len(signals) < min_samples:
        return True
    prices = {s.price_now for s in signals}
    return len(prices) <= 1


def _nearest_signal(signals: Sequence[Signal], window_end: datetime, target_tl: float) -> Signal:
    """Pilih signal yang ``time_left`` (window_end - ts) terdekat ke ``target_tl``."""
    return min(
        signals,
        key=lambda s: abs((window_end - s.ts).total_seconds() - target_tl),
    )


class Calibrator:
    """Kalibrator reliability-curve (streaming; deterministik).

    Args:
        vols: Grid kandidat volatilitas (per √detik).
        checkpoints: Target ``time_left`` (detik) — sampel diambil di tick terdekat.
        min_samples: Ambang minimum sampel spot agar ronde disertakan.
        engine: SignalEngine (default baru) — hanya dipakai ``compute``.
    """

    def __init__(
        self,
        *,
        vols: Sequence[Decimal] = _DEFAULT_VOLS,
        checkpoints: Sequence[float] = _DEFAULT_CHECKPOINTS,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        engine: SignalEngine | None = None,
    ) -> None:
        self._vols = tuple(vols)
        self._checkpoints = tuple(checkpoints)
        self._min_samples = min_samples
        self._engine = engine or SignalEngine()
        self._accs = [_VolAcc(vol=v) for v in self._vols]
        self._included = 0
        self._excluded = 0

    def feed(self, rnd: Round, signals: Sequence[Signal]) -> bool:
        """Proses satu ronde. Kembalikan True bila disertakan (bukan stub)."""
        if rnd.resolved_outcome is None or _is_stub(signals, self._min_samples):
            self._excluded += 1
            return False
        self._included += 1
        resolved = rnd.resolved_outcome.value
        for target_tl in self._checkpoints:
            sig = _nearest_signal(signals, rnd.window_end, target_tl)
            # leader & outcome vol-independent (tergantung Δ saja).
            base = self._engine.compute(rnd, sig.price_now, sig.ts, self._vols[0])
            outcome = 1 if base.leader == resolved else 0
            for acc in self._accs:
                p = float(self._engine.compute(rnd, sig.price_now, sig.ts, acc.vol).p_win)
                acc.add(p, outcome)
        return True

    def result(self) -> CalibrationResult:
        """Rakit :class:`CalibrationResult` + rekomendasi (Brier min, tie ECE)."""
        reports = tuple(self._report(acc) for acc in self._accs)
        ranked = [r for r in reports if r.n > 0]
        recommended = min(ranked, key=lambda r: (r.brier, r.ece)).vol if ranked else None
        return CalibrationResult(
            included_rounds=self._included,
            excluded_rounds=self._excluded,
            checkpoints=self._checkpoints,
            reports=reports,
            recommended_vol=recommended,
        )

    def _report(self, acc: _VolAcc) -> VolReport:
        bins: list[ReliabilityBin] = []
        ece = 0.0
        for idx, b in enumerate(acc.bins):
            lo, hi = _bin_bounds(idx)
            pred = b.pred_sum / b.count if b.count else 0.0
            real = b.win_sum / b.count if b.count else 0.0
            if b.count and acc.n:
                ece += (b.count / acc.n) * abs(pred - real)
            bins.append(ReliabilityBin(lo, hi, b.count, _q(pred, "0.001"), _q(real, "0.001")))
        brier = acc.brier_sum / acc.n if acc.n else 0.0
        logloss = acc.logloss_sum / acc.n if acc.n else 0.0
        return VolReport(
            vol=acc.vol,
            brier=_q(brier),
            logloss=_q(logloss),
            ece=_q(ece),
            n=acc.n,
            bins=tuple(bins),
        )


async def run_calibration(  # noqa: PLR0913 - flag CLI eksplisit (keyword-only)
    settings: Settings,
    *,
    db: str | None = None,
    vols: Sequence[Decimal] = _DEFAULT_VOLS,
    checkpoints: Sequence[float] = _DEFAULT_CHECKPOINTS,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> CalibrationResult:
    """Stream ronde resolved (SQL pushdown B7), kalibrasi, kembalikan hasil."""
    calibrator = Calibrator(vols=vols, checkpoints=checkpoints, min_samples=min_samples)
    store = await Store.open(db or settings.db_url)
    try:
        rounds = await store.get_resolved_rounds(since=since, until=until, limit=limit)
        for rnd in rounds:
            signals = await store.get_signals(rnd.round_no)
            calibrator.feed(rnd, signals)
    finally:
        await store.close()
    return calibrator.result()


# ----- formatting & CSV -----


def format_result(result: CalibrationResult) -> str:
    """Render hasil kalibrasi: ringkasan metrik per vol + reliability rekomendasi."""
    lines = [
        f"Ronde disertakan: {result.included_rounds} | "
        f"dikecualikan (stub): {result.excluded_rounds} | "
        f"checkpoints: {len(result.checkpoints)}",
        "",
    ]
    if result.recommended_vol is None:
        lines.append("Tidak ada ronde berdata-bagus -> kalibrasi tak bisa dihitung.")
        lines.append("Kumpulkan data soak spot-nyata (pasca-fix B9) lalu ulangi.")
        return "\n".join(lines)

    for r in result.reports:
        tag = ""
        if r.vol == result.recommended_vol:
            tag = "  <-- REKOMENDASI (Brier min)"
        elif r.n and float(r.ece) >= _OVERCONFIDENT_ECE:
            tag = "  <-- OVERCONFIDENT"
        lines.append(
            f"vol={r.vol!s:<5} Brier={r.brier}  logloss={r.logloss}  " f"ECE={r.ece}  n={r.n}{tag}"
        )

    rec = next(r for r in result.reports if r.vol == result.recommended_vol)
    lines.append("")
    lines.append(f"Reliability (vol={rec.vol}):")
    warn = False
    for b in rec.bins:
        flag = ""
        if 0 < b.count < _UNDERPOPULATED:
            flag = "  (!) data belum cukup"
            warn = True
        lines.append(f"  bin[{b.lo}-{b.hi}) pred={b.predicted} real={b.realized} n={b.count}{flag}")
    if warn:
        lines.append("")
        lines.append(
            "PERINGATAN: sebagian bin under-populated (< "
            f"{_UNDERPOPULATED} sampel) -> rekomendasi belum kokoh; kumpulkan lebih banyak data."
        )
    lines.append("")
    lines.append(
        "Catatan: rekomendasi hanya DICETAK. Set backtest_vol_per_sqrt_sec secara MANUAL "
        "(keputusan operator), bukan otomatis."
    )
    return "\n".join(lines)


def to_csv(result: CalibrationResult) -> str:
    """Ekspor reliability per (vol, bin) sebagai CSV deterministik."""
    rows = ["vol,brier,logloss,ece,n,bin_lo,bin_hi,count,pred,real"]
    for r in result.reports:
        for b in r.bins:
            rows.append(
                f"{r.vol},{r.brier},{r.logloss},{r.ece},{r.n},"
                f"{b.lo},{b.hi},{b.count},{b.predicted},{b.realized}"
            )
    return "\n".join(rows) + "\n"


# ----- CLI -----


def _decimal_list(value: str) -> list[Decimal]:
    return [Decimal(x) for x in value.split(",") if x.strip()]


def _float_list(value: str) -> list[float]:
    return [float(x) for x in value.split(",") if x.strip()]


def _parse_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="btcbot-calibrate",
        description="Kalibrasi volatilitas via reliability curve (prasyarat G1).",
    )
    p.add_argument("--db", default=None, help="path/URL DB (default: Settings.db_url)")
    p.add_argument("--days", type=int, default=None, help="hanya ronde N hari terakhir")
    p.add_argument("--max-rounds", type=int, default=None, help="batasi N ronde terbaru")
    p.add_argument("--since", default=None, help="filter window_end >= ISO-8601")
    p.add_argument("--until", default=None, help="filter window_end <= ISO-8601")
    p.add_argument("--vol-grid", default=None, help="grid vol (mis. 5,10,20,40,80,160)")
    p.add_argument(
        "--checkpoints", default=None, help="checkpoint time_left (mis. 60,45,30,20,10,5)"
    )
    p.add_argument(
        "--min-samples",
        type=int,
        default=_DEFAULT_MIN_SAMPLES,
        help=f"min sampel spot/ronde agar disertakan (default {_DEFAULT_MIN_SAMPLES})",
    )
    p.add_argument("--csv", default=None, help="ekspor reliability ke file CSV")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry-point: ``python -m btcbot.backtest.calibrate [flags]``."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()

    since = _parse_iso(args.since) if args.since else None
    until = _parse_iso(args.until) if args.until else None
    if args.days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=args.days)
        since = max(since, cutoff) if since is not None else cutoff

    vols = _decimal_list(args.vol_grid) if args.vol_grid else list(_DEFAULT_VOLS)
    checkpoints = _float_list(args.checkpoints) if args.checkpoints else list(_DEFAULT_CHECKPOINTS)

    result = asyncio.run(
        run_calibration(
            settings,
            db=args.db,
            vols=vols,
            checkpoints=checkpoints,
            min_samples=args.min_samples,
            since=since,
            until=until,
            limit=args.max_rounds,
        )
    )
    print(format_result(result))  # noqa: T201 - output laporan ke stdout
    if args.csv:
        from pathlib import Path  # noqa: PLC0415 - hanya saat ekspor

        Path(args.csv).write_text(to_csv(result), encoding="utf-8")
        print(f"[csv] ditulis ke {args.csv}", file=sys.stderr)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
