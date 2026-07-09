"""loss_diagnostics.py — Diagnostic CLI untuk menganalisis filled entries dan loss patterns.

Tool read-only untuk G1 REVISI: mengidentifikasi pola loss pada parameter backtest tertentu
tanpa mengubah behavior replay, strategy, sizing, atau fee.

Usage:
    python -m btcbot.backtest.loss_diagnostics \\
        --db "sqlite+aiosqlite:///./analisis4.db" \\
        --since "2026-07-06T01:20:00+00:00" \\
        --until "2026-07-09T08:25:00+00:00" \\
        --t-entry 60 \\
        --delta-threshold 50 \\
        --max-price 0.99 \\
        --starting-balance 500 \\
        --max-rounds 1300 \\
        --csv output.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplayTick
from btcbot.config.settings import get_settings
from btcbot.data.store import Store
from btcbot.domain.models import Round, Signal

if TYPE_CHECKING:
    from collections.abc import Sequence


_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class EntryDetail:
    """Detail satu filled entry untuk analisis loss."""

    # Round info
    round_no: int
    window_start: datetime
    window_end: datetime
    start_price: Decimal
    resolved_outcome: str
    
    # Entry info
    entry_ts: datetime
    time_left_sec: float
    side_taken: str
    leader: str
    entry_price: Decimal
    size: Decimal
    
    # Signal at entry
    price_now: Decimal
    delta: Decimal
    abs_delta: Decimal
    p_win: Decimal
    ask_win: Decimal
    net_edge: Decimal
    
    # Config used
    max_price_config: Decimal
    
    # Result
    result: str  # "WIN" or "LOSS"
    pnl: Decimal
    
    # Optional book info (if available)
    best_bid_leader: Decimal | None = None
    best_ask_leader: Decimal | None = None
    depth_available: Decimal | None = None


def _bucket_entry_price(price: Decimal) -> str:
    """Bucket entry price: <=0.95, (0.95,0.97], (0.97,0.99], >0.99."""
    if price <= Decimal("0.95"):
        return "<=0.95"
    if price <= Decimal("0.97"):
        return "(0.95,0.97]"
    if price <= Decimal("0.99"):
        return "(0.97,0.99]"
    return ">0.99"


def _bucket_abs_delta(delta: Decimal) -> str:
    """Bucket abs_delta: [0,50), [50,60), [60,75), [75,100), [100+)."""
    d = float(delta)
    if d < 50:
        return "[0,50)"
    if d < 60:
        return "[50,60)"
    if d < 75:
        return "[60,75)"
    if d < 100:
        return "[75,100)"
    return "[100+)"


def _bucket_time_left(sec: float) -> str:
    """Bucket time_left: [0,15), [15,30), [30,45), [45,60], (60+)."""
    if sec < 15:
        return "[0,15)"
    if sec < 30:
        return "[15,30)"
    if sec < 45:
        return "[30,45)"
    if sec <= 60:
        return "[45,60]"
    return "(60+)"


def _bucket_p_win(p: Decimal) -> str:
    """Bucket p_win: [0.50,0.80), [0.80,0.90), [0.90,0.95), [0.95,0.98), [0.98,1.00]."""
    if p < Decimal("0.80"):
        return "[0.50,0.80)"
    if p < Decimal("0.90"):
        return "[0.80,0.90)"
    if p < Decimal("0.95"):
        return "[0.90,0.95)"
    if p < Decimal("0.98"):
        return "[0.95,0.98)"
    return "[0.98,1.00]"


@dataclass
class BucketStats:
    """Stats for one bucket."""
    
    entries: int = 0
    wins: int = 0
    losses: int = 0
    pnl_sum: Decimal = _ZERO
    
    @property
    def win_rate(self) -> float:
        return self.wins / self.entries if self.entries > 0 else 0.0
    
    @property
    def avg_pnl(self) -> Decimal:
        return self.pnl_sum / Decimal(self.entries) if self.entries > 0 else _ZERO


class LossDiagnostics:
    """Collector and analyzer for filled entry details."""
    
    def __init__(self) -> None:
        self.entries: list[EntryDetail] = []
        
    def add(self, entry: EntryDetail) -> None:
        """Add one filled entry detail."""
        self.entries.append(entry)
    
    def summary(self) -> dict[str, object]:
        """Generate summary statistics."""
        if not self.entries:
            return {
                "total_entries": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_pnl": _ZERO,
            }
        
        wins = sum(1 for e in self.entries if e.result == "WIN")
        losses = len(self.entries) - wins
        net_pnl = sum((e.pnl for e in self.entries), _ZERO)
        
        return {
            "total_entries": len(self.entries),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(self.entries),
            "net_pnl": net_pnl,
        }
    
    def loss_by_side(self) -> dict[str, BucketStats]:
        """Loss count by side UP/DOWN."""
        buckets: dict[str, BucketStats] = defaultdict(BucketStats)
        for e in self.entries:
            b = buckets[e.side_taken]
            b.entries += 1
            if e.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl_sum += e.pnl
        return dict(buckets)
    
    def bucket_by_entry_price(self) -> dict[str, BucketStats]:
        """Bucket performance by entry_price."""
        buckets: dict[str, BucketStats] = defaultdict(BucketStats)
        for e in self.entries:
            key = _bucket_entry_price(e.entry_price)
            b = buckets[key]
            b.entries += 1
            if e.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl_sum += e.pnl
        return dict(buckets)
    
    def bucket_by_abs_delta(self) -> dict[str, BucketStats]:
        """Bucket performance by abs_delta."""
        buckets: dict[str, BucketStats] = defaultdict(BucketStats)
        for e in self.entries:
            key = _bucket_abs_delta(e.abs_delta)
            b = buckets[key]
            b.entries += 1
            if e.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl_sum += e.pnl
        return dict(buckets)
    
    def bucket_by_time_left(self) -> dict[str, BucketStats]:
        """Bucket performance by time_left_sec."""
        buckets: dict[str, BucketStats] = defaultdict(BucketStats)
        for e in self.entries:
            key = _bucket_time_left(e.time_left_sec)
            b = buckets[key]
            b.entries += 1
            if e.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl_sum += e.pnl
        return dict(buckets)
    
    def bucket_by_p_win(self) -> dict[str, BucketStats]:
        """Bucket performance by p_win."""
        buckets: dict[str, BucketStats] = defaultdict(BucketStats)
        for e in self.entries:
            key = _bucket_p_win(e.p_win)
            b = buckets[key]
            b.entries += 1
            if e.result == "WIN":
                b.wins += 1
            else:
                b.losses += 1
            b.pnl_sum += e.pnl
        return dict(buckets)


def _load_rounds_with_signals(
    store: Store,
    since: datetime | None,
    until: datetime | None,
    limit: int | None,
) -> list[tuple[Round, list[Signal]]]:
    """Load resolved rounds with their signals (for delta/p_win at entry)."""
    import asyncio
    
    async def _load() -> list[tuple[Round, list[Signal]]]:
        rounds = await store.get_resolved_rounds(since=since, until=until, limit=limit)
        result: list[tuple[Round, list[Signal]]] = []
        for rnd in rounds:
            signals = await store.get_signals(rnd.round_no)
            result.append((rnd, signals))
        return result
    
    return asyncio.run(_load())


async def run_diagnostics(
    config: ReplayConfig,
    store: Store,
    *,
    since: datetime | None,
    until: datetime | None,
    limit: int | None,
) -> LossDiagnostics:
    """Run replay and collect entry details for diagnostics."""
    from btcbot.backtest.report import load_round_replays
    
    diagnostics = LossDiagnostics()
    engine = ReplayEngine(config)
    bankroll = config.starting_balance
    
    rounds_gen = load_round_replays(store, since=since, until=until, limit=limit)
    
    # Load all rounds with signals for delta/p_win lookup
    rounds_with_signals = _load_rounds_with_signals(store, since, until, limit)
    signals_map = {rnd.round_no: sigs for rnd, sigs in rounds_with_signals}
    
    async for rnd, ticks in rounds_gen:
        result, diag, obs = engine.observe(rnd, ticks, bankroll=bankroll)
        
        if result is None or diag is None:
            continue  # No entry
        
        # Update bankroll
        bankroll = result.balance_after
        
        # Find entry signal (last signal where we made entry decision)
        # We need to find signal at entry_ts which we don't have directly
        # So we approximate: find signal with time_left closest to decision
        signals = signals_map.get(rnd.round_no, [])
        if not signals:
            continue
        
        # Find signal at or near entry (time_left <= t_entry)
        entry_signals = [s for s in signals if s.time_left_sec <= config.params.t_entry_sec]
        if not entry_signals:
            continue
        
        # Use the FIRST signal that passed t_entry filter (earliest entry opportunity)
        entry_signal = entry_signals[0]
        
        # Determine result
        is_win = diag.won
        result_str = "WIN" if is_win else "LOSS"
        
        # Extract book info if available (we don't have it from RoundResult)
        # For now, leave as None - user can enhance if needed
        
        detail = EntryDetail(
            round_no=rnd.round_no,
            window_start=rnd.window_start,
            window_end=rnd.window_end,
            start_price=rnd.start_price,
            resolved_outcome=rnd.resolved_outcome.value if rnd.resolved_outcome else "",
            entry_ts=entry_signal.ts,
            time_left_sec=entry_signal.time_left_sec,
            side_taken=result.side_taken,
            leader=entry_signal.leader,
            entry_price=result.entry_price,
            size=result.size,
            price_now=entry_signal.price_now,
            delta=entry_signal.delta,
            abs_delta=abs(entry_signal.delta),
            p_win=entry_signal.p_win,
            ask_win=entry_signal.ask_win,
            net_edge=entry_signal.net_edge,
            max_price_config=config.params.max_price,
            result=result_str,
            pnl=result.pnl,
        )
        
        diagnostics.add(detail)
    
    return diagnostics


def format_report(diagnostics: LossDiagnostics) -> str:
    """Format diagnostic report as text."""
    lines: list[str] = []
    
    lines.append("=" * 80)
    lines.append("LOSS DIAGNOSTICS REPORT")
    lines.append("=" * 80)
    lines.append("")
    
    # Overall summary
    summary = diagnostics.summary()
    lines.append("=== OVERALL SUMMARY ===")
    lines.append(f"Total Entries:  {summary['total_entries']}")
    lines.append(f"Wins:           {summary['wins']}")
    lines.append(f"Losses:         {summary['losses']}")
    lines.append(f"Win Rate:       {summary['win_rate']:.1%}")
    lines.append(f"Net PnL:        ${summary['net_pnl']:.2f}")
    lines.append("")
    
    # Loss by side
    lines.append("=== PERFORMANCE BY SIDE ===")
    lines.append(f"{'Side':<10} {'Entries':>8} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'Net PnL':>10} {'Avg PnL':>10}")
    lines.append("-" * 80)
    for side, stats in sorted(diagnostics.loss_by_side().items()):
        lines.append(
            f"{side:<10} {stats.entries:>8} {stats.wins:>6} {stats.losses:>7} "
            f"{stats.win_rate:>6.1%} {stats.pnl_sum:>10.2f} {stats.avg_pnl:>10.2f}"
        )
    lines.append("")
    
    # By entry price
    lines.append("=== PERFORMANCE BY ENTRY PRICE ===")
    lines.append(f"{'Bucket':<15} {'Entries':>8} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'Net PnL':>10} {'Avg PnL':>10}")
    lines.append("-" * 80)
    buckets_price = diagnostics.bucket_by_entry_price()
    for bucket in ["<=0.95", "(0.95,0.97]", "(0.97,0.99]", ">0.99"]:
        if bucket in buckets_price:
            stats = buckets_price[bucket]
            lines.append(
                f"{bucket:<15} {stats.entries:>8} {stats.wins:>6} {stats.losses:>7} "
                f"{stats.win_rate:>6.1%} {stats.pnl_sum:>10.2f} {stats.avg_pnl:>10.2f}"
            )
    lines.append("")
    
    # By abs_delta
    lines.append("=== PERFORMANCE BY ABS_DELTA ===")
    lines.append(f"{'Bucket':<15} {'Entries':>8} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'Net PnL':>10} {'Avg PnL':>10}")
    lines.append("-" * 80)
    buckets_delta = diagnostics.bucket_by_abs_delta()
    for bucket in ["[0,50)", "[50,60)", "[60,75)", "[75,100)", "[100+)"]:
        if bucket in buckets_delta:
            stats = buckets_delta[bucket]
            lines.append(
                f"{bucket:<15} {stats.entries:>8} {stats.wins:>6} {stats.losses:>7} "
                f"{stats.win_rate:>6.1%} {stats.pnl_sum:>10.2f} {stats.avg_pnl:>10.2f}"
            )
    lines.append("")
    
    # By time_left
    lines.append("=== PERFORMANCE BY TIME_LEFT ===")
    lines.append(f"{'Bucket':<15} {'Entries':>8} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'Net PnL':>10} {'Avg PnL':>10}")
    lines.append("-" * 80)
    buckets_time = diagnostics.bucket_by_time_left()
    for bucket in ["[0,15)", "[15,30)", "[30,45)", "[45,60]", "(60+)"]:
        if bucket in buckets_time:
            stats = buckets_time[bucket]
            lines.append(
                f"{bucket:<15} {stats.entries:>8} {stats.wins:>6} {stats.losses:>7} "
                f"{stats.win_rate:>6.1%} {stats.pnl_sum:>10.2f} {stats.avg_pnl:>10.2f}"
            )
    lines.append("")
    
    # By p_win
    lines.append("=== PERFORMANCE BY P_WIN ===")
    lines.append(f"{'Bucket':<15} {'Entries':>8} {'Wins':>6} {'Losses':>7} {'Win%':>7} {'Net PnL':>10} {'Avg PnL':>10}")
    lines.append("-" * 80)
    buckets_pwin = diagnostics.bucket_by_p_win()
    for bucket in ["[0.50,0.80)", "[0.80,0.90)", "[0.90,0.95)", "[0.95,0.98)", "[0.98,1.00]"]:
        if bucket in buckets_pwin:
            stats = buckets_pwin[bucket]
            lines.append(
                f"{bucket:<15} {stats.entries:>8} {stats.wins:>6} {stats.losses:>7} "
                f"{stats.win_rate:>6.1%} {stats.pnl_sum:>10.2f} {stats.avg_pnl:>10.2f}"
            )
    lines.append("")
    
    return "\n".join(lines)


def write_csv(diagnostics: LossDiagnostics, path: Path) -> None:
    """Write all entries to CSV."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        
        # Header
        writer.writerow([
            "round_no",
            "window_start",
            "window_end",
            "entry_ts",
            "time_left_sec",
            "side_taken",
            "leader",
            "resolved_outcome",
            "result",
            "start_price",
            "price_now",
            "delta",
            "abs_delta",
            "p_win",
            "ask_win",
            "entry_price",
            "max_price_config",
            "size",
            "net_edge",
            "pnl",
            "best_bid_leader",
            "best_ask_leader",
            "depth_available",
        ])
        
        # Data rows
        for e in diagnostics.entries:
            writer.writerow([
                e.round_no,
                e.window_start.isoformat(),
                e.window_end.isoformat(),
                e.entry_ts.isoformat(),
                f"{e.time_left_sec:.1f}",
                e.side_taken,
                e.leader,
                e.resolved_outcome,
                e.result,
                str(e.start_price),
                str(e.price_now),
                str(e.delta),
                str(e.abs_delta),
                str(e.p_win),
                str(e.ask_win),
                str(e.entry_price),
                str(e.max_price_config),
                str(e.size),
                str(e.net_edge),
                str(e.pnl),
                str(e.best_bid_leader) if e.best_bid_leader else "",
                str(e.best_ask_leader) if e.best_ask_leader else "",
                str(e.depth_available) if e.depth_available else "",
            ])


