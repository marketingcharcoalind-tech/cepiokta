# 15. Pure Intra-Market Arbitrage Detector

> **Status**: Planned (Fase 1 research track)  
> **Mode**: Read-only detector + analysis  
> **Execution**: NONE (no orders, no live trading)

---

## 1. Executive Summary

**Pure intra-market arbitrage** adalah strategi lock-pair yang membeli BOTH UP dan DOWN jika total cost (termasuk fee + slippage) < $1. Karena salah satu outcome akan settle $1, ini secara teori adalah **outcome-independent profit**.

**PERBEDAAN KRITIS dengan strategi saat ini**:
- Strategi saat ini = **directional** (resolution farming / fair-value taker)
- Pure arb = **two-leg lock-pair** (bukan prediksi arah)

**Kenapa belum dieksekusi**:
- Risiko utama BUKAN prediksi outcome, tapi **execution risk**
- Jika satu kaki fill tapi kaki kedua gagal → exposed directional risk
- Harga bisa berubah, depth hilang, latency, competition, fees bisa berubah

**Fase aman**:
1. **Phase 1 (Pre-G1)**: Read-only detector + logging opportunity
2. **Phase 2 (Paper)**: Two-leg fill simulation
3. **Phase 3 (Live)**: Micro-stakes ONLY jika G1/G2 membuktikan opportunity stabil & RiskManager siap

Dokumen ini adalah **rencana** untuk riset read-only di Fase 1, bukan blueprint untuk eksekusi.

---

## 2. Definition: Pure Intra-Market Arbitrage

### 2.1 Kondisi Opportunity

Binary market dengan token UP dan DOWN. Opportunity exists jika:

```
ask_up + ask_down + fee_up + fee_down + slippage_buffer < 1.00
```

Di mana:
- `ask_up` = best ask price untuk token UP
- `ask_down` = best ask price untuk token DOWN
- `fee_up` = estimated fee untuk buy UP (crypto_fees_v2 ~7%)
- `fee_down` = estimated fee untuk buy DOWN
- `slippage_buffer` = estimasi slippage execution (mis. 0.2%)

### 2.2 Net Lock Edge

```
net_lock_edge = 1.00 - (ask_up + ask_down + fee_up + fee_down + slippage_buffer)
```

Jika `net_lock_edge > 0` → theoretically profitable.

### 2.3 Theoretically Outcome-Independent

Setelah lock:
- Jika outcome = UP → token UP settle $1, token DOWN settle $0
- Jika outcome = DOWN → token DOWN settle $1, token UP settle $0
- Total settlement = $1 guaranteed

**Profit teoritis** = $1 - total_cost = net_lock_edge * size

### 2.4 Reality: Execution Risk

**Anti-pattern** = menganggap UP+DOWN < $1 selalu profit tanpa fee/slippage.

**Real risks**:
1. **One-leg fill risk**: Kaki pertama fill, kaki kedua reject/timeout → exposed directional
2. **Price movement**: Harga berubah antara leg 1 dan leg 2
3. **Depth disappears**: Competitor mengambil liquidity
4. **Latency**: Delay execution
5. **Fee changes**: Actual fee > estimate
6. **Slippage**: Actual fill price > best ask

---

## 3. Comparison: Pure Arb vs Current Strategy

| Aspect | Current Strategy (Resolution Farming) | Pure Intra-Market Arb |
|--------|--------------------------------------|----------------------|
| **Type** | Directional (predict outcome) | Non-directional (lock pair) |
| **Entry** | Single side (UP or DOWN) | Both sides (UP + DOWN) |
| **Risk** | Outcome prediction wrong | Execution: one-leg fill failure |
| **Edge source** | Fair-value model + late entry | Market inefficiency (sum asks < $1) |
| **Capital** | ~10% balance per trade | Need capital for BOTH legs |
| **Complexity** | Signal → size → fill | Two-leg coordination, cancel, hedge |
| **Dependency** | BTC price, vol, trend | Book depth, latency, competition |
| **Phase** | Already backtested (G1 REVISI) | Not yet measured (planned) |

