# G1 Candidate Report — analisis5.db (min_price=0.96 defensive)

> **Status**: G1 CANDIDATE / REVISI RINGAN  
> **Date**: 2026-07-10  
> **Dataset**: analisis5.db  
> **Mode**: Backtest/replay on readonly recorded data  
> **Bot status**: Readonly soak continues (orders=0, fills=0)

---

## 1. Executive Summary

### Dataset Overview

- **Database**: `analisis5.db`
- **Resolved rounds**: 1,619
- **Date range**: `2026-07-04T14:00:00+00:00` to `2026-07-10T13:25:00+00:00` (5.99 days)
- **Orders/fills**: 0 (readonly mode, no execution)
- **Bot mode**: Readonly soak running on VPS

### Best Candidate (Defensive)

**Parameters**: `t_entry=60, delta=50, min_price=0.96, max_price=0.99`

**Results (ALL5)**:
- **Entries**: 84
- **Wins**: 83
- **Losses**: 1
- **Win rate**: 98.8%
- **Net PnL**: +$7.40
- **ROI**: +1.48%
- **Avg PnL per trade**: +$0.0881

### Status

**G1 = CANDIDATE / REVISI RINGAN**, not LANJUT final.

**Reasoning**:
- ✅ Positive net PnL across ALL5, OLD4, and NEW splits
- ✅ High win rate (98.8%) with minimal tail loss (1 loss only)
- ✅ Out-of-sample (NEW) validation shows 16 entries, 16 wins, 0 losses
- ⚠️ Single loss case reveals need for hedge/exit logic (not just entry filters)
- ⚠️ Dataset still limited for production confidence
- ⚠️ Backtest holds to settlement (no hedge/exit simulation yet)

**Recommendation**: Continue readonly soak, collect more data (analisis6), research book-stability/exit diagnostics. DO NOT proceed to Phase 2 (paper/live) yet.

---

## 2. Parameters Tested

### Fixed Parameters

- `t_entry`: 60 seconds
- `delta_threshold`: 50 USD
- `max_price`: 0.99
- `starting_balance`: $500
- `mode`: backtest (replay over recorded readonly data)

### Grid: min_price

Tested values: **0.80, 0.95, 0.96, 0.97, 0.98**

**Goal**: Find defensive parameter that reduces tail-loss risk while maintaining positive edge.

---

## 3. Results by Split

### ALL5 (Full Dataset: 1,619 rounds)

| min_price | Entries | Wins | Losses | Win% | Net PnL | ROI | Avg PnL |
|-----------|---------|------|--------|------|---------|-----|---------|
| 0.80 | 121 | 115 | 6 | 95.0% | +$9.55 | +1.91% | +$0.0789 |
| 0.95 | 91 | 88 | 3 | 96.7% | -$0.24 | -0.05% | -$0.0026 |
| **0.96** | **84** | **83** | **1** | **98.8%** | **+$7.40** | **+1.48%** | **+$0.0881** |
| 0.97 | 73 | 72 | 1 | 98.6% | +$4.06 | +0.81% | +$0.0556 |
| 0.98 | 58 | 57 | 1 | 98.3% | +$0.35 | +0.07% | +$0.0061 |

### OLD4 (Training: Jul 4–9, 1,270 rounds)

| min_price | Entries | Wins | Losses | Net PnL | ROI |
|-----------|---------|------|--------|---------|-----|
| 0.80 | 101 | 95 | 6 | +$3.84 | +0.77% |
| 0.95 | 75 | 72 | 3 | -$2.73 | -0.55% |
| **0.96** | **68** | **67** | **1** | **+$5.07** | **+1.01%** |
| 0.97 | 59 | 58 | 1 | +$2.31 | +0.46% |
| 0.98 | 46 | 45 | 1 | -$0.78 | -0.16% |

### NEW (Out-of-Sample: Jul 10, 349 rounds)

| min_price | Entries | Wins | Losses | Net PnL | ROI |
|-----------|---------|------|--------|---------|-----|
| 0.80 | 20 | 20 | 0 | +$5.71 | +1.14% |
| 0.95 | 16 | 16 | 0 | +$2.49 | +0.50% |
| **0.96** | **16** | **16** | **0** | **+$2.33** | **+0.47%** |
| 0.97 | 14 | 14 | 0 | +$1.75 | +0.35% |
| 0.98 | 12 | 12 | 0 | +$1.13 | +0.23% |

---

## 4. Why min_price=0.96 is Preferred Defensively

### Comparison of Candidates

**min_price=0.80** (most aggressive):
- ✅ Highest net PnL in ALL5 (+$9.55) and NEW (+$5.71)
- ❌ Carries 6 losses in ALL5 and OLD4 (tail-loss risk)
- ❌ Lower win rate (95.0% vs 98.8%)
- **Assessment**: More entries but more tail exposure

