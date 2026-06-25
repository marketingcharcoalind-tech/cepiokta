# 14 — Multi-Market Scaling (BTC/ETH/SOL × 5m/15m)

> Perluasan dari "BTC 5m saja" menjadi banyak market untuk lebih banyak
> peluang. Prinsip: **rancang multi-market sejak awal, aktifkan bertahap**.

---

## 14.1 Kenapa Multi-Market (untung) — & Caveat Jujur
**Keuntungan:**
- Lebih banyak ronde/peluang per jam → lebih banyak "lemparan dadu" untuk
  merealisasikan edge (jika edge memang ada).
- Diversifikasi *event timing* (settlement tidak bersamaan).
- Fair-value engine yang SAMA dipakai semua aset (cuma beda `sigma`).

**Caveat (WAJIB sadar):**
1. **Korelasi tinggi.** BTC/ETH/SOL bergerak searah. Saat BTC dump, ETH & SOL
   ikut. Jadi diversifikasi ARAH terbatas — jangan all-in "UP" di 3 aset
   sekaligus (itu satu taruhan besar, bukan tiga kecil).
2. **Modal dibagi.** Lebih banyak market = alokasi per market menipis kecuali
   modal ditambah.
3. **Likuiditas berbeda.** Market ETH/SOL pendek sering lebih tipis dari BTC →
   slippage lebih besar. Perlu gating likuiditas per market.
4. **Kalibrasi per aset.** `sigma` & parameter beda (SOL > ETH > BTC volatilnya).
5. **Kompleksitas = lebih banyak failure mode.** Bangun SETELAH pipeline single-
   market jalan; jangan tambah multi-market di Fase 0.

## 14.2 Rekomendasi
> **Design multi, enable incremental.** Kode mendukung daftar market dari config,
> tapi NYALAKAN satu per satu: buktikan edge di BTC 5m → validasi → nyalakan
> market berikutnya (BTC 15m), validasi, dst. Tiap market lulus checklist edge
> (docs/13 §13.7) sebelum diaktifkan dengan ukuran nyata.

---

## 14.3 Model Konfigurasi Market
```python
@dataclass
class MarketSpec:
    asset: str          # "BTC" | "ETH" | "SOL"
    timeframe: str      # "5m" | "15m"
    enabled: bool       # nyalakan bertahap
    weight: Decimal     # bobot alokasi modal (0..1)
    price_feed: str     # sumber Chainlink utk aset ini
    params: dict | None # override per-market (T_ENTRY_SEC, DELTA_THRESHOLD, ...)
```
Contoh (YAML/JSON config, bukan hardcode):
```yaml
markets:
  - {asset: BTC, timeframe: 5m,  enabled: true,  weight: 0.40}
  - {asset: BTC, timeframe: 15m, enabled: false, weight: 0.20}
  - {asset: ETH, timeframe: 5m,  enabled: false, weight: 0.15}
  - {asset: ETH, timeframe: 15m, enabled: false, weight: 0.10}
  - {asset: SOL, timeframe: 5m,  enabled: false, weight: 0.10}
  - {asset: SOL, timeframe: 15m, enabled: false, weight: 0.05}
```

## 14.4 Arsitektur Multi-Market
```
                 ┌──────────────── MarketScanner ────────────────┐
                 │ baca MarketSpec[enabled]; discovery via Gamma  │
                 │ untuk tiap aset×timeframe; jadwalkan ronde     │
                 └───────────────┬───────────────────────────────┘
                                 │ spawn 1 worker per market aktif
        ┌────────────────────────┼───────────────────────────────┐
        ▼                        ▼                                 ▼
 ┌─────────────┐         ┌─────────────┐                   ┌─────────────┐
 │ Worker BTC5m│         │ Worker ETH5m│        ...        │ Worker SOL5m│
 │ feed=BTC    │         │ feed=ETH    │                   │ feed=SOL    │
 │ signal→strat│         │ signal→strat│                   │ signal→strat│
 └──────┬──────┘         └──────┬──────┘                   └──────┬──────┘
        └───────────────────────┼─────────────────────────────────┘
                                 ▼  (SEMUA order lewat sini)
                 ┌───────────────────────────────────────────────┐
                 │ RiskManager GLOBAL  (bankroll & exposure share)│
                 │  + per-market cap + per-asset cap + korelasi   │
                 └───────────────┬───────────────────────────────┘
                                 ▼
              shared: OMS · store · Telegram notifier · clock
```
- **Satu** RiskManager, bankroll, store, Telegram untuk semua worker.
- Tiap worker pakai `PriceFeed` aset-nya & `sigma` aset-nya.
- Worker = instance dari pipeline yang sama (domain/strategy reusable).

