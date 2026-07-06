# VPS Commands - Backtest Grid Investigation

**PENTING**: Semua command di sini untuk ANDA jalankan di VPS. Saya (Kiro) TIDAK punya akses VPS dan TIDAK bisa menjalankan apa pun. Saya hanya menyiapkan command dan analisis - verifikasi terjadi setelah ANDA paste output.

---

## Prerequisites: Create Backup Database

Bot masih jalan, jadi kita analisis di backup untuk hindari "database is locked".

```bash
cd ~/cepiokta

# 1. Create consistent backup (VACUUM INTO while bot runs)
sqlite3 btcbot.db "VACUUM INTO 'analisis.db';"

# 2. Verify integrity
sqlite3 analisis.db "PRAGMA integrity_check;"
# Expected: ok

# 3. Check size
ls -lh analisis.db btcbot.db
```

**Paste output:**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 1: Check Current Vol Setting

**Purpose**: Verify what `backtest_vol_per_sqrt_sec` value is actually used

```bash
cd ~/cepiokta

echo "=== Check .env for BACKTEST_VOL_PER_SQRT_SEC ==="
grep -i "BACKTEST_VOL" .env || echo "(NOT SET - using default)"

echo ""
echo "=== Code default (settings.py line 119) ==="
grep -B 1 "backtest_vol_per_sqrt_sec.*Decimal" src/btcbot/config/settings.py | head -2
```

**Expected Output**:
```
=== Check .env for BACKTEST_VOL_PER_SQRT_SEC ===
(NOT SET - using default)

=== Code default (settings.py line 119) ===
    # TODO: calibrate from realized vol
    backtest_vol_per_sqrt_sec: Decimal = Decimal("5")
```

**Paste ACTUAL output:**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 2: Analyze |Δ| Distribution

**Purpose**: Check if ALL |delta| > 0.10 (explains identical grid results)

```bash
cd ~/cepiokta

python scripts/analyze_delta_distribution.py analisis.db
```

**Expected Output** (if hypothesis correct):
```
=== |Δ| Distribution at time_left <= 60 ===
Total ticks: XXXX
Min:     X.XXXXXX
P25:     X.XXXXXX
Median:  X.XXXXXX
P75:     X.XXXXXX
Max:     XX.XXXXXX

|Δ| < 0.02:     0 (  0.0%)
|Δ| < 0.05:     X (  X.X%)
|Δ| < 0.10:    XX ( XX.X%)
|Δ| < 0.20:   XXX ( XX.X%)

[Interpretation with colored emoji: 🔴🟡✅]
```

**Paste FULL output:**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 3: Run Volatility Calibration

**Purpose**: Get calibrated vol + reliability curve

```bash
cd ~/cepiokta

python -m btcbot.backtest.calibrate \
    --db "sqlite+aiosqlite:///./analisis.db" \
    --vols 5,10,20,40,80 \
    --min-samples 20
```

**Expected Output**:
```
=== VOLATILITY CALIBRATION ===
Rounds: XXX (XXX samples after filtering stubs)

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

[... similar for vol=10,20,40,80 ...]

✅ RECOMMENDATION: vol=XX (Brier minimum: 0.XXXX)
[or warning if ECE high]
```

**Paste FULL output (all vol candidates):**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 4: Check Fee Calculation

**Purpose**: Verify fee formula against actual data

```bash
cd ~/cepiokta

python scripts/check_fee_calculation.py analisis.db
```

**Expected Output**:
```
=== Fee Calculation Check ===

Formula: net_edge = p_win - ask_win - fee_per_share - expected_slippage
...

  p_win ask_win net_edge fee+slip expected_fee     diff
----------------------------------------------------------------------
 0.XXXX  0.XXXX   -0.XXXX   0.XXXX       0.XXXX   0.XXXX
[... 30 rows ...]

NOTE:
- If diff is consistently ~0, our formula is correct.
- If diff is consistently ~-expected_fee/2, article formula may be correct.
...
```

**Paste FULL output:**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 5: Run Delta Sensitivity Test

**Purpose**: Prove delta_threshold wiring is correct with synthetic data

```bash
cd ~/cepiokta

python -m pytest tests/backtest/test_delta_sensitivity.py -v
```

**Expected Output**:
```
================================ test session starts =================================
tests/backtest/test_delta_sensitivity.py::TestDeltaThresholdSensitivity::test_small_delta_filtered_by_high_threshold PASSED [ 33%]
tests/backtest/test_delta_sensitivity.py::TestDeltaThresholdSensitivity::test_grid_delta_affects_entered_count PASSED [ 66%]
tests/backtest/test_delta_sensitivity.py::TestDeltaThresholdSensitivity::test_all_deltas_above_threshold_gives_same_result PASSED [100%]

================================= 3 passed in 0.XX ===================================
```

**Paste ACTUAL output:**
```
[PASTE OUTPUT HERE]
```

---

## COMMAND 6 (Optional): Re-run Backtest with Calibrated Vol

**IF** calibration recommends vol ≠ 5, set it and re-run backtest to see if reliability improves.

```bash
cd ~/cepiokta

# 1. Set calibrated vol in .env (example: vol=10)
echo "BACKTEST_VOL_PER_SQRT_SEC=10" >> .env

# 2. Verify
grep BACKTEST_VOL .env

# 3. Re-run backtest report (ONLY if you want to verify impact)
python -m btcbot.backtest.report \
    --db "sqlite+aiosqlite:///./analisis.db" \
    --max-rounds 400 \
    --starting-balance 500 \
    --grid \
    --t-entry 30,45,60 \
    --delta-grid 0.10,0.20,0.40 \
    --max-price 0.90,0.95,0.99 \
    > backtest_report_vol10.txt

# 4. Check reliability section
grep -A 20 "=== RELIABILITY" backtest_report_vol10.txt
```

**Paste reliability section:**
```
[PASTE OUTPUT HERE]
```

---

## Summary Checklist

After running all commands, paste outputs above, then we can determine:

- [ ] **COMMAND 1**: What vol is being used? (default=5 or custom?)
- [ ] **COMMAND 2**: Distribution of |Δ| - are all > 0.10? (explains TEMUAN 1?)
- [ ] **COMMAND 3**: Calibration results - what vol is recommended? ECE values?
- [ ] **COMMAND 4**: Fee formula check - does implied match expected?
- [ ] **COMMAND 5**: Test passes - proves delta wiring correct

Based on outputs, we'll conclude:
- **TEMUAN 1**: Code wiring correct? Data issue? Action needed?
- **TEMUAN 2**: Vol calibrated? Population mismatch? Action needed?
- **Fee Formula**: Current formula correct? Needs API verification?

**DO NOT conclude anything until I review your actual output!**
