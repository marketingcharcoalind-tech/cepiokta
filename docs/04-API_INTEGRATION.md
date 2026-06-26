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
- **Slug terverifikasi (live)**: `{asset}-updown-{5m|15m}-{epoch}` (mis.
  `btc-updown-5m-1782480000`). `round_no = epoch`; `epoch % 300 == 0` (5m) /
  `% 900 == 0` (15m). Filter andal pakai **regex slug**, bukan teks judul.
- **Query (terverifikasi)**: market hidup → `GET /markets?closed=false&active=true`;
  market selesai/resolusi → WAJIB `GET /markets?...&closed=true` (tanpa flag itu
  hasil selalu kosong). Header **`User-Agent` browser wajib**.
- Window ronde: `eventStartTime` → `endDate` (BUKAN `startDate` = tanggal listing,
  ~24 jam sebelumnya).
- Ambil per ronde: `condition_id`, `token_id_up`, `token_id_down`,
  `window_start`, `window_end`, `start_price` (acuan), `tick_size`,
  `min_order_size`, status resolusi.
- **FEE (terverifikasi, KRITIS)**: market crypto up/down BERBIAYA — parse
  `feesEnabled:true`, `feeType:"crypto_fees_v2"`, `feeSchedule.rate:0.07`,
  `takerOnly:true`. Asumsi zero-fee SALAH; net_edge/PnL wajib net fee ~7% taker.
- `outcomePrices` Gamma **STALE** untuk market cepat → jangan dipakai sebagai
  harga live (harga live dari order book CLOB). Untuk **resolusi** lihat §4.6.
- Cache jadwal window; refresh menjelang pergantian ronde.

## 4.4 Market Data Realtime (WSS)
> ✅ **Terverifikasi (live)**: path WS = **`/ws/market`** (BUKAN `/ws`). Channel
> `market` mengirim **DUA bentuk pesan**: (a) **LIST/array** = snapshot orderbook
> awal per token; (b) **DICT** = `price_change` (`price_changes[]`, `side=BUY`→bid,
> `side=SELL`→ask, `size=0` hapus level). Maintain `BookState` per asset
> (`best_bid=max(bids)`, `best_ask=min(asks)`).
> **Keepalive**: server tak balas ping protokol → set `ping_interval=None` /
> `ping_timeout=None` (matikan keepalive library) + heartbeat aplikasi `"PING"`
> tiap 10s (task terpisah). Tanpa ini koneksi mati `1011 keepalive ping timeout`
> ~45s meski data mengalir. Stale > 30s → reconnect.

- Subscribe channel `market` untuk `token_id` UP & DOWN → best bid/ask + depth.
- Subscribe channel `user` (terotentikasi) → status order & fill kita.
- Tangani: reconnect dengan backoff, heartbeat/ping aplikasi, deteksi **stale**
  (tidak ada update > N ms ⇒ trigger circuit breaker), resubscribe setelah reconnect.

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
- **Resolusi outcome (terverifikasi, Gamma = ground truth)**: `outcomes` &
  `outcomePrices` datang sebagai **JSON-string** → `json.loads`. Pemenang = index
  bernilai `"1"`: `["1","0"]`→**UP**, `["0","1"]`→**DOWN** (`outcomes`=`["Up","Down"]`).
  Resolved hanya bila `closed==true` DAN `outcomePrices` definitif (tepat satu
  ≥0.99, sisanya ≤0.01). `umaResolutionStatus` boleh `"resolved"` tapi **JANGAN**
  dijadikan syarat. `resolution_source="gamma"`.
- **Basis risk**: gunakan feed Chainlink yang sama untuk prediksi; feed exchange
  (Binance/Coinbase) hanya pelengkap latensi, BUKAN acuan resolusi.
- **Chainlink address (terverifikasi)**: BTC/USD **Data Feed**
  `0xc907E116054Ad103354f2D350FD2514433D57F6f` (Polygon).
- **RPC failover** (`adapters/chainlink.py`): baca Data Feeds via daftar RPC
  terurut `POLYGON_RPC_URL` (chainstack) + `POLYGON_RPC_FALLBACKS`
  (publicnode → blastapi → blockpi). Endpoint dianggap GAGAL bila
  exception/timeout/HTTP(403/5xx)/JSON-RPC, `price<=0`, atau data STALE
  (`now-updatedAt > CHAINLINK_MAX_STALENESS_SEC`) → coba endpoint berikutnya.
  Semua gagal → `AllRpcFailedError` (Δ=None + gap, tanpa crash). RPC publik
  (Cloudflare-fronted) menolak tanpa UA browser → semua request kirim header
  `User-Agent: Mozilla/5.0 ...`.
- **Data Streams vs Data Feeds (penting)**: resolusi market sebenarnya memakai
  Chainlink **Data Streams** (≠ Data Feeds yang kita baca). Maka `settlement_price`
  saat ini **best-effort** (cross-check Data Feeds); adapter Data Streams menyusul
  (blocker **B2b**) untuk presisi harga akhir-window.
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
- [ ] Base URL & versi CLOB V2 terbaru. *(`py-clob-client` diarsip Mei 2026 → wajib CLOB V2 + signing EIP-712 sendiri.)*
- [ ] Skema EIP-712 order V2 yang valid.
- [x] Nama channel WSS & format pesan. *(✅ `/ws/market`; LIST snapshot + `price_change` DICT; BUY→bid/SELL→ask; keepalive ping_interval=None + "PING" 10s — §4.4)*
- [x] Cara tepat membaca Chainlink BTC/USD di Polygon (feed address / data stream). *(✅ Data Feed `0xc907…57F6f` + RPC failover; resolusi pakai Data Streams → B2b — §4.6)*
- [~] Aturan fee, tick size, min order size market BTC 5m. *(✅ fee terverifikasi: `crypto_fees_v2` rate 0.07 taker-only; reverse-engineer formula base→profit di G1 — §4.3)*
- [ ] Batasan geografis / kepatuhan akun.