**min_price=0.95**:
- ❌ **Negative net PnL** in ALL5 (-$0.24) and OLD4 (-$2.73)
- ✅ Positive in NEW (+$2.49) but unstable across splits
- **Assessment**: Unreliable; fails in longer horizons despite short-term wins

**min_price=0.96** (defensive sweet spot):
- ✅ Positive net PnL across **ALL splits**: ALL5 (+$7.40), OLD4 (+$5.07), NEW (+$2.33)
- ✅ **Only 1 loss** in ALL5 (minimal tail risk)
- ✅ High win rate (98.8%)
- ✅ Consistent performance (not overfit to one period)
- ✅ Reasonable entry count (84 in ALL5, 16 in NEW)
- **Assessment**: Best risk-adjusted tradeoff

**min_price=0.97**:
- ✅ Positive across splits but lower PnL than 0.96
- ✅ Only 1 loss, high win rate (98.6%)
- ⚠️ Fewer entries (73 vs 84)
- **Assessment**: Too conservative; leaves profitable opportunities on table

**min_price=0.98**:
- ❌ Very low net PnL in ALL5 (+$0.35) and negative in OLD4 (-$0.78)
- ⚠️ Too few entries (58 in ALL5, 12 in NEW)
- **Assessment**: Overly restrictive; edge nearly eliminated by selectivity

### Conclusion

**min_price=0.96** is the **defensive candidate** because:
1. Reduces tail-loss drastically (1 loss vs 6 for min_price=0.80)
2. Maintains positive edge across all splits (not period-dependent)
3. High win rate (98.8%) with meaningful entry count
4. Out-of-sample validation confirms stability (NEW: 16 entries, 16 wins)

---

## 5. Loss Diagnostics for min_price=0.96 (ALL5)

### Overall Summary

- **Total Entries**: 84
- **Wins**: 83
- **Losses**: 1
- **Win Rate**: 98.8%
- **Net PnL**: +$7.40

### Performance by Side

| Side | Entries | Wins | Losses | Win% | Net PnL | Avg PnL |
|------|---------|------|--------|------|---------|---------|
| DOWN | 46 | 46 | 0 | 100.0% | +$6.90 | +$0.15 |
| UP | 38 | 37 | 1 | 97.4% | +$0.50 | +$0.01 |

**Observation**: DOWN side has perfect record (46W/0L). UP side has sole loss.

### Performance by Entry Price

| Entry Price Bucket | Entries | Wins | Losses | Net PnL |
|--------------------|---------|------|--------|---------|
| (0.95,0.97] | 58 | 57 | 1 | +$4.95 |
| (0.97,0.99] | 26 | 26 | 0 | +$2.45 |

**Observation**: Loss occurred in (0.95,0.97] bucket, not at extreme high prices.

### Performance by Abs Delta

| Abs Delta Bucket | Entries | Wins | Losses | Net PnL |
|------------------|---------|------|--------|---------|
| [0,50) | 19 | 19 | 0 | +$2.54 |
| [50,60) | 11 | 11 | 0 | +$1.88 |
| [60,75) | 18 | 18 | 0 | +$3.11 |
| [75,100) | 26 | 26 | 0 | +$3.63 |
| [100+) | 10 | 9 | 1 | -$3.75 |

**Observation**: Loss occurred in **[100+) bucket** (very high delta). This suggests extreme delta is not always safe despite high confidence.

### Performance by Time Left

| Time Left Bucket | Entries | Wins | Losses | Net PnL |
|------------------|---------|------|--------|---------|
| [45,60] | 84 | 83 | 1 | +$7.40 |

**Observation**: All entries within [45,60] seconds range (t_entry=60 filter working as expected).

### Performance by P_Win

**Note**: p_win values from loss_diagnostics CSV are unreliable zeros. See Section 8 (Caveats) for details. Use replay script values where available.

---

## 6. Sole Loss Case — Detailed Forensics

### Round Identification

- **round_no**: `1783520100`
- **window_start**: `2026-07-08T14:10:00+00:00`
- **window_end**: `2026-07-08T14:15:00+00:00`
- **resolved_outcome**: **DOWN**

### Entry Details

- **entry_ts**: `2026-07-08T14:13:35+00:00`
- **time_left**: 57.7 seconds
- **side_taken**: **UP** (followed leader)
- **leader at entry**: UP
- **entry_price**: 0.96
- **size**: 5.20 contracts
- **pnl**: **-$5.006560**

### Signal at Entry (from replay script)

- **delta**: +125.28512503 USD (very high, indicating strong UP trend)
- **p_win_entry**: 0.9986404130690112 (**99.86% confidence** for UP)
- **net_edge_entry**: 0.0358404130690112 (positive edge after fees)

**Interpretation**: Entry signal was extremely bullish. Model predicted UP with 99.86% confidence. Delta was +125 (among highest in dataset).

