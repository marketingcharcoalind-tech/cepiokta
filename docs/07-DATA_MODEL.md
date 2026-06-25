# 07 — Data Model

> Semua uang/harga = `Decimal`. Semua waktu = UTC aware. ID stabil & unik.

## 7.1 Entity Inti (domain/models.py)
```python
@dataclass
class Round:
    condition_id: str
    round_no: int                 # mis. 48247
    token_id_up: str
    token_id_down: str
    window_start: datetime        # UTC
    window_end: datetime
    start_price: Decimal          # acuan resolusi (Chainlink)
    tick_size: Decimal
    min_order_size: Decimal
    status: str                   # scheduled|active|closed|resolved
    resolved_outcome: str | None  # "UP"|"DOWN"|None

@dataclass
class BookLevel: price: Decimal; size: Decimal
@dataclass
class OrderBook:
    token_id: str; ts: datetime
    bids: list[BookLevel]; asks: list[BookLevel]

@dataclass
class Signal:
    round_no: int; ts: datetime
    price_now: Decimal; delta: Decimal; time_left_sec: float
    p_win: Decimal; leader: str; ask_win: Decimal; net_edge: Decimal

@dataclass
class OrderRequest:
    client_id: str; token_id: str; side: str   # BUY|SELL
    price: Decimal; size: Decimal; order_type: str  # FOK|FAK|GTC
@dataclass
class OrderAck:
    client_id: str; order_id: str; status: str; ts: datetime
@dataclass
class Fill:
    order_id: str; token_id: str; price: Decimal; size: Decimal; ts: datetime
@dataclass
class Position:
    round_no: int; token_id: str; size: Decimal; avg_price: Decimal
@dataclass
class RoundResult:
    round_no: int; side_taken: str; entry_price: Decimal; size: Decimal
    hedge_cost: Decimal; settled: Decimal; pnl: Decimal; balance_after: Decimal
```

## 7.2 Skema DB (SQL, cocok SQLite/Postgres)
```sql
CREATE TABLE rounds (
  condition_id TEXT, round_no INTEGER PRIMARY KEY, token_up TEXT, token_down TEXT,
  window_start TIMESTAMPTZ, window_end TIMESTAMPTZ, start_price NUMERIC,
  tick_size NUMERIC, min_order_size NUMERIC, status TEXT, resolved_outcome TEXT);

CREATE TABLE book_snapshots (         -- fase 0 recorder (bisa besar; pertimbangkan kompresi/parquet)
  id BIGSERIAL PRIMARY KEY, round_no INTEGER, token_id TEXT, ts TIMESTAMPTZ,
  best_bid NUMERIC, best_ask NUMERIC, bid_depth NUMERIC, ask_depth NUMERIC, raw JSONB);

CREATE TABLE signals (
  id BIGSERIAL PRIMARY KEY, round_no INTEGER, ts TIMESTAMPTZ, price_now NUMERIC,
  delta NUMERIC, time_left_sec REAL, p_win NUMERIC, leader TEXT, ask_win NUMERIC, net_edge NUMERIC);

CREATE TABLE orders (
  client_id TEXT PRIMARY KEY, order_id TEXT, round_no INTEGER, token_id TEXT,
  side TEXT, price NUMERIC, size NUMERIC, order_type TEXT, status TEXT,
  mode TEXT, created_at TIMESTAMPTZ);

CREATE TABLE fills (
  id BIGSERIAL PRIMARY KEY, order_id TEXT, token_id TEXT, price NUMERIC,
  size NUMERIC, ts TIMESTAMPTZ);

CREATE TABLE round_results (
  round_no INTEGER PRIMARY KEY, side_taken TEXT, entry_price NUMERIC, size NUMERIC,
  hedge_cost NUMERIC, settled NUMERIC, pnl NUMERIC, balance_after NUMERIC, mode TEXT);

CREATE TABLE equity_curve (
  ts TIMESTAMPTZ PRIMARY KEY, balance NUMERIC, mode TEXT);
```

## 7.3 Catatan
- `mode` disimpan di setiap order/result → bisa pisah paper vs live.
- Recorder fase 0 boleh tulis ke Parquet untuk backtest cepat.
- Index: `book_snapshots(round_no, ts)`, `signals(round_no, ts)`.



---

## ADDENDUM (v1.3) — Multi-Market Fields
Tambah `asset` (BTC|ETH|SOL) & `timeframe` (5m|15m) — atau `market_key =
asset_timeframe` — ke: `rounds`, `orders`, `round_results`, `equity_curve`.
Index per `market_key`. PnL & metrik dapat dipecah per market. Lihat docs/14 §14.9.