**Key insight**: Pure arb menghindar outcome risk, tapi **introduces execution risk**. Bukan "strictly better", hanya "different risk profile".

---

## 4. Why Not Execute Yet?

### 4.1 Execution Risk > Outcome Risk

Strategi resolution farming sudah 94.1% win rate (t_entry=60, delta=50, max_price=0.99). Artinya outcome prediction relatif reliable.

Pure arb menghindari outcome risk, tapi:
- Butuh two-leg atomic-ish execution
- Satu kaki gagal = worse than directional loss (karena exposed dengan cost 2x)
- Belum ada measurement: seberapa sering opportunity muncul? Berapa lama bertahan? Depth cukup?

### 4.2 Infrastructure Not Ready

Live pure arb butuh:
- **RiskManager**: veto one-leg exposure
- **Two-leg OMS**: submit, monitor, cancel if partial
- **Hedge plan**: jika one-leg stuck, hedge via opposite side
- **Idempotency**: prevent double-fill
- **Latency optimization**: minimize gap antara leg 1 & leg 2

Saat ini infrastructure hanya mendukung single-side directional.

### 4.3 Unknown Opportunity Frequency

Kita belum tahu:
- Berapa kali per hari opportunity muncul?
- Berapa lama opportunity bertahan? (10ms? 100ms? 1s?)
- Depth rata-rata berapa? (cukup untuk size meaningful?)
- Apakah competitor juga hunting opportunity yang sama?
- Apakah fee/slippage estimate akurat?

**Measurement first, execution later.**

---

## 5. Phase 1: Read-Only Detector (Pre-G1)

### 5.1 Goal

Mengukur **frequency, duration, depth, net_lock_edge** dari pure arb opportunities TANPA eksekusi.

### 5.2 Scope

- **Input**: Book snapshots (UP & DOWN) dari data recorder
- **Output**: Log/report opportunity metrics
- **Execution**: NONE (no orders, no API calls)
- **Mode**: Backtest on recorded data OR readonly live monitoring

### 5.3 Data to Record Per Opportunity

| Field | Type | Description |
|-------|------|-------------|
| `round_no` | int | Round identifier |
| `ts` | datetime | Timestamp opportunity detected |
| `token_up` | str | Token ID untuk UP |
| `token_down` | str | Token ID untuk DOWN |
| `best_ask_up` | Decimal | Best ask price UP |
| `ask_depth_up` | Decimal | Depth at best ask UP |
| `best_ask_down` | Decimal | Best ask price DOWN |
| `ask_depth_down` | Decimal | Depth at best ask DOWN |
| `sum_asks` | Decimal | ask_up + ask_down |
| `fee_estimate_total` | Decimal | fee_up + fee_down |
| `slippage_buffer` | Decimal | Estimated slippage |
| `net_lock_edge` | Decimal | 1 - sum_asks - fee - slippage |
| `max_lock_size` | Decimal | min(depth_up, depth_down) |
| `duration_ms` | int | How long opportunity lasted |
| `reject_reason` | str | If invalid: why? |

### 5.4 Rejection Reasons

Opportunity rejected if:
- `net_lock_edge <= MIN_LOCK_EDGE` (mis. 0.1%)
- `max_lock_size < MIN_DEPTH` (mis. 5 contracts)
- `sum_asks >= MAX_SUM_ASKS` (mis. 0.99)
- Book empty on either side
- Latency > threshold

### 5.5 Analysis Metrics (G1 Report)

Phase 1 detector harus menghasilkan report:

1. **Opportunity Count**:
   - Total opportunities per day
   - Valid vs rejected breakdown
   - Reject reason distribution

2. **Duration Distribution**:
   - Median, p25, p75, max
   - Bucketed: <10ms, 10-100ms, 100ms-1s, >1s

3. **Net Lock Edge Distribution**:
   - Histogram by 0.1% buckets
   - Median, mean, max

4. **Depth Distribution**:
   - max_lock_size histogram
   - Percentage with size >= 10, 50, 100 contracts

