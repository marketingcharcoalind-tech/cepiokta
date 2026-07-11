# Book Stability Diagnostic Guide

## Purpose

Read-only diagnostic tool to analyze post-entry book behavior for entered replay trades
and measure whether book instability/whipsaw signals could detect dangerous rounds.

**G1 REVISI Context:**  
analisis5.db validation identified sole loss (round `1783520100`) as a book whipsaw case:
- Entry: UP @0.96, time_left 57.7s, delta +125, p_win 0.99864
- Outcome: DOWN, pnl -$5.01
- Book showed panic/reprice mid-window (UP ask crashed to ~0.87-0.91, DOWN bid spiked ~0.10-0.13)

**Goal:**  
Answer: "Would book-instability warning have detected this loss, and how many winning
trades would it also flag?"

## Important: Read-Only Diagnostic Only

This is **measurement/observability only**. It does NOT:
- Change strategy behavior
- Implement exit/hedge logic
- Create execution paths
- Submit orders or simulate live trading
- Proceed to Phase 2

This diagnostic helps inform whether exit/hedge logic based on book stability is worth
pursuing in Phase 2 paper simulation.

## How It Works

1. **Reproduce entered trades**: Uses existing `ReplayEngine.observe()` with same parameters
   as backtest to identify which trades were entered.

2. **Load post-entry book snapshots**: For each entered trade, loads `book_snapshots`
   from entry_ts until window_end.

3. **Compute stability metrics**:
   - Determine leader side (side_taken) and opposite side
   - Find min/max bids/asks for leader and opposite after entry
   - Compute drawdown metrics (e.g., entry_price - min_leader_bid)
   - Check threshold flags (e.g., leader_bid <= 0.90, opposite_bid >= 0.10)
   - Set composite `book_flip_warning` flag if any threshold triggered
   - Find first instability timestamp

4. **Aggregate and report**:
   - Overall summary (entries, wins, losses, PnL)
   - Statistics by side (UP/DOWN)
   - Statistics by threshold flag (True/False)
   - Instability timing (avg seconds to warning among wins vs losses)
   - Detailed loss case forensics

## Installation

No installation required. Module is part of the btcbot package.

## Usage

### Basic Command

```bash
python -m btcbot.backtest.book_stability_diagnostics \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --until "2100-01-01T00:00:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --max-rounds 2000 \
  --starting-balance 500
```

### With Custom Thresholds

```bash
python -m btcbot.backtest.book_stability_diagnostics \
  --db "sqlite+aiosqlite:///./analisis5.db" \
  --since "2026-07-04T14:00:00+00:00" \
  --t-entry 60 \
  --delta-threshold 50 \
  --min-price 0.96 \
  --max-price 0.99 \
  --leader-bid-warn 0.88 \
  --opposite-bid-warn 0.12 \
  --leader-ask-warn 0.91 \
  --drawdown-warn 0.08 \
  --csv book_stability_096_custom.csv
```

## Parameters

### Replay Parameters (must match backtest)
- `--db`: Database URL (e.g., `sqlite+aiosqlite:///./analisis5.db`)
- `--since`: Start datetime (ISO 8601)
- `--until`: End datetime (ISO 8601)
- `--t-entry`: t_entry parameter (seconds before window_end to enter)
- `--delta-threshold`: delta_threshold parameter (USD)
- `--min-price`: min_price filter
- `--max-price`: max_price filter
- `--max-rounds`: Max rounds to process
- `--starting-balance`: Starting balance for replay

### Stability Threshold Parameters (configurable)
- `--leader-bid-warn` (default 0.90): Leader best_bid below this triggers warning
- `--opposite-bid-warn` (default 0.10): Opposite best_bid above this triggers warning
- `--leader-ask-warn` (default 0.93): Leader best_ask below this triggers warning
- `--drawdown-warn` (default 0.06): Leader bid drawdown (entry_price - min_bid) above this triggers warning

### Output
- `--csv`: Optional CSV output file path

## Metrics Explained

### Core Metrics (per entered trade)
- `round_no`: Round identifier
- `entry_ts`: Approximate entry timestamp
- `time_left_entry`: Seconds from entry to window_end
- `side_taken`: UP/DOWN (leader side)
- `resolved_outcome`: UP/DOWN (actual outcome)
- `result`: WIN/LOSS
- `pnl`: PnL for this trade
- `entry_price`: Entry price

### Book Extremes (post-entry)
- `min_leader_bid_after_entry`: Lowest leader best_bid seen after entry
- `max_opposite_bid_after_entry`: Highest opposite best_bid seen after entry
- `min_leader_ask_after_entry`: Lowest leader best_ask seen after entry
- `max_opposite_ask_after_entry`: Highest opposite best_ask seen after entry

