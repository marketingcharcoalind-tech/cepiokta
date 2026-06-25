# 10 — Roadmap & Milestones

> Kerjakan berurutan. Jangan menulis kode fase N+1 sebelum fase N punya test
> yang lulus. Tiap fase punya Definition of Done (DoD).

## Fase 0 — Scaffolding & Read-only Data  (Gate G0)
- [ ] Setup repo: struktur `src/btcbot/...`, tooling (uv/poetry, ruff, mypy, pytest, pre-commit, CI).
- [ ] Config via pydantic-settings; MODE default `readonly`; secrets via env.
- [ ] `adapters/clock.py` (System+Sim).
- [ ] `adapters/gamma.py`: discovery market BTC 5m (round, token_id, window, start_price).
- [ ] `adapters/clob_ws.py`: stream orderbook (reconnect, heartbeat, stale).
- [ ] `adapters/chainlink.py`: price_now + start_price.
- [ ] `data/store.py` + `data/recorder.py`: rekam book_snapshots, signals, rounds, resolusi.
- [ ] `app/cli.py`: boot sequence + mode readonly (hanya log, TANPA order).
- **DoD**: bot jalan berhari-hari merekam data; nol order; test adapter (mock) hijau.

## Fase 1 — Backtest / Replay  (Gate G1)
- [ ] `domain/market.py`, `domain/signal.py` (p_win, net_edge), `domain/strategy.py`, `exec/sizing.py`.
- [ ] `backtest/replay.py` dengan fill model (slippage, latensi, kompetisi).
- [ ] Laporan metrik + kalibrasi + sensitivitas (docs/09).
- **DoD**: laporan edge bersih jujur. Keputusan lanjut/stop berdasarkan data.
  *(Jika edge ≤ 0 setelah biaya: berhenti / revisi strategi — itu hasil yang valid.)*

## Fase 2 — Paper Trading  (Gate G2)
- [ ] `exec/oms.py` mode paper (simulasi fill realtime), `risk/manager.py` aktif.
- [ ] `app/paper.py`: loop realtime, ledger PnL, equity_curve, log ala referensi.
- [ ] Reconciliation simulasi; alerting dasar.
- **DoD**: ratusan ronde paper; PnL konsisten dgn backtest; nol mismatch.

## Fase 3 — Live Micro-stakes  (Gate G3)
- [ ] `Signer` EIP-712 (CLOB V2) + auth REST; `oms.py` mode live.
- [ ] Risk limits super-konservatif; `LIVE_CONFIRMED=yes`; kill-switch + circuit breaker teruji.
- [ ] Monitoring penuh (Prometheus/Grafana) + alert (Telegram/Discord).
- [ ] `app/live.py`: notional $1–$5/ronde, daily-loss kecil.
- **DoD**: live berjalan aman; PnL live tercatat & direkonsiliasi; tidak ada
  insiden risk yang lolos.

## Fase 4 — Hardening & Scale  (Gate G4)
- [ ] Tuning parameter dari data live; ADR untuk perubahan.
- [ ] Ketahanan: failover RPC/WSS, deploy Docker+systemd, backup DB.
- [ ] Naikkan ukuran HANYA jika PnL live positif & stabil.
- **DoD**: sistem produksi observability-lengkap; scaling berbasis bukti.

## Catatan untuk AI Agent
- Tulis ADR di `docs/adr/` saat menyimpang.
- Jangan pernah loncat ke Fase 3 tanpa G0–G2 lulus.



---

## ADDENDUM (v1.1) — Telegram Control Plane (cross-cutting)
Bersifat lintas-fase; bangun bertahap (lihat PROMPT_GUIDE `PROMPT T.*`):
- **Fase 0/2 — Notifikasi (T.1):** boot + hasil ronde + event risk dikirim ke
  Telegram (Notifier, read-only). Aman ditambahkan sejak awal.
- **Fase 2 — Perintah read-only (T.2):** `/status /pnl /positions /recent` + tombol.
- **Fase 2/3 — Aksi kontrol (T.3):** `/pause /resume /kill` + whitelist + konfirmasi
  2-langkah; `kill` terhubung ke RiskManager. Wajib sebelum/seiring live (Fase 3).
- **DoD:** Telegram down tidak memengaruhi trading; perintah hanya dari whitelist.
