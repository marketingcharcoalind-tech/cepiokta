# PROGRESS TRACKER — 5min-btc-polymarket

> Update setiap PROMPT selesai. Status: ⬜ belum · 🟦 berjalan · ✅ selesai · ⛔ blocked · ⏭️ defer/skip
>
> Mulai: `2026-06-25` | Target G3: `belum ditentukan`

## Prasyarat

| Item | Status | Catatan |
|---|:--:|---|
| Agent + GitHub + verifikasi VPS | ✅ | Semua klaim test harus dibuktikan output VPS |
| Python 3.11 virtualenv | ✅ | `source venv/bin/activate`; jangan `uv run` |
| Audit handoff/docs/spec/kode | ✅ | Selesai 2026-07-12 |
| Wallet/USDC/API/private key live | ⬜ | Jangan disiapkan sebelum G2 + approval |

## FASE 0 — Read-only (G0)

| Prompt | Tugas | Status | Tanggal | Bukti |
|:--:|---|:--:|---|---|
| 0.1 | Tooling/CI | ✅ | 2026-06-25 | pytest, Ruff, Black, mypy |
| 0.2 | Settings/MODE/secrets | ✅ | 2026-06-25 | Default readonly + live gate |
| 0.3 | Clock | ✅ | 2026-06-25 | UTC-aware, injectable |
| 0.4 | Gamma | ✅ | 2026-06-26 | Slug/window/fee/resolution |
| 0.5 | CLOB WS market | ✅ | 2026-06-26 | `/ws/market`, reconnect/stale |
| 0.6 | Chainlink | ✅ | 2026-06-26 | Data Feed + RPC failover |
| 0.7 | Store/recorder/resolver | ✅ | 2026-06-26 | SQLite, Gamma outcome, LVCF |
| 0.8 | Readonly runner | ✅ | 2026-06-27 | Soak nyata, no orders |
| 0.7+ | Sizing/paper config | ✅ | 2026-06-27 | Kelly + caps |

> **G0: ✅ LULUS.** Bukti safety terbaru 2026-07-13: readonly dataset `orders=0`, `fills=0`.

## FASE 1 — Backtest (G1)

| Prompt | Tugas | Status | Tanggal | Bukti |
|:--:|---|:--:|---|---|
| 1.1 | Interval loader | ✅ | 2026-06-27 | Pure + UTC |
| 1.2 | Signal + fee | ✅ | 2026-06-27 | Net edge, fee 7% |
| 1.3 | Strategy | ✅ | 2026-06-27 | Never-fade, entry/hedge/exit |
| 1.4 | Sizing | ✅ | 2026-06-27 | Decimal + caps |
| 1.5 | Replay/fill | ✅ | 2026-07-11 | Fee/slippage/competition/time latency |
| 1.6 | Report/diagnostics | ✅ | 2026-07-12 | ALL5/OLD4/NEW, 50–1000 ms |
| 1.7 | Pure-arb detector | ✅ | 2026-07-12 | Measurement selesai |

**Directional G1: ✅ LANJUT PAPER, BUKAN LIVE.** Kandidat `t_entry=60`, `delta=50`, `min_price=0.96`, `max_price=0.99`, balance `500`. Baseline 84 entry, 83W/1L, `+$7.40`; tetap positif pada ALL5/OLD4/NEW hingga 1000 ms.

**Pure-arb execution: ⏭️ DEFER/STOP.** 462 episode, median 0 ms, max 2 ms; depth dan `$504.46` hanya upper-bound proxy. Jangan membuat two-leg OMS.

## FASE 2 — Paper Trading (G2)

| Prompt | Tugas | Modul | Status | DoD | Tanggal | Bukti |
|:--:|---|---|:--:|:--:|---|---|
| 2.1 | Risk Manager | `risk/manager.py` | ✅ | ✅ | 2026-07-13 | 27/27 VPS + Ruff/Black/mypy + 0/0 |
| 2.2 | Paper OMS | `exec/oms.py` | ✅ | ✅ | 2026-07-13 | 41/41 gabungan + quality gates + 0/0 |
| 2.3 | Paper runner + ledger | `app/paper.py` | ✅ | ✅ | 2026-07-13 | 46/46 gabungan + Ruff/Black/mypy + 0/0 |
| T.1 | Telegram notifier push | `adapters/telegram.py` | ⬜ | ⬜ | | Berikutnya sesuai urutan |
| 2.4 | Reconciliation + alert | reconcile/alert | ⬜ | ⬜ | | Mismatch harus freeze |
| T.2 | Telegram read-only commands | `app/control.py` | ⬜ | ⬜ | | Setelah ledger/status stabil |
| T.3 | Telegram pause/resume/kill | control + risk | ⬜ | ⬜ | | Setelah 2.4, sebelum Gate G2 |

