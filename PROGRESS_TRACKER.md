# PROGRESS TRACKER — 5min-btc-polymarket

> Catatan status terbaru ada di `HANDOFF_2026-07-11.md`. File historis lengkap tetap tersedia di riwayat Git.

## Status Aktif

- Fase: Pre-G1 / Fase 1 backtest
- G1: **BLOCKED / REVISI RINGAN**
- Runtime: readonly soak, orders/fills harus 0/0
- Kandidat: `t_entry=60`, `delta=50`, `min_price=0.96`, `max_price=0.99`

## Time-Based Latency

- [x] Core selector tick/time
- [x] ReplayEngine entry/hedge/exit integration
- [x] Non-vacuous engine tests tervalidasi VPS: 58/58 lulus pada 2026-07-11
- [x] Time-latency sensitivity CLI ditambahkan: `backtest/time_latency_sensitivity.py`
- [ ] Test CLI baru diverifikasi di VPS
- [ ] Sensitivity 50/100/250/500/1000 ms dijalankan pada `analisis5.db`
- [ ] ALL/OLD/NEW split dibandingkan
- [ ] G1 final report dan keputusan LANJUT/REVISI/STOP

## Safety

Tetap readonly. Jangan masuk Phase 2, OMS, signer, private key, API key, atau live sebelum G1 resmi LANJUT.
