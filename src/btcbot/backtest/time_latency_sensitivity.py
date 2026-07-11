"""Read-only time-latency sensitivity analysis for the G1 candidate.

Streams every resolved round from SQLite once, then evaluates independent replay
configs for tick latency 0/1 and time latency 50/100/250/500/1000 ms. Nothing is
written to the database and no execution adapter is imported.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, load_round_replays
from btcbot.config.settings import get_settings
from btcbot.data.store import Store
from btcbot.domain.models import Outcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from btcbot.backtest.replay import ReplayTick, RoundObservation
    from btcbot.domain.models import Round, RoundResult

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LatencyVariant:
    """One independent latency configuration."""

    name: str
    mode: str
    ticks: int | None = None
    milliseconds: int | None = None


DEFAULT_VARIANTS: tuple[LatencyVariant, ...] = (
    LatencyVariant("tick_0", "ticks", ticks=0),
    LatencyVariant("tick_1", "ticks", ticks=1),
    LatencyVariant("time_50ms", "time", milliseconds=50),
    LatencyVariant("time_100ms", "time", milliseconds=100),
    LatencyVariant("time_250ms", "time", milliseconds=250),
    LatencyVariant("time_500ms", "time", milliseconds=500),
    LatencyVariant("time_1000ms", "time", milliseconds=1000),
)


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    """Final metrics for one latency variant."""

    name: str
    rounds: int
    signal_attempts: int
    fills: int
    entries: int
    wins: int
    losses: int
    net_pnl: Decimal
    roi: Decimal
    final_balance: Decimal
    no_future_entry: int
    no_future_hedge: int
    no_future_exit: int
    latency_median_ms: float
    latency_p95_ms: float
    latency_max_ms: float
    overshoot_median_ms: float
    overshoot_p95_ms: float
    stale_target_book_rate: float
    target_book_age_median_ms: float
    target_book_age_p95_ms: float
    up_entries: int
    down_entries: int
    loss_rounds: tuple[int, ...]


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear percentile with deterministic interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    fraction = position - lower
    if lower + 1 >= len(ordered):
        return ordered[-1]
    return ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])


def build_variant_configs(
    base: ReplayConfig,
    variants: Sequence[LatencyVariant] = DEFAULT_VARIANTS,
) -> tuple[tuple[LatencyVariant, ReplayConfig], ...]:
    """Build isolated configs without mutating the baseline."""
    built: list[tuple[LatencyVariant, ReplayConfig]] = []
    for variant in variants:
        if variant.mode == "ticks":
            if variant.ticks is None or variant.ticks < 0:
                raise ValueError(f"Invalid tick latency for {variant.name}")
            config = replace(base, latency_mode="ticks", latency_ticks=variant.ticks)
        elif variant.mode == "time":
            if variant.milliseconds is None or variant.milliseconds < 0:
                raise ValueError(f"Invalid time latency for {variant.name}")
            config = replace(base, latency_mode="time", latency_ms=variant.milliseconds)
        else:
            raise ValueError(f"Unknown latency mode: {variant.mode}")
        built.append((variant, config))
    return tuple(built)


@dataclass(slots=True)
class _Accumulator:
    variant: LatencyVariant
    engine: ReplayEngine
    starting_balance: Decimal
    bankroll: Decimal
    rounds: int = 0
    signal_attempts: int = 0
    fills: int = 0
    entries: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: Decimal = _ZERO
    no_future_entry: int = 0
    no_future_hedge: int = 0
    no_future_exit: int = 0
    up_entries: int = 0
    down_entries: int = 0
    loss_rounds: list[int] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    overshoots: list[float] = field(default_factory=list)
    target_book_ages: list[float] = field(default_factory=list)
    stale_target_books: int = 0

    def observe(self, rnd: Round, ticks: Sequence[ReplayTick]) -> None:
        """Replay one round and update only this variant's bankroll."""
        self.rounds += 1
        result, _diagnostic, obs = self.engine.observe(rnd, ticks, bankroll=self.bankroll)
        self.signal_attempts += obs.enter_orders_yielded
        self.fills += obs.fills
        self.no_future_entry += obs.no_future_tick_entry_attempts
        self.no_future_hedge += obs.no_future_tick_hedge_attempts
        self.no_future_exit += obs.no_future_tick_exit_attempts
        if result is None:
            return
        self._record_entry(rnd, ticks, result, obs)

    def _record_entry(
        self,
        rnd: Round,
        ticks: Sequence[ReplayTick],
        result: RoundResult,
        obs: RoundObservation,
    ) -> None:
        self.entries += 1
        self.net_pnl += result.pnl
        self.bankroll = result.balance_after
        won = (
            result.side_taken == rnd.resolved_outcome.value
            if rnd.resolved_outcome
            else result.pnl > _ZERO
        )
        if won:
            self.wins += 1
        else:
            self.losses += 1
            self.loss_rounds.append(rnd.round_no)
        if result.side_taken == Outcome.UP.value:
            self.up_entries += 1
        else:
            self.down_entries += 1

        if obs.realized_entry_latency_ms is not None:
            self.latencies.append(obs.realized_entry_latency_ms)
        if obs.entry_execution_overshoot_ms is not None:
            self.overshoots.append(obs.entry_execution_overshoot_ms)

        execution_index = obs.actual_entry_execution_tick_index
        if execution_index is None or not 0 <= execution_index < len(ticks):
            return
        execution_tick = ticks[execution_index]
        target_book = (
            execution_tick.book_up
            if result.side_taken == Outcome.UP.value
            else execution_tick.book_down
        )
        age_ms = max(0.0, (execution_tick.ts - target_book.ts).total_seconds() * 1000.0)
        self.target_book_ages.append(age_ms)
        if age_ms > 0.0:
            self.stale_target_books += 1

    def row(self) -> SensitivityRow:
        stale_rate = (
            self.stale_target_books / len(self.target_book_ages) if self.target_book_ages else 0.0
        )
        roi = self.net_pnl / self.starting_balance if self.starting_balance > _ZERO else _ZERO
        return SensitivityRow(
            name=self.variant.name,
            rounds=self.rounds,
            signal_attempts=self.signal_attempts,
            fills=self.fills,
            entries=self.entries,
            wins=self.wins,
            losses=self.losses,
            net_pnl=self.net_pnl,
            roi=roi,
            final_balance=self.bankroll,
            no_future_entry=self.no_future_entry,
            no_future_hedge=self.no_future_hedge,
            no_future_exit=self.no_future_exit,
            latency_median_ms=_percentile(self.latencies, 0.50),
            latency_p95_ms=_percentile(self.latencies, 0.95),
            latency_max_ms=max(self.latencies, default=0.0),
            overshoot_median_ms=_percentile(self.overshoots, 0.50),
            overshoot_p95_ms=_percentile(self.overshoots, 0.95),
            stale_target_book_rate=stale_rate,
            target_book_age_median_ms=_percentile(self.target_book_ages, 0.50),
            target_book_age_p95_ms=_percentile(self.target_book_ages, 0.95),
            up_entries=self.up_entries,
            down_entries=self.down_entries,
            loss_rounds=tuple(self.loss_rounds),
        )


