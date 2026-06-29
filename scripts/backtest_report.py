"""Skrip CLI tipis: laporan metrik backtest (G1).

Delegasi ke ``btcbot.backtest.report.main`` (semua logika ada di sana). Setara:
    uv run python -m btcbot.backtest.report [flags]
    uv run python scripts/backtest_report.py [flags]
READ-ONLY (tanpa order/private key). Lihat ``--help`` untuk daftar flag.
"""

from __future__ import annotations

from btcbot.backtest.report import main

if __name__ == "__main__":
    raise SystemExit(main())
