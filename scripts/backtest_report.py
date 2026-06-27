"""Skrip CLI: laporan metrik backtest dari data terekam (docs/09 §9.4).

Muat ronde resolved + tick (``btcbot.backtest.replay.load_round_replays``),
jalankan replay, lalu cetak: Net PnL (setelah fee), ROI, win-rate, distribusi
net_edge, reliability curve (label Gamma), max drawdown, varians, sensitivity
grid, dan ablation (fee/slippage/latensi). READ-ONLY (tanpa order/private key).

Pakai:
    uv run python scripts/backtest_report.py
    uv run python scripts/backtest_report.py --grid --ablation --limit 500
    uv run python scripts/backtest_report.py --plot ./reports
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, load_round_replays
from btcbot.backtest.report import (
    ablation,
    build_report,
    format_ablation,
    format_grid,
    format_report,
    sensitivity_grid,
)
from btcbot.config.settings import Settings, get_settings
from btcbot.data.store import Store


def _resolve_delta_threshold(settings: Settings, override: str | None) -> Decimal:
    """Resolusi DELTA_THRESHOLD ke Decimal ('auto' → 0 = tanpa filter Δ)."""
    raw = override if override is not None else settings.delta_threshold
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return Decimal("0")  # 'auto' → tanpa filter (digantikan kalibrasi G1)


async def generate(
    settings: Settings,
    *,
    limit: int | None,
    delta_override: str | None,
    with_grid: bool,
    with_ablation: bool,
    plot_dir: str | None,
) -> str:
    """Muat data, jalankan replay, dan rakit teks laporan."""
    delta = _resolve_delta_threshold(settings, delta_override)
    config = ReplayConfig.from_settings(settings, delta_threshold=delta)

    store = await Store.open(settings.db_url)
    try:
        rounds = await load_round_replays(store, limit=limit)
    finally:
        await store.close()

    if not rounds:
        return (
            "Tidak ada ronde resolved + data book di DB.\n"
            "Rekam data dulu (readonly) lalu --resolve-backfill, baru jalankan laporan ini."
        )

    summary = ReplayEngine(config).run(rounds)
    report = build_report(summary, config.starting_balance)
    sections = [format_report(report)]

    if with_grid:
        cells = sensitivity_grid(
            rounds,
            config,
            t_entry_values=[10, 20, 30],
            delta_values=[Decimal("0"), Decimal("25"), Decimal("50")],
            max_price_values=[Decimal("0.95"), Decimal("0.97"), Decimal("0.99")],
        )
        sections.append(format_grid(cells))
    if with_ablation:
        sections.append(format_ablation(ablation(rounds, config)))
    if plot_dir is not None:
        sections.append(_maybe_plot(report, plot_dir))
    return "\n\n".join(sections)


def _maybe_plot(report: object, out_dir: str) -> str:
    """Simpan plot reliability (PNG) bila matplotlib terpasang; else lewati."""
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        return "(plot dilewati: matplotlib tidak terpasang)"

    import os  # noqa: PLC0415

    from btcbot.backtest.report import BacktestReport  # noqa: PLC0415

    assert isinstance(report, BacktestReport)
    os.makedirs(out_dir, exist_ok=True)
    xs = [float((b.lo + b.hi) / 2) for b in report.reliability if b.count > 0]
    pred = [float(b.predicted) for b in report.reliability if b.count > 0]
    real = [float(b.realized) for b in report.reliability if b.count > 0]
    fig, ax = plt.subplots()
    ax.plot([0.5, 1.0], [0.5, 1.0], "--", color="gray", label="ideal")
    ax.plot(xs, real, "o-", label="realized")
    ax.plot(xs, pred, "s-", label="predicted")
    ax.set_xlabel("p_win")
    ax.set_ylabel("hit-rate")
    ax.set_title("Reliability curve")
    ax.legend()
    path = os.path.join(out_dir, "reliability.png")
    fig.savefig(path)
    plt.close(fig)
    return f"(plot disimpan: {path})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backtest-report", description="Laporan metrik backtest")
    parser.add_argument("--limit", type=int, default=None, help="batas jumlah ronde")
    parser.add_argument(
        "--delta-threshold", default=None, help="override DELTA_THRESHOLD (Decimal)"
    )
    parser.add_argument("--grid", action="store_true", help="sertakan sensitivity grid")
    parser.add_argument("--ablation", action="store_true", help="sertakan tabel ablation")
    parser.add_argument("--plot", default=None, help="direktori output plot PNG (opsional)")
    args = parser.parse_args(argv)

    settings = get_settings()
    text = asyncio.run(
        generate(
            settings,
            limit=args.limit,
            delta_override=args.delta_threshold,
            with_grid=args.grid,
            with_ablation=args.ablation,
            plot_dir=args.plot,
        )
    )
    print(text)  # noqa: T201 - output laporan ke stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
