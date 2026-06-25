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
- Terapkan **slippage**: isi menelusuri level book, bukan semua di best ask.
- Tambah **latensi**: keputusan pakai book `t`, fill pakai book `t+latency`
  (book bisa sudah bergerak → simulasikan adverse selection).
- Asumsikan **kompetisi**: opsi konservatif = hanya dapat fill jika ada surplus
  size di atas yang "diambil bot lain".

## 9.4 Metrik Wajib Dilaporkan
- Net PnL, ROI, jumlah ronde, win-rate aktual.
- **Kalibrasi**: bucket `p_win` vs realized hit-rate (reliability curve).
- Distribusi `net_edge` saat entry; berapa % ronde lulus filter.
- Max drawdown, varians PnL, Sharpe-like ratio.
- Sensitivitas grid: `T_ENTRY_SEC` × `DELTA_THRESHOLD` × `MAX_PRICE`.
- **Ablation**: PnL dengan vs tanpa fee, vs tanpa slippage, vs tanpa latensi.
  (Untuk lihat apakah edge hilang setelah biaya — biasanya iya.)

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
