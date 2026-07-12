# PROGRESS TRACKER - 5min-btc-polymarket

> Status: `⬜ belum` · `🟦 aktif/pending validation` · `✅ selesai` · `⛔ blocked` · `⏭️ defer/skip`
>
> Posisi diperbarui: **2026-07-13 Asia/Bangkok**  
> Sumber posisi terbaru: `HANDOFF_2026-07-13.md` + output VPS tervalidasi.  
> **Live tetap FORBIDDEN.**

---

## Status Eksekutif

```text
G0 readonly                    ✅ LULUS
G1 directional                 ✅ LANJUT PAPER
Pure-arb execution             ⏭️ DEFER/STOP
Phase 2 infrastructure         ✅ code/focused tests
Shared operational runtime     ✅ code/focused tests
Telegram control/notifier      ✅ code/UI/real-send smoke
Bounded network smoke OFF      ✅ LULUS
Full-round market loop         ✅ 517 ticks
Gamma settlement smoke         ⛔ resolution timeout
Operational reconciliation     🟦 wired/tested, real smoke pending settlement
Paper order/fill path          🟦 belum terjadi pada real smoke
G2 paper soak                  ⬜ belum dimulai
G3 live                        ⛔ FORBIDDEN
```

---

## Prasyarat

| # | Item | Status | Catatan |
|---|---|:---:|---|
| P1 | Repo dan coding workflow siap | ✅ | GitHub `main`, VPS `~/cepiokta` |
| P2 | Python 3.11 virtualenv siap | ✅ | VPS: `source venv/bin/activate` |
| P3 | Tooling pytest/Ruff/Black/mypy | ✅ | focused gates berulang kali hijau |
| P4 | Dokumen dan handoff dibaca/audit | ✅ | handoff 2026-07-13 diperbarui |
| P5 | Wallet/private CLOB credentials | ⏭️ | sengaja kosong sampai live gate; jangan diisi |

---

## FASE 0 - Scaffolding & Read-only Data (G0)

| Prompt | Tugas | Status | DoD | Tanggal | Catatan |
|---|---|:---:|:---:|---|---|
| 0.1 | Repo/tooling/CI | ✅ | ✅ | 2026-06-25 | Python, pytest, Ruff, Black, mypy |
| 0.2 | Settings/MODE/secrets | ✅ | ✅ | 2026-06-25 | default readonly, live double gate |
| 0.3 | Clock adapter | ✅ | ✅ | 2026-06-25 | SystemClock + SimClock UTC-aware |
| 0.4 | Gamma discovery | ✅ | ✅ | 2026-06-25 | slug/window/fee parsing tervalidasi |
| 0.5 | CLOB market WebSocket | ✅ | ✅ | 2026-06-25 | `/ws/market`, snapshot/change, reconnect |
| 0.6 | Chainlink price source | ✅ | ✅ | 2026-06-25 | Data Feed + RPC failover |
| 0.7 | Store + recorder + resolver | ✅ | ✅ | 2026-06-26 | SQLite, resolution Gamma, retention |
| 0.8 | Readonly CLI/runner | ✅ | ✅ | 2026-06-27 | supervised resilient loop |

**G0 result:** `analisis5.db` berulang kali diverifikasi `orders=0`, `fills=0`. Tidak berubah selama Phase 2 dan semua smoke terbaru.

---

## FASE 1 - Backtest / Replay (G1)

