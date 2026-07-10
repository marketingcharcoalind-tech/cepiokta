# Pure Arbitrage Detector — Docs-Only Sync Summary (Task G6)

## Status: ✅ COMPLETE

**Commit**: `05d8683`  
**Date**: 2026-07-09  
**Scope**: Documentation synchronization ONLY (no code implementation, no execution)

---

## Summary

Docs-only synchronization untuk **Pure Intra-Market Arbitrage Detector** sebagai riset track tambahan di Fase 1. Ini adalah strategi lock-pair (beli UP+DOWN jika sum cost < $1) yang berbeda dari strategi directional saat ini.

**PENTING**: Ini BUKAN implementasi. Hanya dokumentasi rencana. Implementasi code nanti setelah G1 decision.

---

## Files Changed (12 files)

### 1. NEW FILE: `docs/15-PURE_ARBITRAGE_DETECTOR.md` (459 lines)

**Isi:**
- **Definisi**: Pure lock-pair arbitrage = beli UP+DOWN jika `ask_up + ask_down + fee + slippage < 1`
- **Perbedaan dari strategi saat ini**:
  - Current = directional (outcome risk)
  - Pure arb = two-leg lock (execution risk)
- **Phase Plan**:
  - Phase 1 (Pre-G1): Read-only detector + measurement
  - Phase 2: Paper simulation (two-leg fill)
  - Phase 3: Live micro (IF G1/G2 pass)
- **Data to record**: opportunity count, duration_ms, net_lock_edge, depth, reject_reason
- **G1 Metrics**: frequency, duration, theoretical PnL, simulated PnL, comparison vs directional
- **Safety gates**: Detector read-only, NO OMS, NO secrets
- **Anti-patterns**: Assume sum<1 = profit (ignore execution risk), one-leg exposure without hedge
- **Module specs**: `domain/arbitrage.py` (detect_lock_pair), `backtest/arb_detector.py` (replay)

### 2. `PROMPT_GUIDE.md` (+29 lines)

**Added**: PROMPT 1.7 — Pure Intra-Market Arbitrage Detector (READ-ONLY)
- Baca docs/15
- Implementasi nanti: detector read-only + CSV report
- Calculate `net_lock_edge = 1 - (ask_up + ask_down + fee_total + slippage_buffer)`
- TIDAK ADA OMS, TIDAK ADA order, TIDAK ADA signer
- DoD (future): report comparison pure arb vs resolution farming

### 3. `PROGRESS_TRACKER.md` (+3 lines)

**Added**:
- Task 1.7: `Pure arb detector read-only | docs/15 + backtest/arb_detector.py (future) | ⬜`
- Blocker ARB1: `Pure arb belum diukur | perlu detector read-only | planned | ⬜`
- Measurement row: `Pure arb detector (G1) | opportunity count, duration_ms, depth, net_lock_edge, simulated two-leg fill success`

### 4. `docs/13-STRATEGY_PLAYBOOK.md` (+42 lines)

**Added**: Strategy #2b — Pure Intra-Market Lock-Pair Arbitrage
- Definisi: beli BOTH UP dan DOWN jika total cost < $1
- Perbedaan dari #1 (directional): outcome-independent vs outcome risk
- Reality: Execution Risk > Outcome Risk
- Infrastruktur dibutuhkan: two-leg OMS, hedge plan, idempotency, RiskManager veto
- Status: PLANNED (measurement belum ada)
- Kapan pakai: SETELAH detector (Fase 1) buktikan opportunity frequent+stable

**Updated**: Progression path
- `[#1]` → `[#2 Delta-Hedge]` → `[#2b Pure Lock-Pair]` → `[#3 MM]`
- Note: #2b = parallel track, complement (bukan replacement)

### 5. `docs/09-TESTING_AND_BACKTESTING.md` (+43 lines)

**Restructured**: Section 9.4 split into 9.4.1 (Directional) and 9.4.2 (Pure Arb)

