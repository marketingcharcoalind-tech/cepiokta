# Book Stability Diagnostic — Implementation Summary

## Task Completed

Created read-only book stability diagnostic tool for G1 REVISI investigation.

## Context

G1 REVISI / CANDIDATE status from analisis5.db validation:
- Best candidate: `min_price=0.96`, 84 entries, 83W/1L, +$7.40, ROI +1.48%
- Sole loss: round `1783520100` - book whipsaw case
  - Entry: UP @0.96, time_left 57.7s, delta +125, p_win 0.99864
  - Outcome: DOWN, pnl -$5.01
  - Book showed panic/reprice (UP ask crashed, DOWN bid spiked)

## Goal

Answer: "Would book-instability warning have detected this loss, and how many winning
trades would it also flag?"

This is **measurement only** to inform whether exit/hedge logic based on book stability
is worth pursuing in Phase 2. Does NOT implement exit/hedge, does NOT change strategy,
does NOT proceed to Phase 2.

## Deliverables

### 1. New Module
**`src/btcbot/backtest/book_stability_diagnostics.py`** (415 lines)
- `BookStabilityMetrics` dataclass (per-entry book behavior)
- `StabilityThresholds` dataclass (configurable warning thresholds)
- `BucketStats` dataclass (aggregation by side/flag)
- `BookStabilityDiagnostics` collector with aggregation functions
- `_compute_stability_metrics()` — compute all flags and extremes for one trade
- `run_diagnostics()` — main async function:
  1. Load resolved rounds
  2. Reproduce entered trades using ReplayEngine
  3. For each entry, load post-entry book snapshots
  4. Compute stability metrics
  5. Aggregate results
- `format_report()` — text report formatter
- `write_csv()` — CSV export
- CLI with argparse (--db, --since, --until, --t-entry, --delta-threshold, --min-price,
  --max-price, --max-rounds, --starting-balance, --csv, threshold flags)
- `main_async()` / `main()` — asyncio-safe entry point

### 2. Tests
**`tests/backtest/test_book_stability_diagnostics.py`** (195 lines)
- `TestBucketStats`: win_rate, avg_pnl, zero_entries
- `TestComputeStabilityMetrics`:
  - `test_stable_winning_trade_no_warning` — stable book, no flags
  - `test_leader_bid_drops_below_threshold` — leader bid crash triggers warning
  - `test_opposite_bid_spikes_above_threshold` — opposite bid spike triggers warning
  - `test_leader_ask_drops_below_threshold` — leader ask crash triggers warning
  - `test_drawdown_triggers_warning` — large drawdown triggers warning
  - `test_first_instability_is_earliest` — first_instability_ts is earliest trigger
  - `test_no_post_entry_snapshots_handled_safely` — empty snapshots no crash
- `TestCLIParser`: `test_parser_accepts_thresholds` — CLI accepts all threshold flags

**Test Results:** ✅ 11/11 passed in 0.24s

### 3. Documentation
**`BOOK_STABILITY_DIAGNOSTIC_GUIDE.md`** (321 lines)
- Purpose & G1 REVISI context
- "Read-Only Diagnostic Only" safety statement
- How it works (4-step process)
- Usage examples (basic + custom thresholds)
- Parameter documentation (replay params + threshold params)
- Metrics explained (core, book extremes, derived, flags, timing)
- Interpreting results (key questions, false positive tradeoff)
- What it does NOT tell you (exit prices, hedge effectiveness, Phase 2 readiness)
- Next steps after running diagnostic
- Example VPS command for analisis5.db
- Safety checklist
- Related documentation links

## Metrics Computed (Per Entry)

### Core Metrics
- round_no, entry_ts, time_left_entry, side_taken, resolved_outcome, result, pnl, entry_price

### Book Extremes (Post-Entry)
- min_leader_bid_after_entry, max_opposite_bid_after_entry
- min_leader_ask_after_entry, max_opposite_ask_after_entry

### Derived Metrics
- leader_bid_drawdown = entry_price - min_leader_bid
- opposite_bid_spike = max_opposite_bid
- leader_ask_drawdown = entry_price - min_leader_ask

### Instability Flags
- leader_bid_below_0_95, leader_bid_below_0_90
- leader_ask_below_0_95, leader_ask_below_0_90
- opposite_bid_above_0_05, opposite_bid_above_0_10, opposite_bid_above_0_15
- **book_flip_warning** (composite) = True if ANY of:
  - leader_bid <= leader_bid_warn (default 0.90), OR
  - opposite_bid >= opposite_bid_warn (default 0.10), OR
  - leader_ask <= leader_ask_warn (default 0.93), OR
  - leader_bid_drawdown >= drawdown_warn (default 0.06)

### Timing
- first_instability_ts, seconds_after_entry_to_instability, time_left_at_instability

## Report Output Sections

1. **Overall Summary**: total_entries, wins, losses, win_rate, net_pnl
2. **Thresholds**: values used for warnings
3. **By Side**: UP/DOWN breakdown
4. **Instability Timing**: avg seconds to instability (wins vs losses with warnings)
5. **By Threshold Flags**: statistics for each flag (True/False)
6. **Loss Cases Detail**: forensics for each loss (round, timing, book extremes, flags)

## Usage Example (VPS Command for analisis5.db)

```bash
cd ~/cepiokta
source venv/bin/activate
python -m btcbot.backtest.book_stability_diagnostics \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --until "2100-01-01T00:00:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --max-rounds 2000 \
  --starting-balance 500 \
  --csv book_stability_096_analisis5.csv
```

