"""Read-only replay detector for pure intra-market lock-pair arbitrage.

The detector reads recorded books, measures contiguous valid episodes, and writes
only optional CSV/text output. It never imports or calls OMS, signing, or order APIs.

Recorded snapshots contain best price plus aggregate side depth, not exact depth at
the best level. Replay depth and theoretical PnL are therefore explicit upper-bound
proxies, never executable-profit claims. Duration follows the recorder's documented
last-value-carried-forward (LVCF) semantics: a state remains effective until the
next unique paired-book state.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from btcbot.backtest.replay import ReplayTick, load_round_replays
from btcbot.config.settings import get_settings
from btcbot.data.store import Store
from btcbot.domain.arbitrage import REJECT_REASONS, ArbOpportunity, detect_lock_pair
from btcbot.domain.fees import CryptoFeesV2, FeeModel

if TYPE_CHECKING:
    from collections.abc import Sequence

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ArbDetectorConfig:
    """Conservative measurement parameters, not execution parameters."""

    fee_model: FeeModel
    slippage_buffer: Decimal = Decimal("0.002")
    min_lock_edge: Decimal = Decimal("0.001")
    min_depth: Decimal = Decimal("5")
    max_lock_size: Decimal = Decimal("50")
    max_sum_asks: Decimal = Decimal("0.99")

    def __post_init__(self) -> None:
        if not (_ZERO < self.max_sum_asks <= Decimal("1")):
            raise ValueError("max_sum_asks must be in (0, 1]")
        if self.slippage_buffer < _ZERO:
            raise ValueError("slippage_buffer must be non-negative")
        if self.min_lock_edge < _ZERO:
            raise ValueError("min_lock_edge must be non-negative")
        if self.min_depth <= _ZERO or self.max_lock_size <= _ZERO:
            raise ValueError("depth and max_lock_size must be positive")


@dataclass(frozen=True, slots=True)
class ArbEpisode:
    """One contiguous run of unique valid paired-book states."""

    round_no: int
    start_ts: datetime
    end_ts: datetime
    implied_duration_ms: int
    observations: int
    best_net_edge: Decimal
    min_depth_upper_bound: Decimal
    theoretical_pnl_upper_bound: Decimal


@dataclass(frozen=True, slots=True)
class ArbDetectionReport:
    rounds_processed: int
    ticks_evaluated: int
    unique_book_states: int
    valid_states: int
    episodes: tuple[ArbEpisode, ...]
    reject_counts: dict[str, int]
    theoretical_pnl_upper_bound: Decimal


@dataclass(slots=True)
class _EpisodeBuilder:
    round_no: int
    start_ts: datetime
    last_valid_ts: datetime
    observations: int
    best_edge: Decimal
    min_depth_upper_bound: Decimal
    best_pnl_upper_bound: Decimal

    def add(
        self,
        opportunity: ArbOpportunity,
        observation_ts: datetime,
        max_lock_size: Decimal,
    ) -> None:
        self.last_valid_ts = observation_ts
        self.observations += 1
        edge = opportunity.net_lock_edge or _ZERO
        self.best_edge = max(self.best_edge, edge)
        self.min_depth_upper_bound = min(self.min_depth_upper_bound, opportunity.max_lock_size)
        size = min(opportunity.max_lock_size, max_lock_size)
        self.best_pnl_upper_bound = max(self.best_pnl_upper_bound, edge * size)

    def finish(self, effective_until: datetime) -> ArbEpisode:
        end_ts = max(self.last_valid_ts, effective_until)
        duration = max(0, int((end_ts - self.start_ts).total_seconds() * 1000))
        return ArbEpisode(
            self.round_no,
            self.start_ts,
            end_ts,
            duration,
            self.observations,
            self.best_edge,
            self.min_depth_upper_bound,
            self.best_pnl_upper_bound,
        )


def _book_state_key(tick: ReplayTick) -> tuple[object, ...]:
    """Deduplicate LVCF ticks that repeat an unchanged paired-book state."""
    up_ask = tick.book_up.asks[0] if tick.book_up.asks else None
    down_ask = tick.book_down.asks[0] if tick.book_down.asks else None
    return (
        tick.book_up.ts,
        tick.book_down.ts,
        None if up_ask is None else up_ask.price,
        None if up_ask is None else up_ask.size,
        None if down_ask is None else down_ask.price,
        None if down_ask is None else down_ask.size,
    )


def detect_round_episodes(
    round_no: int,
    ticks: Sequence[ReplayTick],
    config: ArbDetectorConfig,
) -> tuple[tuple[ArbEpisode, ...], dict[str, int], int, int, int]:
    """Group unique states and measure LVCF-implied opportunity lifetimes."""
    episodes: list[ArbEpisode] = []
    rejects = dict.fromkeys(REJECT_REASONS, 0)
    current: _EpisodeBuilder | None = None
    valid_states = 0
    unique_states = 0
    previous_key: tuple[object, ...] | None = None
    last_tick_ts: datetime | None = None

    for tick in ticks:
        last_tick_ts = tick.ts
        state_key = _book_state_key(tick)
        if state_key == previous_key:
            continue
        previous_key = state_key
        unique_states += 1
        opportunity = detect_lock_pair(
            round_no=round_no,
            book_up=tick.book_up,
            book_down=tick.book_down,
            fee_model=config.fee_model,
            slippage_buffer=config.slippage_buffer,
            min_lock_edge=config.min_lock_edge,
            min_depth=config.min_depth,
            max_sum_asks=config.max_sum_asks,
        )
        if opportunity.valid:
            valid_states += 1
            edge = opportunity.net_lock_edge or _ZERO
            pnl = edge * min(opportunity.max_lock_size, config.max_lock_size)
            if current is None:
                current = _EpisodeBuilder(
                    round_no,
                    tick.ts,
                    tick.ts,
                    1,
                    edge,
                    opportunity.max_lock_size,
                    pnl,
                )
            else:
                current.add(opportunity, tick.ts, config.max_lock_size)
        else:
            if opportunity.reject_reason is not None:
                rejects[opportunity.reject_reason] += 1
            if current is not None:
                episodes.append(current.finish(tick.ts))
                current = None

    if current is not None:
        episodes.append(current.finish(last_tick_ts or current.last_valid_ts))
    return tuple(episodes), rejects, len(ticks), unique_states, valid_states


async def replay_arb_detection(  # noqa: PLR0913
    store: Store,
    config: ArbDetectorConfig,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    max_rounds: int | None = None,
    progress_every: int = 100,
) -> ArbDetectionReport:
    """Stream recorded rounds once and return aggregate read-only metrics."""
    all_episodes: list[ArbEpisode] = []
    rejects = dict.fromkeys(REJECT_REASONS, 0)
    rounds = ticks_total = unique_states = valid_states = 0
    async for rnd, ticks in load_round_replays(store, since=since, until=until, limit=max_rounds):
        rounds += 1
        episodes, round_rejects, tick_count, round_unique, round_valid = detect_round_episodes(
            rnd.round_no, ticks, config
        )
        all_episodes.extend(episodes)
        ticks_total += tick_count
        unique_states += round_unique
        valid_states += round_valid
        for reason, count in round_rejects.items():
            rejects[reason] += count
        if progress_every > 0 and rounds % progress_every == 0:
            sys.stderr.write(
                f"processed={rounds} ticks={ticks_total} unique_states={unique_states} "
                f"episodes={len(all_episodes)}\n"
            )
            sys.stderr.flush()
    theoretical = sum((episode.theoretical_pnl_upper_bound for episode in all_episodes), _ZERO)
    return ArbDetectionReport(
        rounds,
        ticks_total,
        unique_states,
        valid_states,
        tuple(all_episodes),
        rejects,
        theoretical,
    )


def _percentile(values: Sequence[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = int(position)
    fraction = position - lower
    if lower + 1 >= len(ordered):
        return float(ordered[-1])
    return ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])


def format_report(report: ArbDetectionReport) -> str:
    durations = [episode.implied_duration_ms for episode in report.episodes]
    edges = [episode.best_net_edge for episode in report.episodes]
    depths = [episode.min_depth_upper_bound for episode in report.episodes]
    median_edge = sorted(edges)[len(edges) // 2] if edges else _ZERO
    median_depth = sorted(depths)[len(depths) // 2] if depths else _ZERO
    lines = [
        "=== PURE ARBITRAGE DETECTOR (READ-ONLY) ===",
        f"rounds processed : {report.rounds_processed}",
        f"ticks evaluated  : {report.ticks_evaluated}",
        f"unique paired-book states: {report.unique_book_states}",
        f"valid states     : {report.valid_states}",
        f"opportunity episodes: {len(report.episodes)}",
        f"LVCF-implied duration ms p25/median/p75/max: "
        f"{_percentile(durations, 0.25):.1f}/{_percentile(durations, 0.50):.1f}/"
        f"{_percentile(durations, 0.75):.1f}/{max(durations, default=0)}",
        f"median best edge : {median_edge}",
        f"median aggregate-depth upper bound: {median_depth}",
        f"theoretical PnL upper bound before two-leg execution risk: "
        f"{report.theoretical_pnl_upper_bound}",
        "reject counts (unique states):",
    ]
    lines.extend(f"  {reason}: {report.reject_counts.get(reason, 0)}" for reason in REJECT_REASONS)
    lines.extend(
        [
            "WARNING: duration is LVCF-implied until the next recorded state.",
            "WARNING: recorder has aggregate side depth, not best-level depth.",
            "WARNING: depth and PnL are upper-bound proxies, not executable estimates.",
            "WARNING: Phase 1 does not simulate atomic two-leg fills.",
        ]
    )
    return "\n".join(lines)


def write_csv(report: ArbDetectionReport, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(tuple(ArbEpisode.__dataclass_fields__))
        for episode in report.episodes:
            writer.writerow(
                (
                    episode.round_no,
                    episode.start_ts.isoformat(),
                    episode.end_ts.isoformat(),
                    episode.implied_duration_ms,
                    episode.observations,
                    str(episode.best_net_edge),
                    str(episode.min_depth_upper_bound),
                    str(episode.theoretical_pnl_upper_bound),
                )
            )


def _parse_iso(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only pure lock-pair detector")
    parser.add_argument("--db", required=True)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--max-sum-asks", default="0.99")
    parser.add_argument("--slippage-buffer", default="0.002")
    parser.add_argument("--min-lock-edge", default="0.001")
    parser.add_argument("--min-depth", default="5")
    parser.add_argument("--max-lock-size", default="50")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--csv", type=Path)
    return parser


async def main_async(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    config = ArbDetectorConfig(
        fee_model=CryptoFeesV2(settings.fee_rate, settings.fee_exponent),
        slippage_buffer=Decimal(args.slippage_buffer),
        min_lock_edge=Decimal(args.min_lock_edge),
        min_depth=Decimal(args.min_depth),
        max_lock_size=Decimal(args.max_lock_size),
        max_sum_asks=Decimal(args.max_sum_asks),
    )
    store = await Store.open(args.db)
    try:
        report = await replay_arb_detection(
            store,
            config,
            since=_parse_iso(args.since) if args.since else None,
            until=_parse_iso(args.until) if args.until else None,
            max_rounds=args.max_rounds,
            progress_every=args.progress_every,
        )
    finally:
        await store.close()
    sys.stdout.write(format_report(report) + "\n")
    if args.csv is not None:
        write_csv(report, args.csv)
        sys.stdout.write(f"CSV: {args.csv}\n")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