### Prompt 2.1

✅ hard limits · rolling rate limit · pause/resume · kill · breakers · reconciliation mismatch fatal · fail-closed.

### Prompt 2.2

✅ paper-only mode · RiskManager wajib · FOK/FAK · slippage level-walk · competition · latency · idempotency · UTC · no CLOB REST/signer/secret/live.

### Prompt 2.3

✅ signal → strategy → sizing → risk → Paper OMS · ✅ paper orders/fills persistence · ✅ Decimal ledger · ✅ fee 7% net-of-fee · ✅ entry/position/settlement Gamma · ✅ win/loss PnL · ✅ `round_results` + `equity_curve` mode paper · ✅ unresolved round fail-closed · ✅ readonly dataset tetap 0/0.

**GATE G2:** ⬜ ratusan ronde paper · ⬜ PnL konsisten backtest · ⬜ nol mismatch.

> **G2: 🟦 DIMULAI. Core 3/4 prompt selesai; Telegram cross-cutting belum. Live tetap dilarang.**

## TELEGRAM CONTROL PLANE

Urutan: `T.1 → 2.4 → T.2 → T.3 → Gate G2`.

| Item | Status |
|---|:--:|
| BotFather/token baru | ⬜ |
| Chat ID + whitelist | ⬜ |
| Env VPS tanpa commit/log token | ⬜ |
| Whitelist test | ⬜ |
| Confirm kill 2 langkah | ⬜ |
| Telegram down tidak stop core | ⬜ |
| P&L/error/drawdown notifications | ⬜ |

## FASE 3 — Live Micro (G3)

| Prompt | Status | Catatan |
|:--:|:--:|---|
| 3.1 Signer EIP-712 | ⛔ | Dilarang sebelum G2 + approval |
| 3.2 OMS live | ⛔ | Dilarang sebelum G2 |
| 3.3 Live gate/limits | ⛔ | `LIVE_CONFIRMED` belum boleh |
| 3.4 Monitoring | ⬜ | Setelah paper stabil |
| 3.5 Live runner | ⛔ | Jangan dibuat sekarang |

## Status Ringkas

```text
Fase 0 [##########] 9/9  G0: ✅ LULUS
Fase 1 [##########] 7/7  G1 directional: ✅ LANJUT PAPER
Pure-arb                         : ✅ diukur, ⏭️ execution defer
Fase 2 [########  ] 3/4  core: 2.1–2.3 ✅, T.1/2.4/T.2/T.3 tersisa
Fase 3 [          ] 0/5  G3: ⛔ belum boleh
Fase 4 [          ] 0/3  G4: ⬜ belum
```

## Blocker / Risiko Aktif

| # | Deskripsi | Status |
|---|---|:--:|
| TD1 | Full repo baseline masih punya 5 test lama gagal + lint/mypy debt, bukan regresi 2.1–2.3 | 🟦 |
| WD1 | VPS punya modifikasi lokal dan output analisis; jangan `git add .`, reset, atau clean | 🟦 |
| B1 | CLOB REST V2/EIP-712 belum verified, blokir live | ⛔ |
| B2b | Chainlink Data Streams belum ada | 🟦 |
| DATA1 | Recorder hanya best price + aggregate depth | 🟦 |
| G1R1 | NEW split kecil, perlu ratusan ronde paper | 🟦 |
| G1R2 | Satu loss; exit warning false positive | 🟦 |
| SEC1 | Telegram token/GitHub PAT lama pernah terpapar, revoke/rotate jika belum | 🟦 |

## Decision Log

| Tanggal | Keputusan | Alasan |
|---|---|---|
| 2026-06-26 | Fee 7% wajib | `crypto_fees_v2` verified |
| 2026-07-11 | Time-based latency | Event tick bukan durasi stabil |
| 2026-07-12 | G1 directional lanjut paper | Positif lintas split hingga 1000 ms |
| 2026-07-12 | Pure-arb execution defer | Median 0 ms, max 2 ms |
| 2026-07-13 | Prompt 2.1 selesai | 27/27 + quality + 0/0 |
| 2026-07-13 | Prompt 2.2 selesai | 41/41 + quality + 0/0 |
| 2026-07-13 | Prompt 2.3 selesai | 46/46 + quality + 0/0 |