| Prompt | Tugas | Status | DoD | Tanggal | Catatan |
|---|---|:---:|:---:|---|---|
| 1.1 | Interval loader | ✅ | ✅ | 2026-06-27 | pure/deterministic |
| 1.2 | Signal engine | ✅ | ✅ | 2026-06-27 | p_win/net edge, fee 7% |
| 1.3 | Strategy | ✅ | ✅ | 2026-06-27 | entry/hedge/exit, never-fade |
| 1.4 | Sizing | ✅ | ✅ | 2026-06-27 | fractional Kelly + caps |
| 1.5 | Replay/fill model | ✅ | ✅ | 2026-06-27 | fee/slippage/latency/competition |
| 1.6 | Reporting/calibration | ✅ | ✅ | 2026-06-27 | metrics, splits, ablation, diagnostics |
| 1.7 | Pure-arb detector measurement | ✅ | ✅ | 2026-07-12 | read-only measurement selesai |
| 1.7 execution | Two-leg pure-arb execution | ⏭️ | ⏭️ | 2026-07-12 | STOP: median 0 ms, max 2 ms |

### G1 Directional Decision

Kandidat:

```text
t_entry=60
delta_threshold=50
min_price=0.96
max_price=0.99
starting_balance=500
```

Bukti:

```text
Baseline tick-1: 84 entries, 83W/1L, Net PnL +$7.40, ROI +1.48%
ALL5 latency: 50ms +7.70, 100ms +8.32, 250ms +7.88,
                500ms +7.40, 1000ms +6.48
Positive on ALL5, OLD4, NEW
```

**G1 result:** ✅ LANJUT ke PAPER, bukan live.

### Pure-Arb Result

```text
462 episodes
LVCF duration p25/median/p75/max = 0/0/0/2 ms
Depth/PnL = upper-bound proxy
```

**Decision:** ⏭️ execution DEFER/STOP. Jangan membuat two-leg OMS.

---

## FASE 2 - Paper Trading Infrastructure

| Prompt | Tugas | Modul utama | Status | DoD | Tanggal | Bukti/Catatan |
|---|---|---|:---:|:---:|---|---|
| 2.1 | Risk Manager | `risk/manager.py` | ✅ | ✅ focused | 2026-07-13 | hard limits, pause/resume, kill, breakers, fail-closed |
| 2.2 | Paper OMS | `exec/oms.py` | ✅ | ✅ focused | 2026-07-13 | paper-only, FOK/FAK, partial, level-walk, idempotent |
| 2.3 | Paper runner + ledger | `app/paper.py` | ✅ | ✅ focused | 2026-07-13 | Decimal, fee 7%, Gamma settlement, persistence |
| 2.4 | Reconciliation + alert | `app/reconcile.py` | ✅ | ✅ focused | 2026-07-13 | mismatch freezes/kills + critical event |
| 2.OP1 | Shared composition root | `app/paper_runtime.py` | ✅ | ✅ focused | 2026-07-13 | one RiskManager/ledger/control state |
| 2.OP2 | P&L/error notification policy | `app/paper_notifications.py` | ✅ | ✅ focused | 2026-07-13 | win/loss/milestone/DD/errors/dedup |
| 2.OP3 | Notified runtime lifecycle | `app/paper_notification_runtime.py` | ✅ | ✅ focused | 2026-07-13 | Telegram transport queue start/stop |
| 2.OP4 | Real-adapter operational loop | `app/operational_paper.py` | ✅ | ✅ focused | 2026-07-13 | Gamma + CLOB WS + Chainlink + signal |
| 2.OP5 | Bounded safe entrypoint | `app/operational_paper_entrypoint.py` | ✅ | ✅ focused | 2026-07-13 | private creds rejected; execution default OFF |
| 2.OP6 | Triple opt-in full-round execution | entrypoint | ✅ | ✅ focused | 2026-07-13 | flag + `PAPER_ONLY` + `--full-round`, start lag <=2s |
| 2.OP7 | Operational settlement reconciliation | operational loop | ✅ | ✅ code/test | 2026-07-13 | auto snapshot/reconcile after settlement |
| 2.OP8 | Real Gamma settlement smoke | smoke DB | ⛔ | ⬜ | 2026-07-13 | timeout after ~180s; no result/reconcile |
| 2.OP9 | Real paper order/fill smoke | smoke DB | 🟦 | ⬜ | 2026-07-13 | 0 order/0 fill; no natural entry that round |
| 2.OP10 | Restart/idempotency recovery | operational service | ⬜ | ⬜ | | in-memory sequence not enough for restart |
| 2.OP11 | Unified supervised service | market + Telegram polling | ⬜ | ⬜ | | shared tasks/graceful shutdown pending |
| 2.OP12 | Multi-round short smoke | background bounded | ⬜ | ⬜ | | only after settlement blocker fixed |