5. **Theoretical Locked PnL**:
   - Sum(net_lock_edge * min(max_lock_size, position_limit))
   - Comparison vs actual resolution farming PnL

6. **Simulated Two-Leg Fill**:
   - Success rate assumption (e.g., 90%, 95%, 99%)
   - One-leg exposure loss scenario
   - Net PnL after execution risk

7. **Comparison**:
   - Pure arb opportunity PnL vs resolution farming PnL
   - Risk-adjusted return (execution risk penalty)

---

## 6. Phase 2: Paper Trading Two-Leg (Not Yet)

**Pre-requisite**: G1 detector report shows opportunity stabil & frequent.

Phase 2 akan:
- Simulate two-leg order submission (no actual orders)
- Model fill latency (tick delay between leg 1 & leg 2)
- Track partial fill scenarios
- Measure one-leg exposure frequency
- Refine fee/slippage estimates

**DoD Phase 2**: Paper log shows net PnL after execution risk > resolution farming.

---

## 7. Phase 3: Live Micro-Stakes (Future)

**Pre-requisite**: G1 + G2 + RiskManager + two-leg OMS ready.

Phase 3 akan:
- Live execution dengan max notional sangat kecil ($1-$5 per lock)
- Two-leg atomic-ish submission (submit both, cancel if one fails)
- Real-time monitoring one-leg exposure
- Hedge plan if stuck
- Circuit breaker if edge < threshold

**DoD Phase 3**: Live locked pairs profitable net-of-fee after 50+ trades.

---

## 8. Safety Gates

### 8.1 Phase 1 (Detector Read-Only)

✅ **ALLOWED**:
- Read book_snapshots from DB
- Calculate sum_asks + fee + slippage
- Log opportunity to CSV/DB
- Generate report/metrics

❌ **FORBIDDEN**:
- Call OMS / order submission
- Call CLOB API (kecuali read-only book fetch yang sudah ada)
- Add private key / API key
- Modify strategy.py / signal.py / sizing.py
- Touch `.env` live
- Submit any transaction

### 8.2 Phase 2 (Paper)

✅ **ALLOWED** (in addition to Phase 1):
- Simulate order submission (write to paper ledger, not API)
- Model fill latency / slippage / partial fill

❌ **FORBIDDEN**:
- Actual order submission
- Live API calls

### 8.3 Phase 3 (Live Micro)

✅ **ALLOWED** (in addition to Phase 1+2):
- Submit real orders (two-leg)
- Max notional per lock: $1-$5
- RiskManager veto required
- Idempotency checks
- Cancel/hedge if one-leg stuck

❌ **FORBIDDEN** until RiskManager ready:
- Large notional
- One-leg execution without hedge plan
- Skip veto

---

## 9. Module Design (Planned)

### 9.1 Domain Module: `domain/arbitrage.py`

```python
@dataclass(frozen=True)
class ArbOpportunity:
    round_no: int
    ts: datetime
    token_up: str
    token_down: str
    ask_up: Decimal
    ask_down: Decimal
    depth_up: Decimal
    depth_down: Decimal
    sum_asks: Decimal
    fee_total: Decimal
    slippage_buffer: Decimal
    net_lock_edge: Decimal
    max_lock_size: Decimal
    valid: bool
    reject_reason: str | None

def detect_lock_pair(
    book_up: OrderBook,
    book_down: OrderBook,
    fee_model: FeeModel,
    slippage_buffer: Decimal,
    min_lock_edge: Decimal,
    min_depth: Decimal,
) -> ArbOpportunity | None:
    """
    Deteksi pure intra-market arb opportunity.
    
    Returns ArbOpportunity jika sum_asks + fee + slippage < 1.
    Domain pure function, NO side effects, NO API calls.
    """
    ...
```

### 9.2 Backtest Module: `backtest/arb_detector.py`

