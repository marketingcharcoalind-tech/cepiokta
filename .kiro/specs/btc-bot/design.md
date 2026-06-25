# Design — btc-bot (Kiro Spec)

## Overview
Arsitektur berlapis: Adapters (I/O) → Domain (murni) → Exec/Risk → App.
Detail penuh: /docs/02-ARCHITECTURE.md & /docs/08-MODULE_SPECS.md.

## Components
- **Adapters**: clock, gamma (REST discovery → RoundMeta; filter struktural
  BTC 5m via outcomes Up/Down + durasi window 300s; pagination + backoff),
  clob (REST+signing), clob_ws (market+user WSS), chainlink (price truth:
  ChainlinkDataFeed via eth_call, read-only; di balik PriceSource Protocol
  agar Data Streams bisa menyusul).
- **Domain (murni)**: market (interval-loader), signal (trend/edge), strategy
  (entry/hedge/exit).
- **Exec**: sizing (fractional Kelly+caps), oms (order mgmt, paper/live).
- **Risk**: manager (veto, kill-switch, circuit breaker).
- **Data**: store (DB), recorder (fase 0).
- **Backtest**: replay engine + fill model.
- **App**: cli (boot), paper, live runner.

## Data Models
Lihat /docs/07-DATA_MODEL.md (Round, OrderBook, Signal, OrderRequest, Fill,
Position, RoundResult + skema SQL).

## Dependency Rule
Domain TIDAK mengimpor adapters. Inversi via Protocol, injeksi di App.
Arah: app → (domain, exec, risk, data) → adapters.

## Key Flows
- Trade loop & sequence diagram: /docs/02-ARCHITECTURE.md §2.3–2.4.
- Strategy math (p_win, net_edge, hedge): /docs/05-STRATEGY_SPEC.md.

## Mode Gating
MODE=readonly|backtest|paper|live (default readonly). live butuh
LIVE_CONFIRMED=yes. Adapters & OMS cek MODE; Risk aktif di semua mode.

## Sizing & Paper Trading (Phase 0.7)
- Sizing (`exec/sizing.py`) = fractional Kelly (`KELLY_FRACTION`) dibatasi
  `min()` dari empat cap: `MAX_NOTIONAL_ROUND/ask`, `(bankroll *
  MAX_BANKROLL_FRACTION)/ask`, `depth*FILL_SAFETY`, plus gerbang `MIN_EDGE`.
  Invariant: never-fade, tidak beli > `MAX_PRICE`, `size >= 0`.
- Bankroll aktif via `active_bankroll()`: saat `PAPER_TRADING=true` memakai
  `PAPER_STARTING_BALANCE` / saldo paper berjalan; jalur live belum tersedia
  (fase pra-live). Lihat docs/06 §6.2 & docs/11.

## Error Handling
- WSS: reconnect backoff, heartbeat, stale → circuit breaker.
- REST: retry+backoff pada 429/5xx; idempotency key.
- Reconciliation mismatch → freeze + alert.

## Testing Strategy
Unit (domain), integrasi (adapters mock), backtest (data terekam), paper
(realtime sim). Detail: /docs/09-TESTING_AND_BACKTESTING.md.



---

## Telegram Control Plane (docs/12)
- **adapters/telegram.py** (python-telegram-bot v20+): Notifier (emit via
  asyncio.Queue, non-blocking) + command/button handler (whitelist-guarded).
- **app/control.py — ControlFacade**: pintu kontrol tunggal (Telegram & CLI):
  status/pnl/positions/recent/pause/resume/kill/set_mute → memanggil RiskManager.
- **Decoupling**: core memancarkan BotEvent ke event bus; Telegram = AUXILIARY,
  best-effort. Telegram down tidak memengaruhi jalur trading.
- **Keamanan**: whitelist chat_id, konfirmasi 2-langkah utk kill/pause, audit log,
  token sebagai secret.
