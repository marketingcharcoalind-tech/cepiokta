# Loss Diagnostics Tool — User Guide

## Purpose

Tool untuk menganalisis **SEMUA filled entries** dan mengidentifikasi **pola loss** pada parameter backtest tertentu. Ini adalah bagian dari Fase Pre-G1 REVISI untuk mencari cara memfilter loss tanpa overfitting.

## Installation Status

✅ File created: `src/btcbot/backtest/loss_diagnostics.py`  
⚠️ **BELUM DITEST di VPS** — User harus menjalankan command di bawah untuk verifikasi

## VPS Commands (Step-by-step)

### Step 1: Git Pull (Get Latest Code)

```bash
cd ~/cepiokta
git pull origin main
```

### Step 2: Activate Environment

```bash
source venv/bin/activate
```

### Step 3: Create Backup Database

**PENTING**: Analisis harus di backup, bukan DB live yang sedang ditulis bot.

```bash
cd ~/cepiokta
sqlite3 btcbot.db "VACUUM INTO 'analisis4.db';"
sqlite3 analisis4.db "PRAGMA integrity_check;"
```

Expected output: `ok`

### Step 4: Run Loss Diagnostics

**Parameter yang sama dengan current best** (t_entry=60, delta=50, max_price=0.99):

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
- Should process ~949 rounds (LATE split)
- Should produce 85 filled entries (matching current best backtest result)
- Output to console: formatted text report
- Output to file: `loss_diagnostics_late_t60_d50_p99.csv`

### Step 5: Review Output

Output harus mencakup:

#### A. Overall Summary
- Total Entries: 85
- Wins: ~80 (94.1% win rate)
- Losses: ~5
- Net PnL: ~$2.89

#### B. Performance by Side
- UP vs DOWN comparison
- Apakah salah satu side lebih sering loss?

#### C. Performance by Entry Price
Buckets: `<=0.95`, `(0.95,0.97]`, `(0.97,0.99]`, `>0.99`
- Apakah loss concentrated di high price?

#### D. Performance by Abs Delta
Buckets: `[0,50)`, `[50,60)`, `[60,75)`, `[75,100)`, `[100+)`
- Apakah ada "sweet spot" delta yang lebih reliable?

#### E. Performance by Time Left
Buckets: `[0,15)`, `[15,30)`, `[30,45)`, `[45,60]`, `(60+)`
- Apakah entry terlalu early/late lebih sering loss?

#### F. Performance by P_Win
Buckets: `[0.50,0.80)`, `[0.80,0.90)`, `[0.90,0.95)`, `[0.95,0.98)`, `[0.98,1.00]`
- Apakah high p_win entries masih bisa loss (overconfidence issue)?

## CSV Output

File CSV berisi **detail setiap entry** dengan kolom:

- `round_no`, `window_start`, `window_end`
- `entry_ts`, `time_left_sec`
- `side_taken`, `leader`, `resolved_outcome`, `result`
- `start_price`, `price_now`, `delta`, `abs_delta`
- `p_win`, `ask_win`, `entry_price`, `max_price_config`
- `size`, `net_edge`, `pnl`
- Book info (currently empty, can be enhanced later)

User bisa import CSV ke spreadsheet untuk analisis lebih detail.

## Expected Issues & Solutions

### Issue 1: Module Not Found

**Error**: `ModuleNotFoundError: No module named 'btcbot.backtest.loss_diagnostics'`

**Solution**: 
```bash
# Ensure you did git pull
git pull origin main
# Verify file exists
ls -la src/btcbot/backtest/loss_diagnostics.py
```

### Issue 2: Database Locked

**Error**: `database is locked`

**Solution**: Bot sedang nulis. Pakai backup database (Step 3).

### Issue 3: Zero Entries

**Output**: `Total Entries: 0`

**Possible Causes**:
- Date range salah (check `--since` / `--until`)
- Delta threshold terlalu tinggi
- Max price terlalu rendah
- Database tidak ada signals (run `sqlite3 analisis4.db "SELECT COUNT(*) FROM signals;"`)

**Solution**: Verify parameters match backtest that produced 85 entries.

### Issue 4: Entry Count Mismatch

**Expected**: 85 entries  
**Got**: Different number

**Possible Causes**:
- Signal lookup logic issue (code uses first signal with `time_left <= t_entry`)
- Database content changed

**Solution**: Paste full output to Kiro for diagnosis.

## Troubleshooting: Verify Backtest Parameters

To confirm the tool is using the same parameters as your successful backtest:

```bash
# Check what parameters you used in previous backtest
python -m btcbot.backtest.report \
    --db "sqlite+aiosqlite:///./analisis4.db" \
    --since "2026-07-06T01:20:00+00:00" \
    --until "2026-07-09T08:25:00+00:00" \
    --grid t-entry=60 delta=50 max-price=0.99 \
    --starting-balance 500 \
    --max-rounds 1300
```

The output should show `entered=85` for the t_entry=60/delta=50/max_price=0.99 combination.

## Next Steps After Running

1. **Paste full output** back to Kiro (both console and `head loss_diagnostics_late_t60_d50_p99.csv`)
2. **Identify loss patterns**: Which buckets show concentrated losses?
3. **Propose filters**: Based on patterns, suggest additional filters to exclude loss cases
4. **Verify no overfitting**: Filters must be justified by theory, not just curve-fitting

## Implementation Notes (for Kiro)

### What Works
- ✅ Module structure with dataclasses and bucket functions
- ✅ CSV export capability
- ✅ Text report formatter
- ✅ CLI argument parser
- ✅ Integration with existing ReplayEngine

### Potential Enhancements (if needed)
- [ ] Add book depth info (currently None)
- [ ] Add more granular time buckets
- [ ] Add correlation analysis between features
- [ ] Add visual plots (requires matplotlib)

### Code Integrity
- Read-only operation: NO changes to strategy/signal/sizing/fee logic
- Uses existing replay infrastructure: `ReplayEngine.observe()`
- Deterministic: Same parameters → same entries
- No side effects: Diagnostics collection doesn't change PnL

## Status

**Created**: 2026-07-09  
**Tested on VPS**: ❌ PENDING USER  
**Integrated**: ✅ Code committed to repo  
**Documented**: ✅ This guide

---

**USER TODO**: Run Step 1-5 above and paste output back to Kiro for verification.
