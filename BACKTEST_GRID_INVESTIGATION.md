# Backtest Grid Investigation - Pre-G1 Analysis

**Date**: 2026-07-06  
**Context**: Backtest grid (391 rounds, --max-rounds 400) shows two anomalies  
**Goal**: Prepare analysis commands for user to run on VPS (NO `uv`, use `python -m`)

---

## TEMUAN 1: DELTA_THRESHOLD Tampak INERT

### Gejala
Grid sweep delta=0.02/0.05/0.10 menghasilkan hasil IDENTIK di semua kombinasi.
Contoh: t_entry=60/max_price=0.99 → entered=39, net_pnl=-4.55 untuk SEMUA delta.

### Analisis Kode: Wiring CORRECT

**Flow Chart**:
```
CLI args (--delta-grid)
  ↓
report.py:main() line 817: delta_values=_dec_list(args.delta_grid)
  ↓
report.py:generate_report() line 679: delta_values parameter
  ↓
report.py:_grid_configs() line 625: delta_threshold=delta (line 625)
  ↓
replay.py:ReplayConfig.params.delta_threshold
  ↓
strategy.py:Strategy._consider_entry() line 221: if abs(signal.delta) < p.delta_threshold
```

**Key Files & Lines**:
1. **report.py line 625**: `delta_threshold=delta` - Grid delta CORRECTLY wired into params
2. **strategy.py line 221**: `if abs(signal.delta) < p.delta_threshold: return NoOp(reason="abs_delta<threshold")`

**Conclusion**: Code wiring is CORRECT. Delta parameter flows from CLI → grid config → strategy filter.

### Hypothesis: Data Issue (All |Δ| > 0.10)

If all ticks in dataset have |Δ| > 0.10, then delta thresholds 0.02/0.05/0.10 would all pass the same ticks.

---

## TEMUAN 2: Model p_win OVERCONFIDENT

### Gejala
Reliability bin [0.90,1.00): predicted=0.981 vs realized=0.667 (n=9)
Net edge negatif kemungkinan karena p_win inflated → entry pada false edge.

### Analisis Kode: Vol Parameter Flow

**Flow Chart**:
```
settings.py line 119: backtest_vol_per_sqrt_sec: Decimal = Decimal("5")  # DEFAULT
  ↓
replay.py:ReplayConfig.from_settings() line 454: vol=settings.backtest_vol_per_sqrt_sec
  ↓
replay.py:ReplayEngine.__init__() stores self._vol
  ↓
replay.py:ReplayEngine._exec_tick() line 768: signal = self._signal_engine.compute(..., self._vol, ...)
  ↓
signal.py:SignalEngine.compute() line 83: vol parameter
  ↓
signal.py line 101: sigma_left = float(vol) * math.sqrt(time_left_sec)
  ↓
signal.py line 102: z = float(delta) / max(sigma_left, self._eps)
  ↓
signal.py line 103: p_win = normal_cdf(abs(z))
```

**Key Files & Lines**:
1. **settings.py line 119**: `backtest_vol_per_sqrt_sec: Decimal = Decimal("5")` - DEFAULT value
2. **replay.py line 454**: `vol=settings.backtest_vol_per_sqrt_sec` - Vol injected into ReplayConfig
3. **signal.py line 101**: `sigma_left = float(vol) * math.sqrt(time_left_sec)` - Vol used in p_win calc

**Environment Variable**: `BACKTEST_VOL_PER_SQRT_SEC` in .env (if set, overrides default)

### Hypothesis: Vol Not Calibrated

Calibration output shows vol=5 has ECE=0.0263 @ 358 rounds (good calibration).
But if this vol=5 was NOT set in .env and backtest used default=5, then we're good.
HOWEVER, if calibration recommended different vol (e.g., vol=10) but it was NOT applied, that's the issue.

---

## RUMUS FEE: Current Implementation

**File**: `src/btcbot/domain/fees.py`