```python
async def replay_arb_detection(
    store: Store,
    since: datetime,
    until: datetime,
    config: ArbDetectorConfig,
) -> ArbDetectionReport:
    """
    Replay book snapshots, detect opportunities, return report.
    
    READ-ONLY. No orders. No execution.
    """
    ...

@dataclass
class ArbDetectionReport:
    total_opportunities: int
    valid_opportunities: int
    total_duration_ms: int
    median_duration_ms: float
    net_edge_histogram: dict[str, int]
    depth_histogram: dict[str, int]
    theoretical_pnl: Decimal
    simulated_pnl_95pct_fill: Decimal
    ...
```

### 9.3 Integration: `backtest/report.py`

Tambahkan command:
```bash
python -m btcbot.backtest.report \
    --mode arb-detection \
    --db "sqlite+aiosqlite:///./analisis4.db" \
    --since "2026-07-06" \
    --until "2026-07-09" \
    --min-lock-edge 0.001 \
    --min-depth 5 \
    --csv arb_opportunities.csv
```

Output: CSV + summary report seperti loss_diagnostics.

---

## 10. Configuration (Planned)

### 10.1 Environment Variables

```ini
# Pure Arbitrage Detector (Phase 1 read-only)
ARB_DETECTOR_ENABLED=false          # Enable detector in replay/readonly
ARB_MAX_SUM_ASKS=0.99               # Max sum(ask_up, ask_down)
ARB_SLIPPAGE_BUFFER=0.002           # 0.2% slippage estimate
ARB_MIN_LOCK_EDGE=0.001             # Min 0.1% net edge
ARB_MIN_DEPTH=5                     # Min 5 contracts depth
ARB_MAX_LOCK_SIZE=50                # Max size per lock (safety cap)
```

**IMPORTANT**: Ini hanya config plan. Jangan ubah `.env` live sekarang.

### 10.2 Settings: `config/settings.py`

```python
@dataclass
class ArbDetectorSettings:
    enabled: bool = False
    max_sum_asks: Decimal = Decimal("0.99")
    slippage_buffer: Decimal = Decimal("0.002")
    min_lock_edge: Decimal = Decimal("0.001")
    min_depth: Decimal = Decimal("5")
    max_lock_size: Decimal = Decimal("50")
```

---

## 11. Data Model (Planned)

### 11.1 Table: `arb_opportunities` (Optional)

```sql
CREATE TABLE arb_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_no INTEGER NOT NULL,
    ts TEXT NOT NULL,
    token_up TEXT NOT NULL,
    token_down TEXT NOT NULL,
    ask_up TEXT NOT NULL,
    ask_down TEXT NOT NULL,
    depth_up TEXT NOT NULL,
    depth_down TEXT NOT NULL,
    sum_asks TEXT NOT NULL,
    fee_total TEXT NOT NULL,
    slippage_buffer TEXT NOT NULL,
    net_lock_edge TEXT NOT NULL,
    max_lock_size TEXT NOT NULL,
    duration_ms INTEGER,
    valid INTEGER NOT NULL,          -- 0 or 1
    reject_reason TEXT,
    mode TEXT NOT NULL               -- 'backtest' or 'readonly'
);

CREATE INDEX idx_arb_opp_round ON arb_opportunities(round_no);
CREATE INDEX idx_arb_opp_ts ON arb_opportunities(ts);
CREATE INDEX idx_arb_opp_valid ON arb_opportunities(valid);
```

**Alternative**: CSV export saja, tidak perlu table. Decide saat implementasi.

---

## 12. Testing Strategy

### 12.1 Unit Tests

- `test_detect_lock_pair()`: edge cases (empty book, sum=1, sum<1, depth=0)
- `test_net_lock_edge_calculation()`: fee/slippage math
- `test_reject_reasons()`: validation logic

### 12.2 Integration Tests

- Backtest replay pada 1 ronde dengan synthetic book data
- Verify opportunity detected/rejected correctly

### 12.3 Backtest Validation

- Run detector on LATE split (949 rounds)
- Compare opportunity count vs resolution farming entries
- Sanity check: jika opportunities = 0 → either market efficient or detector bug

---

## 13. Comparison Metrics (G1 Report)

