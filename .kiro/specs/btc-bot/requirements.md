# Requirements — btc-bot (Kiro Spec)

## Pengantar
Bot trading otomatis untuk market Polymarket "BTC Up/Down 5 menit". Strategi:
end-of-window settlement arbitrage. Prioritas: infrastruktur untuk MENGUKUR edge
secara jujur, dengan alur aman readonly → backtest → paper → live micro-stakes.
(Lihat blueprint lengkap di /docs.)

## Requirement 1 — Read-only Market Data
**User story:** Sebagai operator, saya ingin bot merekam data market BTC 5m
realtime tanpa trading, agar saya punya dataset untuk analisa & backtest.
- WHEN bot start in MODE=readonly THEN sistem TIDAK mengirim order apa pun.
- WHEN ronde baru terbentuk THEN sistem mencatat round_no, token_id UP/DOWN,
  window_start/end, start_price (Chainlink).
- WHILE window aktif THE sistem merekam orderbook (best bid/ask, depth) & harga.
- IF WSS putus THEN sistem reconnect dgn backoff DAN menandai data sebagai gap.
- IF tidak ada update > STALE_MS THEN sistem set status stale.

## Requirement 2 — Signal & Edge Estimation
- WHEN time_left ≤ T_ENTRY_SEC THEN sistem menghitung Δ, p_win, ask_win, net_edge.
- net_edge HARUS = p_win − ask_win − fee − expected_slippage.
- IF data harga stale THEN signal TIDAK dianggap valid.

## Requirement 3 — Strategy Decision
- WHEN net_edge ≥ MIN_EDGE DAN MIN_PRICE ≤ ask ≤ MAX_PRICE DAN |Δ| ≥ threshold
  THEN sistem mengusulkan EnterOrder pada sisi leader.
- WHEN p_win < P_EXIT ATAU book flip ≥ FLIP_RATIO THEN sistem mengusulkan
  Hedge/Exit.
- Strategi TIDAK PERNAH fade (melawan arah leader) dan TIDAK beli > MAX_PRICE.

## Requirement 4 — Backtest
- Sistem dapat memutar ulang data terekam dengan fill model (slippage, latensi,
  kompetisi) DAN melaporkan net PnL, kalibrasi p_win, drawdown, sensitivitas.
- IF edge ≤ 0 setelah biaya THEN laporan menyatakannya jelas (hasil valid).

## Requirement 5 — Paper Trading
- WHEN MODE=paper THEN order disimulasikan realtime tanpa uang nyata, dengan
  ledger PnL & equity_curve, dan Risk Manager tetap aktif.

## Requirement 6 — Risk Management
- Setiap order WAJIB lewat Risk Manager yang bisa VETO.
- Sistem menegakkan MAX_NOTIONAL_ROUND, MAX_OPEN_EXPOSURE, MAX_DAILY_LOSS,
  MAX_CONSEC_LOSSES, MIN_BALANCE, rate limit.
- Kill-switch (manual & otomatis) DAN circuit breaker (WSS down/stale/clock drift)
  HARUS ada sebelum live.

## Requirement 7 — Live Execution (gated)
- WHEN MODE=live THEN sistem MENOLAK start kecuali LIVE_CONFIRMED=yes.
- Order ditandatangani EIP-712 skema CLOB V2; idempotency client-order-id.
- Notional awal sangat kecil ($1–$5/ronde).

## Requirement 8 — Observability & Reconciliation
- Sistem mencatat audit log tiap keputusan & order (JSON terstruktur).
- Setelah settle, sistem merekonsiliasi order↔fill↔posisi↔resolusi↔saldo;
  IF mismatch THEN freeze + alert.
- Alert (Telegram/Discord) untuk kill-switch, circuit breaker, drawdown, mismatch.

## Requirement 9 — Secrets & Config
- Tidak ada secret di source/Git. Config tervalidasi; MODE default readonly.

## Non-Functional
- Numerik uang/harga pakai Decimal; waktu UTC; NTP sync.
- Deterministik untuk test (clock & RNG injectable).
- Latensi rendah (host dekat infra Polymarket).



---

## Requirement 10 — Telegram Control Plane (lihat docs/12)
**User story:** Sebagai operator, saya ingin memantau & mengontrol bot dari
Telegram (dengan tombol & notifikasi) saat di-deploy di VPS, tanpa membuka terminal.
- WHEN event penting terjadi (boot, hasil ronde, hedge, kill, circuit breaker,
  drawdown, mismatch, error, heartbeat) THEN sistem mengirim notifikasi Telegram.
- WHEN pengguna mengirim /status /pnl /positions /recent THEN sistem membalas data terkini.
- WHEN pengguna mengirim /pause /resume /kill THEN sistem meminta konfirmasi
  2-langkah DAN menjalankan aksi via RiskManager DAN mencatat audit log.
- IF chat_id pengirim TIDAK ada di TELEGRAM_ALLOWED_CHAT_IDS THEN perintah diabaikan + di-log.
- IF Telegram tidak tersedia/lambat THEN core trading TETAP berjalan (best-effort,
  notifikasi via queue, tidak memblok).
- TELEGRAM_BOT_TOKEN diperlakukan sebagai secret (tidak di source/log).



---

## Requirement 11 — Strategi (docs/13)
- Sistem memakai fair-value engine; strategi #1 (taker) WAJIB; #2/#3 opsional &
  hanya diaktifkan setelah level bawah profit live. Edge wajib terkalibrasi.

## Requirement 12 — Multi-Market (docs/14)
- Sistem mendukung BTC/ETH/SOL × {5m,15m} via config (MarketSpec), dijalankan
  per-market worker dgn shared RiskManager.
- IF market belum lulus validasi edge+likuiditas THEN tidak diaktifkan live.
- Risk WAJIB menegakkan limit per-market, per-asset, korelasi (BTC/ETH/SOL satu
  faktor), dan global. Default hanya BTC 5m enabled.
