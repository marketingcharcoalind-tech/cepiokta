# 13 — Strategy Playbook

> Kodifikasi keputusan strategi. Inti: **SATU fair-value engine, tiga cara
> monetisasi**. Tidak ada "strategi sakti" — edge = kualitas fair-value +
> latensi + hedging + disiplin risk. Lihat juga docs/05 (signal/edge).

---

## 13.1 Taker vs Market Maker (recap)
| Aspek | Taker/Arb (#1, #2) | Market Maker (#3) |
|---|---|---|
| Order | FOK/FAK ambil dari book | GTC limit, 2 sisi |
| Profit | tangkap mispricing | tangkap spread |
| Fee | bayar taker | terima spread (+rebate) |
| Risiko | reversal + lomba latensi | adverse selection + inventory |
| Modal | kecil | besar + inventory mgmt |
| Kesulitan | rendah–menengah | tinggi |

Bot kita SAAT INI = taker (end-of-window). Playbook ini memperluasnya.

---

## 13.2 JANTUNG: Fair-Value Engine (dipakai SEMUA strategi)
Estimasi probabilitas & harga wajar token UP/DOWN secara real-time.
```
# Model dasar (Gaussian); ganti/kalibrasi sesuai data nyata
d          = (spot_now − start_price) / (sigma_asset * sqrt(time_left))
P_up       = N(d)                  # CDF normal standar
fair_up    = P_up                  # token settle $1 => fair = probabilitas
fair_down  = 1 − P_up
```
Input penting:
- `spot_now`, `start_price` dari feed yang SELARAS sumber resolusi (Chainlink).
- `sigma_asset` = estimasi volatilitas **per aset** (BTC≠ETH≠SOL) per √detik;
  estimasi dari realized vol jendela terbaru.
- `time_left` detik tersisa.

**Wajib KALIBRASI:** bandingkan `P_up` model vs hit-rate nyata (reliability
curve). Model yang tidak terkalibrasi = edge palsu. (docs/09 §9.4)

---

## 13.3 Strategi #1 — Fair-Value Opportunistic Taker  ⭐ DEFAULT
Ambil saat market menyimpang dari fair value melebihi biaya. Berlaku KAPAN SAJA
dalam window (bukan cuma 20 detik terakhir).
```
edge_buy = fair_side − best_ask_side − fee − est_slippage
if edge_buy >= MIN_EDGE and depth_ok and liquidity_ok:
    size = sizing(edge_buy, fair_side, best_ask_side, bankroll, caps, depth)
    BUY side (FOK/FAK) @ best_ask_side
```
- ✅ Superset end-of-window; realistis untuk ritel/menengah; modal kecil.
- ✅ Tetap pegang ke settlement ATAU exit/hedge bila fair berubah.
- ⚠️ Lomba latensi; edge tipis; butuh kalibrasi bagus.
- **Kapan pakai:** SEKARANG (Fase 1–3). Ini path utama.

## 13.4 Strategi #2 — Delta-Hedged Cross-Venue Arbitrage  🛡️ risiko terendah
Netralkan arah dengan BTC/ETH/SOL asli di venue lain.
```
Beli "UP" Polymarket (cost = ask*size)  +  short underlying perp setara delta
=> kunci selisih bila (1 − ask) > biaya hedge + fee + basis buffer
```
- ✅ Edge "paling murni" (terlindungi arah); varians rendah.
- ⚠️ Butuh akun+modal 2 venue, eksekusi 2-kaki cepat, kelola **basis risk**
  (feed Polymarket=Chainlink, hedge=exchange → bisa beda tipis).
- ⚠️ Termasuk arb antar-market Polymarket sendiri (konsistensi 5m/15m/hourly).
- **Kapan pakai:** setelah #1 stabil & Anda punya infra 2-venue.

## 13.5 Strategi #3 — Market Making + Fair-Value Anchor + Inventory Skew  📈 plafon tertinggi
Pasang quote 2 sisi di sekitar fair value; miringkan oleh inventory; tarik saat referensi bergerak.
```
bid = fair − half_spread − skew(inventory)
ask = fair + half_spread − skew(inventory)
post GTC bid & ask (ukuran sesuai risk)
on reference move > epsilon: cancel/replace cepat (anti adverse selection)
on inventory > cap: skew lebih agresif / stop satu sisi
```
- ✅ Profit spread terus-menerus; skalabel; bukan taruhan arah.
- ❌ TERSULIT: butuh latensi terendah; lambat = di-"makan" informed flow.
- **Kapan pakai:** hanya jika punya infra latensi rendah & model terkalibrasi.

---

## 13.6 Jalur Naik Level (progression)
```
[#1 Fair-Value Taker]  --(stabil, PnL live + setelah biaya)-->
[#2 Delta-Hedge Arb]   --(infra 2-venue, basis terkendali)-->
[#3 Market Making]     --(latensi rendah, kalibrasi terbukti)
```
Gate naik level:
- #1→#2: #1 profit live ≥ N hari; tersedia akun+modal venue hedge; basis diukur.
- #2→#3: latensi order ack rendah & stabil; reprice < pergerakan referensi;
  uji MM di paper menunjukkan spread-capture > adverse selection.

## 13.7 Checklist "Apakah Edge Benar-Benar Ada?"
- [ ] Reliability curve terkalibrasi (P_up model ≈ hit-rate nyata).
- [ ] Ada momen `fair − market_ask > fee + slippage` yang cukup sering.
- [ ] Edge bertahan SETELAH fee + slippage + latensi (ablation).
- [ ] Edge stabil lintas hari & lintas parameter (bukan overfit).
- [ ] Likuiditas cukup untuk ukuran yang diinginkan.
> Jika tidak lulus: strategi itu BUKAN edge. Revisi / matikan market itu.

## 13.8 Pemilihan Strategi per Kondisi
| Kondisi | Strategi cocok |
|---|---|
| Modal kecil, mulai, infra biasa | #1 Fair-Value Taker |
| Ingin varians rendah, punya 2 venue | #2 Delta-Hedge |
| Likuiditas tebal + latensi rendah | #3 Market Making |
| Likuiditas tipis / spread lebar | hindari MM; #1 selektif |
| Volatilitas ekstrem (berita) | perketat threshold / circuit breaker |

## 13.9 Anti-pattern
- ❌ Jalankan MM tanpa latensi rendah → dimakan informed flow.
- ❌ Taker tanpa kalibrasi fair-value → kira ada edge padahal tidak.
- ❌ #2 tanpa ukur basis risk → "arb" malah rugi karena feed beda.
- ❌ Naik level sebelum level bawah profit live.
