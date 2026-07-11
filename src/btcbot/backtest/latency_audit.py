"""backtest/latency_audit.py — Read-only latency behavior audit (G1 blocker diagnostic).

Comprehensive audit of tick-based latency model before G1 decision.

VERIFIED VPS EVIDENCE (analisis5.db, t_entry=60, delta=50, min_price=0.96):
- 84 entries, 83W/1L, net PnL +$7.40
- BACKTEST_LATENCY_TICKS=1
- Actual decision-to-fill: min=0.000s, median=0.001s, p95=0.479s, max=1.021s
- 41/84 exactly 0ms, 68/84 <=10ms, 74/84 <=100ms, 4/84 >1s
- Proves one event tick is NOT a stable real-time latency model

PURPOSE:
Answer critical questions before G1:
1. How often does `ticks[min(i + latency_ticks, n - 1)]` clamp to final tick?
2. How often are decision and execution timestamps identical?
3. How often are they different events with same timestamp?
4. What is target/opposite book age at decision and execution?
5. Does execution use fresh or LVCF-stale book?
6. How does event density affect realized latency?
7. Are two token updates milliseconds apart treated as network latency?
8. What happens when insufficient future ticks exist?
9. Does tick-based latency affect entry, hedge, and exit?
10. How sensitive are entries/PnL to latency_ticks = 0,1,2,3,5?

DELIVERABLE:
Read-only observability. NO strategy changes. NO replay behavior changes.
This is measurement only to inform G1 decision.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplayTick
from btcbot.data.store import Store
from btcbot.domain.models import Outcome

if TYPE_CHECKING:
    from collections.abc import Sequence

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LatencyAuditEntry:
    """Latency audit metrics for one successful entry."""

    # Identity
    round_no: int
    result: str  # WIN/LOSS
    pnl: Decimal

    # Tick indices
    decision_tick_index: int
    requested_execution_tick_index: int
    actual_execution_tick_index: int
    total_tick_count: int
    latency_ticks_config: int
    clamped_to_last_tick: bool

    # Timestamps
    decision_ts: datetime
    execution_ts: datetime
    realized_latency_ms: float
    same_timestamp: bool
    decision_time_left: float  # seconds
    execution_time_left: float

    # Entry details
    target_side: str  # UP/DOWN
    decision_ask: Decimal | None
    execution_ask: Decimal | None
    execution_limit_price: Decimal
    filled: bool
    entry_price: Decimal
    entry_size: Decimal

    # Book age diagnostics
    decision_target_book_ts: datetime
    decision_target_book_age_ms: float
    execution_target_book_ts: datetime
    execution_target_book_age_ms: float
    decision_opposite_book_ts: datetime
    decision_opposite_book_age_ms: float
    execution_opposite_book_ts: datetime
    execution_opposite_book_age_ms: float

    # Book change detection
    target_book_changed: bool
    opposite_book_changed: bool


@dataclass
class LatencyAuditDiagnostics:
    """Aggregated latency audit diagnostics."""

    entries: list[LatencyAuditEntry] = field(default_factory=list)

    def add(self, entry: LatencyAuditEntry) -> None:
        """Add one entry audit."""
        self.entries.append(entry)

    def summary(self) -> dict[str, object]:
        """Overall summary statistics."""
        if not self.entries:
            return {"total_entries": 0}
        
        wins = sum(1 for e in self.entries if e.result == "WIN")
        losses = sum(1 for e in self.entries if e.result == "LOSS")
        pnl = sum((e.pnl for e in self.entries), _ZERO)
        
        # Latency stats
        latencies = [e.realized_latency_ms for e in self.entries]
        latencies_sorted = sorted(latencies)
        n = len(latencies_sorted)
        
        clamped = sum(1 for e in self.entries if e.clamped_to_last_tick)
        same_ts = sum(1 for e in self.entries if e.same_timestamp)
        target_changed = sum(1 for e in self.entries if e.target_book_changed)
        opposite_changed = sum(1 for e in self.entries if e.opposite_book_changed)
        
        return {
            "total_entries": len(self.entries),
            "wins": wins,
            "losses": losses,
            "win_rate": 100.0 * wins / len(self.entries),
            "net_pnl": pnl,
            "latency_ms_min": latencies_sorted[0],
            "latency_ms_p25": latencies_sorted[n // 4] if n >= 4 else latencies_sorted[0],
            "latency_ms_median": latencies_sorted[n // 2],
            "latency_ms_p75": latencies_sorted[3 * n // 4] if n >= 4 else latencies_sorted[-1],
            "latency_ms_p95": latencies_sorted[int(0.95 * n)] if n >= 20 else latencies_sorted[-1],
            "latency_ms_max": latencies_sorted[-1],
            "clamped_to_last_tick": clamped,
            "same_timestamp": same_ts,
            "target_book_changed": target_changed,
            "opposite_book_changed": opposite_changed,
        }

    def latency_buckets(self) -> dict[str, tuple[int, int, Decimal]]:
        """Entry counts, wins/losses, PnL by realized latency bucket."""
        buckets = {
            "0ms": (0, 0, _ZERO),
            "(0,10]ms": (0, 0, _ZERO),
            "(10,50]ms": (0, 0, _ZERO),
            "(50,100]ms": (0, 0, _ZERO),
            "(100,250]ms": (0, 0, _ZERO),
            "(250,500]ms": (0, 0, _ZERO),
            "(500,1000]ms": (0, 0, _ZERO),
            ">1000ms": (0, 0, _ZERO),
        }
        
        for e in self.entries:
            lat_ms = e.realized_latency_ms
            if lat_ms == 0:
                bucket = "0ms"
            elif lat_ms <= 10:
                bucket = "(0,10]ms"
            elif lat_ms <= 50:
                bucket = "(10,50]ms"
            elif lat_ms <= 100:
                bucket = "(50,100]ms"
            elif lat_ms <= 250:
                bucket = "(100,250]ms"
            elif lat_ms <= 500:
                bucket = "(250,500]ms"
            elif lat_ms <= 1000:
                bucket = "(500,1000]ms"
            else:
                bucket = ">1000ms"
            
            count, wins, pnl = buckets[bucket]
            buckets[bucket] = (
                count + 1,
                wins + (1 if e.result == "WIN" else 0),
                pnl + e.pnl
            )
        
        return buckets


async def run_latency_audit(
    store: Store,
    config: ReplayConfig,
    since: datetime | None,
    until: datetime | None,
    max_rounds: int | None,
) -> LatencyAuditDiagnostics:
    """Run latency audit on resolved rounds.

    Args:
        store: Store instance
        config: ReplayConfig (same as backtest)
        since: optional start datetime filter
        until: optional end datetime filter
        max_rounds: optional limit on rounds processed

    Returns:
        LatencyAuditDiagnostics with all audit metrics
    """
    from btcbot.backtest.replay import load_round_replays

    # Run replay to get entered trades with latency observability
    engine = ReplayEngine(config)
    bankroll = config.starting_balance
    diagnostics = LatencyAuditDiagnostics()

    # Stream rounds and run replay with bankroll compounding
    async for rnd, ticks in load_round_replays(store, since=since, until=until, limit=max_rounds):
        if not ticks or rnd.window_end is None:
            continue
        
        # Audit this round's entry attempt
        entry_audit = _audit_round_entry(engine, rnd, ticks, bankroll, config.latency_ticks)
        
        if entry_audit is not None:
            diagnostics.add(entry_audit)
            # Update bankroll for next round (preserve compounding)
            bankroll = bankroll + entry_audit.pnl

    return diagnostics


def _audit_round_entry(
    engine: ReplayEngine,
    rnd,
    ticks: Sequence[ReplayTick],
    bankroll: Decimal,
    latency_ticks_config: int,
) -> LatencyAuditEntry | None:
    """Audit latency behavior for one round's entry attempt.
    
    This function replays the round and captures detailed latency observability
    WITHOUT changing replay behavior. It re-simulates decision-to-execution
    to extract audit metrics.
    """
    # Run the round to see if entry occurs
    result, _diag, obs = engine.observe(rnd, ticks, bankroll=bankroll)
    
    if result is None or obs.entry_decision_ts is None or obs.entry_fill_ts is None:
        # No entry occurred
        return None
    
    # Entry occurred - now audit the latency behavior
    # Find the decision tick index
    decision_tick_index = None
    for i, tick in enumerate(ticks):
        if tick.ts == obs.entry_decision_ts:
            decision_tick_index = i
            break
    
    if decision_tick_index is None:
        raise RuntimeError(
            f"Round {rnd.round_no}: could not find decision tick with ts={obs.entry_decision_ts}"
        )
    
    # Compute execution tick indices
    n = len(ticks)
    requested_execution_tick_index = decision_tick_index + latency_ticks_config
    actual_execution_tick_index = min(requested_execution_tick_index, n - 1)
    clamped = requested_execution_tick_index >= n
    
    decision_tick = ticks[decision_tick_index]
    execution_tick = ticks[actual_execution_tick_index]
    
    # Timestamps and realized latency
    decision_ts = decision_tick.ts
    execution_ts = execution_tick.ts
    realized_latency_ms = (execution_ts - decision_ts).total_seconds() * 1000
    same_timestamp = (decision_ts == execution_ts)
    
    decision_time_left = (rnd.window_end - decision_ts).total_seconds()
    execution_time_left = (rnd.window_end - execution_ts).total_seconds()
    
    # Determine target side
    target_side = result.side_taken
    target_outcome = Outcome(target_side)
    
    # Get books
    decision_target_book = decision_tick.book_up if target_outcome is Outcome.UP else decision_tick.book_down
    decision_opposite_book = decision_tick.book_down if target_outcome is Outcome.UP else decision_tick.book_up
    execution_target_book = execution_tick.book_up if target_outcome is Outcome.UP else execution_tick.book_down
    execution_opposite_book = execution_tick.book_down if target_outcome is Outcome.UP else execution_tick.book_up
    
    # Book timestamps and ages
    decision_target_book_ts = decision_target_book.ts
    decision_target_book_age_ms = (decision_ts - decision_target_book_ts).total_seconds() * 1000
    execution_target_book_ts = execution_target_book.ts
    execution_target_book_age_ms = (execution_ts - execution_target_book_ts).total_seconds() * 1000
    
    decision_opposite_book_ts = decision_opposite_book.ts
    decision_opposite_book_age_ms = (decision_ts - decision_opposite_book_ts).total_seconds() * 1000
    execution_opposite_book_ts = execution_opposite_book.ts
    execution_opposite_book_age_ms = (execution_ts - execution_opposite_book_ts).total_seconds() * 1000
    
    # Book change detection
    target_book_changed = (decision_target_book_ts != execution_target_book_ts)
    opposite_book_changed = (decision_opposite_book_ts != execution_opposite_book_ts)
    
    # Entry details
    decision_ask = decision_target_book.asks[0].price if decision_target_book.asks else None
    execution_ask = execution_target_book.asks[0].price if execution_target_book.asks else None
    execution_limit_price = result.entry_price  # Approximation (actual limit may differ slightly)
    filled = True  # Entry occurred means fill succeeded
    
    # Result classification
    result_label = "WIN" if result.pnl > _ZERO else "LOSS"
    
    return LatencyAuditEntry(
        round_no=rnd.round_no,
        result=result_label,
        pnl=result.pnl,
        decision_tick_index=decision_tick_index,
        requested_execution_tick_index=requested_execution_tick_index,
        actual_execution_tick_index=actual_execution_tick_index,
        total_tick_count=n,
        latency_ticks_config=latency_ticks_config,
        clamped_to_last_tick=clamped,
        decision_ts=decision_ts,
        execution_ts=execution_ts,
        realized_latency_ms=realized_latency_ms,
        same_timestamp=same_timestamp,
        decision_time_left=decision_time_left,
        execution_time_left=execution_time_left,
        target_side=target_side,
        decision_ask=decision_ask,
        execution_ask=execution_ask,
        execution_limit_price=execution_limit_price,
        filled=filled,
        entry_price=result.entry_price,
        entry_size=result.size,
        decision_target_book_ts=decision_target_book_ts,
        decision_target_book_age_ms=decision_target_book_age_ms,
        execution_target_book_ts=execution_target_book_ts,
        execution_target_book_age_ms=execution_target_book_age_ms,
        decision_opposite_book_ts=decision_opposite_book_ts,
        decision_opposite_book_age_ms=decision_opposite_book_age_ms,
        execution_opposite_book_ts=execution_opposite_book_ts,
        execution_opposite_book_age_ms=execution_opposite_book_age_ms,
        target_book_changed=target_book_changed,
        opposite_book_changed=opposite_book_changed,
    )


def format_report(diagnostics: LatencyAuditDiagnostics) -> str:
    """Format text report from audit diagnostics."""
    lines = ["=" * 80, "LATENCY AUDIT REPORT", "=" * 80, ""]
    
    # Overall summary
    summ = diagnostics.summary()
    lines.append("Overall Summary:")
    lines.append(f"  Total entries: {summ['total_entries']}")
    lines.append(f"  Wins: {summ['wins']}, Losses: {summ['losses']}, Win rate: {summ['win_rate']:.1f}%")
    lines.append(f"  Net PnL: ${summ['net_pnl']:.2f}")
    lines.append("")
    
    lines.append("Realized Latency Distribution (ms):")
    lines.append(f"  Min: {summ['latency_ms_min']:.3f}")
    lines.append(f"  P25: {summ['latency_ms_p25']:.3f}")
    lines.append(f"  Median: {summ['latency_ms_median']:.3f}")
    lines.append(f"  P75: {summ['latency_ms_p75']:.3f}")
    lines.append(f"  P95: {summ['latency_ms_p95']:.3f}")
    lines.append(f"  Max: {summ['latency_ms_max']:.3f}")
    lines.append("")
    
    lines.append("Tick-Based Latency Model Issues:")
    lines.append(f"  Clamped to last tick: {summ['clamped_to_last_tick']}/{summ['total_entries']}")
    lines.append(f"  Same timestamp (decision=execution): {summ['same_timestamp']}/{summ['total_entries']}")
    lines.append(f"  Target book changed: {summ['target_book_changed']}/{summ['total_entries']}")
    lines.append(f"  Opposite book changed: {summ['opposite_book_changed']}/{summ['total_entries']}")
    lines.append("")
    
    # Latency buckets
    buckets = diagnostics.latency_buckets()
    lines.append("Latency Buckets (entries, wins, PnL):")
    for bucket_name, (count, wins, pnl) in buckets.items():
        if count > 0:
            lines.append(f"  {bucket_name:15s}: {count:3d} entries, {wins:3d}W, ${pnl:+8.2f}")
    lines.append("")
    
    return "\n".join(lines)


def write_csv(diagnostics: LatencyAuditDiagnostics, path: Path) -> None:
    """Write audit metrics to CSV file."""
    import csv
    
    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "round_no", "result", "pnl",
            "decision_tick_index", "requested_execution_tick_index", "actual_execution_tick_index",
            "total_tick_count", "latency_ticks_config", "clamped_to_last_tick",
            "decision_ts", "execution_ts", "realized_latency_ms", "same_timestamp",
            "decision_time_left", "execution_time_left",
            "target_side", "decision_ask", "execution_ask", "execution_limit_price",
            "filled", "entry_price", "entry_size",
            "decision_target_book_ts", "decision_target_book_age_ms",
            "execution_target_book_ts", "execution_target_book_age_ms",
            "decision_opposite_book_ts", "decision_opposite_book_age_ms",
            "execution_opposite_book_ts", "execution_opposite_book_age_ms",
            "target_book_changed", "opposite_book_changed",
        ]
        
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for e in diagnostics.entries:
            row = {
                "round_no": e.round_no,
                "result": e.result,
                "pnl": f"{e.pnl:.6f}",
                "decision_tick_index": e.decision_tick_index,
                "requested_execution_tick_index": e.requested_execution_tick_index,
                "actual_execution_tick_index": e.actual_execution_tick_index,
                "total_tick_count": e.total_tick_count,
                "latency_ticks_config": e.latency_ticks_config,
                "clamped_to_last_tick": str(e.clamped_to_last_tick),
                "decision_ts": e.decision_ts.isoformat(),
                "execution_ts": e.execution_ts.isoformat(),
                "realized_latency_ms": f"{e.realized_latency_ms:.3f}",
                "same_timestamp": str(e.same_timestamp),
                "decision_time_left": f"{e.decision_time_left:.2f}",
                "execution_time_left": f"{e.execution_time_left:.2f}",
                "target_side": e.target_side,
                "decision_ask": f"{e.decision_ask:.6f}" if e.decision_ask else "",
                "execution_ask": f"{e.execution_ask:.6f}" if e.execution_ask else "",
                "execution_limit_price": f"{e.execution_limit_price:.6f}",
                "filled": str(e.filled),
                "entry_price": f"{e.entry_price:.6f}",
                "entry_size": f"{e.entry_size:.2f}",
                "decision_target_book_ts": e.decision_target_book_ts.isoformat(),
                "decision_target_book_age_ms": f"{e.decision_target_book_age_ms:.3f}",
                "execution_target_book_ts": e.execution_target_book_ts.isoformat(),
                "execution_target_book_age_ms": f"{e.execution_target_book_age_ms:.3f}",
                "decision_opposite_book_ts": e.decision_opposite_book_ts.isoformat(),
                "decision_opposite_book_age_ms": f"{e.decision_opposite_book_age_ms:.3f}",
                "execution_opposite_book_ts": e.execution_opposite_book_ts.isoformat(),
                "execution_opposite_book_age_ms": f"{e.execution_opposite_book_age_ms:.3f}",
                "target_book_changed": str(e.target_book_changed),
                "opposite_book_changed": str(e.opposite_book_changed),
            }
            writer.writerow(row)


def _parse_iso(s: str) -> datetime:
    """Parse ISO datetime string to timezone-aware datetime."""
    from datetime import datetime, timezone
    
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Latency audit (read-only G1 blocker diagnostic)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", required=True, help="Database URL")
    parser.add_argument("--since", help="Start datetime (ISO 8601)")
    parser.add_argument("--until", help="End datetime (ISO 8601)")
    parser.add_argument("--t-entry", type=int, default=60, help="t_entry parameter (seconds)")
    parser.add_argument("--delta-threshold", type=float, default=50.0, help="delta_threshold (USD)")
    parser.add_argument("--min-price", type=float, default=0.96, help="min_price parameter")
    parser.add_argument("--max-price", type=float, default=0.99, help="max_price parameter")
    parser.add_argument("--max-rounds", type=int, help="Max rounds to process")
    parser.add_argument("--starting-balance", type=float, default=500.0, help="Starting balance")
    parser.add_argument("--csv", help="Output CSV file path")
    return parser


async def main_async(argv: list[str] | None = None) -> int:
    """Async main entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    
    # Parse datetime args
    since = _parse_iso(args.since) if args.since else None
    until = _parse_iso(args.until) if args.until else None
    
    # Build replay config
    from btcbot.backtest.replay import ReplayConfig
    from btcbot.config.settings import get_settings
    
    settings = get_settings()
    base = ReplayConfig.from_settings(settings, delta_threshold=Decimal(str(args.delta_threshold)))
    
    from dataclasses import replace
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
    
    # Connect to store
    store = await Store.open(args.db)
    try:
        # Run audit
        diagnostics = await run_latency_audit(store, config, since, until, args.max_rounds)
    finally:
        await store.close()
    
    # Print report
    report = format_report(diagnostics)
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
