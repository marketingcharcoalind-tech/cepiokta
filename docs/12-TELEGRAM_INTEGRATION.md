# 12 — Telegram Integration (Control Plane & Notifications)

> Tujuan: memantau & mengontrol bot dari **Telegram** (tanpa harus buka
> terminal VPS), lengkap dengan **notifikasi push** dan **tombol interaktif**
> (inline keyboard). Telegram = lapisan kontrol/monitoring AUXILIARY — **bukan**
> bagian dari jalur kritikal trading.

---

## 12.1 Prinsip Desain (WAJIB)
1. **Decoupled / best-effort.** Jika Telegram down/lambat, bot HARUS tetap
   trading normal. Notifikasi lewat queue async; kegagalan kirim hanya di-log,
   tidak meng-crash bot dan tidak memblok event loop trading.
2. **Telegram = control plane, bukan data plane.** Perintah dari Telegram
   memanggil `ControlFacade` yang sama dipakai CLI — Telegram tidak menyentuh
   internal modul langsung.
3. **Keamanan dulu.** Kontrol jarak jauh (terutama KILL) = permukaan serangan.
   Wajib whitelist + konfirmasi (lihat §12.3).
4. **Anti-spam.** Notifikasi per-ronde bisa di-mute / di-rate-limit.

---

## 12.2 Penempatan Arsitektur
```
        ┌──────────────────────── BOT CORE (jalur kritikal) ───────────────────┐
        │  data -> signal -> strategy -> sizing -> RISK -> OMS -> store         │
        └───────────────┬───────────────────────────────────────┬─────────────┘
                        │ emit BotEvent (async queue)            │ baca/aksi via
                        ▼                                         │ ControlFacade
        ┌───────────────────────────┐                ┌───────────▼──────────────┐
        │  adapters/telegram.py      │  perintah ────>│  app/control.py          │
        │  - Notifier (push)         │<──── balasan   │  ControlFacade:          │
        │  - Command/Button handler  │                │  status/pnl/positions/   │
        │  (python-telegram-bot v20) │                │  pause/resume/kill/mode  │
        └───────────────────────────┘                └──────────────┬───────────┘
                                                                     │ kill() ->
                                                                     ▼
                                                          risk/manager.py
                                                          (kill-switch yg SAMA
                                                           dgn CLI/file)
```
- `ControlFacade` (`app/control.py`) = satu-satunya pintu kontrol; dipakai oleh
  Telegram DAN CLI. Memanggil RiskManager untuk kill/pause.
- Notifier subscribe ke event bus internal (queue) yang sudah dipakai alerting
  (menggantikan/melengkapi `ALERT_WEBHOOK_URL`).

---

## 12.3 Model Keamanan (KRITIKAL — jangan dilewati)
| Aturan | Implementasi |
|---|---|
| Token rahasia | `TELEGRAM_BOT_TOKEN` dari env/secret manager. JANGAN commit/log. |
| **Whitelist user** | Hanya `chat_id`/`user_id` di `TELEGRAM_ALLOWED_CHAT_IDS` yang boleh memberi perintah. Pesan dari ID lain → diabaikan + di-log. |
| Konfirmasi aksi berbahaya | KILL, PAUSE, ganti MODE wajib 2 langkah: tombol → "✅ Konfirmasi / ❌ Batal". |
| Aksi destruktif live | KILL saat MODE=live tetap butuh konfirmasi + dicatat audit log. |
| Anti-replay | Callback button pakai token sekali pakai / timestamp; abaikan callback kedaluwarsa. |
| Least privilege | Bot Telegram tidak bisa mengubah secret/limit via chat (limit hanya via config/redeploy). |
| Rate limit perintah | Throttle perintah per user untuk cegah abuse. |

> Tanpa whitelist, siapa pun yang tahu nama bot bisa mengirim `/kill`. Whitelist
> adalah syarat mutlak sebelum mengaktifkan perintah kontrol.

---

