# 04 — API Integration

> ⚠️ **Verifikasi sebelum koding eksekusi.** Polymarket sudah bermigrasi ke
> **CLOB V2** (V1 SDK `py-clob-client` diarsipkan). Selalu cek dokumentasi
> resmi terbaru Polymarket untuk endpoint, skema order, dan auth sebelum
> menulis kode yang mengirim order. Jika berbeda dari dokumen ini, dokumen
> resmi yang menang — lalu update file ini.

## 4.1 Komponen API yang Dipakai
| Sumber | Guna | Tipe |
|---|---|---|
| **Gamma API** | discovery market (slug, condition_id, token_id UP/DOWN, start/end, harga awal, status) | REST |
| **CLOB V2 REST** | submit/cancel order, ambil orderbook, saldo/posisi | REST + signing |
| **CLOB V2 WSS** | stream realtime: channel `market` (orderbook/trades) & `user` (order/fill milik kita) | WebSocket |
| **Chainlink BTC/USD** | "price truth" selaras sumber resolusi (di Polygon) | on-chain / data streams |

## 4.2 Autentikasi (alur)
1. Punya wallet EOA (private key) di Polygon — simpan via secret (env).
2. **Derive API credentials** (api_key/secret/passphrase) dari signature wallet
   sesuai prosedur CLOB V2.
3. Order ditandatangani **EIP-712** dengan **skema V2** (skema V1 ditolak
   production). Header request CLOB butuh kredensial hasil langkah 2.
4. Simpan kredensial di memori proses saja; jangan log.

> Implementasi: `adapters/clob.py` membungkus auth+REST; `exec/oms.py` membuat
> & menandatangani order. Sediakan interface `Signer` yang bisa di-mock.

## 4.3 Discovery Market (Gamma)
- Cari market crypto **"BTC Up/Down 5m"** yang aktif/berikutnya.
- Ambil per ronde: `condition_id`, `token_id_up`, `token_id_down`,
  `window_start`, `window_end`, `start_price` (acuan), `tick_size`,
  `min_order_size`, status resolusi.
- Cache jadwal window; refresh menjelang pergantian ronde.

## 4.4 Market Data Realtime (WSS)
- Subscribe channel `market` untuk `token_id` UP & DOWN → best bid/ask + depth.
- Subscribe channel `user` (terotentikasi) → status order & fill kita.
- Tangani: reconnect dengan backoff, heartbeat/ping, deteksi **stale** (tidak ada
  update > N ms ⇒ trigger circuit breaker), resubscribe setelah reconnect.

## 4.5 Order Lifecycle
- **Tipe order**: 
  - `FOK` (Fill-Or-Kill) / `FAK/IOC` — taker, cocok untuk entry ekor-window.
  - `GTC` — maker (limit nongkrong), opsional untuk hedge/likuiditas.
- Field order minimal: `token_id`, `side(BUY/SELL)`, `price`, `size`,
  `order_type`, nonce/expiry, signature.
- **Idempotency**: simpan client order id; jangan double-submit saat retry.
- **Rate limits**: hormati limit REST; throttle; exponential backoff pada 429.
- **Fill tracking**: utamakan channel `user` (WSS) daripada polling.

## 4.6 Resolusi & Settlement
- Market BTC pendek resolve via **Chainlink** (Polygon): UP bila harga akhir
  ≥ start, DOWN bila lebih rendah. Settle otomatis beberapa detik–menit setelah
  window tutup.
- **Basis risk**: gunakan feed Chainlink yang sama untuk prediksi; feed exchange
  (Binance/Coinbase) hanya pelengkap latensi, BUKAN acuan resolusi.
- Rekonsiliasi: setelah settle, cocokkan posisi → PnL aktual ke `store.py`.

## 4.7 Adapter Interface (kontrak ringkas)
```python
class GammaClient(Protocol):
    async def next_btc5m_round(self) -> Round: ...
    async def get_round(self, condition_id: str) -> Round: ...

class ClobClient(Protocol):
    async def get_orderbook(self, token_id: str) -> OrderBook: ...
    async def place_order(self, o: OrderRequest) -> OrderAck: ...
    async def cancel(self, order_id: str) -> None: ...
    async def balances(self) -> Balances: ...

class ClobWS(Protocol):
    async def stream_market(self, token_ids: list[str]) -> AsyncIterator[BookUpdate]: ...
    async def stream_user(self) -> AsyncIterator[UserUpdate]: ...

class PriceFeed(Protocol):  # Chainlink BTC/USD
    async def price_now(self) -> Decimal: ...
    async def start_price(self, window_start: datetime) -> Decimal: ...
```

## 4.8 Hal yang Harus Diverifikasi Manusia Sebelum Live
- [ ] Base URL & versi CLOB V2 terbaru.
- [ ] Skema EIP-712 order V2 yang valid.
- [ ] Nama channel WSS & format pesan.
- [ ] Cara tepat membaca Chainlink BTC/USD di Polygon (feed address / data stream).
- [ ] Aturan fee, tick size, min order size market BTC 5m.
- [ ] Batasan geografis / kepatuhan akun.