def _parse_iso(s: str) -> datetime:
    """Parse ISO datetime string to UTC-aware datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    p = argparse.ArgumentParser(
        description="Analyze filled entries and loss patterns from backtest replay"
    )
    p.add_argument("--db", required=True, help="Database URL")
    p.add_argument("--since", help="ISO datetime to filter rounds (window_end >= since)")
    p.add_argument("--until", help="ISO datetime to filter rounds (window_end <= until)")
    p.add_argument("--t-entry", type=int, required=True, help="T_ENTRY parameter (seconds)")
    p.add_argument("--delta-threshold", type=float, required=True, help="Delta threshold (USD)")
    p.add_argument("--max-price", type=float, required=True, help="Max price to buy")
    p.add_argument("--starting-balance", type=float, default=500.0, help="Starting balance")
    p.add_argument("--max-rounds", type=int, help="Max rounds to process")
    p.add_argument("--csv", help="Output CSV file path")
    return p


async def main_async(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    
    # Parse datetime filters
    since = _parse_iso(args.since) if args.since else None
    until = _parse_iso(args.until) if args.until else None
    
    # Build config with specified parameters
    config = ReplayConfig.from_settings(
        settings,
        delta_threshold=Decimal(str(args.delta_threshold)),
    )
    
    # Override specific parameters
    from dataclasses import replace
    config = replace(
        config,
        params=replace(
            config.params,
            t_entry_sec=args.t_entry,
            delta_threshold=Decimal(str(args.delta_threshold)),
            max_price=Decimal(str(args.max_price)),
        ),
        limits=replace(
            config.limits,
            max_price=Decimal(str(args.max_price)),
        ),
        starting_balance=Decimal(str(args.starting_balance)),
    )
    
    # Run diagnostics
    store = await Store.open(args.db)
    try:
        diagnostics = await run_diagnostics(
            config,
            store,
            since=since,
            until=until,
            limit=args.max_rounds,
        )
    finally:
        await store.close()
    
    # Print report
    report = format_report(diagnostics)
    print(report)
    
    # Write CSV if requested
    if args.csv:
        csv_path = Path(args.csv)
        write_csv(diagnostics, csv_path)
        print(f"\nCSV written to: {csv_path}", file=sys.stderr)
    
    return 0


def main(argv: list[str] | None = None) -> int:
    """Sync wrapper for main_async."""
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
