# PROGRESS TRACKER — 5min-btc-polymarket

> Update setiap PROMPT selesai. Status: ⬜ belum · 🟦 berjalan · ✅ selesai · ⛔ blocked · ⏭️ defer
>
> Mulai: `2026-06-25` | Target G3: `belum ditentukan`

## FASE 0 — Read-only (G0)

| Prompt | Status | Bukti ringkas |
|:--:|:--:|---|
| 0.1–0.8 + 0.7 sizing | ✅ | Recorder/resolver/Gamma/CLOB WS/Chainlink/CLI/sizing selesai |

> **G0: ✅ LULUS.** Readonly dataset terbaru: `orders=0`, `fills=0`.

## FASE 1 — Backtest (G1)

| Prompt | Status | Bukti ringkas |
|:--:|:--:|---|
| 1.1–1.6 directional | ✅ | ALL5/OLD4/NEW positif hingga latency 1000 ms |
| 1.7 pure-arb detector | ✅ | 462 episode; median 0 ms, max 2 ms |

> **Directional G1: ✅ LANJUT PAPER, BUKAN LIVE.** Kandidat `t=60`, `delta=50`, `min=0.96`, `max=0.99`, balance `500`; baseline 84 entry, 83W/1L, `+$7.40`.
>
> **Pure-arb execution: ⏭️ DEFER/STOP.** Depth dan `$504.46` hanya upper-bound proxy. Jangan membuat two-leg OMS.

## FASE 2 — Paper Trading (G2)

| Prompt | Tugas | Modul | Status | DoD | Tanggal | Bukti |
|:--:|---|---|:--:|:--:|---|---|
| 2.1 | Risk Manager | `risk/manager.py` | ✅ | ✅ | 2026-07-13 | 27/27 + Ruff/Black/mypy + 0/0 |
| 2.2 | Paper OMS | `exec/oms.py` | ✅ | ✅ | 2026-07-13 | 41/41 gabungan + quality + 0/0 |
| 2.3 | Paper runner/ledger | `app/paper.py` | ✅ | ✅ | 2026-07-13 | 46/46 gabungan + quality + 0/0 |
| T.1 | Telegram notifier push | `adapters/telegram.py` | ✅ | ✅ | 2026-07-13 | 55/55 gabungan + Ruff/Black/mypy + 0/0 |
| 2.4 | Reconciliation + alert | reconcile/alert | ⬜ | ⬜ | | Berikutnya |
| T.2 | Telegram read-only commands | `app/control.py` | ⬜ | ⬜ | | Setelah 2.4 |
| T.3 | Telegram pause/resume/kill | control + risk | ⬜ | ⬜ | | Sebelum Gate G2 |

### 2.1 Risk Manager

✅ caps/limits · rate limit · pause/resume · kill · circuit breakers · mismatch fatal · fail-closed.

### 2.2 Paper OMS

✅ paper-only · risk wajib · FOK/FAK · level-walk · competition · latency · idempotency · no live path.

### 2.3 Paper runner

✅ signal→strategy→sizing→risk→OMS · ✅ Decimal ledger · ✅ fee 7% · ✅ Gamma settlement · ✅ paper orders/fills/results/equity · ✅ unresolved fail-closed.

### T.1 Telegram notifier

✅ bounded async queue · ✅ producer non-blocking · ✅ retry · ✅ failures contained · ✅ critical priority over info when queue full · ✅ HTTP mocked/no network · ✅ timestamp UTC-aware · ✅ token tidak masuk event/log · ✅ belum ada command/control (sesuai scope T.1).

**Urutan berikut:** `2.4 → T.2 → T.3 → Gate G2`.

**GATE G2:** ⬜ ratusan ronde paper · ⬜ PnL konsisten backtest · ⬜ nol mismatch.

> **G2: 🟦 BERJALAN. Live tetap dilarang.**

## Telegram setup/gate

| Item | Status |
|---|:--:|
| Notifier T.1 | ✅ |
| BotFather/token baru | ⬜ |
| Chat ID + whitelist | ⬜ |
| Env VPS tanpa commit/log token | ⬜ |
| Read-only commands T.2 | ⬜ |
| Confirm kill 2 langkah T.3 | ⬜ |
| Telegram down tidak stop core | ✅ unit-tested |

## FASE 3 — Live Micro (G3)

| Prompt | Status | Catatan |
|:--:|:--:|---|
| 3.1 Signer | ⛔ | Dilarang sebelum G2 + approval |
| 3.2 Live OMS | ⛔ | Dilarang sebelum G2 |
| 3.3 Live gate | ⛔ | Belum boleh |
| 3.4 Monitoring | ⬜ | Setelah paper stabil |
| 3.5 Live runner | ⛔ | Jangan dibuat sekarang |

## Status Ringkas

```text
G0 readonly          ✅ LULUS
G1 directional       ✅ LANJUT PAPER
Pure-arb execution   ⏭️ DEFER
2.1 Risk             ✅
2.2 Paper OMS        ✅
2.3 Paper runner     ✅
T.1 Notifier         ✅
2.4 Reconciliation  ⬜ NEXT
T.2/T.3 Control      ⬜
G2                    🟦 belum lulus
G3 live               ⛔ dilarang
```

## Blocker/Risiko

| # | Deskripsi | Status |
|---|---|:--:|
| TD1 | Full repo baseline 5 test lama gagal + lint/mypy debt, bukan regresi 2.1–T.1 | 🟦 |
| WD1 | VPS ada file lokal/output analisis; jangan `git add .`, reset, clean | 🟦 |
| B1 | CLOB REST V2/EIP-712 belum verified | ⛔ live |
| B2b | Chainlink Data Streams belum ada | 🟦 |
| DATA1 | Recorder best price + aggregate depth | 🟦 |
| G1R1 | NEW split kecil; perlu ratusan ronde paper | 🟦 |
| SEC1 | Token Telegram/GitHub PAT lama pernah terpapar; revoke/rotate | 🟦 |

## Decision Log

| Tanggal | Keputusan | Bukti |
|---|---|---|
| 2026-07-12 | G1 directional lanjut paper | Positif lintas split sampai 1000 ms |
| 2026-07-12 | Pure-arb defer | Median 0 ms, max 2 ms |
| 2026-07-13 | 2.1 selesai | 27/27 + quality + 0/0 |
| 2026-07-13 | 2.2 selesai | 41/41 + quality + 0/0 |
| 2026-07-13 | 2.3 selesai | 46/46 + quality + 0/0 |
| 2026-07-13 | T.1 selesai | 55/55 + quality + 0/0 |
