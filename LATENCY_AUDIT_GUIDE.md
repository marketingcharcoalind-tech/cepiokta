# LATENCY_AUDIT_GUIDE.md — Latency Audit Documentation (G1 Blocker)

**Status**: Task 5 (in-progress)  
**Created**: 2026-07-11  
**Context**: G1 decision blocker — measure tick-based latency model before advancing

---

## 1. PURPOSE

The latency audit is a **read-only diagnostic tool** designed to comprehensively measure the behavior of the current tick-based latency model before making the G1 LANJUT decision.

**IMPORTANT**: This document describes the corrected latency audit implementation. The initial version (commit b5cb123) contained a correctness bug that produced invalid results.

### Bug in b5cb123 (FIXED)

**Problem**: The initial implementation reconstructed `decision_tick_index` by searching for the first tick whose timestamp matched `obs.entry_decision_ts`:

```python
for i, tick in enumerate(ticks):
    if tick.ts == obs.entry_decision_ts:
        decision_tick_index = i
        break
```

**Why this was wrong**: Multiple reconstructed book events can share the same timestamp. Timestamp equality does not uniquely identify the decision event. The selected index could be earlier than the actual decision index, producing incorrect execution indices and latency measurements.

**Evidence of bug**: b5cb123 incorrectly reported 83/84 entries at 0ms latency (median/p95 0ms, max 1ms), contradicting verified exact replay observability showing 41/84 at 0ms, median 1ms, p95 479ms, max 1021ms.

**Fix**: The corrected implementation uses **exact tick indices** captured directly from ReplayEngine at the moment of successful entry. No timestamp-based reconstruction occurs. Indices are passed through observability: `entry_decision_tick_index`, `requested_entry_execution_tick_index`, `actual_entry_execution_tick_index`, `entry_execution_clamped`.

**DO NOT TRUST** any VPS results from b5cb123. Only results from the corrected version are valid.

### G1 Context

**Candidate**: `t_entry=60`, `delta=50`, `min_price=0.96`, `max_price=0.99`  
**Dataset**: `analisis5.db`  
**Results**: 84 entries, 83W/1L, net PnL +$7.40  
**Config**: `BACKTEST_LATENCY_TICKS=1`

**VPS Evidence** (verified decision-to-fill distribution):
- **Min**: 0.000s
- **P25**: 0.000s
- **Median**: 0.001s
- **P75**: 0.004s
- **P95**: 0.479s
- **Max**: 1.021s
- **41/84** entries had exactly **0ms** latency
- **68/84** entries had **≤10ms** latency
- **74/84** entries had **≤100ms** latency
- **4/84** entries had **>1s** latency

**Conclusion**: One event tick is **NOT** a stable real-time latency model.

---

## 2. CRITICAL QUESTIONS

The audit answers these questions before G1:

1. **How often does `ticks[min(i + latency_ticks, n - 1)]` clamp to the final tick?**
   - Measures structural limitation when insufficient future ticks exist

2. **How often are decision and execution timestamps identical?**
   - Identifies zero-latency cases (latency_ticks=0 or same-timestamp events)

3. **How often are they different events with the same timestamp?**
   - Detects multiple token updates at identical timestamps

4. **Which token caused the decision tick and execution tick: UP or DOWN?**
   - Determines if latency is event-driven per token

5. **What is the age of the UP and DOWN books at decision and execution?**
   - Measures book staleness (last-value-carried-forward risk)

6. **Does execution use a fresh target-side book or a last-value-carried-forward stale book?**
   - Critical for understanding execution quality

7. **How does event density affect realized latency?**
   - Sparse ticks → high realized latency despite low latency_ticks

8. **Are two token updates recorded milliseconds apart and incorrectly treated as network latency?**
   - Distinguishes venue event timestamps from true network latency

9. **What happens when insufficient future ticks exist?**
   - Quantifies final-tick clamping impact

10. **Does the same tick-based latency assumption also affect entry, hedge, and exit?**
    - Currently: audit focuses on entry; hedge/exit share same pattern

11. **How sensitive are entries/PnL to latency_ticks = 0,1,2,3,5?**
    - Observational sensitivity analysis (not implemented in first version)

12. **What fixed real-time latency candidates should be tested later?**
    - Suggested: 50ms, 100ms, 250ms, 500ms, 1000ms

---

## 3. METRICS CAPTURED

### Per Entry Metrics

The audit captures these metrics for each successful entry:

#### Identity
- `round_no`: Round number
- `result`: WIN or LOSS
- `pnl`: Profit/loss for the entry

#### Tick Indices
- `decision_tick_index`: Index of tick when entry decision was made
- `requested_execution_tick_index`: `decision_tick_index + latency_ticks_config`
- `actual_execution_tick_index`: `min(requested, n-1)` (clamped to last tick)
- `total_tick_count`: Total number of ticks in round
- `latency_ticks_config`: Configured latency_ticks value
- `clamped_to_last_tick`: Boolean — was execution tick clamped?

