# 01 — Project Context

## 1.1 Latar Belakang
`5min-btc-polymarket` adalah bot trading otomatis untuk market prediksi
**Polymarket "Bitcoin Up or Down — 5 menit"**. Tiap 5 menit terbentuk satu
ronde: market resolve **UP** jika harga BTC di akhir window ≥ harga di awal
window, dan **DOWN** jika lebih rendah. Tiap outcome adalah token yang settle
ke **$1.00** (menang) atau **$0.00** (kalah).

## 1.2 Strategi Inti (ringkas)
"**Take the already-settled side**": menjelang detik-detik akhir window, jika
pergerakan BTC sudah cukup besar (mis. −$91), outcome praktis hampir pasti.
Jika orderbook MASIH menjual sisi pemenang di bawah $1.00 (mis. $0.96), bot
beli dan menangkap selisih menuju $1.00. Detail penuh di
`docs/05-STRATEGY_SPEC.md`.

## 1.3 Tujuan (Goals)
- G1. Infrastruktur data realtime yang andal (orderbook, harga, resolusi).
- G2. Mampu **mengukur edge bersih** (setelah fee, slippage, kompetisi).
- G3. Paper trading realistis sebelum uang nyata.
- G4. Eksekusi live yang aman dengan risk manager & kill-switch.
- G5. Observability (log, metrik, alert) seperti boot sequence di referensi.

## 1.4 Bukan Tujuan (Non-Goals)
- ❌ Mereplikasi kurva profit screenshot (itu tidak realistis).
- ❌ Menjamin profit. Blueprint ini netral terhadap hasil.
- ❌ HFT kelas exchange (colocation FPGA). Targetnya bot ritel/menengah.
- ❌ Trading aset selain BTC 5m pada fase awal (boleh diperluas nanti).

## 1.5 Honest Assessment (WAJIB dibaca)
Screenshot referensi menunjukkan win-rate ~100% dengan harga beli $0.86–$0.99.
Secara teori pasar efisien hal ini bermasalah:

1. **Kontradiksi harga vs kepastian.** Jika outcome benar-benar pasti, bot lain
   sudah menaikkan harga ke ~$0.99–$1.00 → edge habis. Jika harga masih $0.86,
   artinya outcome BELUM pasti → ada risiko reversal nyata.
2. **Profil risiko buruk.** Beli @ $0.96 = risiko $0.96 untuk untung $0.04.
   Satu kalah menghapus ~24 menang. Win-rate harus sangat tinggi DAN stabil.
3. **Likuiditas.** Fill x1800 @ $0.99 di market 5 menit jarang ada tanpa
   slippage besar.
4. **Biaya tak terlihat.** Gas Polygon, spread, latensi, kompetisi.

**Sikap proyek:** perlakukan screenshot sebagai *spesifikasi UI/arsitektur*,
bukan bukti profit. Bangun untuk mencari tahu apakah edge benar-benar ada.

## 1.6 Asumsi & Ketidakpastian
- API Polymarket = **CLOB V2** (V1 sudah deprecated/diarsipkan). Verifikasi versi
  terbaru sebelum koding eksekusi. Lihat `docs/04-API_INTEGRATION.md`.
- Resolusi market crypto pendek memakai **Chainlink** di Polygon → patokan harga
  bot HARUS selaras dengan sumber resolusi (hindari basis risk).
- Ada **restriksi geografis** & kemungkinan regulasi. Tanggung jawab pengguna.

## 1.7 Glossary
| Istilah | Arti |
|---|---|
| Window/Round | Periode 5 menit satu market UP/DOWN. |
| Start price | Harga BTC saat window dibuka (acuan resolusi). |
| Δ (delta) | harga_now − start_price dalam window berjalan. |
| Settle | Pelunasan token ke $1.00 (menang) / $0.00 (kalah). |
| Edge | Ekspektasi profit setelah biaya = p·payout − cost. |
| Fade | Melawan arah; strategi ini "never fade". |
| Book flip | Orderbook tiba-tiba berbalik (mis. 95/5) → sinyal reprice. |
| Micro-hedge | Beli sebagian sisi lawan untuk membatasi rugi saat keyakinan turun. |
| Slippage | Selisih harga harapan vs harga fill aktual. |
| Basis risk | Risiko karena feed harga bot ≠ feed resolusi (oracle). |



---

## ADDENDUM (v1.3) — Perluasan Scope & Strategi
**Scope market diperluas** dari "BTC 5m saja" menjadi multi-market:
BTC/ETH/SOL × {5m, 15m} — dirancang multi, **diaktifkan bertahap** (BTC 5m dulu).
Lihat `docs/14-MULTI_MARKET_SCALING.md`.
**Strategi**: bukan satu strategi tunggal, melainkan SATU fair-value engine +
3 keluarga monetisasi (#1 taker, #2 delta-hedge, #3 market making). Default =
#1. Lihat `docs/13-STRATEGY_PLAYBOOK.md`. Catatan: BTC/ETH/SOL berkorelasi →
diversifikasi arah terbatas (lihat caveat docs/14 §14.1).
