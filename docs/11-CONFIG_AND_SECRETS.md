# 11 — Config & Secrets

## 11.1 Prinsip
- **Tidak ada secret di source/Git.** Semua via env / secret manager.
- Config tervalidasi (`pydantic-settings`). MODE default `readonly`.
- `live` butuh `LIVE_CONFIRMED=yes` (gerbang ganda).

## 11.2 Environment Variables (.env.example — commit yang INI saja)
```dotenv
# --- mode ---
MODE=readonly                 # readonly | backtest | paper | live
LIVE_CONFIRMED=no             # harus 'yes' untuk live

# --- wallet / chain (SECRET; jangan commit nilai asli) ---
POLYGON_RPC_URL=
WALLET_PRIVATE_KEY=           # SECRET — gunakan secret manager di prod
CLOB_API_KEY=                 # hasil derive dari signature (CLOB V2)
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=

# --- endpoints (verifikasi versi terbaru!) ---
GAMMA_BASE_URL=
CLOB_REST_URL=
CLOB_WSS_URL=
CHAINLINK_BTCUSD_SOURCE=      # feed/data-stream BTC/USD di Polygon
CHAINLINK_FEED_TYPE=data_feed # data_feed (default) | data_stream (nanti)
CHAINLINK_MAX_STALENESS_SEC=120 # umur maks updatedAt (detik) sebelum stale

# --- strategy params (lihat docs/05) ---
T_ENTRY_SEC=20
DELTA_THRESHOLD=auto          # 'auto' = skala dgn volatilitas
MIN_PRICE=0.80
MAX_PRICE=0.99
MIN_EDGE=0.01
FLIP_RATIO=0.90
HEDGE_FRACTION=0.5

# --- sizing & risk (lihat docs/06) ---
BANKROLL_FLOOR=50
MAX_NOTIONAL_ROUND=5           # cap absolut $ per ronde (> 0)
MAX_BANKROLL_FRACTION=0.02     # cap % bankroll per ronde (0 < f <= 1)
FILL_SAFETY=0.8                # cap likuiditas: fraksi depth (0 < f <= 1)
MAX_OPEN_EXPOSURE=10
MAX_DAILY_LOSS_PCT=5
MAX_CONSEC_LOSSES=5
KELLY_FRACTION=0.25            # quarter-Kelly (0 < f <= 1); jangan full-Kelly
MAX_ORDERS_PER_MIN=30
STALE_MS=1500

# --- paper trading ---
PAPER_TRADING=true             # true = simulasi (tanpa order nyata / private key)
PAPER_STARTING_BALANCE=200     # saldo virtual awal (= niat modal live); > 0

# --- infra ---
DB_URL=sqlite+aiosqlite:///./btcbot.db
LOG_LEVEL=INFO
ALERT_WEBHOOK_URL=            # Telegram/Discord
```

## 11.3 Settings Loader (sketsa)
```python
from pydantic_settings import BaseSettings
class Settings(BaseSettings):
    mode: str = "readonly"
    live_confirmed: str = "no"
    t_entry_sec: int = 20
    min_price: Decimal = Decimal("0.80")
    max_price: Decimal = Decimal("0.99")
    # ... dst, dengan validator
    class Config: env_file = ".env"

    def assert_live_ok(self):
        if self.mode == "live" and self.live_confirmed != "yes":
            raise RuntimeError("live mode butuh LIVE_CONFIRMED=yes")
```

## 11.4 Manajemen Secret di Produksi
- Gunakan secret manager (cloud KMS / Vault / docker secrets), bukan `.env` plain.
- Private key idealnya di signer terisolasi; minimalkan exposure di memori/log.
- Rotasi kredensial CLOB; principle of least privilege pada akun.

## 11.5 .gitignore (wajib)
```gitignore
.env
*.db
__pycache__/
data/recordings/
secrets/
```



---

## ADDENDUM (v1.1) — Env Telegram
```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=            # SECRET — dari @BotFather, jangan commit/log
TELEGRAM_ALLOWED_CHAT_IDS=     # whitelist user yang boleh memberi perintah (comma-separated)
TELEGRAM_NOTIFY_CHAT_ID=       # chat tujuan notifikasi
TELEGRAM_PER_ROUND_NOTIFY=true # notif tiap ronde (bisa di-mute via /mute)
TELEGRAM_HEARTBEAT_MIN=30      # interval heartbeat (menit); 0 = off
TELEGRAM_DAILY_SUMMARY=true
```
- Jika `TELEGRAM_ENABLED=false`/token kosong → bot jalan normal tanpa Telegram.
- `TELEGRAM_BOT_TOKEN` diperlakukan sebagai secret (sama dgn private key).
- Settings harus memvalidasi: jika ENABLED=true maka token & NOTIFY_CHAT_ID wajib ada.



---

## ADDENDUM (v1.2) — Env Notifikasi P&L & Error
Detail perilaku: `docs/12-TELEGRAM_INTEGRATION.md` §12.12.
```dotenv
# --- P&L notifications (otomatis ke Telegram) ---
NOTIFY_PNL_PER_TRADE=true      # notif tiap ronde selesai (bisa di-mute)
NOTIFY_PNL_WINS=true           # notif saat menang
NOTIFY_PNL_LOSSES=true         # notif saat kalah (TIDAK ikut ter-mute)
NOTIFY_PROFIT_MILESTONE=true   # notif saat profit kumulatif lewat kelipatan
PROFIT_MILESTONE_STEP=50       # kelipatan milestone profit ($)
NOTIFY_NEW_EQUITY_HIGH=true    # notif balance rekor baru
NOTIFY_DAILY_PNL_SUMMARY=true  # ringkasan harian/sesi
DAILY_SUMMARY_TIME=23:59       # jam kirim ringkasan
ALERT_CONSEC_LOSSES=3          # alert setelah N kalah beruntun (+auto-pause)
ALERT_DRAWDOWN_PCT=5           # alert saat drawdown dari peak >= X%
ALERT_DAILY_LOSS_PCT=4         # peringatan dini sebelum MAX_DAILY_LOSS_PCT

# --- Error notifications (butuh perbaikan) ---
NOTIFY_ERRORS=true
NOTIFY_ERROR_MIN_SEVERITY=warn # info|warn|critical
NOTIFY_ACTION_REQUIRED=true    # error 'butuh tindakan' + cara fix + tombol cepat
ERROR_DEDUP_WINDOW_SEC=300     # anti-spam error yang sama
```
Aturan: notifikasi **loss** & **ACTION REQUIRED** selalu dikirim (bypass /mute).
Settings harus divalidasi & punya default aman.



---

## ADDENDUM (v1.3) — Env Multi-Market
```dotenv
MARKETS_CONFIG=./markets.yaml      # daftar MarketSpec (asset,timeframe,enabled,weight)
MAX_OPEN_EXPOSURE_MARKET=10
MAX_OPEN_EXPOSURE_ASSET=15
MAX_CORRELATED_DIRECTIONAL=20      # batas net-arah lintas aset korelasi
MAX_OPEN_EXPOSURE_GLOBAL=30
PER_MARKET_MIN_DEPTH=auto          # gating likuiditas per market
# feed per aset (verifikasi sumber Chainlink masing-masing)
CHAINLINK_ETHUSD_SOURCE=
CHAINLINK_SOLUSD_SOURCE=
```
`markets.yaml` contoh ada di docs/14 §14.3. Aktifkan market satu per satu
(enabled: true) hanya setelah lulus validasi edge + likuiditas.
