# PROGRESS TRACKER — 5min-btc-polymarket

> Update file ini setiap menyelesaikan satu PROMPT (lihat `PROMPT_GUIDE.md`).
> Status: ⬜ belum · 🟦 sedang dikerjakan · ✅ selesai · ⛔ blocked · ⏭️ di-skip
>
> Mulai: `2026-06-25` | Target G3 (live micro): `belum ditentukan`

---

## 🔑 Prasyarat (sebelum Fase 0)

| # | Item | Status | Catatan |
|---|------|:------:|---------|
| P1 | AI coding agent siap | ✅ | Workflow agent + GitHub + verifikasi VPS aktif |
| P2 | Repo Git dibuat + blueprint disalin ke root | ✅ | `marketingcharcoalind-tech/cepiokta` |
| P3 | Python 3.11+ + virtualenv terpasang | ✅ | VPS Python 3.11, `source venv/bin/activate`; jangan `uv run` |
| P4 | PROMPT 0 kickoff + audit blueprint | ✅ | Handoff dan seluruh docs/spec telah diaudit ulang 2026-07-12 |
| P5 | Wallet Polygon + USDC.e + RPC untuk live | ⬜ | Jangan disiapkan sebelum Fase 3 disetujui |

---

## ▶️ Cara baca tabel

`Prompt` = nomor di `PROMPT_GUIDE.md` · `Modul` = file target · `DoD` = Definition of Done tervalidasi.

---

## 🟢 FASE 0 — Scaffolding & Read-only Data (Gate G0)

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 0.1 | Setup repo & tooling | pyproject, CI, Makefile | ✅ | ✅ | 2026-06-25 | Python 3.11, ruff, Black, mypy strict, pytest, GH Actions |
| 0.2 | Settings, MODE gating, secrets | config/settings.py, .env.example | ✅ | ✅ | 2026-06-25 | Default readonly; `assert_live_ok()`; secret tidak di source |
| 0.3 | Clock adapter | adapters/clock.py | ✅ | ✅ | 2026-06-25 | SystemClock + SimClock; UTC-aware; deterministik |
| 0.4 | Gamma adapter | adapters/gamma.py | ✅ | ✅ | 2026-06-26 | Slug/window/fee live terverifikasi; resolver `closed=true` |
| 0.5 | CLOB WebSocket market data | adapters/clob_ws.py | ✅ | ✅ | 2026-06-26 | `/ws/market`; LIST/DICT; heartbeat; reconnect; stale detection |
| 0.6 | Chainlink price feed | adapters/chainlink.py | ✅ | ✅ | 2026-06-26 | Data Feed read-only + RPC failover; Data Streams masih blocker basis-risk |
| 0.7 | Store + Recorder + Resolver | data/store.py, recorder.py, resolver.py | ✅ | ✅ | 2026-06-26 | SQLite, retensi LVCF, Gamma outcome, snapshot aman |
| 0.8 | CLI boot + runner readonly | app/cli.py | ✅ | ✅ | 2026-06-27 | Soak readonly nyata; supervisor/retry; tidak ada order |
| 0.7+ | Sizing + paper config | exec/sizing.py, config | ✅ | ✅ | 2026-06-27 | Fractional Kelly + cap bankroll/notional/depth |

**GATE G0:** ✅ recorder/resolver/discovery/Chainlink/CLOB WS berjalan · ✅ mode readonly · ✅ orders/fills `0/0`.

> Status G0: **✅ LULUS** | Bukti terbaru safety: `orders=0`, `fills=0` pada 2026-07-13.

---

