# Test fixtures

Semua fixture di sini adalah **capture LIVE asli** dari Polymarket (VPS
jaringan-bersih) dan menjadi snapshot schema untuk mengunci regresi.

## `gamma_updown_live_fixture.json`

Respons asli Gamma `/markets` (banyak market up/down lintas aset & timeframe,
mis. `btc-updown-5m-1782478200`, `btc-updown-15m-1782477900`, ETH/SOL/XRP/...).
Dipakai oleh `tests/adapters/test_gamma.py`.

Karakteristik yang diuji:
- Identifikasi via **slug** `^(asset)-updown-(5m|15m)-(epoch)$` (bukan teks judul).
- `epoch` = waktu resolusi = `endDate` = `window_end`; sanity kelipatan 300/900.
- `window_start` dari `eventStartTime` (mis. 12:50) — **bukan** `startDate`
  (tanggal listing, mis. 2026-06-25).
- `clobTokenIds` sejajar index dengan `outcomes` → Up/Down tidak tertukar.
- `feeSchedule {exponent, rate, takerOnly, rebateRate}` (`crypto_fees_v2`).
- `resolutionSource` = Chainlink **Data Streams**.

## `ws_market_capture.json`

Rekaman pesan WS channel `market` (`wss://.../ws/market`). Array berisi urutan
frame: frame[0] = **book snapshot** (JSON **array**, satu objek per token UP/DOWN),
frame[1..] = **price_change** (dict; `price_changes[]` dgn side BUY/SELL, size 0
⇒ hapus level; ada `best_bid`/`best_ask`). Dipakai oleh `tests/adapters/test_clob_ws.py`.

Token: UP=`77079965...513`, DOWN=`68703425...843` (condition
`0xf18e1439...76ef`). best_bid = harga tertinggi bids; best_ask = terendah asks.