### Focused VPS Gates

```text
Shared runtime:                    39 tests passed + Ruff/Black/mypy
P&L/error addendum:                20 tests passed + real Telegram send
Operational market/reconciliation: 19 tests passed + Ruff/Black/mypy
Full-round safety/reconcile scope:  22 tests passed + Ruff/Black/mypy
Readonly DB always:                orders=0, fills=0
```

---

## Real Smoke Evidence

### Bounded Network Smoke, Execution OFF

```text
SMOKE round=1783882800 ticks=3 settled=False reason=smoke_limit execution=disabled
paper.db orders=0
paper.db fills=0
paper.db signals=3
analisis5.db orders=0 fills=0
process stopped cleanly
```

**Status:** ✅ Gamma/Chainlink/CLOB WS/signal persistence pipeline works.

### Full-Round Paper Smoke #1

DB terpisah: `paper.db.smoke1`

```text
SMOKE round=1783884000 ticks=517 settled=False
reason=resolution_timeout execution=enabled reconciliation=None
smoke_rc=0
PRAGMA integrity_check=ok
rounds=1
signals=517
orders=0
fills=0
results=0
main paper.db orders=0 fills=0
analisis5.db orders=0 fills=0
process stopped
```

**Interpretasi:**

- ✅ next-boundary/full-round loop bekerja;
- ✅ 517 real ticks diproses;
- ✅ databases aman dan integrity ok;
- 🟦 tidak ada natural signal yang menghasilkan order/fill;
- ⛔ Gamma belum resolved dalam polling ~180 detik;
- ⛔ belum ada result dan reconciliation nyata.

---

## TELEGRAM CONTROL PLANE

| Prompt | Tugas | Status | DoD | Tanggal | Catatan |
|---|---|:---:|:---:|---|---|
| T.0 | Bot token/chat whitelist setup | ✅ | ✅ VPS | 2026-07-13 | token baru hanya `.env`; old token must remain revoked |
| T.1 | Best-effort notifier | ✅ | ✅ | 2026-07-13 | queue/retry/failure isolation |
| T.1+ | P&L/error addendum | ✅ | ✅ | 2026-07-13 | policy + production transport wiring |
| T.2 | Read-only commands/menu | ✅ | ✅ | 2026-07-13 | status/balance/PnL/positions/recent/config |
| T.3 | Pause/resume/kill controls | ✅ | ✅ | 2026-07-13 | whitelist, confirm/cancel, expiry, anti-replay |
| T.UI | Persistent reply keyboard | ✅ | ✅ focused/UI | 2026-07-13 | bottom menu tested; polling later stopped |
| T.OP | Controls use operational RiskManager | ✅ | ✅ focused | 2026-07-13 | pause affects OMS shared risk |
| T.SVC | Polling + market loop one service | ⬜ | ⬜ | | pending after settlement smoke |

Real send proof:

```text
SMOKE RESULT sent=1 failed=0 dropped=0
ACTION REQUIRED test received in Telegram
```

---

## GATE G2

| Requirement | Status | Evidence needed |
|---|:---:|---|
| Ratusan ronde paper | ⬜ | long soak after smoke gates |
| PnL paper consistent with backtest | ⬜ | enough fills/results |
| Zero unresolved reconciliation mismatch | ⬜ | settled multi-round results |
| Shared risk controls proven operationally | 🟦 | focused tests pass; full runtime test pending |
| Telegram reads real operational state | 🟦 | focused tests pass; unified service pending |
| No private/live path | ✅ | safety checks and empty credentials |