**Added**: 9.4.2 Pure Intra-Market Arbitrage Detector (Planned)
- **Phase 1 Metrics**: opportunity count, duration distribution, net_lock_edge distribution, depth distribution, theoretical PnL
- **Phase 2 Metrics**: two-leg success rate, one-leg exposure scenarios, simulated net PnL
- **Phase 3 Metrics**: actual fill success, one-leg stuck frequency, real PnL
- **Headline Metrics (G1)**: frequency, median duration, median edge, median depth, theoretical vs directional PnL
- **Ablation**: fee accuracy, slippage accuracy, latency impact, competition, one-leg failure rate

### 6. `docs/07-DATA_MODEL.md` (+103 lines)

**Added**: ADDENDUM — Pure Arbitrage Opportunity Fields (Planned)

**Schema**: Optional table `arb_opportunities`
- Fields: round_no, ts, token_up, token_down, ask_up, ask_down, depth_up, depth_down, sum_asks, fee_total, slippage_buffer, net_lock_edge, max_lock_size, duration_ms, valid, reject_reason, mode
- Indexes: round_no, ts, valid
- Reject reasons: net_edge_too_low, depth_insufficient, sum_asks_too_high, empty_book, latency_exceeded

**Domain Model**: `ArbOpportunity` dataclass

**Note**: NO migration sekarang. Table creation optional. CSV export alternative.

### 7. `docs/08-MODULE_SPECS.md` (+62 lines)

**Added**: 8.13b backtest/arb_detector.py — Pure Arbitrage Detector (PLANNED)

**Contract**:
```python
def detect_lock_pair(
    book_up: OrderBook,
    book_down: OrderBook,
    fee_model: FeeModel,
    slippage_buffer: Decimal,
    min_lock_edge: Decimal,
    min_depth: Decimal,
) -> ArbOpportunity | None
```

**Integration**:
```python
async def replay_arb_detection(
    store: Store,
    since: datetime,
    until: datetime,
    config: ArbDetectorConfig,
) -> ArbDetectionReport
```

**Dependencies**: domain/models, domain/fees. NO adapters/OMS/signing.

**DoD**: Detector identifies valid opportunities, rejects invalid, records metrics, NO execution.

### 8. `docs/10-ROADMAP.md` (+9 lines)

**Updated**: Phase 1 — Backtest / Replay (Gate G1)

**Added**: OPTIONAL Pure Arbitrage Detector (read-only)
- Tambahan riset track untuk mengukur lock-pair opportunities
- `backtest/arb_detector.py`: detect where `ask_up+ask_down+fee+slippage<1`
- Report: frequency, duration, net_lock_edge, depth, theoretical PnL
- Read-only measurement ONLY (no execution)
- BUKAN syarat untuk lulus G1, tapi complement analysis

