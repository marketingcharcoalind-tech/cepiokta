# 09 — Testing & Backtesting

## 9.1 Filosofi
Tujuan utama testing di proyek ini bukan sekadar "kode jalan", tapi **menjawab:
apakah edge nyata ada?** Kode bisa benar 100% dan strategi tetap rugi. Pisahkan
"benar secara teknis" (unit test) dari "menguntungkan" (backtest/paper).

## 9.2 Piramida Test
- **Unit** (mayoritas): domain murni — `market.py`, `signal.py`, `strategy.py`,
  `sizing.py`, `risk/manager.py`. Deterministik (SimClock, seed tetap).
- **Integrasi**: adapters dengan server/WSS di-mock (`respx`, fake WS). Uji
  reconnect, stale detection, idempotency, rate-limit/backoff.
- **Replay/backtest**: end-to-end pada data terekam fase 0.
- **Paper (live-sim)**: realtime, tanpa uang nyata.

## 9.3 Backtest Harness (backtest/replay.py)
Input: `book_snapshots` + `signals` + `rounds` (hasil recorder fase 0).
Proses tiap ronde dengan SimClock → SignalEngine → Strategy → Sizer →
PaperOMS(fill model) → catat `round_results`.

**Fill model realistis (kritikal):**
- Order taker FOK/FAK hanya terisi jika `ask ≤ harga order` DAN depth cukup.
- **Fee taker ~7% (terverifikasi, `crypto_fees_v2`)**: kurangi dari tiap fill;
  PnL settlement net-of-fee. Asumsi zero-fee SALAH.
- Terapkan **slippage**: isi menelusuri level book, bukan semua di best ask.
- Tambah **latensi**: keputusan pakai book `t`, fill pakai book `t+latency`
  (book bisa sudah bergerak → simulasikan adverse selection).
- Asumsikan **kompetisi**: opsi konservatif = hanya dapat fill jika ada surplus
  size di atas yang "diambil bot lain".
- **Label resolusi = Gamma** (`outcomePrices`, lihat docs/07 §7.3.2): UP/DOWN dari
  index bernilai `"1"`, bukan asumsi `Δ≥0` semata.

## 9.4 Metrik Wajib Dilaporkan

### 9.4.1 Strategi Directional (#1 Fair-Value Taker)
- Net PnL, ROI, jumlah ronde, win-rate aktual.
- **Kalibrasi**: bucket `p_win` vs realized hit-rate (reliability curve).
- Distribusi `net_edge` saat entry; berapa % ronde lulus filter.
- Max drawdown, varians PnL, Sharpe-like ratio.
- Sensitivitas grid: `T_ENTRY_SEC` × `DELTA_THRESHOLD` × `MAX_PRICE`.
- **Ablation**: PnL dengan vs tanpa fee, vs tanpa slippage, vs tanpa latensi.
  Fee taker **~7%** WAJIB disertakan; headline **Net PnL setelah fee**.
  (Untuk lihat apakah edge hilang setelah biaya — biasanya iya.)

### 9.4.2 Pure Intra-Market Arbitrage Detector (Planned — docs/15)
**Phase 1: Read-Only Measurement**
- **Opportunity count**: total opportunities detected per day
- **Valid vs rejected**: breakdown by reject_reason (net_lock_edge too low, depth insufficient, sum_asks >= 1)
- **Duration distribution**: median, p25, p75, max (ms)
  - Buckets: <10ms, 10-100ms, 100ms-1s, >1s
  - Short duration (<100ms) may be impossible to fill
- **Net lock edge distribution**: `1 - (ask_up + ask_down + fee_total + slippage_buffer)`
  - Histogram by 0.1% buckets
  - Median, mean, max
- **Depth distribution**: `max_lock_size = min(depth_up, depth_down)`
  - Histogram: <5, 5-10, 10-50, 50-100, >100 contracts
  - Percentage with size >= min thresholds
- **Theoretical locked PnL**: `sum(net_lock_edge * min(max_lock_size, position_limit))`

**Phase 2: Simulated Two-Leg Fill (Future)**
- **Two-leg success rate**: assuming N% success (90%, 95%, 99%)
- **One-leg exposure scenarios**: frequency, loss impact
- **Simulated net PnL**: after execution risk penalty
- **Comparison**: pure arb PnL vs directional strategy PnL

**Phase 3: Live Execution (Future)**
- Actual two-leg fill success rate
- One-leg stuck frequency & recovery
- Real net PnL after fees + execution failures

**Headline Metrics (G1 Report):**
- Opportunity frequency (per day)
- Median duration (can we fill in time?)
- Median net_lock_edge (profit per lock)
- Median max_lock_size (capital required)
- Theoretical PnL vs directional PnL
- Estimated PnL after execution risk

**Ablation for Pure Arb:**
- Fee estimate accuracy (actual vs estimated)
- Slippage estimate accuracy
- Latency impact on two-leg coordination
- Competition (how many opportunities disappear before we can fill?)
- One-leg fill failure rate simulation

## 9.5 Kriteria Lulus untuk Naik Fase
- Fase 1→2: backtest menunjukkan `net_edge > 0` yang **stabil** di beberapa
  rentang parameter & beberapa hari data berbeda (bukan overfit satu hari).
- Fase 2→3: paper trading ratusan ronde, PnL konsisten dengan backtest,
  tidak ada bug reconciliation.
- Fase 3 scale: PnL **live** (bukan paper) positif setelah biaya nyata.

## 9.6 Hygiene
- Determinisme: clock & RNG injectable; seed dicatat.
- Data versioned (simpan dataset backtest + hash).
- No flaky tests; CI hijau wajib sebelum merge.
- Property-based test untuk sizing/risk (mis. size tak pernah > cap; risk tak
  pernah meloloskan order > limit).
