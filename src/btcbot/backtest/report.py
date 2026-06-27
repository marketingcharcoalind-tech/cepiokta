"""backtest/report.py — metrik & laporan backtest (docs/09 §9.4).

Hitung metrik dari :class:`~btcbot.backtest.replay.ReplaySummary` (murni):
Net PnL, ROI, win-rate, distribusi ``net_edge`` saat entry, **reliability curve**
(``p_win`` prediksi vs hit-rate realisasi — pakai label nyata Gamma), max drawdown,
varians, **sensitivity grid** (``T_ENTRY_SEC`` x ``DELTA_THRESHOLD`` x ``MAX_PRICE``),
dan **ablation** (dengan vs tanpa fee/slippage/latensi).

Headline = **Net PnL setelah fee** (PROMPT_GUIDE ✅ VERIFIED REALITY #3). Plot PNG
opsional (matplotlib) bila terpasang; default output tabel teks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplaySummary
from btcbot.domain.fees import ZeroFee

if TYPE_CHECKING:
    from collections.abc import Sequence

    from btcbot.backtest.replay import ReplayTick, RoundDiagnostics
    from btcbot.domain.models import Round

_ZERO = Decimal("0")


# ----- struktur metrik -----


@dataclass(frozen=True, slots=True)
class Distribution:
    """Ringkasan distribusi (mis. net_edge saat entry)."""

    count: int
    minimum: Decimal
    p25: Decimal
    median: Decimal
    mean: Decimal
    p75: Decimal
    maximum: Decimal


@dataclass(frozen=True, slots=True)
class ReliabilityBucket:
    """Satu bin reliability curve: prediksi p_win vs realisasi hit-rate."""

    lo: Decimal
    hi: Decimal
    count: int
    predicted: Decimal  # rata-rata p_win prediksi di bin
    realized: Decimal  # fraksi menang (label Gamma) di bin


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Laporan metrik backtest lengkap (docs/09 §9.4)."""

    rounds_total: int
    rounds_entered: int
    wins: int
    losses: int
    win_rate: Decimal
    starting_balance: Decimal
    final_balance: Decimal
    net_pnl: Decimal
    roi: Decimal
    pnl_mean: Decimal
    pnl_variance: Decimal
    pnl_stdev: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    net_edge_dist: Distribution
    reliability: tuple[ReliabilityBucket, ...]


@dataclass(frozen=True, slots=True)
class GridCell:
    """Satu sel sensitivity grid."""

    t_entry_sec: int
    delta_threshold: Decimal
    max_price: Decimal
    rounds_entered: int
    net_pnl: Decimal
    roi: Decimal
    win_rate: Decimal


@dataclass(frozen=True, slots=True)
class AblationRow:
    """Satu baris ablation (fee/slippage/latensi on/off)."""

    name: str
    rounds_entered: int
    net_pnl: Decimal
    roi: Decimal
    win_rate: Decimal


# ----- helper numerik (Decimal) -----


def _percentile(sorted_vals: list[Decimal], q: Decimal) -> Decimal:
    """Persentil ``q`` (0..1) via interpolasi linear (nearest-rank sederhana)."""
    if not sorted_vals:
        return _ZERO
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * Decimal(len(sorted_vals) - 1)
    lo_idx = int(pos)
    frac = pos - Decimal(lo_idx)
    if lo_idx + 1 >= len(sorted_vals):
        return sorted_vals[-1]
    return sorted_vals[lo_idx] + frac * (sorted_vals[lo_idx + 1] - sorted_vals[lo_idx])


def compute_distribution(values: Sequence[Decimal]) -> Distribution:
    """Hitung ringkasan distribusi dari ``values``."""
    if not values:
        return Distribution(0, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO)
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered, _ZERO) / Decimal(n)
    return Distribution(
        count=n,
        minimum=ordered[0],
        p25=_percentile(ordered, Decimal("0.25")),
        median=_percentile(ordered, Decimal("0.5")),
        mean=mean,
        p75=_percentile(ordered, Decimal("0.75")),
        maximum=ordered[-1],
    )


def _variance(values: Sequence[Decimal]) -> Decimal:
    """Varians populasi (Decimal)."""
    n = len(values)
    if n == 0:
        return _ZERO
    mean = sum(values, _ZERO) / Decimal(n)
    return sum(((v - mean) ** 2 for v in values), _ZERO) / Decimal(n)


def _sqrt(value: Decimal) -> Decimal:
    """Akar kuadrat (Decimal via float; cukup untuk pelaporan)."""
    if value <= _ZERO:
        return _ZERO
    return Decimal(str(math.sqrt(float(value))))


