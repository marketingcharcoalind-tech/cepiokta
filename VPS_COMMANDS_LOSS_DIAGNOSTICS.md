# VPS Commands: Loss Diagnostics Tool

## Quick Start (Copy-Paste Ready)

### 1. Pull Latest Code
```bash
cd ~/cepiokta
git pull origin main
```

Expected: Should show 3 new files pulled.

---

### 2. Activate Environment
```bash
source venv/bin/activate
```

---

### 3. Create Fresh Backup
```bash
sqlite3 btcbot.db "VACUUM INTO 'analisis4.db';"
sqlite3 analisis4.db "PRAGMA integrity_check;"
```

Expected output: `ok`

---

### 4. Run Loss Diagnostics (Current Best Parameters)
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

**Expected**:
- Processing ~949 rounds
- 85 filled entries
- Net PnL ~$2.89
- Win rate ~94.1%
- Console report + CSV file created

---

### 5. Check CSV Output
```bash
head -n 10 loss_diagnostics_late_t60_d50_p99.csv
```

---

## What to Paste Back to Kiro

Copy-paste ALL of the following:

1. **Console output from Step 4** (entire loss diagnostics report)
2. **CSV header from Step 5** (first 10 lines)
3. **Any error messages** if command fails

---

## Troubleshooting

### Error: Module Not Found
```bash
# Verify file exists
ls -la src/btcbot/backtest/loss_diagnostics.py
```

If missing → git pull didn't work, try again.

### Error: Database Locked
Use backup database (Step 3), not live `btcbot.db`.

### Output Shows 0 Entries
Check parameters match your successful backtest. Verify:
```bash
sqlite3 analisis4.db "SELECT COUNT(*) FROM rounds WHERE resolved_outcome IS NOT NULL AND window_end >= '2026-07-06 01:20:00' AND window_end <= '2026-07-09 08:25:00';"
```

Expected: ~949 rows

---

## Expected Output Preview

```
================================================================================
LOSS DIAGNOSTICS REPORT
================================================================================

=== OVERALL SUMMARY ===
Total Entries:  85
Wins:           80
Losses:         5
Win Rate:       94.1%
Net PnL:        $2.89

=== PERFORMANCE BY SIDE ===
Side       Entries   Wins Losses   Win%   Net PnL   Avg PnL
--------------------------------------------------------------------------------
UP              42     40      2   95.2%      1.50      0.04
DOWN            43     40      3   93.0%      1.39      0.03

[... more buckets ...]
```

CSV format:
```
round_no,window_start,window_end,entry_ts,time_left_sec,side_taken,leader,resolved_outcome,result,start_price,price_now,delta,abs_delta,p_win,ask_win,entry_price,max_price_config,size,net_edge,pnl,best_bid_leader,best_ask_leader,depth_available
1234,2026-07-06T01:20:00+00:00,2026-07-06T01:25:00+00:00,2026-07-06T01:24:00+00:00,45.0,UP,UP,UP,WIN,104500,104560,60,60,0.96,0.98,0.98,0.99,10,0.01,1.50,,,
[... more rows ...]
```

---

**DONE**: Paste output back to Kiro for analysis.
