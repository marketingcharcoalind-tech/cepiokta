# Pre-G1 Blocker Fixes — Summary Report

**Date**: 2026-07-02  
**Branch**: `fix/pre-g1-blockers` → merged to `main`  
**Status**: ✅ COMPLETE — All critical blockers resolved

---

## Overview

Fixed 3 critical bugs blocking Gate G1 (backtest vonis). These bugs would have:
1. Caused loss of ground truth labels (resolution data)
2. Created inconsistent round_no keys across modules
3. Silently disabled delta threshold filtering

**Note**: Task description mentioned FIX 1a (SIZING_SUCCESS) and FIX 1b (_OVERCONFIDENT_ECE) as NameErrors, but these constants were already defined in previous commits (Task G4). No action needed.

---

## Fixes Implemented

### ✅ FIX 2a — Store.upsert_round() preserves resolution (HIGH priority)

**Problem**:  
`INSERT OR REPLACE` deletes and recreates entire row, wiping out `settlement_price`, `resolution_source`, and reverting `status` from `resolved` back to `active`. Ground truth labels lost when recorder restarts.

**Solution**:  
Changed to `INSERT ... ON CONFLICT(round_no) DO UPDATE SET` with:
- `status = CASE WHEN rounds.status = 'resolved' THEN rounds.status ELSE excluded.status END`
- `resolved_outcome = COALESCE(rounds.resolved_outcome, excluded.resolved_outcome)`
- `settlement_price` and `resolution_source` columns NOT mentioned in UPDATE (only set via `set_resolution`)

**Files Changed**:
- `src/btcbot/data/store.py`: `upsert_round()` method (23 lines changed)
- `tests/data/test_store.py`: Added `test_upsert_preserves_resolution_on_rerecord()` (32 lines)

**Verification**:
```python
# Test sequence:
# 1. Record round
# 2. Set resolution (outcome=UP, settlement_price=64252.00, source=gamma)
# 3. Update status to RESOLVED
# 4. Re-record SAME round (simulating restart)
# 5. Verify resolution data STILL exists (not wiped)
assert resolution.outcome is Outcome.UP
assert resolution.settlement_price == Decimal("64252.00")
assert round.status is RoundStatus.RESOLVED
```

**Impact**: Critical for calibration and reliability curve (requires resolved_outcome labels).

---

### ⚠️ FIX 2b — round_no from window_end epoch (HIGH priority, BREAKING)

**Problem**:  
`run_readonly()` calculated `round_no = int(meta.start_time.timestamp())`, but:
- Verified convention: epoch = window_END (from slug `asset-updown-tf-epoch`)
- `domain/market.round_no_for()` uses window_END
- Slug discovery uses window_END

Result: Cross-references between modules fail (different keys for same round).

**Solution**:  
```python
# OLD
round_no = int(meta.start_time.timestamp())

# NEW
round_no = int(meta.end_time.timestamp())  # window_end epoch (selaras market.round_no_for & slug)
```

**Files Changed**:
- `src/btcbot/app/cli.py`: Line 120 (1 line changed)

**BREAKING CHANGE**:  
Round key changes by +300s (5m) or +900s (15m). Old data incompatible.

**Migration Options**:
1. **Start fresh DB** (recommended for pre-production)
2. **Migrate existing data**:
   ```sql
   UPDATE rounds SET round_no = round_no + 300 WHERE ...;  -- for 5m markets
   UPDATE book_snapshots SET round_no = round_no + 300 WHERE ...;
   UPDATE signals SET round_no = round_no + 300 WHERE ...;
   UPDATE round_results SET round_no = round_no + 300 WHERE ...;
   UPDATE equity_curve SET round_no = round_no + 300 WHERE ...;
   ```

**Impact**: Critical for Phase 2+ when multiple modules reference rounds by `round_no`.

---

### ✅ FIX 3 — delta_threshold='auto' implements vol-scaling (MED priority)

**Problem**:  
`_resolve_delta()` with `'auto'` fell through to `except` clause → returned `_ZERO`, completely disabling delta threshold filter. Strategy would enter on ANY price movement, including noise.

**Solution**:  
Implemented proper vol-scaling:
```python
if isinstance(raw, str) and raw.strip().lower() == "auto":
    return settings.backtest_vol_per_sqrt_sec * Decimal(
        str(math.sqrt(float(settings.t_entry_sec)))
    )
```

This gives `threshold = vol * sqrt(T_ENTRY_SEC)` ≈ 1σ price movement over entry window (consistent with `sigma_left` in SignalEngine).

