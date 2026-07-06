"""Analyze |Δ| distribution at entry window (TEMUAN 1 investigation).

Checks if all |delta| > 0.10 in the dataset, which would explain why
delta_threshold grid sweep (0.02, 0.05, 0.10) produces identical results.

Usage:
    python scripts/analyze_delta_distribution.py analisis.db
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/analyze_delta_distribution.py <db_file>", file=sys.stderr)
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
    
    # Get signals where time_left <= 60 (default T_ENTRY)
    query = """
    SELECT 
        ABS(CAST(delta AS REAL)) as abs_delta
    FROM signals 
    WHERE time_left_sec <= 60
        AND delta IS NOT NULL
        AND delta != ''
    ORDER BY abs_delta
    """
    
    try:
        cursor = conn.execute(query)
        deltas = [row[0] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Error querying database: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    
    if not deltas:
        print("❌ No signals found with time_left <= 60")
        print("   Database may be empty or schema unexpected.")
        return 1
    
    n = len(deltas)
    
    print(f"=== |Δ| Distribution at time_left <= 60 ===")
    print(f"Total ticks: {n}")
    print(f"Min:     {deltas[0]:.6f}")
    print(f"P25:     {deltas[n//4]:.6f}")
    print(f"Median:  {deltas[n//2]:.6f}")
    print(f"P75:     {deltas[3*n//4]:.6f}")
    print(f"Max:     {deltas[-1]:.6f}")
    print()
    
    # Count by threshold
    under_002 = sum(1 for d in deltas if d < 0.02)
    under_005 = sum(1 for d in deltas if d < 0.05)
    under_010 = sum(1 for d in deltas if d < 0.10)
    under_020 = sum(1 for d in deltas if d < 0.20)
    
    print(f"|Δ| < 0.02: {under_002:5d} ({100*under_002/n:5.1f}%)")
    print(f"|Δ| < 0.05: {under_005:5d} ({100*under_005/n:5.1f}%)")
    print(f"|Δ| < 0.10: {under_010:5d} ({100*under_010/n:5.1f}%)")
    print(f"|Δ| < 0.20: {under_020:5d} ({100*under_020/n:5.1f}%)")
    print()
    
    # Interpretation
    if under_010 == 0:
        print("🔴 FINDING: ALL ticks have |Δ| >= 0.10")
        print("   → This explains why delta thresholds 0.02/0.05/0.10 give IDENTICAL results.")
        print("   → Delta filter is WORKING CORRECTLY, but data has no small deltas.")
        print()
        print("   RECOMMENDATION:")
        print("   - Use coarser delta grid: --delta-grid 0.10,0.20,0.40")
        print("   - OR accept that delta filter doesn't discriminate for this dataset")
        return 0
    
    if under_002 < n * 0.01:
        print("🟡 FINDING: Very few ticks with |Δ| < 0.02")
        print(f"   → Only {under_002}/{n} ({100*under_002/n:.1f}%) have |Δ| < 0.02")
        print("   → Grid delta=0.02 vs 0.05 may show minimal difference")
        print()
        
        if under_010 < n * 0.05:
            print("   → Also few ticks with |Δ| < 0.10")
            print("   → Grid values 0.02/0.05/0.10 are too fine for this dataset")
            print()
            print("   RECOMMENDATION: Use coarser grid matching data distribution")
        else:
            print("   → But decent variation between 0.05 and 0.10")
            print("   → Grid should show SOME difference if wiring correct")
        return 0
    
    # Substantial variation
    print("✅ FINDING: Dataset has sufficient variation in |Δ|")
    print(f"   → {under_002} ticks ({100*under_002/n:.1f}%) with |Δ| < 0.02")
    print(f"   → {under_005} ticks ({100*under_005/n:.1f}%) with |Δ| < 0.05")
    print(f"   → {under_010} ticks ({100*under_010/n:.1f}%) with |Δ| < 0.10")
    print()
    print("   If grid delta=0.02/0.05/0.10 still produces IDENTICAL results,")
    print("   there MAY be a wiring bug (though code review found none).")
    print()
    print("   NEXT STEPS:")
    print("   1. Check entry diagnostics: how many filtered by 'abs_delta<threshold'?")
    print("   2. Run test: python -m pytest tests/backtest/test_delta_sensitivity.py -v")
    print("   3. If test passes, bug is likely in data/filtering, not core logic")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