**G2 overall:** ⬜ BELUM LULUS.

---

## Active Work / Next Steps

### 1. Fix Gamma resolution wait/retry - ACTIVE

- extend bounded resolution polling to realistic 10-15 minutes;
- configurable but bounded by outer VPS timeout;
- retry transient Gamma errors with backoff;
- never invent outcome from BTC delta/Chainlink;
- Gamma remains ground truth;
- unresolved state must stay fail-closed and alert.

### 2. Run full-round smoke #2 in a new DB

Use new DB such as `paper.db.smoke2`. Never delete `paper.db.smoke1`.

Success criterion:

```text
round_result=1
Gamma outcome available
reconciliation_ok=True
DB integrity=ok
main paper.db unchanged
analisis5.db orders/fills=0/0
```

No-trade settlement is acceptable for this criterion.

### 3. Prove real paper order/fill path

- do not alter strategy to force trade;
- run natural full rounds until candidate produces entry;
- verify order/fill only in smoke DB;
- verify risk gate, net fee PnL, settlement, reconciliation;
- verify Telegram state/control against same runtime.

### 4. Restart/idempotency validation

- persist/recover client ID sequence;
- define open-position recovery;
- prove restart creates no duplicate fill.

### 5. Unified supervised service

```text
market loop
+ Telegram polling
+ notifier worker
+ one RiskManager
+ one ledger/control source
+ graceful shutdown
+ failure isolation
```

### 6. Short multi-round smoke, then G2 soak

- 3-5 rounds bounded first;
- then hundreds of rounds background;
- monitor disk/RAM/log, alerts, fill rate, PnL, DD, stale events, mismatch count.

---

## Active Blockers / Debt

| ID | Description | Impact | Status/Plan |
|---|---|---|---|
| RES-P1 | Gamma not resolved within ~180s | no result/reconcile in smoke | ⛔ active; extend bounded retry |
| ORD-P1 | No real paper order/fill yet | OMS path not proven on live data | 🟦 run natural rounds |
| REC-P1 | Reconciliation not reached in real smoke | zero mismatch not proven E2E | 🟦 blocked by resolution |
| RST-P1 | Restart recovery not proven | duplicate risk | ⬜ build after settlement smoke |
| SVC-P1 | Market + Telegram polling not unified | controls not live during full runtime | ⬜ pending |
| B2b | Chainlink Data Streams missing | live basis-risk blocker | 🟦 before live only |
| B1 | CLOB V2 REST/EIP-712 unverified | live blocker | ⛔ do not touch before G2 |
| TG-SEC | Old Telegram token exposed historically | security | keep revoked; new token only VPS |
| TD1 | 5 historical full-suite failures | full CI debt | separate cleanup |
| TD2 | historical lint/mypy debt | baseline debt | separate cleanup |
| WD1 | VPS local files/modification | data loss risk | never reset/clean/add-all |
| DEP1 | `websockets.legacy` warning | future dependency debt | separate upgrade |

---

## Current Action

```text
ACTIVE TASK:
Fix bounded Gamma resolution polling/retry so the next full-round smoke can
produce a Gamma settlement and invoke operational reconciliation.

LAST REAL SMOKE:
round=1783884000
ticks=517
execution=enabled
orders=0
fills=0
resolution=timeout
result=none
reconciliation=none
all DB safety/integrity checks passed

NEXT SUCCESS:
full round -> Gamma outcome -> round_result -> reconciliation_ok=True
```

---

## Live / Later Phases

### FASE 3 - Live Micro

All tasks remain ⛔ FORBIDDEN until G2 passes. Do not create signer, private CLOB auth, or live order path.

### FASE 4 - Hardening & Scale

Not started. Systemd/background deployment, failover hardening, tuning, multi-market, and scaling wait for G2/G3 evidence.
