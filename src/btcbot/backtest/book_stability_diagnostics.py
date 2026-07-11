"""backtest/book_stability_diagnostics.py — Read-only book stability analysis.

Analyzes post-entry book behavior for already-entered replay trades to measure
whether book instability/whipsaw signals could detect dangerous rounds.

G1 REVISI context: analisis5.db sole loss (round 1783520100) was a book whipsaw case:
- Entry: UP @0.96, time_left 57.7s, delta +125, p_win 0.99864
- Outcome: DOWN, pnl -$5.01
- Book showed panic/reprice mid-window (UP ask crashed, DOWN bid spiked)

Purpose: READ-ONLY diagnostic to answer: "Would book-instability warning have
detected this loss, and how many winning trades would it also flag?"

This is measurement only. Does NOT change strategy, replay fill logic, or create
execution paths. Does NOT proceed to Phase 2. Read-only/observability.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayConfig, ReplayEngine
from btcbot.data.store import Store
from btcbot.domain.models import Outcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from btcbot.backtest.replay import RoundResult
    from btcbot.data.store import BookSnapshot

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class BookStabilityMetrics:
    """Book stability metrics for one entered trade (post-entry behavior)."""

    # Identity
    round_no: int
    entry_ts: datetime
    time_left_entry: float  # seconds from entry to window_end
    side_taken: str  # UP/DOWN
    resolved_outcome: str  # UP/DOWN
    result: str  # WIN/LOSS
    pnl: Decimal

    # Entry state
    entry_price: Decimal

    # Post-entry book extremes (leader = side_taken)
    min_leader_bid_after_entry: Decimal | None
    max_opposite_bid_after_entry: Decimal | None
    min_leader_ask_after_entry: Decimal | None
    max_opposite_ask_after_entry: Decimal | None

    # Derived metrics
    leader_bid_drawdown: Decimal  # entry_price - min_leader_bid
    opposite_bid_spike: Decimal  # max_opposite_bid
    leader_ask_drawdown: Decimal  # entry_price - min_leader_ask

    # Instability flags (True = warning triggered)
    leader_bid_below_0_95: bool
    leader_bid_below_0_90: bool
    leader_ask_below_0_95: bool
    leader_ask_below_0_90: bool
    opposite_bid_above_0_05: bool
    opposite_bid_above_0_10: bool
    opposite_bid_above_0_15: bool
    book_flip_warning: bool  # composite warning flag

    # Timing of first instability
    first_instability_ts: datetime | None
    seconds_after_entry_to_instability: float | None
    time_left_at_instability: float | None


@dataclass
class StabilityThresholds:
    """Configurable thresholds for book instability detection."""

    leader_bid_warn: Decimal = Decimal("0.90")
    opposite_bid_warn: Decimal = Decimal("0.10")
    leader_ask_warn: Decimal = Decimal("0.93")
    drawdown_warn: Decimal = Decimal("0.06")


@dataclass
class BucketStats:
    """Statistics for a bucket (e.g., by side, by flag)."""

    entries: int = 0
    wins: int = 0
    losses: int = 0
    pnl: Decimal = _ZERO

    def win_rate(self) -> float:
        """Win rate percentage."""
        return 100.0 * self.wins / self.entries if self.entries > 0 else 0.0

    def avg_pnl(self) -> Decimal:
        """Average PnL per entry."""
        return self.pnl / self.entries if self.entries > 0 else _ZERO


@dataclass
class BookStabilityDiagnostics:
    """Aggregated book stability diagnostics."""

    metrics: list[BookStabilityMetrics] = field(default_factory=list)

    def add(self, m: BookStabilityMetrics) -> None:
        """Add one trade's metrics."""
        self.metrics.append(m)

    def summary(self) -> dict[str, object]:
        """Overall summary statistics."""
        if not self.metrics:
            return {
                "total_entries": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_pnl": _ZERO,
            }
        wins = sum(1 for m in self.metrics if m.result == "WIN")
        losses = sum(1 for m in self.metrics if m.result == "LOSS")
        pnl = sum((m.pnl for m in self.metrics), _ZERO)
        return {
            "total_entries": len(self.metrics),
            "wins": wins,
            "losses": losses,
            "win_rate": 100.0 * wins / len(self.metrics),
            "net_pnl": pnl,
        }

    def by_side(self) -> dict[str, BucketStats]:
        """Statistics by side UP/DOWN."""
        buckets: dict[str, BucketStats] = {}
        for m in self.metrics:
            if m.side_taken not in buckets:
                buckets[m.side_taken] = BucketStats()
            b = buckets[m.side_taken]
            b.entries += 1
            if m.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl += m.pnl
        return buckets

    def by_flag(self, flag_name: str) -> dict[str, BucketStats]:
        """Statistics by flag True/False."""
        buckets: dict[str, BucketStats] = {"True": BucketStats(), "False": BucketStats()}
        for m in self.metrics:
            flag_val = getattr(m, flag_name)
            key = "True" if flag_val else "False"
            b = buckets[key]
            b.entries += 1
            if m.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl += m.pnl
        return buckets

    def instability_timing_stats(self) -> dict[str, object]:
        """Average seconds to instability among wins/losses with warnings."""
        wins_with_warn = [
            m for m in self.metrics if m.result == "WIN" and m.book_flip_warning
        ]
        losses_with_warn = [
            m for m in self.metrics if m.result == "LOSS" and m.book_flip_warning
        ]
        avg_wins = (
            sum(
                (
                    m.seconds_after_entry_to_instability
                    for m in wins_with_warn
                    if m.seconds_after_entry_to_instability is not None
                ),
                0.0,
            )
            / len(wins_with_warn)
            if wins_with_warn
            else None
        )
        avg_losses = (
            sum(
                (
                    m.seconds_after_entry_to_instability
                    for m in losses_with_warn
                    if m.seconds_after_entry_to_instability is not None
                ),
                0.0,
            )
            / len(losses_with_warn)
            if losses_with_warn
            else None
        )
        return {
            "avg_seconds_to_instability_wins": avg_wins,
            "avg_seconds_to_instability_losses": avg_losses,
        }