def max_drawdown(balances: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """Drawdown maksimum (absolut, persen) dari kurva ``balances``."""
    peak = None
    max_dd = _ZERO
    max_dd_pct = _ZERO
    for bal in balances:
        if peak is None or bal > peak:
            peak = bal
        dd = peak - bal
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * Decimal("100")) if peak and peak > _ZERO else _ZERO
    return max_dd, max_dd_pct


def compute_reliability(
    diagnostics: Sequence[RoundDiagnostics],
    *,
    n_bins: int = 5,
    lo: Decimal = Decimal("0.5"),
    hi: Decimal = Decimal("1.0"),
) -> tuple[ReliabilityBucket, ...]:
    """Reliability curve: bin ``p_win`` prediksi vs hit-rate realisasi (label Gamma)."""
    if n_bins <= 0 or hi <= lo:
        return ()
    width = (hi - lo) / Decimal(n_bins)
    buckets: list[ReliabilityBucket] = []
    for b in range(n_bins):
        b_lo = lo + width * Decimal(b)
        b_hi = b_lo + width
        in_bin = [
            d
            for d in diagnostics
            if b_lo <= d.p_win_entry < b_hi or (b == n_bins - 1 and d.p_win_entry == b_hi)
        ]
        count = len(in_bin)
        if count == 0:
            buckets.append(ReliabilityBucket(b_lo, b_hi, 0, _ZERO, _ZERO))
            continue
        predicted = sum((d.p_win_entry for d in in_bin), _ZERO) / Decimal(count)
        realized = Decimal(sum(1 for d in in_bin if d.won)) / Decimal(count)
        buckets.append(ReliabilityBucket(b_lo, b_hi, count, predicted, realized))
    return tuple(buckets)


def build_report(summary: ReplaySummary, starting_balance: Decimal) -> BacktestReport:
    """Bangun :class:`BacktestReport` dari :class:`ReplaySummary`."""
    pnls = [r.pnl for r in summary.results]
    net_edges = [d.net_edge_entry for d in summary.diagnostics]

    balances: list[Decimal] = [starting_balance]
    running = starting_balance
    for r in summary.results:
        running = r.balance_after
        balances.append(running)
    dd, dd_pct = max_drawdown(balances)

    entered = summary.rounds_entered
    win_rate = Decimal(summary.wins) / Decimal(entered) if entered else _ZERO
    variance = _variance(pnls)
    return BacktestReport(
        rounds_total=summary.rounds_total,
        rounds_entered=entered,
        wins=summary.wins,
        losses=summary.losses,
        win_rate=win_rate,
        starting_balance=starting_balance,
        final_balance=summary.final_balance,
        net_pnl=summary.total_pnl,
        roi=(summary.total_pnl / starting_balance if starting_balance > _ZERO else _ZERO),
        pnl_mean=(sum(pnls, _ZERO) / Decimal(len(pnls)) if pnls else _ZERO),
        pnl_variance=variance,
        pnl_stdev=_sqrt(variance),
        max_drawdown=dd,
        max_drawdown_pct=dd_pct,
        net_edge_dist=compute_distribution(net_edges),
        reliability=compute_reliability(summary.diagnostics),
    )


# ----- sensitivity & ablation (re-run engine) -----


def sensitivity_grid(
    rounds: Sequence[tuple[Round, Sequence[ReplayTick]]],
    base: ReplayConfig,
    *,
    t_entry_values: Sequence[int],
    delta_values: Sequence[Decimal],
    max_price_values: Sequence[Decimal],
) -> list[GridCell]:
    """Jalankan replay lintas grid (T_ENTRY x DELTA x MAX_PRICE) → daftar sel."""
    cells: list[GridCell] = []
    for t_entry in t_entry_values:
        for delta in delta_values:
            for max_price in max_price_values:
                params = replace(
                    base.params,
                    t_entry_sec=t_entry,
                    delta_threshold=delta,
                    max_price=max_price,
                )
                limits = replace(base.limits, max_price=max_price)
                cfg = replace(base, params=params, limits=limits)
                summary = ReplayEngine(cfg).run(rounds)
                entered = summary.rounds_entered
                cells.append(
                    GridCell(
                        t_entry_sec=t_entry,
                        delta_threshold=delta,
                        max_price=max_price,
                        rounds_entered=entered,
                        net_pnl=summary.total_pnl,
                        roi=(
                            summary.total_pnl / base.starting_balance
                            if base.starting_balance > _ZERO
                            else _ZERO
                        ),
                        win_rate=(Decimal(summary.wins) / Decimal(entered) if entered else _ZERO),
                    )
                )
    return cells


