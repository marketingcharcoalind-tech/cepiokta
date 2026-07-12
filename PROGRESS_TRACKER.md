# PROGRESS TRACKER — 5min-btc-polymarket

> Update setiap PROMPT selesai. Status: ⬜ belum · 🟦 berjalan · ✅ selesai · ⛔ blocked · ⏭️ defer

## Gate utama

| Gate | Status | Bukti |
|---|:--:|---|
| G0 readonly | ✅ | Recorder/resolver/data live; readonly orders/fills 0/0 |
| G1 directional | ✅ LANJUT PAPER | ALL5/OLD4/NEW positif hingga 1000 ms; baseline 84 entry, 83W/1L, +$7.40 |
| Pure-arb execution | ⏭️ | 462 episode, median 0 ms, max 2 ms; proxy bukan executable profit |
| G2 paper | 🟦 | Infrastruktur selesai; ratusan ronde paper dan zero mismatch belum dibuktikan |
| G3 live | ⛔ | Dilarang sebelum G2 + approval eksplisit |

## Fase 2 dan Telegram

| Prompt | Tugas | Status | DoD | Tanggal | Bukti |
|:--:|---|:--:|:--:|---|---|
| 2.1 | Risk Manager | ✅ | ✅ | 2026-07-13 | 27/27 + quality + 0/0 |
| 2.2 | Paper OMS | ✅ | ✅ | 2026-07-13 | 41/41 gabungan + quality + 0/0 |
| 2.3 | Paper runner/ledger | ✅ | ✅ | 2026-07-13 | 46/46 gabungan + quality + 0/0 |
| T.1 | Telegram notifier | ✅ | ✅ | 2026-07-13 | 55/55 gabungan + quality + 0/0 |
| 2.4 | Reconciliation + alert | ✅ | ✅ | 2026-07-13 | 61/61 gabungan + quality + 0/0 |
| T.2 | Read-only commands/buttons | ✅ | ✅ | 2026-07-13 | 72/72 gabungan; whitelist + safe config; quality + 0/0 |
| T.3 | pause/resume/kill | ✅ | ✅ | 2026-07-13 | 80/80 gabungan; 2-step + expiry + anti-replay + audit; quality + 0/0 |

## Scope tervalidasi

- **2.1:** limits, rate limit, pause/resume, kill, breakers, mismatch fatal.
- **2.2:** paper-only, risk wajib, FOK/FAK, level-walk, latency, competition, idempotency.
- **2.3:** signal→strategy→sizing→risk→OMS, Decimal ledger, fee 7%, Gamma settlement, paper persistence.
- **T.1:** non-blocking notifier, retry, failure isolation, critical priority, no token in event/log.
- **2.4:** order→fill→position→Gamma settlement→PnL→balance; mismatch freeze + critical alert.
- **T.2:** whitelist; `/status /balance /pnl /positions /recent /config`; safe buttons; explicit config allowlist; no secret/control action.
- **T.3:** `/pause /resume /kill`; whitelist; confirmation two-step; one-time token; 60s expiry; cancel; anti-replay; audit; same RiskManager; cannot change mode/limits.

## Gate G2 yang masih harus dibuktikan

- ⬜ Wire runtime paper realtime ke adapters live data secara operasional.
- ⬜ Setup Telegram baru via BotFather + chat ID whitelist tanpa membocorkan token.
- ⬜ Jalankan paper selama ratusan ronde.
- ⬜ Bandingkan PnL paper dengan backtest kandidat.
- ⬜ Buktikan zero reconciliation mismatch.
- ⬜ Audit stale book, fill failure, hedge/exit, drawdown, dan no-future behavior.

> **G2 BELUM LULUS.** Selesainya kode bukan bukti strategi paper sudah tervalidasi.

## Telegram setup/gate

| Item | Status |
|---|:--:|
| T.1 notifier | ✅ |
| T.2 read-only commands | ✅ |
| T.3 confirmed controls | ✅ |
| Telegram down tidak stop core | ✅ unit-tested |
| BotFather token baru | ⬜ |
| Chat ID + whitelist | ⬜ |
| Env VPS tanpa commit/log token | ⬜ |
| Tes integrasi nyata kirim pesan | ⬜ |

## Safety dan blocker

| # | Item | Status |
|---|---|:--:|
| Readonly dataset orders/fills | ✅ 0/0 |
| Live signer/order/API/private key | ⛔ dilarang |
| Full repo baseline 5 test lama + lint/mypy debt | 🟦 bukan regresi Fase 2 |
| VPS local files/output | 🟦 jangan `git add .`, reset, clean |
| Chainlink Data Streams | 🟦 sebelum live |
| Full-depth book | 🟦 aggregate depth saja |
| Telegram/GitHub token lama | 🟦 revoke/rotate |

## Status ringkas

```text
G0 readonly          ✅ LULUS
G1 directional       ✅ LANJUT PAPER
Pure-arb execution   ⏭️ DEFER
2.1 Risk             ✅
2.2 Paper OMS        ✅
2.3 Paper runner     ✅
T.1 Notifier         ✅
2.4 Reconciliation  ✅
T.2 Read-only        ✅
T.3 Controls         ✅
G2 paper validation  🟦 NEXT: operational wiring + ratusan ronde
G3 live               ⛔
```

## Decision log

| Tanggal | Keputusan |
|---|---|
| 2026-07-12 | Directional G1 lanjut paper; pure-arb execution defer |
| 2026-07-13 | 2.1 selesai: 27/27 + quality + 0/0 |
| 2026-07-13 | 2.2 selesai: 41/41 + quality + 0/0 |
| 2026-07-13 | 2.3 selesai: 46/46 + quality + 0/0 |
| 2026-07-13 | T.1 selesai: 55/55 + quality + 0/0 |
| 2026-07-13 | 2.4 selesai: 61/61 + quality + 0/0 |
| 2026-07-13 | T.2 selesai: 72/72 + quality + 0/0 |
| 2026-07-13 | T.3 selesai: 80/80 + quality + 0/0 |