def _compute_stability_metrics(
    result: RoundResult,
    round_window_end: datetime,
    post_entry_snapshots: Sequence[BookSnapshot],
    thresholds: StabilityThresholds,
    entry_ts: datetime,
    token_id_up: str,
    token_id_down: str,
) -> BookStabilityMetrics:
    """Compute book stability metrics for one entered trade.

    Args:
        result: RoundResult from ReplayEngine (entry details, PnL)
        round_window_end: window_end datetime for time_left calculation
        post_entry_snapshots: book snapshots from entry_ts to window_end
        thresholds: stability warning thresholds
        entry_ts: approximate entry timestamp
        token_id_up: UP token ID from round
        token_id_down: DOWN token ID from round

    Returns:
        BookStabilityMetrics with all flags and extremes computed.
    """
    # Leader side determination
    leader = Outcome(result.side_taken)
    opposite = Outcome.DOWN if leader is Outcome.UP else Outcome.UP

    # Map to exact token IDs (do NOT use endswith - token IDs are not suffixed with UP/DOWN)
    leader_token = token_id_up if leader is Outcome.UP else token_id_down
    opposite_token = token_id_down if leader is Outcome.UP else token_id_up

    # Separate snapshots by exact token_id equality
    leader_snaps = [s for s in post_entry_snapshots if s.token_id == leader_token]
    opposite_snaps = [s for s in post_entry_snapshots if s.token_id == opposite_token]

    # Compute extremes
    min_leader_bid = min((s.best_bid for s in leader_snaps if s.best_bid is not None), default=None)
    max_opposite_bid = max((s.best_bid for s in opposite_snaps if s.best_bid is not None), default=None)
    min_leader_ask = min((s.best_ask for s in leader_snaps if s.best_ask is not None), default=None)
    max_opposite_ask = max((s.best_ask for s in opposite_snaps if s.best_ask is not None), default=None)

    # Derived metrics
    leader_bid_drawdown = result.entry_price - min_leader_bid if min_leader_bid is not None else _ZERO
    opposite_bid_spike = max_opposite_bid if max_opposite_bid is not None else _ZERO
    leader_ask_drawdown = result.entry_price - min_leader_ask if min_leader_ask is not None else _ZERO

    # Flags
    flag_lb_95 = min_leader_bid is not None and min_leader_bid <= Decimal("0.95")
    flag_lb_90 = min_leader_bid is not None and min_leader_bid <= thresholds.leader_bid_warn
    flag_la_95 = min_leader_ask is not None and min_leader_ask <= Decimal("0.95")
    flag_la_90 = min_leader_ask is not None and min_leader_ask <= thresholds.leader_ask_warn
    flag_ob_05 = max_opposite_bid is not None and max_opposite_bid >= Decimal("0.05")
    flag_ob_10 = max_opposite_bid is not None and max_opposite_bid >= thresholds.opposite_bid_warn
    flag_ob_15 = max_opposite_bid is not None and max_opposite_bid >= Decimal("0.15")

    # Composite book_flip_warning
    book_flip_warning = (
        flag_lb_90
        or flag_ob_10
        or flag_la_90
        or (leader_bid_drawdown >= thresholds.drawdown_warn)
    )

    # Find first instability timestamp
    first_instability_ts: datetime | None = None
    for snap in sorted(post_entry_snapshots, key=lambda s: s.ts):
        is_leader = snap.token_id == leader_token
        triggered = False
        if is_leader:
            if snap.best_bid is not None and snap.best_bid <= thresholds.leader_bid_warn:
                triggered = True
            if snap.best_ask is not None and snap.best_ask <= thresholds.leader_ask_warn:
                triggered = True
            if snap.best_bid is not None:
                dd = result.entry_price - snap.best_bid
                if dd >= thresholds.drawdown_warn:
                    triggered = True
        else:  # opposite
            if snap.best_bid is not None and snap.best_bid >= thresholds.opposite_bid_warn:
                triggered = True
        if triggered:
            first_instability_ts = snap.ts
            break

    # Timing calculations with provided entry_ts
    time_left_entry_calc = (round_window_end - entry_ts).total_seconds()
    seconds_after_entry = (
        (first_instability_ts - entry_ts).total_seconds()
        if first_instability_ts is not None
        else None
    )
    time_left_at_inst = (
        (round_window_end - first_instability_ts).total_seconds()
        if first_instability_ts is not None
        else None
    )

    # Determine result WIN/LOSS
    result_label = "WIN" if result.pnl > _ZERO else "LOSS"

    return BookStabilityMetrics(
        round_no=result.round_no,
        entry_ts=entry_ts,
        time_left_entry=time_left_entry_calc,
        side_taken=result.side_taken,
        resolved_outcome="",  # Fill from round if needed
        result=result_label,
        pnl=result.pnl,
        entry_price=result.entry_price,
        min_leader_bid_after_entry=min_leader_bid,
        max_opposite_bid_after_entry=max_opposite_bid,
        min_leader_ask_after_entry=min_leader_ask,
        max_opposite_ask_after_entry=max_opposite_ask,
        leader_bid_drawdown=leader_bid_drawdown,
        opposite_bid_spike=opposite_bid_spike,
        leader_ask_drawdown=leader_ask_drawdown,
        leader_bid_below_0_95=flag_lb_95,
        leader_bid_below_0_90=flag_lb_90,
        leader_ask_below_0_95=flag_la_95,
        leader_ask_below_0_90=flag_la_90,
        opposite_bid_above_0_05=flag_ob_05,
        opposite_bid_above_0_10=flag_ob_10,
        opposite_bid_above_0_15=flag_ob_15,
        book_flip_warning=book_flip_warning,
        first_instability_ts=first_instability_ts,
        seconds_after_entry_to_instability=seconds_after_entry,
        time_left_at_instability=time_left_at_inst,
    )



