# 05 — Strategy Spec

> Strategi: **End-of-Window Settlement Arbitrage** ("take the already-settled
> side; buy 0.80–0.99; never fade"). Dokumen ini mendefinisikan logika secara
> presisi agar bisa dikoding & dibacktest. Ingat `docs/01` §1.5: edge nyata
> mungkin tipis/negatif — kode HARUS mengukurnya, bukan mengasumsikannya.

## 5.1 Definisi
- `start_price` = harga BTC (Chainlink) saat `window_start`.
- `price_now` = harga BTC terkini.
- `Δ = price_now − start_price`.
- `time_left` = `window_end − now` (detik).
- Outcome menang: `UP` jika `final_price ≥ start_price`, else `DOWN`.
- `ask_win` = harga ask terbaik untuk token sisi yang sedang memimpin
  (UP jika Δ>0, DOWN jika Δ<0).

## 5.2 Parameter (configurable, lihat docs/11)
| Param | Default contoh | Arti |
|---|---|---|
| `T_ENTRY_SEC` | 20 | Hanya entry bila `time_left ≤ T_ENTRY_SEC`. |
| `DELTA_THRESHOLD` | tergantung volatilitas | `|Δ|` minimum agar dianggap "memimpin meyakinkan". Sebaiknya skala dengan ATR/volatilitas window. |
| `MAX_PRICE` | 0.99 | Jangan beli di atas ini (edge habis). |
| `MIN_PRICE` | 0.80 | Jangan beli di bawah ini (kepastian terlalu rendah). |
| `MIN_EDGE` | > 0 setelah biaya | Ambang edge bersih minimum (wajib > fee 7% + slippage). |
| `FEE_RATE` | 0.07 (taker) | Fee `crypto_fees_v2`; dipakai `signal/fees.py`. Kalibrasi G1. |
| `FLIP_RATIO` | 0.90 | Jika book sisi lawan menguat ≥ ini ⇒ trigger hedge. |
| `MAX_NOTIONAL_ROUND` | kecil | Cap nilai per ronde (lihat sizing). |

## 5.3 Model Probabilitas & Edge (WAJIB, jangan diskip)
Jangan asumsikan "memimpin = pasti menang". Estimasikan probabilitas menang
`p_win` secara eksplisit, mis. model sederhana:

```
# z = jarak Δ relatif terhadap volatilitas sisa window
sigma_left = est_vol_per_sqrt_sec * sqrt(time_left)   # estimasi std gerak sisa
z = Δ / max(sigma_left, eps)
p_win = Phi(z)        # CDF normal standar; makin besar z makin pasti
```

Edge bersih per share (beli sisi memimpin @ ask_win, payout $1):
```
gross_edge = p_win * (1 - ask_win) - (1 - p_win) * ask_win
           = p_win - ask_win
net_edge   = gross_edge - fees_per_share - expected_slippage
ENTRY hanya jika: net_edge >= MIN_EDGE
```
> ✅ **Terverifikasi (live, KRITIS)**: market crypto up/down BERBIAYA —
> `feesEnabled:true`, `feeType:"crypto_fees_v2"`, `rate:0.07`, `takerOnly:true`.
> `fees_per_share` **bukan nol**: hitung dari fee taker ~7% via modul
> `signal/fees.py` (pluggable, default konservatif `FEE_RATE=0.07`;
> `# TODO reverse-engineer base notional vs profit — calibrate G1`). Semua
> net_edge/PnL/backtest/paper/live WAJIB net-of-fee.
> Insight kunci: entry menguntungkan hanya bila `p_win > ask_win + fee + slippage`.
> Jika market efisien, `ask_win ≈ p_win` ⇒ `net_edge ≤ 0`. Backtest harus
> membuktikan ada momen `p_win` jelas > `ask_win` **setelah fee 7%**.

## 5.4 Aturan Entry (pseudocode)
```
on each tick within active window:
    if time_left > T_ENTRY_SEC: return
    leader = UP if Δ > 0 else DOWN
    if abs(Δ) < DELTA_THRESHOLD: return          # belum meyakinkan
    ask = best_ask(token[leader])
    if not (MIN_PRICE <= ask <= MAX_PRICE): return
    p = estimate_p_win(Δ, time_left, vol)
    net_edge = p - ask - fee_per_share - est_slippage(size)
    if net_edge < MIN_EDGE: return
    size = sizing(net_edge, p, ask, bankroll, MAX_NOTIONAL_ROUND, depth)
    propose_order(BUY, token[leader], price=ask (FOK/FAK), size)
```

## 5.5 Hedging Logic ("book flip → micro-hedge")
Tujuan: batasi rugi saat keyakinan turun mendekati close (mis. BTC berbalik
atau orderbook flip 95/5 ke sisi lawan).

```
after fill on leader side, keep monitoring:
    recompute Δ, p_win each tick
    flip = depth(opposite) / (depth(leader)+depth(opposite))
    if p_win < P_EXIT  or  flip >= FLIP_RATIO:
        # opsi A: jual posisi leader (jika ada bid layak)
        # opsi B: beli sebagian sisi lawan (micro-hedge) untuk lock loss kecil
        hedge_size = clamp(position_size * HEDGE_FRACTION, min_order, depth_opp)
        propose_order(BUY, token[opposite], best_ask_opposite, hedge_size)
```
Catat biaya hedge sebagai `micro-hedge -$x` (seperti referensi). Hedge yang
benar = asuransi, bukan kosmetik: harus benar-benar mengurangi varians PnL.

## 5.6 Exit / Settlement
- Default: pegang sampai resolve (token settle $1 / $0).
- Early-exit hanya via hedge/jual jika `p_win` jatuh & ada likuiditas bagus.
- Setelah window tutup: rekonsiliasi resolusi → PnL → `store.py`.

## 5.7 Anti-pattern (DILARANG)
- ❌ Entry tanpa cek `net_edge` (asal "memimpin").
- ❌ Beli di atas `MAX_PRICE` (kejar harga).
- ❌ Sizing besar pada depth tipis (slippage menelan edge).
- ❌ Pakai feed harga ≠ sumber resolusi (basis risk).
- ❌ Menahan posisi kalah berharap "balik" (no fade, no hope).

## 5.8 Yang HARUS diukur backtest (lihat docs/09)
- Distribusi `p_win − ask_win` pada `time_left ≤ T_ENTRY_SEC`.
- Hit-rate aktual vs `p_win` (kalibrasi).
- PnL bersih setelah fee+slippage; max drawdown; varians.
- Sensitivitas terhadap `T_ENTRY_SEC`, `DELTA_THRESHOLD`, `MAX_PRICE`.
