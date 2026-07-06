"""Check fee calculation in actual signal data (RUMUS FEE investigation).

Computes implied fee+slippage from signals table and compares with expected
fee formula to verify correctness.

Usage:
    python scripts/check_fee_calculation.py analisis.db
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_fee_calculation.py <db_file>", file=sys.stderr)
        return 1
    
    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}", file=sys.stderr)
        return 1
    
    try:
        import sqlite3
    except ImportError:
        print("Error: sqlite3 module not available", file=sys.stderr)
        return 1
    
    conn = sqlite3.connect(str(db_path))
    
    query = """
    SELECT 
        CAST(p_win AS REAL) as p_win,
        CAST(ask_win AS REAL) as ask_win,
        CAST(net_edge AS REAL) as net_edge
    FROM signals
    WHERE net_edge IS NOT NULL
        AND net_edge != ''
        AND ask_win IS NOT NULL
        AND p_win IS NOT NULL
        AND ask_win > 0
    LIMIT 30
    """
    
    try:
        cursor = conn.execute(query)
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Error querying database: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    
    if not rows:
        print("❌ No signals found with valid p_win, ask_win, net_edge")
        return 1
    
    print("=== Fee Calculation Check ===")
    print()
    print("Formula: net_edge = p_win - ask_win - fee_per_share - expected_slippage")
    print("Rearranged: fee+slip = p_win - ask_win - net_edge")
    print()
    print("Current code formula: fee_per_share = rate * min(p, 1-p)^exponent")
    print("  With rate=0.07, exponent=1 at p=0.50: fee = 0.07 * 0.50 = 0.035")
    print()
    print("Article formula: fee = rate * p * (1-p)")
    print("  With rate=0.07 at p=0.50: fee = 0.07 * 0.50 * 0.50 = 0.0175")
    print()
    print(f"{'p_win':>7} {'ask_win':>7} {'net_edge':>9} {'fee+slip':>8} {'expected_fee':>12} {'diff':>8}")
    print("-" * 70)
    
    # Assuming default slippage = 0 (or small), fee+slip ≈ fee
    rate = 0.07
    
    for p_win, ask, edge in rows:
        # Implied fee+slip from data
        implied = p_win - ask - edge
        
        # Expected fee (our current formula)
        expected_fee = rate * min(ask, 1 - ask)
        
        # Difference
        diff = implied - expected_fee
        
        print(f"{p_win:7.4f} {ask:7.4f} {edge:9.4f} {implied:8.4f} {expected_fee:12.4f} {diff:8.4f}")
    
    print()
    print("NOTE:")
    print("- 'fee+slip' = p_win - ask_win - net_edge (from actual data)")
    print("- 'expected_fee' = 0.07 * min(ask, 1-ask) (current code formula, assuming slip=0)")
    print("- 'diff' = actual - expected")
    print()
    print("If diff is consistently ~0, our formula is correct.")
    print("If diff is consistently ~-expected_fee/2, article formula may be correct.")
    print("If diff varies wildly, slippage component dominates.")
    print()
    print("🚩 REMINDER: Source of truth = Polymarket API crypto_fees_v2 response")
    print("   DO NOT change formula without API verification!")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