#### Timestamps and Latency
- `decision_ts`: Exact timestamp of decision tick
- `execution_ts`: Exact timestamp of execution tick
- `realized_latency_ms`: `(execution_ts - decision_ts)` in milliseconds
- `same_timestamp`: Boolean — decision_ts == execution_ts?
- `decision_time_left`: Seconds remaining in window at decision
- `execution_time_left`: Seconds remaining in window at execution

#### Entry Details
- `target_side`: UP or DOWN (side entered)
- `decision_ask`: Best ask price at decision tick
- `execution_ask`: Best ask price at execution tick
- `execution_limit_price`: Limit price used for entry order
- `filled`: Boolean — was entry filled?
- `entry_price`: Actual fill price
- `entry_size`: Actual fill size (shares)

#### Book Age Diagnostics (LVCF Detection)
- `decision_target_book_ts`: Timestamp of target-side book at decision
- `decision_target_book_age_ms`: Age of target book at decision (ms)
- `execution_target_book_ts`: Timestamp of target-side book at execution
- `execution_target_book_age_ms`: Age of target book at execution (ms)
- `decision_opposite_book_ts`: Timestamp of opposite-side book at decision
- `decision_opposite_book_age_ms`: Age of opposite book at decision (ms)
- `execution_opposite_book_ts`: Timestamp of opposite-side book at execution
- `execution_opposite_book_age_ms`: Age of opposite book at execution (ms)

#### Book Change Detection
- `target_book_changed`: Boolean — did target book timestamp change?
- `opposite_book_changed`: Boolean — did opposite book timestamp change?

**LVCF Risk**: If `target_book_changed == False`, execution uses a **stale** last-value-carried-forward book, not a fresh update. This is a **separate risk** from nominal latency.

---

## 4. AGGREGATE REPORTS

### Overall Summary
- Total entries, wins, losses, win rate, net PnL
- Realized latency distribution: min, P25, median, P75, P95, max
- Tick-based model issues:
  - Count of entries clamped to last tick
  - Count of entries with same timestamp
  - Count of entries with target book changed
  - Count of entries with opposite book changed

### Latency Buckets
Entry counts, wins, and PnL by realized latency:
- `0ms`: Exactly zero latency
- `(0,10]ms`: 0 < latency ≤ 10ms
- `(10,50]ms`
- `(50,100]ms`
- `(100,250]ms`
- `(250,500]ms`
- `(500,1000]ms`
- `>1000ms`: Greater than 1 second

---

## 5. USAGE

### Command-Line Interface

```bash
python -m btcbot.backtest.latency_audit \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --until "2100-01-01T00:00:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --max-rounds 2000 \
  --starting-balance 500 \
  --csv latency_audit_096_analisis5.csv
```

### Parameters

- `--db`: Database URL (required)
- `--since`: Start datetime filter (ISO 8601, optional)
- `--until`: End datetime filter (ISO 8601, optional)
- `--t-entry`: Entry time threshold in seconds (default: 60)
- `--delta-threshold`: Delta threshold in USD (default: 50.0)
- `--min-price`: Minimum price filter (default: 0.96)
- `--max-price`: Maximum price filter (default: 0.99)
- `--max-rounds`: Maximum rounds to process (optional)
- `--starting-balance`: Starting bankroll (default: 500.0)
- `--csv`: Output CSV file path (optional)

### Output

1. **Console Report**: Text summary with overall statistics and latency buckets
2. **CSV Export**: Detailed per-entry metrics (if `--csv` specified)

---

## 6. VPS COMMAND EXAMPLES

### Full Audit on analisis5.db
```bash
cd ~/cepiokta
source venv/bin/activate
python -m btcbot.backtest.latency_audit \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --max-rounds 2000 \
  --starting-balance 500 \
  --csv latency_audit_096_analisis5.csv
```

### Quick Test (Limited Rounds)
```bash
python -m btcbot.backtest.latency_audit \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-rounds 100 \
  --csv latency_audit_test.csv
```

### Help
```bash
python -m btcbot.backtest.latency_audit --help
```

---

## 7. INTERPRETATION GUIDE

### Zero-Latency Entries (0ms)
**Meaning**: Decision and execution timestamps are identical.

**Causes**:
- `latency_ticks=0` configuration
- Multiple events (different tokens) share the same timestamp
- Decision tick index equals execution tick index after clamping

**Risk**: No simulated execution delay → unrealistic for live trading.

### High-Latency Entries (>1s)
**Meaning**: Large time gap between decision and execution despite low latency_ticks.

**Causes**:
- **Sparse ticks**: Low event density → large gaps between consecutive events
- **Final-tick clamping**: Insufficient future ticks force execution at distant tick

**Risk**: Model assumes "1 tick ahead" means ~10-100ms, but reality can be >1s.

### Clamped Entries
**Meaning**: `requested_execution_tick_index >= total_tick_count`

**Causes**:
- Entry decision occurs late in round with few remaining ticks
- `latency_ticks` exceeds available future ticks