async def run_diagnostics(
    store: Store,
    config: ReplayConfig,
    since: datetime | None,
    until: datetime | None,
    max_rounds: int | None,
    thresholds: StabilityThresholds,
) -> BookStabilityDiagnostics:
    """Run book stability diagnostics on resolved rounds.

    1. Stream resolved rounds + ticks using load_round_replays()
    2. Reproduce entered trades using ReplayEngine (with bankroll compounding)
    3. For each entered trade, load post-entry book snapshots
    4. Compute stability metrics
    5. Aggregate into BookStabilityDiagnostics

    Args:
        store: Store instance
        config: ReplayConfig (same as backtest)
        since: optional start datetime filter
        until: optional end datetime filter
        max_rounds: optional limit on rounds processed
        thresholds: stability warning thresholds

    Returns:
        BookStabilityDiagnostics with all metrics
    """
    from btcbot.backtest.replay import load_round_replays

    # Run replay to get entered trades (using streaming loader)
    engine = ReplayEngine(config)
    bankroll = config.starting_balance
    results_list = []
    rounds_list = []
    signals_map: dict[int, list[object]] = {}

    # Stream rounds and run replay with bankroll compounding
    async for rnd, ticks in load_round_replays(store, since=since, until=until, limit=max_rounds):
        # Run round
        result, diag, obs = engine.observe(rnd, ticks, bankroll=bankroll)
        if result is not None:
            # Entry occurred, save for later analysis
            results_list.append(result)
            rounds_list.append(rnd)
            bankroll = result.balance_after
            
            # Load signals for entry_ts approximation (same pattern as loss_diagnostics)
            sigs = await store.get_signals(rnd.round_no)
            if sigs:
                signals_map[rnd.round_no] = sigs

    if not results_list:
        return BookStabilityDiagnostics()

    # For each entered trade, compute stability metrics
    diagnostics = BookStabilityDiagnostics()
    for result, rnd in zip(results_list, rounds_list):
        if rnd.window_end is None:
            continue

        # Approximate entry_ts from signals (same as loss_diagnostics)
        signals = signals_map.get(result.round_no, [])
        if not signals:
            continue

        # Find signal at or near entry (time_left <= t_entry)
        entry_signals = [s for s in signals if s.time_left_sec <= config.params.t_entry_sec]
        if not entry_signals:
            continue

        # Use the FIRST signal that passed t_entry filter (earliest entry opportunity)
        entry_signal = entry_signals[0]
        entry_ts = entry_signal.ts

        # Load book snapshots
        all_snaps = await store.get_book_snapshots(result.round_no)
        # Filter to post-entry only (non-gap, ts >= entry_ts, ts <= window_end)
        post_entry = [
            s for s in all_snaps
            if not s.gap and s.ts >= entry_ts and s.ts <= rnd.window_end
        ]

        # Compute metrics
        metrics = _compute_stability_metrics(
            result, rnd.window_end, post_entry, thresholds,
            entry_ts, rnd.token_id_up, rnd.token_id_down
        )
        # Fill resolved_outcome from round
        metrics = BookStabilityMetrics(
            **{**metrics.__dict__, "resolved_outcome": rnd.resolved_outcome.value if rnd.resolved_outcome else ""}
        )
        diagnostics.add(metrics)

    return diagnostics