## 🟡 FASE 1 — Backtest / Replay (Gate G1)

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 1.1 | Interval loader | domain/market.py | ✅ | ✅ | 2026-06-27 | Pure, clock injectable, UTC-aware |
| 1.2 | Signal engine + fee | domain/signal.py, domain/fees.py | ✅ | ✅ | 2026-06-27 | `p_win`, net edge, `crypto_fees_v2` 7% |
| 1.3 | Strategy entry/hedge/exit | domain/strategy.py | ✅ | ✅ | 2026-06-27 | Never-fade, price band, edge gate |
| 1.4 | Sizing Kelly + caps | exec/sizing.py | ✅ | ✅ | 2026-06-27 | Decimal; notional/bankroll/depth caps |
| 1.5 | Replay + fill model | backtest/replay.py | ✅ | ✅ | 2026-07-11 | Fee, slippage, competition, Gamma settlement, tick/time latency |
| 1.6 | Reporting, diagnostics, sensitivity | backtest/report.py + diagnostics | ✅ | ✅ | 2026-07-12 | Streaming memory-safe; ALL5/OLD4/NEW; 50–1000 ms |
| 1.7 | Pure-arb detector read-only | domain/arbitrage.py, backtest/arb_detector.py | ✅ | ✅ | 2026-07-12 | Measurement selesai; execution **DEFER/STOP** |

### GATE G1 directional

- Net edge positif stabil lintas parameter: ✅ Ya
- Stabil lintas beberapa hari dan split: ✅ Ya, ALL5 + OLD4 + NEW
- Reliability/calibration tooling dan label Gamma: ✅ Selesai
- Edge bertahan setelah fee, slippage, dan latency: ✅ Ya, hingga 1000 ms

> **Hasil G1 directional: ✅ LANJUT KE PHASE 2 PAPER, BUKAN LIVE**
>
> Kandidat: `t_entry=60`, `delta_threshold=50`, `min_price=0.96`, `max_price=0.99`, `starting_balance=500`.
>
> ALL5 net PnL latency 50–1000 ms: `+$6.48` sampai `+$8.32`; baseline tick-1 `+$7.40`, 84 entry, 83W/1L.
>
> Risiko: stale book sekitar 40%, no-future attempts pada latency tinggi, full-depth belum direkam, hanya satu loss tail-risk.

### Prompt 1.7 pure-arb

> **✅ Implementasi dan pengukuran selesai · ⏭️ execution di-skip/defer**
>
> 1.618 ronde, 462 episode, LVCF duration p25/median/p75 `0/0/0 ms`, max `2 ms`.
> Depth dan theoretical PnL `$504.46` hanya upper-bound proxy, bukan profit executable.
> Jangan membuat two-leg OMS sebelum tersedia data full-depth dan duration realistis.

---

## 🟠 FASE 2 — Paper Trading (Gate G2)

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 2.1 | Risk Manager | risk/manager.py | ✅ | ✅ | 2026-07-13 | 27/27 VPS; Ruff, Black, mypy hijau; Decimal + UTC; orders/fills 0/0 |
| 2.2 | OMS mode paper | exec/oms.py | ⬜ | ⬜ | | Berikutnya; simulasi saja, tidak ada API/order nyata |
| 2.3 | Paper runner + ledger | app/paper.py | ⬜ | ⬜ | | Realtime paper, latency time-based, PnL/equity |
| 2.4 | Reconciliation + alert | reconcile + alert | ⬜ | ⬜ | | Mismatch harus freeze + alert |

**Prompt 2.1 mencakup:** ✅ max notional/round · ✅ max exposure · ✅ daily loss · ✅ consecutive loss · ✅ min balance · ✅ rolling rate limit · ✅ pause/resume · ✅ manual/automatic kill · ✅ WSS/stale/clock/spread/liquidity/latency breakers · ✅ reconciliation mismatch fatal · ✅ fail-closed validation.

**GATE G2:** ⬜ ratusan ronde paper · ⬜ PnL konsisten dengan backtest · ⬜ nol reconciliation mismatch.

> Status G2: **🟦 DIMULAI, 1/4 prompt selesai** | Live tetap dilarang.

---

## 🔴 FASE 3 — Live Micro-stakes (Gate G3) ⚠️ UANG NYATA

### Checklist API sebelum mulai