## Implementation Constraints (All Met)

✅ READ-ONLY only  
✅ No writing to DB  
✅ No orders  
✅ No OMS  
✅ No signer  
✅ No strategy changes  
✅ No replay fill logic changes  
✅ No .env changes  
✅ Use Decimal for prices  
✅ Use UTC-aware datetimes  
✅ Memory reasonable (streaming where practical)  
✅ Avoid nested asyncio.run() bug (main_async awaited from main)  
✅ Tests for pure metric logic with synthetic snapshots  
✅ CLI parser accepts thresholds  
✅ Documentation explains purpose, usage, interpretation  

## Files Created/Modified

**Created:**
- `src/btcbot/backtest/book_stability_diagnostics.py` (415 lines)
- `tests/backtest/test_book_stability_diagnostics.py` (195 lines)
- `BOOK_STABILITY_DIAGNOSTIC_GUIDE.md` (321 lines)
- `BOOK_STABILITY_DIAGNOSTIC_SUMMARY.md` (this file)

**Modified:**
- None (read-only diagnostic, no changes to existing code)

## Test Results

```
tests/backtest/test_book_stability_diagnostics.py::TestBucketStats::test_win_rate PASSED
tests/backtest/test_book_stability_diagnostics.py::TestBucketStats::test_win_rate_zero_entries PASSED
tests/backtest/test_book_stability_diagnostics.py::TestBucketStats::test_avg_pnl PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_stable_winning_trade_no_warning PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_leader_bid_drops_below_threshold PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_opposite_bid_spikes_above_threshold PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_leader_ask_drops_below_threshold PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_drawdown_triggers_warning PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_first_instability_is_earliest PASSED
tests/backtest/test_book_stability_diagnostics.py::TestComputeStabilityMetrics::test_no_post_entry_snapshots_handled_safely PASSED
tests/backtest/test_book_stability_diagnostics.py::TestCLIParser::test_parser_accepts_thresholds PASSED

======================== 11 passed in 0.24s =========================
```

## Syntax Check

```
✅ python -m py_compile src/btcbot/backtest/book_stability_diagnostics.py
✅ python -m py_compile tests/backtest/test_book_stability_diagnostics.py
```

## Safety Statement

✅ **Read-only diagnostic** — no execution path changes  
✅ **No OMS, no orders, no fills** — orders=0, fills=0 maintained  
✅ **No secrets** — no API keys, no private keys, no signing  
✅ **No Phase 2** — does NOT proceed to paper/live trading  
✅ **No strategy changes** — strategy.py, signal.py, sizing.py, replay fill logic unchanged  
✅ **No .env changes** — configuration unchanged  
✅ **Mode remains readonly** — bot soak continues unaffected  

## Acceptance Criteria

✅ Tests pass (11/11)  
✅ Syntax valid (py_compile clean)  
✅ No runtime trading files modified (only new diagnostic module + tests + docs)  
✅ Documentation complete (guide + summary)  
✅ VPS command ready  

## Next Steps for User

1. **Run diagnostic on VPS** using command above
2. **Review output** to answer key questions:
   - Did `book_flip_warning` detect the sole loss (round 1783520100)?
   - How many winning trades also triggered warnings (false positive rate)?
   - When did instability appear (early noise vs late signal)?
3. **Document findings** in G1 report update or new investigation doc
4. **Decision**:
   - If warnings catch losses with few false positives → mark "book-stability exit/hedge"
     as promising research track for Phase 2
   - If high false positive rate → may need refined thresholds or focus elsewhere
   - If unclear pattern → need more data (analisis6+)
5. **Continue readonly soak**, collect more data (analisis6+)
6. **DO NOT proceed to Phase 2** until G1 = LANJUT decision

## Related Documentation

- `G1_CANDIDATE_REPORT_ANALISIS5.md` — G1 candidate report (sole loss forensics)
- `LOSS_DIAGNOSTICS_GUIDE.md` — Entry-level loss bucketing
- `docs/05-STRATEGY_SPEC.md` — Strategy specification
- `docs/09-TESTING_AND_BACKTESTING.md` — Backtest framework

## Commit Message

```
feat: add read-only book stability diagnostics

Add book stability diagnostic tool for G1 REVISI investigation:
- Analyzes post-entry book behavior for entered replay trades
- Computes instability flags (leader bid/ask drops, opposite bid spikes, drawdown)
- Composite book_flip_warning flag (leader_bid<=0.90 OR opposite_bid>=0.10 OR leader_ask<=0.93 OR drawdown>=0.06)
- Configurable thresholds via CLI
- Report: overall summary, by side, by flag, instability timing, loss cases detail
- CSV export

Purpose: Answer "Would book-instability warning have detected sole loss (round 1783520100), and how many wins would it flag?"

Context: G1 REVISI / CANDIDATE (analisis5.db, min_price=0.96, 84 entries 83W/1L +$7.40)
Sole loss was book whipsaw (UP ask crashed, DOWN bid spiked mid-window).

Read-only diagnostic only:
- No strategy changes
- No execution paths
- No OMS/orders/fills
- No Phase 2
- No .env changes
- Mode remains readonly

Files:
- src/btcbot/backtest/book_stability_diagnostics.py (415 lines)
- tests/backtest/test_book_stability_diagnostics.py (195 lines, 11 tests pass)
- BOOK_STABILITY_DIAGNOSTIC_GUIDE.md (321 lines)
- BOOK_STABILITY_DIAGNOSTIC_SUMMARY.md (this file)

Tests: 11/11 passed
```
