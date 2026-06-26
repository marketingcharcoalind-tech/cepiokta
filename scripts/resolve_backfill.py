"""Skrip manual: backfill resolusi SEMUA ronde belum-resolve.

Label outcome (up/down) dari Gamma (ground truth) untuk ronde yang window-nya
sudah lewat tapi ``resolved_outcome`` masih kosong. Idempoten: ronde yang sudah
resolved tidak ditimpa. READ-ONLY (tanpa order/private key).

Pakai:
    uv run python scripts/resolve_backfill.py
    # atau setara:
    uv run python -m btcbot.app.cli --resolve-backfill
"""

from __future__ import annotations

import asyncio

from btcbot.app.cli import configure_logging, run_backfill
from btcbot.config.settings import get_settings


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    n = await run_backfill(settings)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