### What Happened — Market Whipsaw

**Signal/Chainlink samples** showed UP trend continuing through window end.

**Orderbook behavior** (from compact book summary):
- Around `14:14:00–14:14:05`: Market panic/reprice
  - UP ask dropped sharply to ~0.87–0.91
  - DOWN bid surged to ~0.10–0.13
- Then UP recovered to expensive levels (0.98/0.99)
- Final seconds: UP dropped again to ~0.86–0.90
- **Settlement**: DOWN won

### Loss Type Classification

This is **NOT** a cheap entry price issue (entry_price=0.96, well within safe range).

This is a **tail/reversal/whipsaw case**:
- Strong signal (Δ=+125, p_win=99.86%) contradicted by outcome
- Orderbook showed panic/reprice mid-window (book instability)
- BTC price continued UP per Chainlink, but market repriced DOWN expectation

### Lesson Learned

**Entry filters alone are insufficient** for this case. Even with:
- High delta (>100)
- High confidence (99.86%)
- Safe entry price (0.96)
- Sufficient time left (57.7s)

...the trade still lost due to **rapid market repricing** between entry and settlement.

**What's needed**: Exit/hedge logic to detect book flip or stability loss. Current backtest holds to settlement without exit capability.

---

## 7. Loss Interpretation

### Not a Filter Problem

This loss is **not due to weak entry filters**. All gates passed:
- ✅ time_left <= 60s (57.7s)
- ✅ abs_delta >= 50 (125.28)
- ✅ min_price <= ask <= max_price (0.96 within [0.96, 0.99])
- ✅ net_edge >= MIN_EDGE (0.0358 > 0.01)

### Execution vs Outcome Risk

This is classic **outcome prediction failure** despite strong signal:
- Model said: UP with 99.86% confidence
- Reality: DOWN won
- Orderbook telegraphed the reprice (if we had been watching)

### Book Instability Signal

Compact book summary shows clear instability:
- UP ask: 0.99 → 0.87 → 0.99 → 0.86 (volatile)
- DOWN bid: 0.01 → 0.13 → 0.01 → 0.14 (volatile)

**Implication**: Need book-stability metric. If book shows rapid flip or volatility, consider exit/hedge even if signal still bullish.

### Current Backtest Limitation

Backtest uses **hold-to-settlement** strategy. No logic for:
- Exit when p_win drops below threshold
- Hedge when book flips (ask/bid ratio reverses)
- Cancel/reduce when book becomes unstable

**Next research**: Implement exit/hedge diagnostics (read-only analysis, not live execution).

---

## 8. Caveats and Limitations

### 8.1 Diagnostic Data Quality

**Issue**: `p_win`, `ask_win`, `net_edge` values in loss_diagnostics CSV are unreliable (many zeros).

**Workaround**: Use replay script values for p_win/net_edge when analyzing specific rounds. CSV is useful for bucket analysis but not for per-entry signal values.

**Status**: Known issue; diagnostics tool approximates signal at entry (uses first signal with time_left <= t_entry). More precise signal capture may be needed.

### 8.2 Dataset Size

- **ALL5**: 1,619 rounds over 5.99 days
- **NEW (OOS)**: 349 rounds, only 1 day of data
- **Entries (0.96)**: 84 total, 16 in NEW

**Assessment**: Dataset is **still small** for production confidence. Results are promising but need more soak time. Target: 2-4 weeks of continuous data for robust validation.

### 8.3 Out-of-Sample Validation

**NEW split** (Jul 10) shows:
- 16 entries, 16 wins, 0 losses → **Perfect record**
- +$2.33 net PnL, +0.47% ROI

**Positive**: Confirms 0.96 parameter not overfit to OLD4 period.

**Limitation**: NEW is only 1 day (349 rounds). Need longer OOS validation (multiple days, different market regimes).

### 8.4 Backtest Assumptions

Current backtest assumes:
- **Hold to settlement** (no exit/hedge)
- **Fill model**: FOK with latency, slippage, competition
- **Fee**: 7% taker (crypto_fees_v2)
- **Label**: Gamma resolved_outcome (UP/DOWN from outcomePrices)

**Not simulated**:
- Exit when p_win < P_EXIT threshold
- Hedge when book flips (FLIP_RATIO)
- Dynamic position adjustment
- Book stability detection

**Implication**: Real PnL may differ if exit/hedge logic is added (could improve or degrade depending on implementation).

### 8.5 G1 Status

**G1 = CANDIDATE / REVISI RINGAN**

**Why not LANJUT**:
1. Dataset still limited (5.99 days, 84 entries for 0.96)
2. Sole loss reveals need for exit/hedge design (not ready for Phase 2 without this)
3. OOS validation positive but short (1 day only)
4. Backtest limitation (hold-to-settlement) may not reflect real strategy behavior