def format_report(diagnostics: BookStabilityDiagnostics, thresholds: StabilityThresholds) -> str:
    """Format text report from diagnostics."""
    lines = ["=" * 80, "BOOK STABILITY DIAGNOSTICS", "=" * 80, ""]

    # Overall summary
    summ = diagnostics.summary()
    lines.append("Overall Summary:")
    lines.append(f"  Total entries: {summ['total_entries']}")
    lines.append(f"  Wins: {summ['wins']}")
    lines.append(f"  Losses: {summ['losses']}")
    lines.append(f"  Win rate: {summ['win_rate']:.1f}%")
    lines.append(f"  Net PnL: ${summ['net_pnl']:.2f}")
    lines.append("")

    # Thresholds used
    lines.append("Thresholds:")
    lines.append(f"  leader_bid_warn: {thresholds.leader_bid_warn}")
    lines.append(f"  opposite_bid_warn: {thresholds.opposite_bid_warn}")
    lines.append(f"  leader_ask_warn: {thresholds.leader_ask_warn}")
    lines.append(f"  drawdown_warn: {thresholds.drawdown_warn}")
    lines.append("")

    # By side
    by_side = diagnostics.by_side()
    lines.append("By Side:")
    for side, stats in sorted(by_side.items()):
        lines.append(
            f"  {side}: {stats.entries} entries, {stats.wins}W/{stats.losses}L, "
            f"win {stats.win_rate():.1f}%, PnL ${stats.pnl:.2f}"
        )
    lines.append("")

    # Instability timing
    timing = diagnostics.instability_timing_stats()
    lines.append("Instability Timing:")
    if timing["avg_seconds_to_instability_wins"] is not None:
        lines.append(f"  Avg seconds to instability (wins with warning): {timing['avg_seconds_to_instability_wins']:.1f}s")
    else:
        lines.append("  Avg seconds to instability (wins with warning): N/A")
    if timing["avg_seconds_to_instability_losses"] is not None:
        lines.append(f"  Avg seconds to instability (losses with warning): {timing['avg_seconds_to_instability_losses']:.1f}s")
    else:
        lines.append("  Avg seconds to instability (losses with warning): N/A")
    lines.append("")

    # By threshold flags
    flags = [
        "leader_bid_below_0_95",
        "leader_bid_below_0_90",
        "opposite_bid_above_0_05",
        "opposite_bid_above_0_10",
        "book_flip_warning",
    ]
    lines.append("By Threshold Flags:")
    for flag in flags:
        by_flag = diagnostics.by_flag(flag)
        lines.append(f"  {flag}:")
        for key in ["True", "False"]:
            stats = by_flag[key]
            if stats.entries > 0:
                lines.append(
                    f"    {key}: {stats.entries} entries, {stats.wins}W/{stats.losses}L, "
                    f"win {stats.win_rate():.1f}%, PnL ${stats.pnl:.2f}"
                )
    lines.append("")

    # Loss cases detail
    losses = [m for m in diagnostics.metrics if m.result == "LOSS"]
    if losses:
        lines.append("=" * 80)
        lines.append("LOSS CASES DETAIL")
        lines.append("=" * 80)
        lines.append("")
        for m in losses:
            lines.append(f"Round: {m.round_no}")
            lines.append(f"  Entry: {m.entry_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC, time_left {m.time_left_entry:.1f}s")
            lines.append(f"  Side: {m.side_taken}, Entry price: {m.entry_price}")
            lines.append(f"  Resolved: {m.resolved_outcome}, PnL: ${m.pnl:.6f}")
            lines.append(f"  Book flip warning: {m.book_flip_warning}")
            if m.first_instability_ts:
                lines.append(f"  First instability: {m.first_instability_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                lines.append(f"  Seconds after entry: {m.seconds_after_entry_to_instability:.1f}s")
                lines.append(f"  Time left at instability: {m.time_left_at_instability:.1f}s")
            lines.append(f"  Min leader bid: {m.min_leader_bid_after_entry}")
            lines.append(f"  Max opposite bid: {m.max_opposite_bid_after_entry}")
            lines.append(f"  Min leader ask: {m.min_leader_ask_after_entry}")
            lines.append(f"  Leader bid drawdown: {m.leader_bid_drawdown:.6f}")
            lines.append("")

    return "\n".join(lines)


def write_csv(diagnostics: BookStabilityDiagnostics, path: Path) -> None:
    """Write metrics to CSV file."""
    import csv

    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "round_no",
            "entry_ts",
            "time_left_entry",
            "side_taken",
            "resolved_outcome",
            "result",
            "pnl",
            "entry_price",
            "min_leader_bid_after_entry",
            "max_opposite_bid_after_entry",
            "min_leader_ask_after_entry",
            "max_opposite_ask_after_entry",
            "leader_bid_drawdown",
            "opposite_bid_spike",
            "leader_ask_drawdown",
            "leader_bid_below_0_95",
            "leader_bid_below_0_90",
            "leader_ask_below_0_95",
            "leader_ask_below_0_90",
            "opposite_bid_above_0_05",
            "opposite_bid_above_0_10",
            "opposite_bid_above_0_15",
            "book_flip_warning",
            "first_instability_ts",
            "seconds_after_entry_to_instability",
            "time_left_at_instability",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in diagnostics.metrics:
            row = {
                "round_no": m.round_no,
                "entry_ts": m.entry_ts.isoformat() if m.entry_ts else "",
                "time_left_entry": f"{m.time_left_entry:.2f}",
                "side_taken": m.side_taken,
                "resolved_outcome": m.resolved_outcome,
                "result": m.result,
                "pnl": f"{m.pnl:.6f}",
                "entry_price": f"{m.entry_price:.6f}",
                "min_leader_bid_after_entry": f"{m.min_leader_bid_after_entry:.6f}" if m.min_leader_bid_after_entry else "",
                "max_opposite_bid_after_entry": f"{m.max_opposite_bid_after_entry:.6f}" if m.max_opposite_bid_after_entry else "",
                "min_leader_ask_after_entry": f"{m.min_leader_ask_after_entry:.6f}" if m.min_leader_ask_after_entry else "",
                "max_opposite_ask_after_entry": f"{m.max_opposite_ask_after_entry:.6f}" if m.max_opposite_ask_after_entry else "",
                "leader_bid_drawdown": f"{m.leader_bid_drawdown:.6f}",
                "opposite_bid_spike": f"{m.opposite_bid_spike:.6f}",
                "leader_ask_drawdown": f"{m.leader_ask_drawdown:.6f}",
                "leader_bid_below_0_95": str(m.leader_bid_below_0_95),
                "leader_bid_below_0_90": str(m.leader_bid_below_0_90),
                "leader_ask_below_0_95": str(m.leader_ask_below_0_95),
                "leader_ask_below_0_90": str(m.leader_ask_below_0_90),
                "opposite_bid_above_0_05": str(m.opposite_bid_above_0_05),
                "opposite_bid_above_0_10": str(m.opposite_bid_above_0_10),
                "opposite_bid_above_0_15": str(m.opposite_bid_above_0_15),
                "book_flip_warning": str(m.book_flip_warning),
                "first_instability_ts": m.first_instability_ts.isoformat() if m.first_instability_ts else "",
                "seconds_after_entry_to_instability": f"{m.seconds_after_entry_to_instability:.2f}" if m.seconds_after_entry_to_instability is not None else "",
                "time_left_at_instability": f"{m.time_left_at_instability:.2f}" if m.time_left_at_instability is not None else "",
            }
            writer.writerow(row)



def _parse_iso(s: str) -> datetime:
    """Parse ISO datetime string to timezone-aware datetime."""
    from datetime import datetime, timezone

    # Handle both with and without Z suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Book stability diagnostics (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="Database URL (e.g., sqlite+aiosqlite:///./analisis5.db)")
    parser.add_argument("--since", help="Start datetime (ISO 8601)")
    parser.add_argument("--until", help="End datetime (ISO 8601)")
    parser.add_argument("--t-entry", type=int, default=60, help="t_entry parameter (seconds)")
    parser.add_argument("--delta-threshold", type=float, default=50.0, help="delta_threshold parameter (USD)")
    parser.add_argument("--min-price", type=float, default=0.96, help="min_price parameter")
    parser.add_argument("--max-price", type=float, default=0.99, help="max_price parameter")
    parser.add_argument("--max-rounds", type=int, help="Max rounds to process")
    parser.add_argument("--starting-balance", type=float, default=500.0, help="Starting balance")
    parser.add_argument("--csv", help="Output CSV file path")
    parser.add_argument("--leader-bid-warn", type=float, default=0.90, help="Leader bid warning threshold")
    parser.add_argument("--opposite-bid-warn", type=float, default=0.10, help="Opposite bid warning threshold")
    parser.add_argument("--leader-ask-warn", type=float, default=0.93, help="Leader ask warning threshold")
    parser.add_argument("--drawdown-warn", type=float, default=0.06, help="Drawdown warning threshold")
    return parser



async def main_async(argv: list[str] | None = None) -> int:
    """Async main entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Parse datetime args
    since = _parse_iso(args.since) if args.since else None
    until = _parse_iso(args.until) if args.until else None

    # Build replay config using proper pattern (from_settings + replace)
    from dataclasses import replace

    from btcbot.backtest.replay import ReplayConfig
    from btcbot.config.settings import get_settings

    settings = get_settings()
    base = ReplayConfig.from_settings(settings, delta_threshold=Decimal(str(args.delta_threshold)))

    # Override with CLI parameters
    config = replace(
        base,
        params=replace(
            base.params,
            t_entry_sec=args.t_entry,
            delta_threshold=Decimal(str(args.delta_threshold)),
            min_price=Decimal(str(args.min_price)),
            max_price=Decimal(str(args.max_price)),
        ),
        limits=replace(base.limits, max_price=Decimal(str(args.max_price))),
        starting_balance=Decimal(str(args.starting_balance)),
    )

    # Thresholds
    thresholds = StabilityThresholds(
        leader_bid_warn=Decimal(str(args.leader_bid_warn)),
        opposite_bid_warn=Decimal(str(args.opposite_bid_warn)),
        leader_ask_warn=Decimal(str(args.leader_ask_warn)),
        drawdown_warn=Decimal(str(args.drawdown_warn)),
    )

    # Connect to store (use Store.open pattern, not Store() + connect())
    store = await Store.open(args.db)
    try:
        # Run diagnostics
        diagnostics = await run_diagnostics(store, config, since, until, args.max_rounds, thresholds)
    finally:
        await store.close()

    # Print report
    report = format_report(diagnostics, thresholds)
    print(report)

    # Write CSV if requested
    if args.csv:
        csv_path = Path(args.csv)
        write_csv(diagnostics, csv_path)
        print(f"\nCSV written to: {csv_path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point (handles asyncio.run)."""
    import asyncio

    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
