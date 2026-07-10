# Design — btc-bot (Kiro Spec)

## Overview
Arsitektur berlapis: Adapters (I/O) → Domain (murni) → Exec/Risk → App.
Detail penuh: /docs/02-ARCHITECTURE.md & /docs/08-MODULE_SPECS.md.

## Components
- **Adapters**: clock, gamma (REST discovery → RoundMeta; identifikasi market
  via **slug** `asset-updown-(5m|15m)-epoch`; window dari eventStartTime/endDate
  bukan startDate; query jendela end_date + UA browser; parse fee `crypto_fees_v2`
  & resolutionSource=Chainlink Data Streams), clob (REST+signing), clob_ws
  (market WSS: book snapshot=array & price_change, BookState per asset, path
  /ws/market; reconnect/backoff/stale), chainlink (price truth: ChainlinkDataFeed
  via eth_call, read-only; PriceSource Protocol; Data Streams menyusul di B2b).
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



---

## Pure Intra-Market Arbitrage Detector (PLANNED — docs/15)

**Status**: Rencana untuk Phase 1 read-only measurement. Belum diimplementasi.

### Component: `domain/arbitrage.py`

**Purpose**: Pure function untuk deteksi lock-pair opportunity.

```python
def detect_lock_pair(
    book_up: OrderBook,
    book_down: OrderBook,
    fee_model: FeeModel,
    slippage_buffer: Decimal,
    min_lock_edge: Decimal,
    min_depth: Decimal,
) -> ArbOpportunity | None
```

**Behavior:**
- Calculate `sum_asks = ask_up + ask_down`
- Calculate `fee_total = fee(ask_up) + fee(ask_down)` using FeeModel
- Calculate `net_lock_edge = 1 - sum_asks - fee_total - slippage_buffer`
- Validate: `net_lock_edge >= min_lock_edge` AND `max_lock_size >= min_depth`
- Return `ArbOpportunity` if valid, else None with reject_reason

**Properties:**
- Pure function (deterministic, no I/O)
- NO dependencies on adapters/OMS/signing
- Domain layer only

### Component: `backtest/arb_detector.py`

**Purpose**: Replay engine untuk detect opportunities from recorded data.

```python
async def replay_arb_detection(
    store: Store,
    since: datetime,
    until: datetime,
    config: ArbDetectorConfig,
) -> ArbDetectionReport
```

**Behavior:**
- Load book_snapshots for UP and DOWN tokens
- For each snapshot tick, call `detect_lock_pair()`
- Track opportunity duration (if same opportunity persists across ticks)
- Accumulate metrics: count, duration distribution, edge distribution, depth distribution
- Generate report: opportunity frequency, theoretical PnL, comparison vs directional

**Integration:**
- Add CLI command: `python -m btcbot.backtest.arb_detector` OR `report.py --mode arb-detection`
- Output: CSV export + summary report (similar to loss_diagnostics)

**Dependencies:**
- domain/arbitrage (detect_lock_pair)
- domain/models (OrderBook, FeeModel)
- domain/fees (FeeModel implementation)
- data/store (read book_snapshots)
- NO dependency on: adapters/clob, exec/oms, risk/manager, signing

### Dependency Rule

- domain/arbitrage MUST NOT import: adapters, exec/oms, risk, signing
- backtest/arb_detector MAY import: domain, data/store
- backtest/arb_detector MUST NOT import: adapters (except Protocol), exec/oms, risk

### Data Flow (Phase 1)

```
book_snapshots (DB)
  → arb_detector.replay_arb_detection()
    → detect_lock_pair() for each tick
      → ArbOpportunity (if valid)
        → accumulate metrics
          → ArbDetectionReport
            → CSV export + summary
```

**NO execution path**. Read-only measurement.

### Future Phases (NOT in current design)

- **Phase 2**: Paper simulation (two-leg OMS mock)
- **Phase 3**: Live execution (requires two-leg OMS, RiskManager veto for one-leg exposure, idempotency, hedge plan)

**Gate**: Phase 2/3 ONLY if G1 shows stable opportunity + infrastructure ready.

### Configuration

Via `ArbDetectorSettings` (docs/11):
- `ARB_DETECTOR_ENABLED` (default: false)
- `ARB_MAX_SUM_ASKS`, `ARB_MIN_LOCK_EDGE`, `ARB_MIN_DEPTH`, etc.

Settings validated in `config/settings.py`.

### Testing Strategy

- Unit tests: `test_detect_lock_pair()` with edge cases (empty book, sum=1, sum<1, depth=0)
- Integration tests: replay on synthetic data, verify metrics
- NO live testing in Phase 1 (read-only only)