- ⬜ Base URL dan versi CLOB V2 terbaru
- ⬜ Skema EIP-712 order V2
- ✅ Nama channel WSS dan format pesan
- 🟦 Chainlink Data Feed tersedia, tetapi Data Streams belum
- ✅ Fee `crypto_fees_v2`, tick size, min order size discovery
- ⬜ Restriksi geografis/kepatuhan akun

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 3.1 | Signer EIP-712 + auth | adapters/clob.py, Signer | ⛔ | ⬜ | | Dilarang sebelum G2 lulus dan approval eksplisit |
| 3.2 | OMS live | exec/oms.py | ⛔ | ⬜ | | Dilarang sebelum G2 |
| 3.3 | Limit konservatif + live gate | risk/config | ⛔ | ⬜ | | `LIVE_CONFIRMED=yes` belum boleh |
| 3.4 | Monitoring & alerting | metrics/alert | ⬜ | ⬜ | | Setelah paper observability stabil |
| 3.5 | Live runner | app/live.py | ⛔ | ⬜ | | Tidak boleh dibuat sekarang |

> Status G3: **⛔ BELUM BOLEH**.

---

## 🟣 FASE 4 — Hardening & Scale (Gate G4)

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 4.1 | Ketahanan & deploy | Docker/systemd/failover | ⬜ | ⬜ | | |
| 4.2 | Tuning berbasis data live + ADR | docs/adr | ⬜ | ⬜ | | |
| 4.3 | Scale-up bersyarat | risk limits | ⬜ | ⬜ | | Hanya jika PnL live positif dan stabil |

---

## 📊 Status Ringkas

```text
Fase 0 [##########] 9/9  G0: ✅ LULUS
Fase 1 [##########] 7/7  G1 directional: ✅ LANJUT KE PAPER
Pure-arb                         : ✅ diukur, ⏭️ execution defer
Fase 2 [##        ] 1/4  G2: 🟦 dimulai
Fase 3 [          ] 0/5  G3: ⛔ belum boleh
Fase 4 [          ] 0/3  G4: ⬜ belum
```

---

## ⛔ Blockers / Risiko Aktif

| # | Deskripsi | Dampak | Rencana | Status |
|---|-----------|--------|---------|:------:|
| TD1 | Full repo baseline belum hijau: 5 test lama gagal, lint/mypy debt lama | Tidak berasal dari Prompt 2.1; perlu cleanup terpisah sebelum gate proyek berikutnya | Perbaiki test delta API lama, timestamp fixture invalid, lalu quality debt bertahap | 🟦 |
| WD1 | VPS working tree punya modifikasi lokal `loss_diagnostics.py` dan banyak output analisis | Risiko tertimpa jika cleanup sembarangan | Jangan `git add .`, jangan reset/clean; audit file per file | 🟦 |
| B1 | CLOB REST V2 + EIP-712 belum diverifikasi | Blokir live | Kerjakan hanya setelah G2 + approval | ⛔ |
| B2b | Chainlink Data Streams belum ada | Basis-risk akhir-window | Riset adapter sebelum live | 🟦 |
| DATA1 | Recorder hanya best price + aggregate side depth | Full-depth/slippage dan pure-arb tidak executable-grade | Paper ukur fill failure; pertimbangkan full-depth terpisah | 🟦 |
| G1R1 | NEW split hanya 349 ronde dan 10–16 entry | Sampel out-of-sample masih kecil | Validasi ratusan ronde paper | 🟦 |
| G1R2 | Satu loss dan book warning banyak false positive | Tail-risk/exit rule belum matang | Ukur hedge/exit di paper, jangan ubah strategy dulu | 🟦 |
| SEC1 | Token Telegram/GitHub PAT lama pernah terpapar | Risiko kredensial | Revoke/rotate jika belum | 🟦 |

---

## 📱 TELEGRAM CONTROL PLANE (cross-cutting — docs/12)

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| T.1 | Notifier Telegram push | adapters/telegram.py | ⬜ | ⬜ | | Kerjakan setelah 2.3 agar event paper/PnL nyata tersedia |
| T.2 | Perintah/tombol read-only | app/control.py + handler | ⬜ | ⬜ | | Setelah paper ledger/status stabil |
| T.3 | pause/resume/kill | control + risk | ⬜ | ⬜ | | Setelah 2.4, wajib sebelum G2/live |

