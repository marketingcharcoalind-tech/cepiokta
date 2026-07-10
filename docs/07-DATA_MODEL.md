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
  tick_size NUMERIC, min_order_size NUMERIC, status TEXT, resolved_outcome TEXT,
  settlement_price TEXT, resolution_source TEXT);   -- additive (lihat §7.3.2)

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

### 7.3.1 Retensi `book_snapshots` (Fase 1) — write-time throttling
Order book in-memory tetap di-update penuh tiap event; hanya PERSISTENSI yang
di-throttle agar soak readonly tidak meledakkan disk (~6 GB/hari → ratusan
MB/hari). Skema kolom TIDAK berubah (tanpa migrasi). Aturan (per token/ronde):
- **Selalu** tulis bila `best_bid`/`best_ask` (harga) berubah, atau snapshot
  pertama token, atau snapshot terakhir (penanda penutup).
- Bila best sama (hanya jitter depth) → maks 1 baris/token per `BOOK_SAMPLE_MS`.
- Fine-grain: bila `window_end - now <= BOOK_FINEGRAIN_SEC` → throttle OFF
  (resolusi penuh saat fase aksi strategi akhir-window).
- Mode `BOOK_PERSIST_MODE=all` → tanpa throttle (regresi perilaku lama).

**Implikasi ke G1 (kalibrasi):** densitas data tidak seragam — tinggi saat
harga bergerak & di akhir-window (45 dtk terakhir), rendah saat tenang. Saat
membaca series untuk kalibrasi/backtest, perlakukan tiap baris sebagai
"berlaku sampai baris berikutnya" (step/last-value-carried-forward), JANGAN
asumsikan interval sampling tetap. best_bid/ask over time + likuiditas + tail
window tetap utuh; jitter depth menengah sengaja dijatuhkan.

### 7.3.2 Resolusi ronde — `resolved_outcome`/`settlement_price`/`resolution_source`
Diisi oleh resolution recorder (`data/resolver.py`) setelah `window_end`:
- `resolved_outcome` = `"UP"`/`"DOWN"` + `status='resolved'`.
- `resolution_source` = `"gamma"` (primer). Gamma melaporkan token pemenang via
  `outcomePrices` (JSON-string, mis. `["1","0"]` → pemenang = index bernilai
  `"1"`) saat market `closed==true` & `outcomePrices` definitif (tepat satu ≥0.99,
  sisanya ≤0.01) — inilah yang benar-benar dibayar (ground truth).
  `umaResolutionStatus` **BUKAN** syarat (boleh `"resolved"` tapi tidak dipakai).
- `settlement_price` = harga Chainlink saat cross-check (best-effort; hanya untuk
  ronde yang BARU berakhir, backfill ronde lama → NULL). Bila outcome Chainlink
  (settlement vs `start_price`) ≠ Gamma → log `resolution_mismatch` (menyingkap
  selisih Data Feeds vs Data Streams → B2b). Outcome final TETAP dari Gamma.
- Konvensi enum `Outcome` (`"UP"`/`"DOWN"`, uppercase). BTC up/down tak punya
  "tie" (resolusi `≥` → Up).
- Migrasi additive idempoten (kolom dicek sebelum `ALTER`); data lama aman.



---

## ADDENDUM (v1.3) — Multi-Market Fields
Tambah `asset` (BTC|ETH|SOL) & `timeframe` (5m|15m) — atau `market_key =
asset_timeframe` — ke: `rounds`, `orders`, `round_results`, `equity_curve`.
Index per `market_key`. PnL & metrik dapat dipecah per market. Lihat docs/14 §14.9.


---

## ADDENDUM — Pure Arbitrage Opportunity Fields (Planned)

> **Status**: Rencana untuk Phase 1 detector (docs/15). Belum diimplementasi.

### Optional Table: `arb_opportunities`

Untuk merekam pure intra-market lock-pair arbitrage opportunities yang terdeteksi (read-only measurement, bukan execution).

