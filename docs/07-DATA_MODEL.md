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
  `outcomePrices` (mis. `["1","0"]`) saat market `closed`/`umaResolutionStatus`
  resolved — inilah yang benar-benar dibayar (ground truth).
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
