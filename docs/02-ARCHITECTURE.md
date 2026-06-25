# 02 — Architecture

## 2.1 Diagram Sistem (high-level)
```
                       ┌──────────────────────────────┐
                       │        CONFIG / SECRETS       │
                       │ env: wallet key, API creds,   │
                       │ risk limits, MODE flag        │
                       └───────────────┬──────────────┘
                                       │
 ┌─────────────────────────── MARKET DATA LAYER ──────────────────────────┐
 │  adapters/chainlink.py   adapters/gamma.py     adapters/clob_ws.py      │
 │  BTC/USD price truth   discover market 5m    orderbook + user fills(WSS)│
 └───────────┬──────────────────┬───────────────────────┬─────────────────┘
             ▼                   ▼                       ▼
 ┌──────────────────────────── STRATEGY ENGINE ───────────────────────────┐
 │ domain/market.py  interval-loader: window aktif & sisa detik            │
 │ domain/signal.py  trend/edge: Δ = price_now − start_price; p_win; edge  │
 │ domain/strategy.py entry filter (T-akhir, |Δ|>thr), hedge, exit         │
 └───────────────────────────────┬────────────────────────────────────────┘
                                  ▼
 ┌──────────────────────── EXECUTION / OMS LAYER ─────────────────────────┐
 │ exec/sizing.py  Kelly-fraksional / cap notional                         │
 │ exec/oms.py     build+sign EIP-712 (CLOB V2), FOK/FAK/GTC, fill tracking│
 └───────────────────────────────┬────────────────────────────────────────┘
                                  ▼ (setiap order lewat sini)
 ┌──────────────────────────── RISK MANAGER ──────────────────────────────┐
 │ risk/manager.py  VETO order; max/round; max daily loss; exposure cap;   │
 │                  kill-switch; circuit breaker (WSS down / price stale)   │
 └───────────────────────────────┬────────────────────────────────────────┘
                                  ▼
 ┌──────────────┐   ┌──────────────────┐   ┌────────────────────────────┐
 │ data/store.py │   │ monitoring/alert │   │ app/cli.py (boot+live log) │
 │ SQLite/PG     │   │ Prom+Grafana /   │   │ tampilan ala screenshot    │
 │ trades/PnL    │   │ Telegram/Discord │   │                            │
 └──────────────┘   └──────────────────┘   └────────────────────────────┘
```

Modul di screenshot referensi memetakan ke sini:
`interval-loader` → `domain/market.py`; `trend` → `domain/signal.py`;
`hedging` → `domain/strategy.py`; `websocket feed` → `adapters/clob_ws.py`;
`authenticating wallet` → `exec/oms.py` + `config`.

## 2.2 Tanggung Jawab Komponen
| Komponen | Tanggung jawab | Tidak boleh |
|---|---|---|
| Adapters | I/O eksternal mentah (REST/WSS/oracle) | menyimpan logika strategi |
| Domain | aturan murni (window, edge, keputusan) | melakukan I/O langsung |
| Exec/OMS | bentuk, tanda tangan, kirim, lacak order | melewati Risk Manager |
| Risk | gerbang akhir; veto; kill-switch | mengambil keputusan alpha |
| Data | persistensi & rekam | bisnis logic |
| App | orkestrasi per-mode (readonly/paper/live) | menulis logika domain |

## 2.3 Aliran Data (loop per ronde)
1. `market.py` deteksi window aktif + sisa waktu (dari Gamma + clock).
2. `clob_ws.py` stream orderbook (best bid/ask, depth) sisi UP & DOWN.
3. `chainlink.py` beri `price_now`; `signal.py` hitung Δ vs `start_price`.
4. `strategy.py`: jika `time_left < T_entry` DAN `|Δ| > threshold` DAN
   `ask_pemenang ≤ max_price` DAN `edge_bersih > 0` → usul order.
5. `sizing.py` tentukan jumlah; `risk/manager.py` veto/izinkan.
6. `oms.py` kirim order (paper: simulasi fill; live: CLOB V2). Track fill via WSS.
7. `strategy.py` pantau book; jika `book flip` / Δ menyusut → `micro-hedge`/exit.
8. Saat window tutup → catat resolusi & PnL ke `store.py`; update metrik.

## 2.4 Sequence: satu trade (paper/live)
```
Clock      Market     Signal     Strategy    Sizing     Risk       OMS        Store
  │  tick    │          │           │          │          │          │          │
  │─────────>│ window?  │           │          │          │          │          │
  │          │─ active ─┼──────────>│          │          │          │          │
  │          │  Δ, ask  │           │          │          │          │          │
  │          │          │─ edge ───>│ decide   │          │          │          │
  │          │          │           │─ size ──>│          │          │          │
  │          │          │           │          │─ check ─>│ veto?    │          │
  │          │          │           │          │          │─ allow ─>│ submit   │
  │          │          │           │          │          │          │─ fill ──>│
  │          │          │           │<──── book flip? micro-hedge ───│          │
  │          │  close   │           │          │          │          │ settle ─>│ PnL
```

## 2.5 Deployment Topology
- **Runtime**: single process async (fase awal) → boleh dipecah worker nanti.
- **DB**: SQLite (lokal/dev) → Postgres (prod).
- **Host**: VPS region dekat infrastruktur Polymarket untuk minimalkan latensi
  (strategi ekor-window sensitif latensi). Docker + systemd/`restart=always`.
- **Time sync**: NTP wajib; clock drift = bug strategi.
- **Observability**: Prometheus exporter + Grafana; alert ke Telegram/Discord.

## 2.6 Mode Operasi (state global)
`MODE = readonly | backtest | paper | live` (default `readonly`).
Adapter & OMS WAJIB cek MODE. `live` butuh `LIVE_CONFIRMED=yes` + risk aktif.
Lihat `docs/06-RISK_MANAGEMENT.md` & `docs/11-CONFIG_AND_SECRETS.md`.

## 2.7 Architecture Decision Records (ADR)
Simpan keputusan besar sebagai `docs/adr/NNNN-judul.md` (mis. pilihan bahasa,
pilihan DB, model fill). Agent WAJIB menulis ADR saat menyimpang dari blueprint.
```



---

## ADDENDUM (v1.1) — Control Plane (Telegram)
Ditambahkan lapisan **control plane** terpisah dari jalur kritikal trading:
```
 BOT CORE ──emit BotEvent (async queue)──> adapters/telegram.py (Notifier + buttons)
 BOT CORE <──ControlFacade (app/control.py)── perintah Telegram (/status /pause /kill ...)
                                             │ kill()/pause() -> risk/manager.py
```
- Telegram = AUXILIARY (best-effort). Telegram down TIDAK menghentikan trading.
- Detail penuh: `docs/12-TELEGRAM_INTEGRATION.md`.