## 12.4 Notifikasi (push: BOT → Telegram)
Event yang dikirim (bisa diatur on/off via config):
| Event | Contoh isi | Default |
|---|---|---|
| Boot/startup | "🟢 5min-btc-polymarket v1.x — all systems go. mode=paper, balance $300" | ON |
| Hasil ronde | "#48247 DOWN @0.96 x250 → settles $1.00 +$10.00 | bal $310.00" | ON (mutable) |
| Hedge | "⚠️ #48251 book flipped 95/5 → micro-hedge -$1.07" | ON |
| Risk: kill-switch | "🛑 KILL-SWITCH aktif (alasan: max daily loss). Semua order dibatalkan." | ON |
| Risk: circuit breaker | "⚡ Circuit breaker: WSS down / price stale. Entry dihentikan." | ON |
| Drawdown / consec loss | "🔻 Drawdown -8% / 5 kalah beruntun → pause." | ON |
| Reconciliation mismatch | "❗ Mismatch posisi vs resolusi #48260 → FREEZE." | ON |
| Heartbeat | "💚 alive | uptime 6h | bal $455 | PnL hari ini +$155" tiap N menit | ON |
| Error | "🔴 Auth/RPC error: ..." | ON |

Ringkasan harian (opsional): PnL, jumlah ronde, win-rate, max DD.

---

## 12.5 Perintah & Tombol (Telegram → BOT)
**Slash commands:**
| Perintah | Fungsi |
|---|---|
| `/start` `/help` | Tampilkan menu + tombol |
| `/status` | Mode, uptime, balance, PnL hari ini, posisi terbuka, status WSS |
| `/balance` | Saldo terkini |
| `/pnl` | PnL hari ini & total, win-rate, ronde |
| `/positions` | Posisi terbuka saat ini |
| `/recent [n]` | n ronde terakhir (default 5) |
| `/config` | Parameter kunci (T_ENTRY_SEC, MAX_PRICE, limit) — read-only |
| `/pause` | Hentikan ENTRY baru (posisi berjalan tetap dikelola) — butuh konfirmasi |
| `/resume` | Lanjutkan entry — butuh konfirmasi |
| `/mute` `/unmute` | Matikan/hidupkan notifikasi per-ronde |
| `/kill` | 🛑 KILL-SWITCH: batalkan semua order + stop — konfirmasi WAJIB |

**Inline keyboard (menu utama):**
```
[ 📊 Status ]  [ 💰 PnL ]  [ 📈 Positions ]
[ ⏸️ Pause ]   [ ▶️ Resume ]
[ 🔕 Mute ]    [ 🛑 KILL ]
```
**Konfirmasi KILL:**
```
⚠️ Yakin KILL? Semua order dibatalkan & trading berhenti.
[ ✅ Ya, KILL sekarang ]   [ ❌ Batal ]
```

---

## 12.6 Module Spec (adapters/telegram.py + app/control.py)
```python
# Event yang dipancarkan core ke Notifier
@dataclass
class BotEvent:
    kind: str          # boot|round|hedge|kill|breaker|drawdown|mismatch|heartbeat|error|daily
    text: str          # pesan siap kirim (sudah diformat)
    severity: str      # info|warn|critical
    ts: datetime

class Notifier(Protocol):
    async def emit(self, e: BotEvent) -> None: ...   # non-blocking (queue), best-effort

# Fasad kontrol — dipakai Telegram & CLI
class ControlFacade(Protocol):
    async def status(self) -> dict: ...
    async def pnl(self) -> dict: ...
    async def positions(self) -> list: ...
    async def recent(self, n: int = 5) -> list: ...
    async def pause(self) -> None: ...      # -> RiskManager.pause_entry()
    async def resume(self) -> None: ...
    async def kill(self, reason: str) -> None: ...  # -> RiskManager.kill_switch()
    async def set_mute(self, on: bool) -> None: ...

class TelegramController(Protocol):
    async def start(self) -> None: ...      # mulai polling/long-poll, daftar handler
    async def emit(self, e: BotEvent) -> None: ...  # impl Notifier
    async def stop(self) -> None: ...
```
- Telegram handler WAJIB cek whitelist sebelum eksekusi perintah.
- `emit()` menulis ke `asyncio.Queue`; task pengirim terpisah → Telegram down
  tidak memblok core.

---

## 12.7 Library
- **python-telegram-bot v20+** (async native) — rekomendasi utama.
- Alternatif: `aiogram` v3.
- Mode update: **long polling** (paling simpel di VPS, tanpa domain/HTTPS) atau
  webhook (butuh domain+TLS). Awali dengan long polling.

---

## 12.8 Config (tambahan untuk docs/11)
```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=            # SECRET — dari @BotFather
TELEGRAM_ALLOWED_CHAT_IDS=     # whitelist, comma-separated (mis. 12345678,98765432)
TELEGRAM_NOTIFY_CHAT_ID=       # chat tujuan notifikasi (grup/DM)
TELEGRAM_PER_ROUND_NOTIFY=true # notifikasi tiap ronde (bisa di-mute)
TELEGRAM_HEARTBEAT_MIN=30      # interval heartbeat (menit); 0 = off
TELEGRAM_DAILY_SUMMARY=true
```
- Jika `TELEGRAM_ENABLED=false` atau token kosong → bot jalan normal tanpa Telegram.

