"""Read-only replay detector for pure intra-market lock-pair arbitrage.

The detector reads recorded books, measures contiguous valid episodes, and writes
only optional CSV/text output. It never imports or calls OMS, signing, or order APIs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
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
    """One contiguous run of valid lock-pair observations."""

    round_no: int
    start_ts: datetime
    end_ts: datetime
    duration_ms: int
    observations: int
    best_net_edge: Decimal
    min_depth: Decimal
    theoretical_pnl: Decimal


@dataclass(frozen=True, slots=True)
class ArbDetectionReport:
    rounds_processed: int
    ticks_evaluated: int
    valid_ticks: int
    episodes: tuple[ArbEpisode, ...]
    reject_counts: dict[str, int]
    theoretical_pnl: Decimal


@dataclass(slots=True)
class _EpisodeBuilder:
    round_no: int
    start_ts: datetime
    end_ts: datetime
    observations: int
    best_edge: Decimal
    min_depth: Decimal
    best_theoretical_pnl: Decimal

    def add(self, opportunity: ArbOpportunity, max_lock_size: Decimal) -> None:
        self.end_ts = opportunity.ts  # type: ignore[assignment]
        self.observations += 1
        edge = opportunity.net_lock_edge or _ZERO
        self.best_edge = max(self.best_edge, edge)
        self.min_depth = min(self.min_depth, opportunity.max_lock_size)
        size = min(opportunity.max_lock_size, max_lock_size)
        self.best_theoretical_pnl = max(self.best_theoretical_pnl, edge * size)

    def finish(self) -> ArbEpisode:
        duration = max(0, int((self.end_ts - self.start_ts).total_seconds() * 1000))
        return ArbEpisode(
            self.round_no,
            self.start_ts,
            self.end_ts,
            duration,
            self.observations,
            self.best_edge,
            self.min_depth,
            self.best_theoretical_pnl,
        )


def detect_round_episodes(
    round_no: int,
    ticks: Sequence[ReplayTick],
    config: ArbDetectorConfig,
) -> tuple[tuple[ArbEpisode, ...], dict[str, int], int, int]:
    """Evaluate one round and group consecutive valid ticks into episodes."""
    episodes: list[ArbEpisode] = []
    rejects = dict.fromkeys(REJECT_REASONS, 0)
    current: _EpisodeBuilder | None = None
    valid_ticks = 0

    for tick in ticks:
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
            valid_ticks += 1
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
                current.add(opportunity, config.max_lock_size)
        else:
            if opportunity.reject_reason is not None:
                rejects[opportunity.reject_reason] += 1
            if current is not None:
                episodes.append(current.finish())
                current = None

    if current is not None:
        episodes.append(current.finish())
    return tuple(episodes), rejects, len(ticks), valid_ticks


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
    rounds = ticks_total = valid_ticks = 0
    async for rnd, ticks in load_round_replays(store, since=since, until=until, limit=max_rounds):
        rounds += 1
        episodes, round_rejects, tick_count, round_valid = detect_round_episodes(
            rnd.round_no, ticks, config
        )
        all_episodes.extend(episodes)
        ticks_total += tick_count
        valid_ticks += round_valid
        for reason, count in round_rejects.items():
            rejects[reason] += count
        if progress_every > 0 and rounds % progress_every == 0:
            sys.stderr.write(
                f"processed={rounds} ticks={ticks_total} episodes={len(all_episodes)}\n"
            )
            sys.stderr.flush()
    theoretical = sum((episode.theoretical_pnl for episode in all_episodes), _ZERO)
    return ArbDetectionReport(
        rounds,
        ticks_total,
        valid_ticks,
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
    durations = [episode.duration_ms for episode in report.episodes]
    edges = [episode.best_net_edge for episode in report.episodes]
    depths = [episode.min_depth for episode in report.episodes]
    median_edge = sorted(edges)[len(edges) // 2] if edges else _ZERO
    median_depth = sorted(depths)[len(depths) // 2] if depths else _ZERO
    lines = [
        "=== PURE ARBITRAGE DETECTOR (READ-ONLY) ===",
        f"rounds processed : {report.rounds_processed}",
        f"ticks evaluated  : {report.ticks_evaluated}",
        f"valid ticks      : {report.valid_ticks}",
        f"opportunity episodes: {len(report.episodes)}",
        f"duration ms p25/median/p75/max: {_percentile(durations, 0.25):.1f}/"
        f"{_percentile(durations, 0.50):.1f}/{_percentile(durations, 0.75):.1f}/"
        f"{max(durations, default=0)}",
        f"median best edge : {median_edge}",
        f"median min depth : {median_depth}",
        f"theoretical locked PnL before execution risk: {report.theoretical_pnl}",
        "reject counts:",
    ]
    lines.extend(f"  {reason}: {report.reject_counts.get(reason, 0)}" for reason in REJECT_REASONS)
    lines.append("WARNING: theoretical only; two-leg fill risk is not simulated here.")
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
                    episode.duration_ms,
                    episode.observations,
                    str(episode.best_net_edge),
                    str(episode.min_depth),
                    str(episode.theoretical_pnl),
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