**Updated DoD**: Laporan edge bersih untuk directional (#1). OPTIONAL: laporan comparison pure arb vs directional.

### 9. `docs/11-CONFIG_AND_SECRETS.md` (+59 lines)

**Added**: ADDENDUM (v1.4) — Env Pure Arbitrage Detector (PLANNED)

**Config**:
```dotenv
ARB_DETECTOR_ENABLED=false           # Default false (opt-in)
ARB_MAX_SUM_ASKS=0.99                # Max sum(ask_up, ask_down)
ARB_SLIPPAGE_BUFFER=0.002            # 0.2% slippage estimate
ARB_MIN_LOCK_EDGE=0.001              # Min 0.1% net edge
ARB_MIN_DEPTH=5                      # Min depth both sides
ARB_MAX_LOCK_SIZE=50                 # Safety cap per lock
```

**Settings Class**: `ArbDetectorSettings` with validation

**IMPORTANT**: Ini hanya rencana config. JANGAN ubah `.env` live sekarang.

### 10. `.kiro/specs/btc-bot/requirements.md` (+44 lines)

**Added**: Requirement 13 — Pure Intra-Market Arbitrage Detector (docs/15)

**User story**: Sebagai researcher, saya ingin mengukur frekuensi dan profitabilitas pure lock-pair arbitrage secara read-only.

**Acceptance Criteria**:
- **Phase 1**: Detector identifies opportunities, rejects invalid, records metrics, NO execution
- **Phase 2**: Simulates two-leg, tracks one-leg exposure
- **Phase 3**: Live execution (gated by G1/G2 + RiskManager)

**Safety Gates**: Detector read-only, no execution path in Phase 1

### 11. `.kiro/specs/btc-bot/design.md` (+107 lines)

**Added**: Component specs for Pure Arbitrage Detector (PLANNED)

**Components**:
- `domain/arbitrage.py`: Pure function `detect_lock_pair()` (NO I/O)
- `backtest/arb_detector.py`: Replay engine `replay_arb_detection()`

**Data Flow** (Phase 1):
```
book_snapshots (DB)
  → replay_arb_detection()
    → detect_lock_pair() per tick
      → ArbOpportunity (if valid)
        → metrics accumulation
          → ArbDetectionReport
            → CSV + summary
```

**Dependency Rule**: domain/arbitrage MUST NOT import adapters/OMS/risk/signing

### 12. `.kiro/specs/btc-bot/tasks.md` (+46 lines)

**Added**: Pure Intra-Market Arbitrage Detector tasks

**Task G6**: Pure arb detector docs/spec sync _(Req 13)_
- ✅ All 12 documentation files updated

**Task G7**: Pure arb detector implementation (Phase 1 read-only) — FUTURE
- domain/arbitrage.py, backtest/arb_detector.py, tests, CLI
- DoD: Detector works, NO execution, NO secrets

**Task G8**: Pure arb paper simulation (Phase 2) — FUTURE

**Task G9**: Pure arb live execution (Phase 3) — FUTURE

**Current Status**: G6 complete (docs-only). G7-G9 await G1 decision.

---

## Key Points Summary

### What Was Added

1. **Comprehensive documentation** (docs/15) explaining pure lock-pair arbitrage strategy
2. **Prompt for future implementation** (PROMPT 1.7) with clear scope
3. **Progress tracking** (task 1.7, blocker ARB1, measurement row)
4. **Strategy documentation** (strategy #2b in playbook)
5. **Testing metrics** (section 9.4.2 with all measurement criteria)
6. **Data model** (optional arb_opportunities table schema)
7. **Module specifications** (domain/arbitrage + backtest/arb_detector contracts)
8. **Roadmap integration** (optional item in Phase 1)
9. **Configuration plan** (ARB_DETECTOR_* env vars)
10. **Requirements** (Requirement 13 with acceptance criteria)
11. **Design specs** (component architecture + data flow)
12. **Task breakdown** (G6-G9 with clear phases)

### What Was NOT Done (Intentionally)

❌ **NO code implementation** — Pure documentation sync only  
❌ **NO changes to strategy.py** — Directional strategy unchanged  
❌ **NO changes to signal.py/sizing.py** — Existing logic untouched  
❌ **NO changes to .env** — Config plan only, no actual config changes  
❌ **NO database migration** — Schema documented but not created  
❌ **NO execution path** — No OMS, no orders, no signing  
❌ **NO secrets added** — Detector will be read-only measurement  
❌ **NO changes to live/paper/backtest runners** — Current behavior unchanged

---

## Safety Statement

### 1. NO Code Execution Path

✅ **Zero lines of executable trading code added**
- All changes are Markdown documentation files only
- No Python/TypeScript/any code that could execute orders
- No modifications to existing trading logic

### 2. NO Order Submission

✅ **No OMS/signing/order code touched**
- `exec/oms.py` — NOT modified
- `adapters/clob.py` — NOT modified
- Signing/EIP-712 — NOT touched
- Strategy decision logic — NOT changed

### 3. NO Secrets/Config Changes

✅ **No secrets exposed or config modified**
- `.env` file — NOT modified (only `.env.example` plan documented)
- No private keys — NOT added
- No API keys — NOT changed
- Database — NOT migrated (schema documented only)

### 4. NO Runtime Behavior Changes

✅ **Existing bot behavior completely unchanged**
- Readonly mode — Still readonly
- Backtest — Same behavior
- Paper trading — Same behavior
- All existing code paths — Untouched

### 5. Read-Only Future Implementation

✅ **When implemented, detector will be read-only**
- Phase 1 scope: measurement only
- No order placement in Phase 1
- No execution until Phase 3 (gated by G1/G2 + RiskManager)
- Pure function approach (domain layer, no I/O)

---

## Git Status

**Branch**: `main`  
**Commit**: `05d8683`  
**Pushed**: ✅ Yes (origin/main)

**Changes**:
- 12 files changed
- 1 new file created (`docs/15-PURE_ARBITRAGE_DETECTOR.md`)
- 11 files modified (all `.md` documentation)
- 1,096 insertions(+), 1 deletion(-)

**Verification**:
- ✅ `python -m py_compile` passed (no syntax errors)
- ✅ `git diff --stat` shows only documentation files
- ✅ No changes to `src/btcbot/**/*.py` (runtime code untouched)
- ✅ No changes to `.env` or database files

---

## Next Steps

### For User (OPTIONAL)

This is **purely informational documentation**. No action required unless you want to implement the detector.

**IF you want to implement pure arb detector (Phase 1) in the future:**

1. **Read docs/15** to understand the full spec
2. **Decide**: Is this worth pursuing? (depends on G1 directional strategy results)
3. **IF YES**: Follow PROMPT 1.7 in PROMPT_GUIDE.md
4. **Implementation scope**: 
   - Create `domain/arbitrage.py` (pure function)
   - Create `backtest/arb_detector.py` (replay engine)
   - Add tests
   - Run detector on recorded data
   - Generate G1 report comparing pure arb vs directional

**IF NO**: Simply ignore. Documentation exists for future reference. No impact on current work.

### For Agent (Future)

**WHEN user explicitly requests implementation**:
1. Read docs/15 first
2. Follow PROMPT 1.7 specification
3. Implement Phase 1 ONLY (read-only measurement)
4. NO Phase 2/3 until G1 decision + explicit approval
5. Maintain safety: read-only, no OMS, no secrets

---

## FAQ

**Q: Apakah ini mengubah bot saat ini?**  
A: TIDAK. Ini hanya dokumentasi. Bot tetap jalan seperti biasa.

**Q: Apakah ini menambahkan trading baru?**  
A: TIDAK. Tidak ada code trading ditambahkan. Hanya rencana di dokumentasi.

**Q: Apakah perlu dijalankan sekarang?**  
A: TIDAK. Ini optional future work. Tidak blocking G1 decision.

**Q: Kapan di-implement?**  
A: Nanti, SETELAH G1 directional strategy decision. Dan hanya jika user minta.

**Q: Apa bedanya dengan strategi saat ini?**  
A: Current = directional (pilih UP atau DOWN). Pure arb = beli BOTH (lock pair).

**Q: Kenapa tidak langsung implement?**  
A: Perlu measurement dulu untuk tahu apakah opportunity sering muncul dan profitable. Measurement = Phase 1 detector (read-only).

**Q: Apakah ini aman?**  
A: Ya. Docs-only, no code, no execution, no secrets, no config changes.

---

## Conclusion

✅ **Task G6 COMPLETE**: Documentation synchronization untuk pure intra-market arbitrage detector.

✅ **Scope verified**: Docs-only, no code execution, no trading changes.

✅ **Safety confirmed**: No execution path, no orders, no secrets.

✅ **Git pushed**: Commit `05d8683` on `origin/main`.

**Status**: Documentation complete. Implementation (G7) awaits future decision. No action required now.

---

**Delivered by**: Kiro AI  
**Date**: 2026-07-09  
**Task**: G6 (Pure Arb Docs/Spec Sync)  
**Result**: ✅ Complete