---

## 12.9 Penanganan Kegagalan
- Telegram timeout/error → retry backoff terbatas, lalu drop pesan (log warning).
  **Tidak** mempengaruhi trading.
- Antrian penuh → buang notifikasi non-kritikal dulu (info), pertahankan critical.
- Saat reconnect Telegram, kirim ringkasan singkat "tersambung kembali".
- KILL via Telegram = jalur kontrol; jika Telegram down, kill-switch CLI/file
  tetap tersedia sebagai cadangan.

---

## 12.10 Setup (untuk pengguna)
1. Chat **@BotFather** di Telegram → `/newbot` → dapatkan **token**.
2. Dapatkan `chat_id` Anda (mis. chat **@userinfobot** atau lihat update API).
3. Isi `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_NOTIFY_CHAT_ID`.
4. Jalankan bot → kirim `/start` → muncul menu tombol.

---

## 12.11 Testing
- Unit: handler menolak chat_id non-whitelist; konfirmasi KILL 2-langkah; format pesan.
- Integrasi: mock Telegram API (jangan hit jaringan saat test).
- Ketahanan: simulasikan Telegram down → pastikan core tetap jalan & notifikasi
  ter-drop dengan aman.
- Keamanan: pastikan token tidak pernah ter-log.



---

# 12.12 ADDENDUM (v1.2) — Notifikasi Profit/Loss & Error (detail + setting)

> Bagian ini melengkapi §12.4 dengan **pengaturan notifikasi P&L dan error
> yang eksplisit & dapat dikonfigurasi**. Semua otomatis: bot memancarkan
> `BotEvent` → Notifier kirim ke Telegram tanpa intervensi manual.

## 12.12.1 Notifikasi Profit & Loss (otomatis)
| Notifikasi | Kapan dipicu | Setting (env) | Default |
|---|---|---|---|
| **Hasil tiap trade (win)** | Ronde menang & settle | `NOTIFY_PNL_WINS` | true |
| **Hasil tiap trade (loss)** | Ronde kalah | `NOTIFY_PNL_LOSSES` | true |
| **Per-trade ringkas** | Tiap ronde selesai (bisa di-mute) | `NOTIFY_PNL_PER_TRADE` | true |
| **Milestone profit** | Profit kumulatif sesi lewat kelipatan | `NOTIFY_PROFIT_MILESTONE`, `PROFIT_MILESTONE_STEP` | true, $50 |
| **Equity high baru (ATH)** | Balance cetak rekor tertinggi sesi | `NOTIFY_NEW_EQUITY_HIGH` | true |
| **Alert kalah beruntun** | N kekalahan berturut-turut | `ALERT_CONSEC_LOSSES` | 3 |
| **Alert drawdown** | Turun ≥ X% dari peak balance | `ALERT_DRAWDOWN_PCT` | 5 |
| **Peringatan dini daily loss** | Loss harian mendekati limit | `ALERT_DAILY_LOSS_PCT` | 4 |
| **Ringkasan harian/sesi** | Jam tertentu / akhir sesi | `NOTIFY_DAILY_PNL_SUMMARY`, `DAILY_SUMMARY_TIME` | true |

**Contoh pesan P&L:**
```
✅ #48247 DOWN @0.96 x250 → settles $1.00  +$10.00
   balance $310.00 | PnL hari ini +$10.00 | win 1/1

❌ #48258 UP @0.94 x200 → settles $0.00  -$188.00
   balance $267.00 | ⚠️ kalah ke-1 | drawdown -3.1%

🎯 Milestone: profit sesi tembus +$150.00 (bal $450.00)
🏆 Equity high baru: $455.30
🔻 ALERT: 3 kalah beruntun → entry di-PAUSE otomatis. /resume untuk lanjut.
📊 Ringkasan harian: 42 ronde | menang 38 | PnL +$155.30 | maxDD -4.2% | bal $455.30
```

> Catatan: notifikasi kalah & alert risiko TIDAK ikut ter-mute oleh `/mute`
> (mute hanya membungkam notifikasi per-ronde yang menang/rutin). Loss & risk
> selalu dikirim demi keselamatan modal.

