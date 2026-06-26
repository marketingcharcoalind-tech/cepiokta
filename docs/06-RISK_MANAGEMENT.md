# 06 — Risk Management

> Risk Manager adalah **gerbang akhir**: setiap order live WAJIB lewat sini dan
> bisa di-VETO. Tidak ada jalur yang melewati Risk Manager.

## 6.1 Batas Keras (hard limits)
| Limit | Default awal | Aksi bila dilampaui |
|---|---|---|
| `MAX_NOTIONAL_ROUND` | $1–$5 (live awal) | veto order |
| `MAX_OPEN_EXPOSURE` | kecil | veto order baru |
| `MAX_DAILY_LOSS` | mis. 5–10% bankroll | kill-switch hari itu |
| `MAX_CONSEC_LOSSES` | mis. 5 | pause + alert |
| `MIN_BALANCE` | floor modal | stop trading |
| `MAX_ORDERS_PER_MIN` | rate sehat | throttle |

## 6.2 Position Sizing
> ✅ **Terverifikasi (live)**: market crypto up/down kena fee taker **~7%**
> (`crypto_fees_v2`). EV/Kelly di bawah WAJIB dihitung **net-of-fee** — gunakan
> `edge`/`p_win` setelah fee 7% + slippage, bukan gross. Sumber: docs/05 §5.3.
- Default: **fractional Kelly** dibatasi cap notional, cap % bankroll, dan depth.
```
kelly_fraction_calc = max(0, (p_win*(1) - (1-p_win)*ask) / ask)   # b = (1-ask)/ask form
size_kelly = KELLY_FRACTION * kelly_fraction_calc * bankroll / ask
size = min(
    size_kelly,
    MAX_NOTIONAL_ROUND / ask,
    (bankroll * MAX_BANKROLL_FRACTION) / ask,   # cap % bankroll per ronde
    depth_available * FILL_SAFETY,
)
if edge <= MIN_EDGE: size = 0                    # tidak entry kalau edge tak cukup
size = round_to(min_order_size, tick)
```
- `KELLY_FRACTION` kecil (mis. 0.1–0.25) di awal. Jangan full-Kelly.
- `MAX_BANKROLL_FRACTION` membatasi risiko per ronde sebagai % bankroll aktif.
  Saat `PAPER_TRADING=true`, bankroll = `PAPER_STARTING_BALANCE` (atau saldo
  paper berjalan); saldo wallet nyata hanya dipakai saat live.
- Hormati `min_order_size` & depth (jangan sapu seluruh book).

## 6.3 Kill-Switch (manual & otomatis)
- File/flag `KILL` atau perintah CLI → batalkan semua order terbuka, stop entry.
- Otomatis trigger bila: `MAX_DAILY_LOSS`, error auth, posisi tak terekonsiliasi,
  saldo di bawah floor.

## 6.4 Circuit Breaker (kondisi pasar/teknis)
Hentikan ENTRY (boleh tetap kelola/exit posisi) bila:
- WSS putus / reconnecting.
- Harga **stale**: tidak ada update Chainlink/orderbook > `STALE_MS`.
- Clock drift terdeteksi (NTP).
- Spread/likuiditas abnormal (depth < ambang).
- Latensi order ack > ambang.

## 6.5 Reconciliation & Konsistensi
- Setiap window: cocokkan order dikirim ↔ fill ↔ posisi ↔ resolusi ↔ saldo.
- Jika tidak cocok → freeze + alert (jangan trading "buta").
- Idempotency client-order-id agar retry tidak dobel.

## 6.6 Mode Gating
- `readonly`/`backtest`/`paper`: Risk Manager tetap aktif (latih logikanya),
  tapi tidak ada uang nyata.
- `live`: butuh `LIVE_CONFIRMED=yes`, limit super-konservatif, alert menyala.

## 6.7 Operasional
- Alert (Telegram/Discord) untuk: kill-switch, circuit breaker, drawdown,
  reconciliation mismatch, error auth/RPC.
- Heartbeat "saya hidup" tiap N menit.
- Audit log immutable untuk tiap keputusan & order.



---

## ADDENDUM (v1.1) — Kill-switch via Telegram
Kill-switch & pause kini juga dapat dipicu dari **Telegram** (lihat docs/12).
- Telegram memanggil `ControlFacade.kill()` / `pause()` yang memakai mekanisme
  kill-switch yang SAMA dengan CLI/file — bukan jalur terpisah.
- Aksi KILL/PAUSE dari Telegram WAJIB: (1) user ada di whitelist, (2) konfirmasi
  2-langkah, (3) tercatat di audit log.
- Telegram down TIDAK menonaktifkan kill-switch CLI/file (cadangan tetap ada).
- Alerting (§6.7) sekarang diutamakan via Telegram (menggantikan/melengkapi ALERT_WEBHOOK_URL).



---

## ADDENDUM (v1.3) — Risk Multi-Market & Korelasi
Tambah limit berlapis (lihat docs/14 §14.6):
- `MAX_OPEN_EXPOSURE_MARKET` (per market), `MAX_OPEN_EXPOSURE_ASSET` (per aset,
  5m+15m), `MAX_CORRELATED_DIRECTIONAL` (net-arah gabungan BTC/ETH/SOL),
  `MAX_OPEN_EXPOSURE_GLOBAL` (portofolio).
- Kill-switch/circuit breaker dua level: **global** (stop semua) & **per-market**
  (matikan satu market yang feed-nya stale).
- Daily-loss/drawdown dihitung **global** (portofolio).
- **Aturan korelasi**: perlakukan BTC/ETH/SOL sebagai satu faktor risiko —
  jangan all-in arah sama lintas aset.