### Derived Metrics
- `leader_bid_drawdown`: entry_price - min_leader_bid (how much leader bid dropped)
- `opposite_bid_spike`: max_opposite_bid (how high opposite bid rose)
- `leader_ask_drawdown`: entry_price - min_leader_ask

### Instability Flags
- `leader_bid_below_0_95`: True if min_leader_bid <= 0.95
- `leader_bid_below_0_90`: True if min_leader_bid <= leader_bid_warn threshold
- `leader_ask_below_0_95`: True if min_leader_ask <= 0.95
- `leader_ask_below_0_90`: True if min_leader_ask <= leader_ask_warn threshold
- `opposite_bid_above_0_05`: True if max_opposite_bid >= 0.05
- `opposite_bid_above_0_10`: True if max_opposite_bid >= opposite_bid_warn threshold
- `opposite_bid_above_0_15`: True if max_opposite_bid >= 0.15
- **`book_flip_warning`**: Composite flag = True if ANY of:
  - leader_bid <= leader_bid_warn (default 0.90), OR
  - opposite_bid >= opposite_bid_warn (default 0.10), OR
  - leader_ask <= leader_ask_warn (default 0.93), OR
  - leader_bid_drawdown >= drawdown_warn (default 0.06)

### Timing
- `first_instability_ts`: Timestamp of first snapshot that triggered any warning
- `seconds_after_entry_to_instability`: Seconds from entry to first warning
- `time_left_at_instability`: Seconds from first warning to window_end

## Interpreting Results

### Key Questions

1. **Did book_flip_warning detect the sole loss (round 1783520100)?**  
   Check LOSS CASES DETAIL section. If `book_flip_warning: True`, then yes.

2. **How many winning trades also triggered warnings (false positives)?**  
   Check "By Threshold Flags" section, `book_flip_warning: True` row.
   - If many wins also flagged → naïve guard may over-filter profitable trades.
   - If few wins flagged → warning is a strong risk signal.

3. **Timing: When did instability appear?**  
   Check "Instability Timing" section.
   - If warnings appear very early (seconds after entry) → may be noise or normal volatility.
   - If warnings appear late (close to window_end) → may be too late to exit profitably.

### False Positive Tradeoff

If `book_flip_warning` triggers on many winning trades, a naïve "exit on warning" strategy
would sacrifice profitable trades to avoid rare losses. This diagnostic quantifies that tradeoff.

**Example interpretation:**
- Total entries: 84
- Wins: 83, Losses: 1
- book_flip_warning=True: 10 entries (1 loss + 9 wins)
- **Interpretation**: Warning catches the 1 loss but also flags 9 wins (10.8% false positive rate).
  An exit-on-warning strategy would sacrifice 9 winning trades to avoid 1 loss (~$7 win vs ~$5 loss).
  Net benefit unclear without modeling exit prices.

## What This Does NOT Tell You

1. **Exit prices**: This diagnostic does NOT simulate what price you'd get if you exited
   on warning. That requires Phase 2 paper simulation with exit fill modeling.

2. **Hedge effectiveness**: Does NOT simulate hedging opposite side on warning.

3. **Strategy changes**: Does NOT modify strategy.py entry/exit logic.

4. **Phase 2 readiness**: Positive diagnostic results (warnings catch losses with few
   false positives) suggest exit/hedge logic is worth researching, but DO NOT mean
   immediate Phase 2 implementation. G1 decision must still be made based on overall edge.

## Next Steps After Running Diagnostic

1. **If book_flip_warning detected the loss with few false positives:**
   - Document findings in G1 report update
   - Mark "book-stability exit/hedge" as promising research track for Phase 2
   - Continue readonly soak, collect more data (analisis6+)
   - DO NOT proceed to Phase 2 until G1 = LANJUT

2. **If book_flip_warning has high false positive rate:**
   - May need refined thresholds or composite logic
   - Or book instability may not be a reliable signal for this market
   - Focus on other diagnostics (e.g., signal quality, fee optimization)

3. **If no clear pattern:**
   - Sample size (84 entries, 1 loss) may be too small
   - Continue soak, revisit with analisis6+ (more losses for pattern detection)

## Example VPS Command (analisis5.db)

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

## Safety

- Read-only: no writes to DB, no orders, no OMS, no signer, no secrets
- No strategy changes
- No .env changes
- No Phase 2 execution paths
- Mode remains readonly
- orders=0, fills=0 maintained

## Related Documentation

- `G1_CANDIDATE_REPORT_ANALISIS5.md` - G1 candidate report (sole loss forensics)
- `LOSS_DIAGNOSTICS_GUIDE.md` - Entry-level loss bucketing
- `docs/05-STRATEGY_SPEC.md` - Strategy specification (entry/exit logic)
- `docs/09-TESTING_AND_BACKTESTING.md` - Backtest framework
