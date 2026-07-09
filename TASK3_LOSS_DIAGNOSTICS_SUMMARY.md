# TASK 3: Loss Diagnostics CLI Tool — DELIVERY SUMMARY

## Status: ✅ CODE READY (Awaiting VPS Verification)

---

## What Was Delivered

### 1. Main Implementation
**File**: `src/btcbot/backtest/loss_diagnostics.py` (459 lines)

**Features**:
- ✅ Read-only diagnostic tool (no changes to strategy/signal/sizing/fee)
- ✅ Collects ALL filled entry details from replay
- ✅ Buckets performance by 5 dimensions:
  - Side (UP/DOWN)
  - Entry Price: `<=0.95`, `(0.95,0.97]`, `(0.97,0.99]`, `>0.99`
  - Abs Delta: `[0,50)`, `[50,60)`, `[60,75)`, `[75,100)`, `[100+)`
  - Time Left: `[0,15)`, `[15,30)`, `[30,45)`, `[45,60]`, `(60+)`
  - P_Win: `[0.50,0.80)`, `[0.80,0.90)`, `[0.90,0.95)`, `[0.95,0.98)`, `[0.98,1.00]`
- ✅ Formatted text report to console
- ✅ CSV export with all entry details (23 columns)
- ✅ CLI with full argument parsing

**Data Collected Per Entry**:
- Round info: round_no, window_start, window_end, start_price, resolved_outcome
- Entry info: entry_ts, time_left_sec, side_taken, leader, entry_price, size
- Signal at entry: price_now, delta, abs_delta, p_win, ask_win, net_edge
- Config: max_price_config
- Result: result (WIN/LOSS), pnl
- Book info placeholders: best_bid_leader, best_ask_leader, depth_available (currently None, can be enhanced)

### 2. Unit Tests
**File**: `tests/backtest/test_loss_diagnostics.py` (15 tests)

**Coverage**:
- ✅ All 4 bucket functions tested with edge cases
- ✅ BucketStats aggregation tested (empty, single, mixed)
- ✅ LossDiagnostics collector tested (add, summary, all 5 bucket methods)
- ✅ **All 15 tests PASS** (verified locally)

### 3. User Documentation
**File**: `LOSS_DIAGNOSTICS_GUIDE.md`

**Contents**:
- Purpose and installation status
- Step-by-step VPS commands (copy-paste ready)
- Expected output description
- Troubleshooting guide for common issues
- Next steps after running

---

## Git Status

✅ **Committed**: `a238cb2` (then rebased to `ada9326`)  
✅ **Pushed** to GitHub: `origin/main`

**Commit Message**:
```
feat: Add loss diagnostics CLI tool for G1 REVISI analysis

- Create src/btcbot/backtest/loss_diagnostics.py: Read-only diagnostic tool
  to analyze filled entries and identify loss patterns
- Collect entry details: round info, signal data, result, PnL
- Bucket performance by: side, entry_price, abs_delta, time_left, p_win
- Export to CSV for detailed analysis
- Add tests/backtest/test_loss_diagnostics.py: 15 unit tests (all pass)
- Add LOSS_DIAGNOSTICS_GUIDE.md: Complete user guide with VPS commands
```

---

## VPS Commands for User (READY TO RUN)

### Step 1: Pull Latest Code
```bash
cd ~/cepiokta
git pull origin main
```

**Expected**: Should pull commit `ada9326` with 3 new files.

### Step 2: Activate Environment
```bash
source venv/bin/activate
```

### Step 3: Create Backup Database
```bash
cd ~/cepiokta
sqlite3 btcbot.db "VACUUM INTO 'analisis4.db';"
sqlite3 analisis4.db "PRAGMA integrity_check;"
```

**Expected output**: `ok`

### Step 4: Run Loss Diagnostics
```bash
python -m btcbot.backtest.loss_diagnostics \
    --db "sqlite+aiosqlite:///./analisis4.db" \
    --since "2026-07-06T01:20:00+00:00" \
    --until "2026-07-09T08:25:00+00:00" \
    --t-entry 60 \
    --delta-threshold 50 \
    --max-price 0.99 \
    --max-rounds 1300 \
    --starting-balance 500 \
    --csv loss_diagnostics_late_t60_d50_p99.csv
```

**Expected Behavior**:
- Process ~949 rounds (LATE split)
- Produce 85 filled entries (matching current best: +$2.89, +0.58% ROI, 94.1% win)
- Output formatted report to console
- Write CSV to `loss_diagnostics_late_t60_d50_p99.csv`

### Step 5: Review CSV Header
```bash
head -n 5 loss_diagnostics_late_t60_d50_p99.csv
```

---

## Expected Output Structure

### Console Report Sections
1. **Overall Summary**: Total entries, wins, losses, win rate, net PnL
2. **Performance by Side**: UP vs DOWN comparison
3. **Performance by Entry Price**: 4 buckets
4. **Performance by Abs Delta**: 5 buckets
5. **Performance by Time Left**: 5 buckets
6. **Performance by P_Win**: 5 buckets

Each bucket shows: entries, wins, losses, win%, net_pnl, avg_pnl

### CSV Columns (23 total)
round_no, window_start, window_end, entry_ts, time_left_sec, side_taken, leader, resolved_outcome, result, start_price, price_now, delta, abs_delta, p_win, ask_win, entry_price, max_price_config, size, net_edge, pnl, best_bid_leader, best_ask_leader, depth_available

---

## Implementation Notes

### Design Decisions

1. **Signal Lookup Strategy**:
   - Uses FIRST signal where `time_left_sec <= t_entry`
   - This approximates the entry decision tick
   - Assumption: Entry happens at first opportunity when gate opens