**Current Formula** (lines 24-35):
```python
def estimate_fee(
    price: Decimal,
    size: Decimal,
    rate: Decimal = DEFAULT_FEE_RATE,  # 0.07
    exponent: int = DEFAULT_FEE_EXPONENT,  # 1
) -> Decimal:
    """fee = size * rate * min(price, 1-price) ** exponent"""
    edge_dist = min(price, _ONE - price)
    edge_dist = max(edge_dist, _ZERO)
    return size * rate * (edge_dist**exponent)
```

**Implementation** (lines 85-87):
```python
def fee_per_share(self, price: Decimal) -> Decimal:
    """Biaya per share = rate * min(price, 1-price) ** exponent."""
    return estimate_fee(price, _ONE, self.rate, self.exponent)
```

**Effective Formula**: `fee_per_share = rate * min(p, 1-p)^exponent`

**Reference Article Claims**: `fee = contracts × rate × p × (1-p)`

**Discrepancy**:
- Our code: `min(p, 1-p)` - symmetric, max at p=0.5
- Article: `p × (1-p)` - symmetric, max at p=0.5 (same shape!)

**Analysis**:
- Both formulas are symmetric and max at p=0.5
- `min(p, 1-p)` ranges [0, 0.5]
- `p × (1-p)` ranges [0, 0.25]
- With rate=0.07, exponent=1:
  - Our formula at p=0.5: 0.07 × 0.5 = 0.035 (3.5%)
  - Article formula at p=0.5: 0.07 × 0.5 × 0.5 = 0.0175 (1.75%)

**🚩 FLAG**: Formulas differ by factor of ~2 at p=0.5. Our implementation may be OVERCHARGING fee.

**Source of Truth**: Polymarket API response `crypto_fees_v2` feeSchedule.

**Recommendation**: 
1. DO NOT change formula without API verification
2. Need to check actual fee charged by Polymarket for test order
3. Fixture `tests/fixtures/gamma_fee_schedule.json` shows `{rate: 0.07, exponent: 1}` but doesn't clarify exact formula

---

## DELIVERABLE: Commands for User to Run on VPS

### Prerequisites

```bash
cd ~/cepiokta

# 1. Create consistent backup (while bot is running)
sqlite3 btcbot.db "VACUUM INTO 'analisis.db';"

# 2. Verify backup integrity
sqlite3 analisis.db "PRAGMA integrity_check;"
# Expected output: ok

# 3. Check backup size
ls -lh analisis.db
```

---

### COMMAND 1: Verify Current Vol Setting

**Purpose**: Check what vol value is actually being used

```bash
# Check .env file
echo "=== .env BACKTEST_VOL_PER_SQRT_SEC ==="
grep -i "BACKTEST_VOL_PER_SQRT_SEC" .env || echo "NOT SET (using default=5)"

# Check default in code
echo ""
echo "=== Code Default (settings.py line 119) ==="
grep -A 1 "backtest_vol_per_sqrt_sec.*Decimal" src/btcbot/config/settings.py
```

**Expected Output**:
```
=== .env BACKTEST_VOL_PER_SQRT_SEC ===
NOT SET (using default=5)

=== Code Default (settings.py line 119) ===
    backtest_vol_per_sqrt_sec: Decimal = Decimal("5")  # TODO calibrate G1
```

---

### COMMAND 2: Analyze |Δ| Distribution at Entry Window