```sql
CREATE TABLE IF NOT EXISTS arb_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_no INTEGER NOT NULL,
    ts TEXT NOT NULL,                   -- ISO8601 UTC
    token_up TEXT NOT NULL,
    token_down TEXT NOT NULL,
    ask_up TEXT NOT NULL,               -- Decimal as string
    ask_down TEXT NOT NULL,
    depth_up TEXT NOT NULL,
    depth_down TEXT NOT NULL,
    sum_asks TEXT NOT NULL,             -- ask_up + ask_down
    fee_total TEXT NOT NULL,            -- fee_up + fee_down
    slippage_buffer TEXT NOT NULL,      -- estimated slippage
    net_lock_edge TEXT NOT NULL,        -- 1 - sum_asks - fee_total - slippage_buffer
    max_lock_size TEXT NOT NULL,        -- min(depth_up, depth_down)
    duration_ms INTEGER,                -- how long opportunity lasted (NULL if not tracked)
    valid INTEGER NOT NULL,             -- 1 = valid opportunity, 0 = rejected
    reject_reason TEXT,                 -- if valid=0: reason (e.g., "net_lock_edge_too_low")
    mode TEXT NOT NULL                  -- 'backtest' or 'readonly'
);

CREATE INDEX IF NOT EXISTS idx_arb_opp_round ON arb_opportunities(round_no);
CREATE INDEX IF NOT EXISTS idx_arb_opp_ts ON arb_opportunities(ts);
CREATE INDEX IF NOT EXISTS idx_arb_opp_valid ON arb_opportunities(valid);
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `round_no` | int | Round identifier |
| `ts` | datetime | Timestamp opportunity detected |
| `token_up` / `token_down` | str | Token IDs |
| `ask_up` / `ask_down` | Decimal | Best ask prices |
| `depth_up` / `depth_down` | Decimal | Depth at best ask |
| `sum_asks` | Decimal | ask_up + ask_down |
| `fee_total` | Decimal | Estimated total fee (fee_up + fee_down, ~7% each) |
| `slippage_buffer` | Decimal | Estimated slippage (e.g., 0.2%) |
| `net_lock_edge` | Decimal | 1 - sum_asks - fee_total - slippage_buffer (profit if > 0) |
| `max_lock_size` | Decimal | min(depth_up, depth_down) — max contracts for lock |
| `duration_ms` | int | How long opportunity lasted (NULL if single snapshot) |
| `valid` | bool | 1 if valid opportunity, 0 if rejected |
| `reject_reason` | str | If rejected: reason (e.g., "net_edge_too_low", "depth_insufficient") |
| `mode` | str | 'backtest' or 'readonly' |

### Reject Reasons

- `"net_lock_edge_too_low"`: net_lock_edge < MIN_LOCK_EDGE threshold
- `"depth_insufficient"`: max_lock_size < MIN_DEPTH threshold
- `"sum_asks_too_high"`: sum_asks >= MAX_SUM_ASKS (e.g., 0.99)
- `"empty_book"`: one or both sides have no liquidity
- `"latency_exceeded"`: detection latency > threshold

### Alternative: CSV Export Only

Table creation OPTIONAL. Detector dapat export langsung ke CSV tanpa persist ke DB.

Decision: Will be made during implementation. For G1 measurement, CSV export sufficient.

### Domain Model (Planned)

```python
@dataclass(frozen=True)
class ArbOpportunity:
    """Pure intra-market lock-pair arbitrage opportunity."""
    round_no: int
    ts: datetime
    token_up: str
    token_down: str
    ask_up: Decimal
    ask_down: Decimal
    depth_up: Decimal
    depth_down: Decimal
    sum_asks: Decimal
    fee_total: Decimal
    slippage_buffer: Decimal
    net_lock_edge: Decimal
    max_lock_size: Decimal
    valid: bool
    reject_reason: str | None
```

### Migration Notes

**NO migration sekarang**. Ini hanya rencana. Jika nanti di-implement:
- Tambah table via migration script
- Update Store class dengan CRUD methods
- Detector writes to store OR exports CSV

**Prinsip**: Detector bersifat read-only. Tidak mengubah strategy/signal/sizing/execution logic.