**Files Changed**:
- `src/btcbot/backtest/report.py`: `_resolve_delta()` function (14 lines changed)
- `tests/backtest/test_report.py`: Added `TestResolveDelta` class (36 lines)

**Verification**:
```python
# Test cases:
assert _resolve_delta(settings, "auto") > Decimal("0")  # NOT zero!
assert _resolve_delta(settings, "0.05") == Decimal("0.05")  # explicit preserved
assert _resolve_delta(settings, "invalid") > Decimal("0")  # fallback to auto
```

**Impact**: Critical for accurate entry filtering in backtest. Zero threshold = noise trading.

---

## Commits

| Hash | Message |
|------|---------|
| `ddd4c54` | `fix(store): preserve resolution on upsert_round (ON CONFLICT, not INSERT OR REPLACE)` |
| `9e4f47e` | `fix(cli): round_no from window_end epoch (align market.round_no_for & slug)` |
| `7f691af` | `fix(report): implement delta_threshold='auto' vol-scaling (was silently 0)` |
| `09e7a48` | `docs(progress): record Pre-G1 blocker fixes in tracker` |
| `cf2690c` | `Merge branch 'fix/pre-g1-blockers' - Critical Pre-G1 fixes` |

---

## Verification Results

### ✅ Import Test
```bash
python -c "import btcbot.exec.sizing, btcbot.backtest.replay, btcbot.backtest.report, btcbot.backtest.calibrate"
# ✓ All critical imports successful - no NameError
```

### ✅ Syntax Check
```bash
python -m py_compile src/btcbot/data/store.py src/btcbot/app/cli.py src/btcbot/backtest/report.py
# Exit Code: 0
```

### ✅ Test Coverage
- **FIX 2a**: `test_upsert_preserves_resolution_on_rerecord` — PASS
- **FIX 3**: `TestResolveDelta` (4 test methods) — PASS

---

## Files Modified Summary

| File | Changes | Type |
|------|---------|------|
| `src/btcbot/data/store.py` | 23 insertions, 2 deletions | Production |
| `src/btcbot/app/cli.py` | 1 insertion, 1 deletion | Production |
| `src/btcbot/backtest/report.py` | 16 insertions, 2 deletions | Production |
| `tests/data/test_store.py` | 32 insertions | Test |
| `tests/backtest/test_report.py` | 44 insertions | Test |
| `PROGRESS_TRACKER.md` | 2 insertions | Documentation |

**Total**: 6 files, 118 insertions, 7 deletions

---

## Impact Assessment

### Before Fixes
- ❌ Ground truth labels lost on recorder restart → calibration impossible
- ❌ round_no mismatch → cross-module references fail
- ❌ Delta filter disabled → noise trading in backtest
- ❌ G1 vonis BLOCKED

### After Fixes
- ✅ Resolution data preserved across restarts
- ✅ round_no consistent across modules
- ✅ Delta filter active with proper vol-scaling
- ✅ **G1 vonis READY TO RUN**

---

## BREAKING CHANGE Warning

**FIX 2b changes round_no keys**:
- 5-minute markets: `round_no` increases by **+300 seconds**
- 15-minute markets: `round_no` increases by **+900 seconds**

**Action Required**:
- Production/staging: **Start with fresh database**
- Development: Migrate existing data using SQL UPDATE (see FIX 2b section above)

**Example**:
```
OLD: round_no = 1782479700 (13:15:00 UTC = window_start)
NEW: round_no = 1782480000 (13:20:00 UTC = window_end)
Difference: +300 seconds
```

---

## Next Steps

1. ✅ All fixes merged to `main`
2. ✅ Branch `fix/pre-g1-blockers` can be deleted (already merged)
3. ⏭️ **Ready for G1 vonis**: Run backtest with real data
4. ⏭️ Decide: LANJUT (edge > 0) vs REVISI vs STOP (edge ≤ 0)

---

## Decision Log Entry (Added to PROGRESS_TRACKER.md)

```
2026-07-02 | Pre-G1 fixes: (1) upsert_round preserve resolution via ON CONFLICT 
           | (2) round_no dari window_END epoch (3) delta_threshold='auto' vol-scaling
           | (1) INSERT OR REPLACE wipes ground truth; (2) round_no START misaligned; 
           | (3) 'auto'=0 disables filter. Fix 2 = BREAKING DATA KEY.
```

---

## Status

🎯 **ALL PRE-G1 BLOCKERS RESOLVED**

Backtest subsystem is now functional and ready for Gate G1 decision.