Detector report harus membandingkan:

| Metric | Resolution Farming (Current) | Pure Arb (Detected) |
|--------|----------------------------|---------------------|
| Opportunities | 85 entries (t=60,d=50,p=0.99) | ? |
| Avg Edge | net_edge @ entry | net_lock_edge |
| Win Rate | 94.1% | 100% (theoretical) |
| Avg PnL/Trade | ~$0.03 | ? |
| Execution Risk | Outcome prediction | One-leg fill failure |
| Capital Efficiency | ~10% per trade | ~20% per lock (both legs) |
| Frequency | Every ~11 rounds | ? |

**Goal**: Tidak menggantikan resolution farming, tapi **complement**. Jika pure arb shows frequent + stable opportunity → add as second strategy.

---

## 14. Anti-Patterns (What NOT to Do)

❌ **Assume UP+DOWN < $1 = profit**: Lupakan fee/slippage.  
❌ **Ignore execution risk**: Two-leg coordination bukan trivial.  
❌ **Use post-hoc opportunity**: Jika duration=1ms, impossible to fill.  
❌ **Skip simulation**: Langsung ke live.  
❌ **One-leg exposure without hedge**: Exposed directional = worse than no trade.  
❌ **Oversize**: Large notional + one-leg fail = large loss.

---

## 15. Roadmap Summary

```
Phase 1 (Pre-G1): Read-only detector + analysis
├── Implement domain/arbitrage.py (detect_lock_pair)
├── Implement backtest/arb_detector.py (replay_arb_detection)
├── Run on LATE split data
├── Generate G1 report: opportunity count, duration, edge, depth
└── Decision: LANJUT Phase 2 or STOP?

Phase 2 (Paper): Two-leg fill simulation
├── Paper OMS for two-leg coordination
├── Model latency/slippage/partial fill
├── Measure one-leg exposure frequency
└── Decision: LANJUT Phase 3 or STOP?

Phase 3 (Live Micro): Real execution
├── RiskManager veto for one-leg exposure
├── Two-leg atomic-ish submission
├── Cancel/hedge if partial
├── Max notional $1-$5 per lock
└── Decision: Scale or STOP?
```

---

## 16. Acceptance Criteria (Phase 1)

✅ Detector identifies opportunities where `sum_asks + fee + slippage < 1`  
✅ Detector rejects opportunities where `net_lock_edge < MIN_LOCK_EDGE`  
✅ Detector records: count, duration, edge, depth per opportunity  
✅ Detector does NOT call OMS / order API  
✅ Detector does NOT require private key / API key  
✅ Backtest report compares pure arb vs resolution farming metrics  
✅ G1 report includes: frequency, duration_median, theoretical_pnl, simulated_pnl  

---

## 17. References

- **Polymarket "100% winrate" strategies**: Intra-market arb, latency arb, cross-platform arb, resolution farming
- **Current strategy**: `docs/13-STRATEGY_PLAYBOOK.md` (#1 Fair-Value Taker)
- **Risk management**: `docs/06-RISK_MANAGEMENT.md` (RiskManager veto gates)
- **Fee model**: `src/btcbot/domain/fees.py` (ProportionalTakerFee ~7%)
- **Data recorder**: `src/btcbot/data/recorder.py` (book snapshots)

---

## 18. Conclusion

Pure intra-market arbitrage adalah **complement strategy** dengan risk profile berbeda dari resolution farming. Opportunity theoretically outcome-independent, tapi **execution risk is real**.

**Fase 1 goal**: Measurement, bukan execution. Kita butuh data untuk jawab:
- Seberapa sering opportunity muncul?
- Berapa lama bertahan?
- Depth cukup untuk size meaningful?
- Net edge after simulated execution risk still > 0?

**Safety first**: Read-only detector → paper simulation → micro live.

**No premature execution**: Jangan loncat ke live sebelum G1/G2 measurement selesai.

---

**NEXT STEP**: Implement PROMPT 1.7 (docs-only sekarang, code nanti jika G1 LANJUT).