## 14.5 Price Feed & Volatilitas per Aset
- `adapters/chainlink.py` → `PriceFeed` per aset: BTC/USD, ETH/USD, SOL/USD
  (selaras sumber resolusi masing-masing market).
- `sigma_asset` diestimasi terpisah (realized vol jendela terbaru per aset).
- Cache & refresh per aset; tandai stale per aset (circuit breaker per market).

## 14.6 Alokasi Modal & Exposure (korelasi-aware)
| Limit | Cakupan |
|---|---|
| `MAX_NOTIONAL_ROUND` | per market per ronde |
| `MAX_OPEN_EXPOSURE_MARKET` | per market |
| `MAX_OPEN_EXPOSURE_ASSET` | per aset (gabung 5m+15m aset itu) |
| `MAX_CORRELATED_DIRECTIONAL` | **gabungan arah sama lintas aset korelasi** (cegah all-in "UP" BTC+ETH+SOL) |
| `MAX_OPEN_EXPOSURE_GLOBAL` | seluruh portofolio |
- Alokasi awal pakai `weight` per MarketSpec; opsional adaptif ke market
  ber-edge & likuid lebih tinggi.
- **Aturan korelasi:** perlakukan BTC/ETH/SOL sebagai satu faktor risiko; batasi
  total net-directional, bukan hanya per market.

## 14.7 Gating Likuiditas per Market
- Tolak entry jika depth < ambang per market (ETH/SOL lebih ketat).
- Sizing dibatasi depth nyata; jangan sapu book tipis.
- Nonaktifkan otomatis market yang spread-nya melebar ekstrem.

## 14.8 Risk: Global vs Per-Market vs Per-Asset
- Kill-switch & circuit breaker bisa **global** (stop semua) atau **per-market**
  (matikan satu market yang feed-nya stale) — implementasikan dua level.
- Daily-loss & drawdown dihitung **global** (portofolio) + opsional per market.

## 14.9 Perubahan Data Model (lihat docs/07)
Tambah kolom `asset` & `timeframe` di `rounds`, `orders`, `round_results`,
`equity_curve` (atau `market_key = asset_timeframe`). PnL bisa dipecah per market.

## 14.10 Rencana Rollout (bertahap)
```
1. BTC 5m  : buktikan edge (G1) → paper (G2) → live micro (G3)   [WAJIB dulu]
2. BTC 15m : aktifkan, kalibrasi sigma & param, validasi edge     → live kecil
3. ETH 5m/15m : cek likuiditas; kalibrasi; validasi; aktifkan
4. SOL 5m/15m : likuiditas paling tipis → paling hati-hati
Setiap langkah: lulus checklist edge (docs/13 §13.7) + likuiditas sebelum scale.
```

## 14.11 Config tambahan (lihat docs/11)
```dotenv
MARKETS_CONFIG=./markets.yaml         # daftar MarketSpec
MAX_OPEN_EXPOSURE_MARKET=10
MAX_OPEN_EXPOSURE_ASSET=15
MAX_CORRELATED_DIRECTIONAL=20         # batas net-arah lintas aset korelasi
MAX_OPEN_EXPOSURE_GLOBAL=30
PER_MARKET_MIN_DEPTH=auto             # gating likuiditas
```

---

## 14.12 Catatan untuk AI Agent
- Generalisasi pipeline single-market jadi **per-market worker** + **MarketScanner**;
  jangan duplikasi logika domain. Domain tetap murni & reusable.
- Tambah MARKET (aset/timeframe) = ubah config, BUKAN ubah kode.
- Jangan aktifkan market baru tanpa validasi edge & likuiditas.