## 12.12.2 Notifikasi Error (butuh perbaikan) — dengan cara fix
Error dikategorikan; yang **ACTION REQUIRED** dikirim segera + saran perbaikan +
biasanya memicu circuit breaker (hentikan entry, kelola posisi).

| Error | Severity | Action Required | Saran perbaikan (disertakan di pesan) |
|---|:---:|:---:|---|
| WSS gagal reconnect (persisten) | critical | ✅ | cek koneksi/internet & CLOB_WSS_URL |
| Auth / API key CLOB invalid | critical | ✅ | regenerate API credentials (CLOB V2) |
| RPC Polygon tak merespons | critical | ✅ | ganti POLYGON_RPC_URL / pakai fallback |
| Saldo USDC tidak cukup | critical | ✅ | top up USDC ke wallet |
| Gas (MATIC) tidak cukup | critical | ✅ | top up MATIC ke wallet |
| Order ditolak berulang | warn→critical | ⚠️ | cek tick_size / min_order_size / harga |
| Reconciliation mismatch | critical | ✅ | FREEZE; cek posisi manual sebelum lanjut |
| Clock drift (NTP) | warn | ✅ | sinkronkan jam server (NTP) |
| Config invalid saat startup | critical | ✅ | perbaiki .env (lihat detail di pesan) |
| Chainlink harga stale | warn | ⚠️ | cek CHAINLINK_BTCUSD_SOURCE / RPC |

**Setting error (env):**
| Setting | Arti | Default |
|---|---|---|
| `NOTIFY_ERRORS` | kirim error ke Telegram | true |
| `NOTIFY_ERROR_MIN_SEVERITY` | minimum severity yang dikirim: info\|warn\|critical | warn |
| `NOTIFY_ACTION_REQUIRED` | kirim error "butuh tindakan" + cara fix | true |
| `ERROR_DEDUP_WINDOW_SEC` | jangan spam error sama dalam window ini | 300 |

**Contoh pesan error (ACTION REQUIRED):**
```
🔴 ACTION REQUIRED — Gas (MATIC) tidak cukup
Bot tidak bisa mengirim order. Saldo MATIC: 0.0021.
👉 Perbaiki: top up MATIC ke wallet 0xABC...123, lalu kirim /resume.
Sejak 09:42 UTC · ⚡ entry dihentikan (circuit breaker) · posisi tetap dikelola.

🔴 ACTION REQUIRED — WSS terputus & gagal reconnect (5x)
Feed market berhenti. 👉 cek internet/endpoint CLOB_WSS_URL.
Entry dihentikan otomatis. Akan resume saat feed pulih.
```
Setiap pesan ACTION REQUIRED menyertakan tombol cepat: `[ 🔄 Retry ] [ ⏸️ Tetap Pause ] [ 📊 Status ]`.

## 12.12.3 Implementasi (untuk agent)
- Tambah `kind` BotEvent: `trade_win`, `trade_loss`, `profit_milestone`,
  `equity_high`, `consec_loss`, `drawdown`, `daily_loss_warn`, `daily_summary`,
  `error_action_required`.
- P&L tracker (di store/ledger) menghitung: PnL sesi, peak balance, drawdown,
  consec_loss, milestone terakhir → memicu event saat ambang terlewati.
- Error handler memetakan exception → kategori tabel di §12.12.2, lampirkan
  remediation text + set `action_required=true` + trigger circuit breaker bila perlu.
- Dedup: simpan hash (kind+detail) terakhir; tahan ulang dalam `ERROR_DEDUP_WINDOW_SEC`.
- Loss & ACTION REQUIRED meng-bypass `/mute`.

## 12.12.4 Config tambahan (lihat juga docs/11)
```dotenv
# --- P&L notifications ---
NOTIFY_PNL_PER_TRADE=true
NOTIFY_PNL_WINS=true
NOTIFY_PNL_LOSSES=true
NOTIFY_PROFIT_MILESTONE=true
PROFIT_MILESTONE_STEP=50
NOTIFY_NEW_EQUITY_HIGH=true
NOTIFY_DAILY_PNL_SUMMARY=true
DAILY_SUMMARY_TIME=23:59
ALERT_CONSEC_LOSSES=3
ALERT_DRAWDOWN_PCT=5
ALERT_DAILY_LOSS_PCT=4
# --- Error notifications ---
NOTIFY_ERRORS=true
NOTIFY_ERROR_MIN_SEVERITY=warn
NOTIFY_ACTION_REQUIRED=true
ERROR_DEDUP_WINDOW_SEC=300
```