**Urutan rekomendasi:** `2.2 → 2.3 → T.1 → 2.4 → T.2 → T.3 → Gate G2`.

### Setup Telegram sebelum T.1

- ⬜ Buat bot via BotFather
- ⬜ Dapatkan chat ID dan whitelist
- ⬜ Isi env Telegram di VPS tanpa commit/log token

### Gate Telegram

- ⬜ Whitelist berfungsi
- ⬜ KILL/pause konfirmasi dua langkah
- ⬜ Telegram down tidak menghentikan core paper/trading
- ⬜ Token tidak pernah ter-log

---

## 📊 Notifikasi P&L & Error (bagian T.1/T.2)

- ⬜ Notifikasi menang/kalah paper
- ⬜ Milestone profit dan equity high
- ⬜ Consecutive loss, drawdown, daily-loss warning
- ⬜ Ringkasan sesi/harian
- ⬜ ACTION REQUIRED + remediation
- ⬜ Dedup anti-spam; loss/error bypass mute
- ⬜ Test event dan error mapping

---

## 🧠 STRATEGI

| Prompt | Tugas | Status | DoD ✔ | Catatan |
|:------:|-------|:------:|:-----:|---------|
| S.1 | Fair-value taker + kalibrasi | ✅ | ✅ | G1 directional lanjut ke paper |
| S.2 | Delta-hedge arb | ⏭️ | ⬜ | Setelah strategi #1 terbukti live |
| S.2b | Pure lock-pair arb | ⏭️ | ✅ measurement | Execution defer: median 0 ms, max 2 ms |
| S.3 | Market making | ⏭️ | ⬜ | Fase lanjut, butuh latency rendah |

---

## 🌐 MULTI-MARKET (Fase 4)

| Prompt | Tugas | Status | DoD ✔ | Catatan |
|:------:|-------|:------:|:-----:|---------|
| M.1 | Market registry + config | ⬜ | ⬜ | |
| M.2 | Scanner + per-market worker | ⬜ | ⬜ | |
| M.3 | Risk multi-market/korelasi | ⬜ | ⬜ | |
| M.4 | Rollout bertahap | ⬜ | ⬜ | BTC 5m wajib pertama |

Aktivasi: 🟦 BTC 5m paper · ⬜ BTC 15m · ⬜ ETH 5m · ⬜ ETH 15m · ⬜ SOL 5m · ⬜ SOL 15m.

---

## 🔬 Hasil Pengukuran Edge

| Sumber | Net PnL | ROI | Win-rate | Catatan |
|--------|---------|-----|----------|---------|
| Backtest baseline tick-1 | +$7.40 | +1.48% | 98.8% (83/84) | Kandidat directional |
| Backtest time latency 50–1000 ms | +$6.48 s.d. +$8.32 | +1.30% s.d. +1.66% | Positif ALL5/OLD4/NEW | G1 lanjut paper |
| Pure-arb | Bukan profit executable | N/A | N/A | `$504.46` upper-bound proxy; execution defer |
| Paper G2 | | | | Belum dimulai |
| Live G3 | | | | Dilarang saat ini |

---

## 🧠 Decision Log ringkas

| Tanggal | Keputusan | Alasan |
|---------|-----------|--------|
| 2026-06-26 | Fee taker 7% wajib di seluruh edge/PnL | `crypto_fees_v2` terverifikasi |
| 2026-07-11 | Ganti evaluasi latency tick menjadi time-based sensitivity | Satu event tick bukan durasi stabil |
| 2026-07-12 | G1 directional LANJUT ke paper | Positif ALL5/OLD4/NEW hingga latency 1000 ms |
| 2026-07-12 | Pure-arb execution DEFER/STOP | Median duration 0 ms, max 2 ms; depth/PnL proxy |
| 2026-07-13 | Prompt 2.1 Risk Manager selesai | 27/27 test VPS + Ruff + Black + mypy; orders/fills 0/0 |