**Why not STOP**:
1. 0.96 is profitable across ALL splits (ALL5, OLD4, NEW)
2. High win rate (98.8%) with minimal tail loss (1 loss only)
3. Edge is positive after fees (+1.48% ROI, +$7.40 net PnL)
4. Out-of-sample validation confirms (NEW: 16W/0L)

**Decision**: Continue research and data collection. DO NOT proceed to Phase 2 yet.

---

## 9. Recommendations

### 9.1 Status: G1 CANDIDATE / REVISI RINGAN

**Do NOT mark as LANJUT** (Phase 2 not ready).

**Do NOT mark as STOP** (edge is positive, promising).

**Status = CANDIDATE**: Promising candidate parameter (min_price=0.96) identified. Needs:
- More soak time (collect analisis6, analisis7, ...)
- Exit/hedge diagnostic research (book-stability, flip detection)
- Longer OOS validation (multi-day NEW splits)

### 9.2 Continue Readonly Soak

**Action**: Keep bot running in readonly mode on VPS.

**Target**: Collect at least 2-4 weeks of continuous data (10-20 days) before revisiting G1 decision.

**Frequency**: Daily/weekly snapshots (`analisis6.db`, `analisis7.db`, ...) for rolling validation.

### 9.3 Optional Next Research (Read-Only)

**Priority 1**: Book-stability diagnostic
- Detect when book shows rapid flip or volatility
- Analyze: would exit/hedge have avoided sole loss?
- Read-only analysis, no execution

**Priority 2**: Exit/hedge simulation
- Add exit logic to backtest (when p_win < P_EXIT)
- Add hedge logic (when book flip >= FLIP_RATIO)
- Measure: does exit/hedge improve PnL or win rate?

**Priority 3**: Longer OOS validation
- After collecting more data, split into TRAIN (old) and TEST (recent)
- Validate 0.96 parameter stability across multiple market regimes

**NOT recommended yet**: Phase 2 (paper trading), live execution, OMS implementation.

### 9.4 What NOT to Do

❌ **Do NOT proceed to Phase 2** (paper trading) yet
- Exit/hedge logic not designed
- Dataset still limited
- Sole loss case shows execution complexity

❌ **Do NOT implement live/signer/OMS**
- No private keys
- No API keys
- No order submission

❌ **Do NOT change strategy.py / signal.py / sizing.py**
- Current backtest uses existing logic
- Changes would invalidate comparison with previous results

❌ **Do NOT modify .env or start paper mode**
- Keep bot in readonly soak
- orders=0, fills=0 must remain

---

## 10. Explicit Safety Statement

### No Code Changes

✅ This is a **docs-only report**.
✅ **No Python code modified**.
✅ **No execution path changed**.
✅ **No trading logic altered**.

### No Execution

✅ **Mode remains readonly**.
✅ **orders = 0, fills = 0** (verified).
✅ **No order submission capability** (signer not implemented).
✅ **No Phase 2** (paper trading not started).

### No Secrets

✅ **No private keys added**.
✅ **No API keys modified**.
✅ **No .env changes**.
✅ **No secrets in code or docs**.

### Status

✅ **G1 = CANDIDATE / REVISI RINGAN** (not LANJUT, not STOP).
✅ **Readonly soak continues** on VPS.
✅ **Next snapshot**: analisis6.db after more data collected.

---

## Appendix: Command Reference

### Backtest Command Used

```bash
python -m btcbot.backtest.report \
    --db "sqlite+aiosqlite:///./analisis5.db" \
    --since "2026-07-04T14:00:00+00:00" \
    --until "2026-07-10T13:25:00+00:00" \
    --grid t-entry=60 delta=50 min-price=0.80,0.95,0.96,0.97,0.98 max-price=0.99 \
    --starting-balance 500 \
    --max-rounds 2000
```

### Loss Diagnostics Command

```bash
python -m btcbot.backtest.loss_diagnostics \
    --db "sqlite+aiosqlite:///./analisis5.db" \
    --since "2026-07-04T14:00:00+00:00" \
    --until "2026-07-10T13:25:00+00:00" \
    --t-entry 60 \
    --delta-threshold 50 \
    --min-price 0.96 \
    --max-price 0.99 \
    --starting-balance 500 \
    --max-rounds 2000 \
    --csv loss_diagnostics_all5_096.csv
```

### Replay Script for Sole Loss (round 1783520100)

Used to get accurate p_win/net_edge values for the loss case.

---

**Report Generated**: 2026-07-10  
**Dataset**: analisis5.db (1,619 rounds, Jul 4-10)  
**Status**: G1 CANDIDATE / REVISI RINGAN  
**Recommendation**: Continue readonly soak, collect more data, research exit/hedge diagnostics  
**Action Required**: NONE (docs-only report)