def _ablation_row(
    name: str,
    rounds: Sequence[tuple[Round, Sequence[ReplayTick]]],
    cfg: ReplayConfig,
) -> AblationRow:
    summary = ReplayEngine(cfg).run(rounds)
    entered = summary.rounds_entered
    return AblationRow(
        name=name,
        rounds_entered=entered,
        net_pnl=summary.total_pnl,
        roi=(summary.total_pnl / cfg.starting_balance if cfg.starting_balance > _ZERO else _ZERO),
        win_rate=(Decimal(summary.wins) / Decimal(entered) if entered else _ZERO),
    )


def ablation(
    rounds: Sequence[tuple[Round, Sequence[ReplayTick]]],
    base: ReplayConfig,
) -> list[AblationRow]:
    """Ablation: baseline vs tanpa fee / tanpa slippage / tanpa latensi.

    Edge sejati biasanya hilang setelah fee — itulah gunanya membandingkan.
    """
    return [
        _ablation_row("baseline (fee+slippage+latency)", rounds, base),
        _ablation_row("no_fee", rounds, replace(base, fee_model=ZeroFee())),
        _ablation_row("no_slippage", rounds, replace(base, slippage_enabled=False)),
        _ablation_row("no_latency", rounds, replace(base, latency_ticks=0)),
    ]


# ----- formatting (tabel teks) -----


def _fmt(value: Decimal, places: str = "0.0001") -> str:
    return str(value.quantize(Decimal(places)))


def format_report(report: BacktestReport) -> str:
    """Render :class:`BacktestReport` sebagai tabel teks (headline Net PnL)."""
    lines = [
        "=== BACKTEST REPORT (mode=backtest) ===",
        f"Net PnL (setelah fee) : {_fmt(report.net_pnl, '0.01')}",
        f"ROI                   : {_fmt(report.roi * 100, '0.01')}%",
        f"Start / Final balance : {_fmt(report.starting_balance, '0.01')} "
        f"-> {_fmt(report.final_balance, '0.01')}",
        f"Rounds total/entered  : {report.rounds_total} / {report.rounds_entered}",
        f"Win / Loss / Win-rate : {report.wins} / {report.losses} / "
        f"{_fmt(report.win_rate * 100, '0.1')}%",
        f"PnL mean / stdev      : {_fmt(report.pnl_mean, '0.0001')} / "
        f"{_fmt(report.pnl_stdev, '0.0001')}",
        f"Max drawdown          : {_fmt(report.max_drawdown, '0.01')} "
        f"({_fmt(report.max_drawdown_pct, '0.01')}%)",
        "",
        "net_edge @ entry (distribusi):",
        f"  n={report.net_edge_dist.count} min={_fmt(report.net_edge_dist.minimum)} "
        f"p25={_fmt(report.net_edge_dist.p25)} median={_fmt(report.net_edge_dist.median)} "
        f"mean={_fmt(report.net_edge_dist.mean)} p75={_fmt(report.net_edge_dist.p75)} "
        f"max={_fmt(report.net_edge_dist.maximum)}",
        "",
        "reliability curve (p_win prediksi vs realisasi — label Gamma):",
        "  bin            n    predicted  realized",
    ]
    for b in report.reliability:
        lines.append(
            f"  [{_fmt(b.lo, '0.01')},{_fmt(b.hi, '0.01')})  {b.count:>4}  "
            f"{_fmt(b.predicted, '0.001'):>9}  {_fmt(b.realized, '0.001'):>8}"
        )
    return "\n".join(lines)


def format_grid(cells: Sequence[GridCell]) -> str:
    """Render sensitivity grid sebagai tabel teks."""
    lines = [
        "=== SENSITIVITY GRID (T_ENTRY x DELTA x MAX_PRICE) ===",
        "  t_entry  delta      max_price  entered  net_pnl     roi%     win%",
    ]
    for c in cells:
        lines.append(
            f"  {c.t_entry_sec:>7}  {_fmt(c.delta_threshold, '0.01'):>9}  "
            f"{_fmt(c.max_price, '0.01'):>9}  {c.rounds_entered:>7}  "
            f"{_fmt(c.net_pnl, '0.01'):>10}  {_fmt(c.roi * 100, '0.01'):>6}  "
            f"{_fmt(c.win_rate * 100, '0.1'):>6}"
        )
    return "\n".join(lines)


def format_ablation(rows: Sequence[AblationRow]) -> str:
    """Render tabel ablation."""
    lines = [
        "=== ABLATION (dengan vs tanpa fee/slippage/latensi) ===",
        "  variant                          entered  net_pnl     roi%     win%",
    ]
    for r in rows:
        lines.append(
            f"  {r.name:<32} {r.rounds_entered:>7}  {_fmt(r.net_pnl, '0.01'):>10}  "
            f"{_fmt(r.roi * 100, '0.01'):>6}  {_fmt(r.win_rate * 100, '0.1'):>6}"
        )
    return "\n".join(lines)
