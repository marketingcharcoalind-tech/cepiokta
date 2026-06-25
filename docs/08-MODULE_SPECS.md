# 08 — Module Specs (Kontrak Interface)

> Semua I/O eksternal di balik Protocol agar bisa di-mock. Domain = murni
> (tanpa I/O). MODE flag dihormati semua eksekutor.

## 8.1 adapters/clock.py
```python
class Clock(Protocol):
    def now(self) -> datetime: ...          # UTC aware
class SystemClock(Clock): ...
class SimClock(Clock):                       # untuk backtest deterministik
    def set(self, t: datetime) -> None: ...
```

## 8.2 adapters/gamma.py — GammaClient
`next_btc5m_round() -> Round`, `get_round(condition_id) -> Round`.

## 8.3 adapters/clob.py — ClobClient
`get_orderbook(token_id)`, `place_order(OrderRequest)->OrderAck`,
`cancel(order_id)`, `balances()`. Menyimpan auth CLOB V2. `Signer` terpisah.

## 8.4 adapters/clob_ws.py — ClobWS
`stream_market(token_ids)` & `stream_user()` (async iterator). Reconnect+backoff,
heartbeat, deteksi stale → emit event ke circuit breaker.

## 8.5 adapters/chainlink.py — PriceFeed
`price_now()`, `start_price(window_start)`. Sumber = Chainlink BTC/USD (selaras
resolusi). Opsional feed exchange untuk latensi (flag terpisah, bukan acuan).

## 8.6 domain/market.py — IntervalLoader ("interval-loader")
`current_window(now) -> Round|None`, `time_left(now) -> float`,
`is_entry_window(now, T_ENTRY_SEC) -> bool`. Murni; pakai Clock.

## 8.7 domain/signal.py — SignalEngine ("trend")
`compute(round, price_now, now, vol) -> Signal` (hitung Δ, p_win, ask_win,
net_edge sesuai docs/05). Fungsi murni & testable.

## 8.8 domain/strategy.py — Strategy ("hedging" + entry)
`on_tick(signal, book, position) -> list[Decision]` dengan Decision =
`EnterOrder | Hedge | Exit | NoOp`. Tanpa I/O.

## 8.9 exec/sizing.py — Sizer
`size(signal, bankroll, depth, limits) -> Decimal` (fractional Kelly + caps).

## 8.10 exec/oms.py — OMS
`submit(decision) -> OrderAck`. Bentuk+tandatangani (live) / simulasi (paper).
Idempotency, retry, fill tracking. WAJIB panggil Risk.check sebelum submit.

## 8.11 risk/manager.py — RiskManager
`check(order, state) -> Allow|Veto(reason)`, `on_event(evt)` (kill-switch,
circuit breaker), `should_halt() -> bool`. Lihat docs/06.

## 8.12 data/store.py & data/recorder.py
`store`: persist rounds/orders/fills/results/equity. `recorder`: fase-0 nulis
book_snapshots+signals untuk backtest.

## 8.13 backtest/replay.py — ReplayEngine
Putar ulang data terekam lewat SignalEngine+Strategy+Sizer+(paper)OMS dengan
SimClock. Output metrik (docs/09).

## 8.14 app/cli.py — Boot & Runner
Tampilkan boot sequence ala referensi:
```
5min-btc-polymarket v1.x
  connecting to Polymarket ...        [ ok ]
  authenticating wallet ...           [ ok ]
  opening websocket feed (BTC 5m) ... [ ok ]
  loading interval-loader module ...  [ ok ]
  loading trend module ...            [ ok ]
  loading hedging module ...          [ ok ]
all systems go.
```
Lalu loop sesuai MODE; cetak log per ronde (round_no, fill, settle, balance).

## 8.15 Aturan Dependensi
`adapters → domain`? TIDAK. Arah: `app → (domain, exec, risk, data) → adapters`.
Domain tidak mengimpor adapters. Inversi via Protocol + injeksi di `app`.



---

## ADDENDUM (v1.1) — Modul Telegram & ControlFacade
Spec lengkap: `docs/12-TELEGRAM_INTEGRATION.md` §12.6.
- `app/control.py` — **ControlFacade**: pintu kontrol tunggal (dipakai Telegram & CLI):
  `status()`, `pnl()`, `positions()`, `recent(n)`, `pause()`, `resume()`,
  `kill(reason)`, `set_mute(on)`. Memanggil RiskManager untuk pause/kill.
- `adapters/telegram.py` — **TelegramController** (impl `Notifier`): `start()`,
  `emit(BotEvent)`, `stop()`. Handler cek whitelist; `emit` non-blocking (asyncio.Queue).
- **Event bus**: core memancarkan `BotEvent` ke queue; task pengirim terpisah →
  kegagalan Telegram tidak memblok loop trading.

### Dependensi
`app → adapters/telegram → app/control (ControlFacade) → risk`. Telegram TIDAK
menyentuh domain/exec langsung; selalu lewat ControlFacade.



---

## ADDENDUM (v1.3) — MarketScanner & Per-Market Worker
Lihat docs/14 §14.4. Generalisasi pipeline single-market:
- `domain/market_registry.py` — baca MarketSpec[] dari config; daftar market aktif.
- `app/scanner.py` — **MarketScanner**: discovery ronde tiap aset×timeframe via
  Gamma; spawn 1 **MarketWorker** per market aktif.
- `app/worker.py` — **MarketWorker**: instance pipeline (signal→strategy→sizing)
  untuk satu market; pakai PriceFeed & sigma aset-nya.
- Shared (satu instance untuk semua worker): RiskManager (global), OMS, store,
  Telegram notifier, clock.
- Domain tetap MURNI & reusable (jangan duplikasi logika per aset).
- Strategi pluggable sesuai docs/13 (#1 default; #2/#3 menyusul).