async def run_sensitivity(  # noqa: PLR0913
    store: Store,
    base: ReplayConfig,
    *,
    since: datetime | None,
    until: datetime | None,
    max_rounds: int | None,
    variants: Sequence[LatencyVariant] = DEFAULT_VARIANTS,
) -> tuple[SensitivityRow, ...]:
    """Stream the dataset once and evaluate every latency config independently."""
    accumulators = [
        _Accumulator(
            variant,
            ReplayEngine(config),
            base.starting_balance,
            base.starting_balance,
        )
        for variant, config in build_variant_configs(base, variants)
    ]
    async for rnd, ticks in load_round_replays(
        store,
        since=since,
        until=until,
        limit=max_rounds,
    ):
        if not ticks:
            continue
        for accumulator in accumulators:
            accumulator.observe(rnd, ticks)
    return tuple(accumulator.row() for accumulator in accumulators)


def format_report(rows: Sequence[SensitivityRow]) -> str:
    """Render compact comparison plus diagnostics for each config."""
    lines = [
        "=== TIME-LATENCY SENSITIVITY (READ-ONLY) ===",
        "variant rounds entries W/L net_pnl roi% final signals/fills no_future(E/H/X)",
    ]
    for row in rows:
        lines.append(
            f"{row.name:<12} {row.rounds:>6} {row.entries:>7} "
            f"{row.wins:>3}/{row.losses:<3} {row.net_pnl:>9.2f} "
            f"{row.roi * Decimal('100'):>6.2f} {row.final_balance:>8.2f} "
            f"{row.signal_attempts:>5}/{row.fills:<5} "
            f"{row.no_future_entry}/{row.no_future_hedge}/{row.no_future_exit}"
        )
    lines.append("")
    for row in rows:
        losses = ",".join(str(value) for value in row.loss_rounds) or "none"
        lines.extend(
            [
                f"[{row.name}] latency ms median/p95/max: "
                f"{row.latency_median_ms:.3f}/{row.latency_p95_ms:.3f}/"
                f"{row.latency_max_ms:.3f}",
                f"[{row.name}] overshoot ms median/p95: "
                f"{row.overshoot_median_ms:.3f}/{row.overshoot_p95_ms:.3f}",
                f"[{row.name}] stale target book: {row.stale_target_book_rate:.1%}; "
                f"age median/p95: {row.target_book_age_median_ms:.3f}/"
                f"{row.target_book_age_p95_ms:.3f} ms",
                f"[{row.name}] entries UP/DOWN: {row.up_entries}/{row.down_entries}; "
                f"loss rounds: {losses}",
            ]
        )
    return "\n".join(lines)