**Risk**: Forces execution at final available tick, which may be stale or far in the future.

### Stale LVCF Book (`target_book_changed == False`)
**Meaning**: Target-side book timestamp did NOT change between decision and execution.

**Causes**:
- Target token had no updates between decision and execution ticks
- Last-value-carried-forward (LVCF) from earlier tick

**Risk**: Execution price/depth may not reflect current market state.

### Fresh Book (`target_book_changed == True`)
**Meaning**: Target-side book timestamp changed between decision and execution.

**Implication**: Execution uses a fresh book update → better reflects current liquidity.

---

## 8. LIMITATIONS

### 1. **Observability Only**
This audit does **NOT change** any replay behavior:
- Same fill selection logic
- Same strategy decisions
- Same PnL calculation
- Same fee/slippage model

It only measures and reports what the current model does.

### 2. **Entry Path Only**
Current implementation focuses on **entry** latency. Hedge and exit paths share the same tick-based latency pattern but are not separately audited yet.

### 3. **No Event Source Attribution**
The audit does **NOT estimate** which token (UP/DOWN) caused each tick event. This would require extending `ReplayTick` with provenance metadata, which is beyond the scope of this read-only diagnostic.

### 4. **No Time-Based Latency Replacement**
This audit **measures** the tick-based model but does **NOT implement** a time-based latency alternative (e.g., fixed 100ms delay). That is a separate task after G1 decision.

### 5. **Determinism Preserved**
The audit reuses `ReplayEngine.observe()`, which is deterministic by construction. All results are reproducible with the same seed and configuration.

---

## 9. FINDINGS (To Be Updated After VPS Run)

### Expected Findings on analisis5.db

Based on existing VPS evidence:
- **41/84 entries** should show `realized_latency_ms == 0.0`
- **~10 entries** should show `clamped_to_last_tick == True`
- **4/84 entries** should show `realized_latency_ms > 1000.0`
- Median latency should be ~1ms (verified)
- P95 latency should be ~479ms (verified)

### Critical Insights

1. **Zero-latency entries are frequent** → Unrealistic for live trading
2. **Sparse tick density causes high latency** → 1 tick ≠ fixed time delay
3. **Clamping to final tick is structural** → Model breaks near end of round
4. **LVCF staleness is separate risk** → Book age ≠ execution latency

---

## 10. NEXT STEPS AFTER AUDIT

### If Audit Confirms Issues:
1. **Implement time-based latency model** (fixed 50-500ms delays)
2. **Add latency sensitivity analysis** (compare latency_ticks=0,1,2,3,5 and time-based 50,100,250,500,1000ms)
3. **Revalidate G1 candidate** with realistic latency model
4. **Update replay.py** to support both tick-based and time-based modes

### If Audit Shows Acceptable Behavior:
1. Document findings and proceed with G1 LANJUT
2. Keep tick-based model as is for Phase 0-1
3. Plan time-based model for Phase 2-3 (live trading)

---

## 11. RELATED TASKS

- **Task 1**: Book Stability Diagnostic (done)
- **Task 2-4**: Book stability bugfixes + exact timestamps (done)
- **Task 5**: Latency Audit (this document) — **G1 BLOCKER**
- **Future**: Time-based latency model replacement (post-G1)

---

## 12. CRITICAL RULES (REPEAT)

1. **Read-only observability only** — NO replay behavior changes
2. **NO strategy changes** — NO fill model changes — NO sizing changes
3. **NO database writes** — NO live mode — NO Phase 2 work
4. **Fail closed** on invalid timestamps or indices
5. **G1 remains blocked** until audit result reviewed

---

## 13. TESTING

See `tests/backtest/test_latency_audit.py` for comprehensive tests:
- Final-tick clamping detection (Test 1)
- Same timestamp detection (Test 2)
- Sparse ticks causing >1s latency (Test 3)
- Stale LVCF book detection (Test 4)
- latency_ticks=0 case (Test 5)
- Successful and failed FOK fills (Tests 6-7)
- Replay behavior unchanged (Test 8) — **CRITICAL**
- latency_ticks greater than remaining (Test 9)
- Book change detection (Test 10)
- No entry classification (Test 11)
- WIN/LOSS classification (Tests 12-13)

All tests ensure audit is read-only and preserves determinism.

---

## 14. COMMIT AND VERIFICATION

After VPS run, commit findings with:
```bash
git add LATENCY_AUDIT_GUIDE.md
git add src/btcbot/backtest/latency_audit.py
git add tests/backtest/test_latency_audit.py
git commit -m "Add latency audit (Task 5, G1 blocker)

- Read-only diagnostic for tick-based latency model
- Measures clamping, same-timestamp, sparse-tick issues
- Detects LVCF stale books vs fresh updates
- Per-entry CSV export + aggregate report
- 13 focused tests ensuring no replay changes
- G1 remains blocked pending audit results"
git push origin main
```

**USER MUST VERIFY** actual VPS output matches expectations before G1 decision.

---

**END OF LATENCY_AUDIT_GUIDE.md**
