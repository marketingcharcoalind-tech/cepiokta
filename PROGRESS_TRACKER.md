# PROGRESS TRACKER — 5min-btc-polymarket

> Update file ini setiap menyelesaikan satu PROMPT (lihat `PROMPT_GUIDE.md`).
> Status: ⬜ belum · 🟦 sedang dikerjakan · ✅ selesai · ⛔ blocked · ⏭️ di-skip
>
> Mulai: `2026-06-25` | Target G3: `belum ditentukan`

---

## 🔑 Prasyarat

| # | Item | Status | Catatan |
|---|------|:------:|---------|
| P1 | AI coding agent siap | ✅ | GitHub + verifikasi output VPS |
| P2 | Repo + blueprint | ✅ | `marketingcharcoalind-tech/cepiokta` |
| P3 | Python 3.11 virtualenv | ✅ | VPS: `source venv/bin/activate`, jangan `uv run` |
| P4 | Kickoff/audit blueprint | ✅ | Handoff, docs, specs, kode dan commit diaudit 2026-07-12 |
| P5 | Wallet/USDC/RPC live | ⬜ | Jangan disiapkan sebelum Fase 3 disetujui |

---

## 🟢 FASE 0 — Scaffolding & Read-only (G0)

| Prompt | Tugas | Modul | Status | DoD | Tanggal | Bukti |
|:--:|---|---|:--:|:--:|---|---|
| 0.1 | Tooling/CI | pyproject, CI | ✅ | ✅ | 2026-06-25 | pytest, Ruff, Black, mypy |
| 0.2 | Settings/MODE/secrets | config/settings.py | ✅ | ✅ | 2026-06-25 | Default readonly, live gate |
| 0.3 | Clock | adapters/clock.py | ✅ | ✅ | 2026-06-25 | UTC-aware, injectable |
| 0.4 | Gamma | adapters/gamma.py | ✅ | ✅ | 2026-06-26 | Slug/window/fee/resolution |
| 0.5 | CLOB WS market | adapters/clob_ws.py | ✅ | ✅ | 2026-06-26 | `/ws/market`, reconnect, stale |
| 0.6 | Chainlink | adapters/chainlink.py | ✅ | ✅ | 2026-06-26 | Data Feed + RPC failover |
| 0.7 | Store/recorder/resolver | data/* | ✅ | ✅ | 2026-06-26 | SQLite, Gamma outcome, LVCF |
| 0.8 | Readonly runner | app/cli.py | ✅ | ✅ | 2026-06-27 | Soak nyata, no orders |
| 0.7+ | Sizing/paper config | exec/sizing.py | ✅ | ✅ | 2026-06-27 | Kelly + caps |

> **G0: ✅ LULUS.** Bukti safety terbaru 2026-07-13: `orders=0`, `fills=0`.

---

## 🟡 FASE 1 — Backtest / Replay (G1)

| Prompt | Tugas | Modul | Status | DoD | Tanggal | Bukti |
|:--:|---|---|:--:|:--:|---|---|
| 1.1 | Interval loader | domain/market.py | ✅ | ✅ | 2026-06-27 | Pure, UTC |
| 1.2 | Signal + fee | domain/signal.py, fees.py | ✅ | ✅ | 2026-06-27 | net edge, fee 7% |
| 1.3 | Strategy | domain/strategy.py | ✅ | ✅ | 2026-06-27 | never-fade, entry/hedge/exit |
| 1.4 | Sizing | exec/sizing.py | ✅ | ✅ | 2026-06-27 | Decimal + caps |
| 1.5 | Replay/fill | backtest/replay.py | ✅ | ✅ | 2026-07-11 | fee, slippage, competition, time latency |
| 1.6 | Report/diagnostics | backtest/* | ✅ | ✅ | 2026-07-12 | ALL5/OLD4/NEW, 50–1000 ms |
| 1.7 | Pure-arb detector | arbitrage.py, arb_detector.py | ✅ | ✅ | 2026-07-12 | Measurement complete; execution defer |

### G1 directional

- ✅ Positif lintas ALL5, OLD4, NEW.
- ✅ Positif setelah fee/slippage dan latency 50–1000 ms.
- ✅ Kandidat: `t_entry=60`, `delta=50`, `min_price=0.96`, `max_price=0.99`, balance `500`.
- Baseline tick-1: 84 entry, 83W/1L, `+$7.40`, ROI `+1.48%`.
- Time latency ALL5: `+$6.48` sampai `+$8.32`.

> **G1 directional: ✅ LANJUT KE PAPER, BUKAN LIVE.**

### Pure-arb

- ✅ 1.618 ronde, 462 episode.
- LVCF duration p25/median/p75 `0/0/0 ms`, max `2 ms`.
- `$504.46` dan depth adalah upper-bound proxy, bukan executable profit.

> **Pure-arb execution: ⏭️ DEFER/STOP.** Jangan membuat two-leg OMS.

---

## 🟠 FASE 2 — Paper Trading (G2)

| Prompt | Tugas | Modul | Status | DoD | Tanggal | Bukti |
|:--:|---|---|:--:|:--:|---|---|
| 2.1 | Risk Manager | risk/manager.py | ✅ | ✅ | 2026-07-13 | 27/27 VPS; Ruff/Black/mypy; 0/0 |
| 2.2 | Paper OMS | exec/oms.py | ✅ | ✅ | 2026-07-13 | 41/41 gabungan risk+OMS; Ruff/Black/mypy; 0/0 |
| 2.3 | Paper runner + ledger | app/paper.py | ⬜ | ⬜ | | Berikutnya |
| 2.4 | Reconciliation + alert | reconcile + alert | ⬜ | ⬜ | | Mismatch freeze |

### Prompt 2.1 selesai

✅ notional/round · exposure · daily loss · loss streak · min balance · rolling rate limit · pause/resume · kill · WSS/stale/clock/spread/liquidity/latency breaker · mismatch fatal · fail-closed.

### Prompt 2.2 selesai

✅ MODE harus paper · ✅ setiap submit lewat Risk Manager · ✅ FOK all-or-nothing · ✅ FAK partial · ✅ BUY asks/SELL bids level-walk · ✅ price limit · ✅ competition fraction · ✅ configurable latency · ✅ idempotent client ID · ✅ UTC-aware · ✅ GTC ditolak · ✅ tidak ada CLOB REST/signer/secret/live path.

**GATE G2:** ⬜ ratusan ronde paper · ⬜ PnL konsisten backtest · ⬜ nol mismatch.

> **G2: 🟦 DIMULAI, 2/4 prompt selesai. Live tetap dilarang.**

---

## 🔴 FASE 3 — Live Micro (G3) ⚠️ UANG NYATA

| Prompt | Tugas | Status | Catatan |
|:--:|---|:--:|---|
| 3.1 | Signer EIP-712/auth | ⛔ | Dilarang sebelum G2 + approval |
| 3.2 | OMS live | ⛔ | Dilarang sebelum G2 |
| 3.3 | Live limits/gate | ⛔ | `LIVE_CONFIRMED` belum boleh |
| 3.4 | Monitoring | ⬜ | Setelah paper stabil |
| 3.5 | Live runner | ⛔ | Jangan dibuat sekarang |

Checklist: ⬜ CLOB REST V2 · ⬜ EIP-712 V2 · ✅ WSS market · 🟦 Chainlink Data Streams · ✅ fee/tick/min size · ⬜ compliance.

> **G3: ⛔ BELUM BOLEH.**

---

## 🟣 FASE 4 — Hardening & Scale

| Prompt | Tugas | Status |
|:--:|---|:--:|
| 4.1 | Deploy/failover | ⬜ |
| 4.2 | Tuning + ADR | ⬜ |
| 4.3 | Scale bersyarat | ⬜ |

---

## 📊 Status Ringkas

```text
Fase 0 [##########] 9/9  G0: ✅ LULUS
Fase 1 [##########] 7/7  G1 directional: ✅ LANJUT PAPER
Pure-arb                         : ✅ diukur, ⏭️ execution defer
Fase 2 [#####     ] 2/4  G2: 🟦 dimulai
Fase 3 [          ] 0/5  G3: ⛔ belum boleh
Fase 4 [          ] 0/3  G4: ⬜ belum
```

---

## ⛔ Blocker / Risiko Aktif

| # | Deskripsi | Dampak/Rencana | Status |
|---|---|---|:--:|
| TD1 | Full repo baseline: 5 test lama gagal + lint/mypy debt | Cleanup terpisah sebelum gate proyek; bukan regresi 2.1/2.2 | 🟦 |
| WD1 | VPS punya modifikasi lokal dan output analisis | Jangan `git add .`, reset, atau clean | 🟦 |
| B1 | CLOB REST V2/EIP-712 belum verified | Blokir live | ⛔ |
| B2b | Chainlink Data Streams belum ada | Basis-risk sebelum live | 🟦 |
| DATA1 | Recorder best price + aggregate depth | Paper ukur fill; full-depth terpisah | 🟦 |
| G1R1 | NEW split kecil | Validasi ratusan ronde paper | 🟦 |
| G1R2 | Satu loss, warning false positive | Ukur hedge/exit paper | 🟦 |
| SEC1 | Telegram token/GitHub PAT lama pernah terpapar | Revoke/rotate jika belum | 🟦 |

---

## 📱 TELEGRAM CONTROL PLANE

| Prompt | Tugas | Status | Urutan |
|:--:|---|:--:|---|
| T.1 | Notifier push | ⬜ | Setelah 2.3 |
| T.2 | Command/tombol read-only | ⬜ | Setelah ledger/status stabil |
| T.3 | pause/resume/kill | ⬜ | Setelah 2.4, sebelum Gate G2 |

**Urutan:** `2.3 → T.1 → 2.4 → T.2 → T.3 → Gate G2`.

Setup: ⬜ BotFather · ⬜ chat ID/whitelist · ⬜ env VPS tanpa commit/log token.
Gate: ⬜ whitelist · ⬜ confirm 2 langkah · ⬜ Telegram down tidak stop core · ⬜ token tidak ter-log.

---

## 📊 Notifikasi P&L/Error

⬜ win/loss · ⬜ milestone/equity high · ⬜ streak/drawdown/daily loss · ⬜ summary · ⬜ ACTION REQUIRED/remediation · ⬜ dedup/mute bypass · ⬜ tests.

---

## 🧠 Strategi dan Multi-market

| Item | Status | Catatan |
|---|:--:|---|
| S.1 Fair-value taker | ✅ | G1 lanjut paper |
| S.2 Delta hedge | ⏭️ | Setelah #1 live terbukti |
| S.2b Pure lock pair | ⏭️ | Measurement selesai, execution defer |
| S.3 Market making | ⏭️ | Butuh latency rendah |
| M.1–M.4 Multi-market | ⬜ | Fase 4, BTC 5m dahulu |

Aktivasi: 🟦 BTC 5m paper · ⬜ BTC 15m · ⬜ ETH 5m · ⬜ ETH 15m · ⬜ SOL 5m · ⬜ SOL 15m.

---

## 🧠 Decision Log

| Tanggal | Keputusan | Alasan |
|---|---|---|
| 2026-06-26 | Fee 7% wajib | `crypto_fees_v2` verified |
| 2026-07-11 | Time-based latency | Event tick bukan durasi stabil |
| 2026-07-12 | G1 directional lanjut paper | Positif lintas split hingga 1000 ms |
| 2026-07-12 | Pure-arb execution defer | Median 0 ms, max 2 ms |
| 2026-07-13 | Prompt 2.1 selesai | 27/27 + quality gate + 0/0 |
| 2026-07-13 | Prompt 2.2 selesai | 41/41 + quality gate + 0/0; paper-only |