def write_csv(rows: Sequence[SensitivityRow], path: Path) -> None:
    """Write one summary row per latency configuration."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(tuple(SensitivityRow.__dataclass_fields__))
        for row in rows:
            writer.writerow(
                [
                    row.name,
                    row.rounds,
                    row.signal_attempts,
                    row.fills,
                    row.entries,
                    row.wins,
                    row.losses,
                    str(row.net_pnl),
                    str(row.roi),
                    str(row.final_balance),
                    row.no_future_entry,
                    row.no_future_hedge,
                    row.no_future_exit,
                    row.latency_median_ms,
                    row.latency_p95_ms,
                    row.latency_max_ms,
                    row.overshoot_median_ms,
                    row.overshoot_p95_ms,
                    row.stale_target_book_rate,
                    row.target_book_age_median_ms,
                    row.target_book_age_p95_ms,
                    row.up_entries,
                    row.down_entries,
                    ";".join(str(value) for value in row.loss_rounds),
                ]
            )


def _parse_iso(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only G1 time-latency sensitivity")
    parser.add_argument("--db", required=True, help="SQLite URL")
    parser.add_argument("--since", help="ISO-8601 lower bound")
    parser.add_argument("--until", help="ISO-8601 upper bound")
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--t-entry", type=int, default=60)
    parser.add_argument("--delta-threshold", default="50")
    parser.add_argument("--min-price", default="0.96")
    parser.add_argument("--max-price", default="0.99")
    parser.add_argument("--starting-balance", default="500")
    parser.add_argument("--csv", type=Path)
    return parser


async def main_async(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    delta = Decimal(args.delta_threshold)
    base = ReplayConfig.from_settings(settings, delta_threshold=delta)
    params = replace(
        base.params,
        t_entry_sec=args.t_entry,
        delta_threshold=delta,
        min_price=Decimal(args.min_price),
        max_price=Decimal(args.max_price),
    )
    limits = replace(base.limits, max_price=Decimal(args.max_price))
    base = replace(
        base,
        params=params,
        limits=limits,
        starting_balance=Decimal(args.starting_balance),
    )
    store = await Store.open(args.db)
    try:
        rows = await run_sensitivity(
            store,
            base,
            since=_parse_iso(args.since) if args.since else None,
            until=_parse_iso(args.until) if args.until else None,
            max_rounds=args.max_rounds,
        )
    finally:
        await store.close()
    sys.stdout.write(format_report(rows) + "\n")
    if args.csv is not None:
        write_csv(rows, args.csv)
        sys.stdout.write(f"CSV: {args.csv}\n")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