2. **Book Info Placeholder**:
   - `best_bid_leader`, `best_ask_leader`, `depth_available` set to None
   - Can be enhanced later if needed (requires extracting from book_snapshots)
   - Not critical for initial loss pattern analysis

3. **Read-Only Guarantee**:
   - Uses existing `ReplayEngine.observe()` method
   - No modifications to strategy/signal/sizing/fee logic
   - Deterministic: Same parameters → same entries
   - No side effects on PnL calculations

4. **Bucket Boundaries**:
   - Entry price: Focuses on high-price region (>0.95) where most entries happen
   - Abs delta: Starts at 50 (threshold) with meaningful intervals
   - Time left: 15-second intervals within t_entry window
   - P_win: Finer granularity at high confidence (>0.90) where overconfidence is suspected

### Code Quality

- ✅ Type hints on all functions
- ✅ Docstrings for all public functions
- ✅ Dataclasses for structured data
- ✅ Frozen/slots for performance
- ✅ Decimal for financial precision
- ✅ Async-ready (uses asyncio)
- ✅ No dependencies on external libraries (uses stdlib + existing btcbot modules)

### Testing Coverage

- ✅ Bucket functions: All edge cases and boundaries
- ✅ BucketStats: Empty, single, multiple entries
- ✅ LossDiagnostics: All aggregation methods
- ✅ 100% pass rate (15/15 tests)

---

## What Was NOT Done (Intentionally)

1. **No enhancement to book info**: Left as None for now, can add later if loss patterns require it
2. **No visualization**: Text report only, no plots (can add matplotlib later if needed)
3. **No integration test with real DB**: Unit tests use synthetic data, VPS run will be first integration test
4. **No modification to replay behavior**: Strictly observational, zero changes to PnL logic

---

## Potential Issues & Mitigation

### Issue 1: Entry Count Mismatch
**Risk**: Tool shows ≠85 entries  
**Cause**: Signal lookup heuristic (first signal with time_left <= t_entry)  
**Mitigation**: User will paste output, we'll diagnose if mismatch occurs  
**Confidence**: Medium (heuristic should work but not verified on real data)

### Issue 2: Module Import Error
**Risk**: `python -m btcbot.backtest.loss_diagnostics` fails  
**Cause**: Module has `if __name__ == "__main__"` but no `__main__.py`  
**Mitigation**: Should work without `__main__.py`, but can add if needed  
**Confidence**: High (standard Python pattern)

### Issue 3: Performance on Large Dataset
**Risk**: Slow on 949 rounds  
**Cause**: Loading all signals upfront  
**Mitigation**: Acceptable for diagnostic tool (run once, analyze carefully)  
**Confidence**: High (replay itself is already slow, diagnostic adds minimal overhead)

---

## Next Steps (After User Runs)

### User Actions Required
1. Run Step 1-5 above on VPS
2. Paste back:
   - Full console output (or at least summary sections)
   - First 10 lines of CSV: `head -n 10 loss_diagnostics_late_t60_d50_p99.csv`
   - Any error messages

### Agent Actions After Output
1. **Verify entry count** matches expected 85
2. **Verify summary** matches backtest: ~$2.89, ~94.1% win
3. **Analyze bucket patterns**:
   - Which side loses more?
   - Which entry price bucket has losses?
   - Is there a "bad" delta range?
   - Are early/late entries worse?
   - Is high p_win still losing (overconfidence)?
4. **Propose filters** based on patterns (must be theory-grounded, not overfit)
5. **Design next experiment** to validate filters

---

## Success Criteria

✅ **Code Quality**: Clean, typed, tested (ACHIEVED)  
✅ **Git Integration**: Committed and pushed (ACHIEVED)  
✅ **Documentation**: Complete user guide (ACHIEVED)  
⏳ **VPS Verification**: Awaiting user output (PENDING)  
⏳ **Loss Patterns Identified**: Awaiting analysis of output (PENDING)  
⏳ **G1 Decision**: Awaiting filter design and next backtest (PENDING)

---

## Files Changed

### New Files
- `src/btcbot/backtest/loss_diagnostics.py` (+459 lines)
- `tests/backtest/test_loss_diagnostics.py` (+368 lines)
- `LOSS_DIAGNOSTICS_GUIDE.md` (+231 lines)
- `TASK3_LOSS_DIAGNOSTICS_SUMMARY.md` (this file)

### Modified Files
- None (pure addition, zero changes to existing code)

---

## Technical Debt / Future Enhancements

1. **Book info extraction**: Enhance `EntryDetail` with actual best_bid/ask/depth from book_snapshots
2. **Correlation analysis**: Add feature to compute correlation between delta/p_win/price
3. **Visual plots**: Add matplotlib charts for bucket distributions
4. **Statistical tests**: Add chi-square test for pattern significance
5. **Streaming mode**: Process rounds incrementally instead of loading all signals upfront
6. **Multi-parameter sweep**: Run diagnostics across grid of t_entry/delta/max_price

---

## DELIVERABLE CHECKLIST

- [x] Implementation complete and tested locally
- [x] Unit tests written and passing (15/15)
- [x] User documentation created
- [x] Code committed to git
- [x] Code pushed to GitHub
- [x] VPS commands prepared (copy-paste ready)
- [x] Expected output documented
- [x] Troubleshooting guide provided
- [ ] **USER MUST**: Run on VPS and paste output
- [ ] **AGENT MUST**: Analyze output and identify loss patterns
- [ ] **NEXT**: Design filters and run next backtest

---

**STATUS**: ✅ Ready for VPS Testing  
**BALL IN**: 👤 User's court (must run Step 1-5 and paste output)  
**COMMIT**: `ada9326`  
**FILES**: 3 new, 0 modified  
**TESTS**: 15/15 pass  
**SAFETY**: Read-only, zero changes to existing logic  
**DATE**: 2026-07-09