**Purpose**: Check if all |Δ| > 0.10 (explains why delta threshold doesn't matter)

```bash
# Create analysis script
cat > analyze_delta_dist.py << 'SCRIPT'
import asyncio
import sqlite3
from decimal import Decimal

async def main():
    conn = sqlite3.connect("analisis.db")
    
    # Get signals where time_left <= 60 (default T_ENTRY)
    query = """
    SELECT 
        ABS(CAST(delta AS REAL)) as abs_delta
    FROM signals 
    WHERE time_left_sec <= 60
        AND delta IS NOT NULL
        AND delta != ''
    """
    
    cursor = conn.execute(query)
    deltas = [row[0] for row in cursor.fetchall()]
    
    if not deltas:
        print("No signals found with time_left <= 60")
        return
    
    deltas.sort()
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
    
    print(f"|Δ| < 0.02: {under_002:5d} ({100*under_002/n:5.1f}%)")
    print(f"|Δ| < 0.05: {under_005:5d} ({100*under_005/n:5.1f}%)")
    print(f"|Δ| < 0.10: {under_010:5d} ({100*under_010/n:5.1f}%)")
    print()
    
    if under_010 == 0:
        print("⚠️  FINDING: ALL ticks have |Δ| >= 0.10")
        print("    This explains why delta thresholds 0.02/0.05/0.10 give identical results.")
        print("    Delta filter is WORKING but data has no small deltas.")
    elif under_002 < n * 0.01:
        print("⚠️  FINDING: Very few ticks with |Δ| < 0.02")
        print("    Grid delta values may be too granular for this dataset.")
    else:
        print("✓  Data has sufficient variation in |Δ|")
        print("   If grid results are still identical, there may be a wiring bug.")
    
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
SCRIPT

# Run analysis
python analyze_delta_dist.py
```

**Expected Output** (hypothesis: all |Δ| > 0.10):
```
=== |Δ| Distribution at time_left <= 60 ===
Total ticks: XXXX
Min:     0.XXXXXX
P25:     X.XXXXXX
Median:  X.XXXXXX
P75:     X.XXXXXX
Max:     XX.XXXXXX

|Δ| < 0.02:     0 (  0.0%)
|Δ| < 0.05:     X (  X.X%)
|Δ| < 0.10:    XX ( XX.X%)

⚠️  FINDING: [interpretation based on data]
```

---

### COMMAND 3: Run Calibration

**Purpose**: Get calibration results and recommended vol

```bash
python -m btcbot.backtest.calibrate \
    --db "sqlite+aiosqlite:///./analisis.db" \
    --vols 5,10,20,40,80 \
    --min-samples 20
```

**Expected Output** (paste FULL output):
```
[Expected format from calibrate.py]
=== VOLATILITY CALIBRATION ===
Rounds: XXX
Total samples: XXXX

Candidate: vol=5
  Brier:  0.XXXX
  Logloss: X.XXXX
  ECE:    0.XXXX
  
  Reliability (predicted → realized):
  [0.50, 0.60): 0.XXX → 0.XXX (n=XXX)
  [0.60, 0.70): 0.XXX → 0.XXX (n=XXX)
  [0.70, 0.80): 0.XXX → 0.XXX (n=XXX)
  [0.80, 0.90): 0.XXX → 0.XXX (n=XXX)
  [0.90, 1.00): 0.XXX → 0.XXX (n=XXX)

[... similar for other vol values ...]

RECOMMENDATION: vol=X (Brier minimum)
```

---

### COMMAND 4: Check Entry Diagnostics from Last Run

**Purpose**: See what prevented entries in actual backtest

```bash
# Extract entry diagnostics from last backtest report
echo "=== ENTRY DIAGNOSTICS (from last report) ==="
# [User should paste the ENTRY DIAGNOSTICS section from their grid output]
# We want to see breakdown of NoOp reasons
```

**Expected Info**:
- How many ticks had `time_left > t_entry`?
- How many had `abs_delta < threshold`?  ← KEY for TEMUAN 1
- How many had `net_edge < min_edge`?    ← KEY for TEMUAN 2

---

### COMMAND 5: Verify Fee Formula (SQL Check)

**Purpose**: Check if fee calculation looks reasonable in actual data

```bash
cat > check_fees.py << 'SCRIPT'
import sqlite3

conn = sqlite3.connect("analisis.db")

query = """
SELECT 
    ask_win,
    net_edge,
    p_win,
    (CAST(p_win AS REAL) - CAST(ask_win AS REAL) - CAST(net_edge AS REAL)) as implied_fee_plus_slip
FROM signals
WHERE net_edge IS NOT NULL
    AND net_edge != ''
    AND ask_win IS NOT NULL
    AND p_win IS NOT NULL
LIMIT 20
"""

print("=== Sample: p_win, ask_win, net_edge, implied_fee+slip ===")
print("p_win    ask_win  net_edge  fee+slip")
for row in conn.execute(query):
    ask, edge, p, implied = row
    print(f"{float(p):7.4f}  {float(ask):7.4f}  {float(edge):8.4f}  {implied:7.4f}")

print()
print("Note: fee+slip = p_win - ask_win - net_edge")
print("With rate=0.07, at ask=0.50: expected fee ≈ 0.035 (our formula) or 0.0175 (article formula)")

conn.close()
SCRIPT

python check_fees.py
```

---

## Test: Delta Threshold Sensitivity

**Purpose**: Prove different delta values produce different `entered` counts on synthetic data

**File**: `tests/backtest/test_delta_sensitivity.py`

```python
"""Test delta_threshold parameter sensitivity (TEMUAN 1 regression)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from btcbot.backtest.replay import ReplayConfig, ReplayEngine, ReplayTick
from btcbot.domain.models import BookLevel, OrderBook, Outcome, Round


def _make_round() -> Round:
    return Round(
        condition_id="test",
        round_no=1000,
        token_id_up="up",
        token_id_down="down",
        window_start=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 10, 5, tzinfo=UTC),
        start_price=Decimal("50000"),
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        status="active",
        resolved_outcome=Outcome.UP,
    )


def _book(price: str) -> OrderBook:
    return OrderBook(
        token_id="up",
        ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
        bids=[],
        asks=[BookLevel(price=Decimal(price), size=Decimal("100"))],
    )


class TestDeltaThresholdSensitivity:
    """Verify delta_threshold parameter affects entry decisions."""

    def test_small_delta_filtered_by_high_threshold(self) -> None:
        """Delta=0.03 should be filtered by threshold=0.05 but pass threshold=0.02."""
        rnd = _make_round()
        
        # Create ticks with SMALL delta (0.03)
        # start_price = 50000, price_now = 50001.5 → delta = 1.5
        ticks = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
                btc_price=Decimal("50001.5"),  # delta = 1.5 < threshold
                book_up=_book("0.55"),
                book_down=_book("0.48"),
            )
        ]
        
        # Config 1: delta_threshold = 0.02 (should PASS, delta=1.5 < 0.02 means FILTER... wait)
        # Actually: abs(delta) < threshold → NoOp
        # So delta=1.5, threshold=2.0 → abs(1.5) < 2.0 → FILTER
        # delta=1.5, threshold=1.0 → abs(1.5) < 1.0 → PASS
        
        # Let me recalculate: need delta that's BETWEEN thresholds
        # delta=15 (1.5 USD on 50k base = 0.003% move - very small)
        # Let's use delta=50 (0.1% move)
        
        # Recreate with clearer delta
        ticks_small = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
                btc_price=Decimal("50025"),  # delta = 25
                book_up=_book("0.55"),
                book_down=_book("0.48"),
            )
        ]
        
        ticks_large = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, 30, tzinfo=UTC),
                btc_price=Decimal("50100"),  # delta = 100
                book_up=_book("0.55"),
                book_down=_book("0.48"),
            )
        ]
        
        cfg_low = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=60,
            delta_threshold=Decimal("20"),  # Filters delta < 20
            min_edge=Decimal("-1"),  # Allow any edge
        )
        
        cfg_high = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=60,
            delta_threshold=Decimal("50"),  # Filters delta < 50
            min_edge=Decimal("-1"),
        )
        
        # Run with small delta (25)
        summary_low_small = ReplayEngine(cfg_low).run([(rnd, ticks_small)])
        summary_high_small = ReplayEngine(cfg_high).run([(rnd, ticks_small)])
        
        # delta=25: should pass threshold=20, fail threshold=50
        assert summary_low_small.rounds_entered == 1, "delta=25 should pass threshold=20"
        assert summary_high_small.rounds_entered == 0, "delta=25 should fail threshold=50"
        
        # Run with large delta (100)
        summary_low_large = ReplayEngine(cfg_low).run([(rnd, ticks_large)])
        summary_high_large = ReplayEngine(cfg_high).run([(rnd, ticks_large)])
        
        # delta=100: should pass both
        assert summary_low_large.rounds_entered == 1, "delta=100 should pass threshold=20"
        assert summary_high_large.rounds_entered == 1, "delta=100 should pass threshold=50"

    def test_grid_delta_affects_entered_count(self) -> None:
        """Grid with different delta values should produce different entered counts."""
        rnd = _make_round()
        
        # Mix of small and large deltas
        ticks = [
            ReplayTick(
                ts=datetime(2026, 1, 1, 10, 4, i, tzinfo=UTC),
                btc_price=Decimal("50000") + Decimal(str(30 * i)),  # delta: 0, 30, 60, 90
                book_up=_book("0.55"),
                book_down=_book("0.48"),
            )
            for i in range(4)
        ]
        
        cfg_delta_20 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("20"),
            min_edge=Decimal("-1"),
        )
        
        cfg_delta_50 = ReplayConfig.from_settings_with_overrides(
            t_entry_sec=120,
            delta_threshold=Decimal("50"),
            min_edge=Decimal("-1"),
        )
        
        summary_20 = ReplayEngine(cfg_delta_20).run([(rnd, ticks)])
        summary_50 = ReplayEngine(cfg_delta_50).run([(rnd, ticks)])
        
        # delta=20 should allow more entries than delta=50
        assert summary_20.rounds_entered >= summary_50.rounds_entered
        assert summary_20.rounds_entered != summary_50.rounds_entered, \
            "Different delta thresholds should produce different entry counts"
```

**Command to Run**:
```bash
python -m pytest tests/backtest/test_delta_sensitivity.py -v
```

**Expected Output**:
```
tests/backtest/test_delta_sensitivity.py::TestDeltaThresholdSensitivity::test_small_delta_filtered_by_high_threshold PASSED
tests/backtest/test_delta_sensitivity.py::TestDeltaThresholdSensitivity::test_grid_delta_affects_entered_count PASSED
```

---

## Summary: What to Expect

### TEMUAN 1 - Delta Threshold
**If wiring is correct (it is)**, then identical results across delta grid means:
- **Hypothesis A**: All |Δ| in dataset > 0.10 (data issue, not code bug)
  - COMMAND 2 will show: `|Δ| < 0.10: 0 (0.0%)`
  - **Action**: Use finer delta grid (0.10, 0.20, 0.40) OR accept that delta filter doesn't matter for this dataset
  
- **Hypothesis B**: Data has small |Δ| but grid results still identical (wiring bug we missed)
  - COMMAND 2 will show: `|Δ| < 0.02: XXX (XX%)`
  - **Action**: Debug further, check entry diagnostics

### TEMUAN 2 - Vol Calibration
**If vol=5 is being used and calibration shows good ECE**:
- **Hypothesis A**: Calibration population ≠ backtest entry population
  - COMMAND 3 reliability should show bins with n=XX samples
  - Check if backtest entries are mostly in overconfident bin [0.90, 1.00)
  - **Action**: Filter calibration to only entry-eligible ticks

- **Hypothesis B**: Vol not applied correctly (unlikely, code shows it's wired)
  - COMMAND 1 will show if .env has different value
  - **Action**: Set correct vol in .env, rerun backtest

### Fee Formula
- Current code may be overcharging by ~2x at p=0.5
- **Action**: Need API verification before changing (out of scope for now, FLAG only)

---

## Next Steps for User

1. **Run all 5 commands** above in order
2. **Paste FULL output** of each command
3. Based on output, we'll determine:
   - Is delta threshold working correctly? (check COMMAND 2)
   - Is vol calibrated correctly? (check COMMAND 1 & 3)
   - What's causing overconfident p_win? (check COMMAND 3 reliability bins)
4. If needed, create follow-up fix with exact code changes

**DO NOT** conclude anything until actual output is reviewed!
