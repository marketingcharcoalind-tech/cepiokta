# PROMPT GUIDE — Copy-Paste untuk AI Coding Agent  (v1.2)

> Panduan ini berisi **prompt siap tempel** untuk membangun `5min-btc-polymarket`
> dari nol sampai bot running, fase demi fase, task demi task.
>
> **Cara pakai:**
> 1. Salin seluruh folder blueprint ini ke root repo proyek baru Anda.
> 2. Tempel prompt secara berurutan ke agent (Antigravity / Kiro / Codex /
>    opencode / Cursor / Claude Code). Satu prompt = satu task.
> 3. Setelah tiap prompt: jalankan test, update `PROGRESS_TRACKER.md`, baru lanjut.
> 4. Jangan loncat fase. Setiap fase ada **GATE** yang harus lulus.
>
> **Aturan emas (ingatkan agent kapanpun perlu):**
> - Default `MODE=readonly`. Tidak ada order live sebelum Fase 0–2 lulus.
> - Tidak ada secret di source. Decimal untuk uang. UTC untuk waktu. Test-first.

### 📌 Changelog
- **v1.2** — Injeksi **TEMUAN NYATA** dari VPS jaringan-bersih (verifikasi lawan
  API live + dikunci sebagai fixture). Tambah blok **✅ VERIFIED REALITY** di
  bawah, plus callout `> ✅ VERIFIED:` di tiap prompt terkait. Koreksi kritis:
  market crypto up/down **BERBIAYA ~7% taker** (asumsi zero-fee SALAH); skema
  adapter nyata (Gamma `closed` flag, WS `/ws/market` LIST/DICT, Chainlink
  failover + Data Streams), resolusi via `outcomePrices`, retensi book. Sinkron
  docs/04,05,06,07,09.
- **v1.1** — Telegram control plane (T.1–T.3) + addendum notifikasi P&L/Error.
- **v1.0** — Prompt fase 0–4 + strategi (S.*) + multi-market (M.*).

---

## ✅ VERIFIED REALITY (v1.2 — ground truth dari VPS jaringan-bersih)

> Semua poin di bawah **sudah dikonfirmasi lawan API live** dan **dikunci sebagai
> fixture**. Ini **sumber kebenaran** untuk SEMUA prompt di bawah. Jika prompt
> lama bertentangan dengan blok ini, blok ini yang menang.

1. **Slug market**: `{asset}-updown-{5m|15m}-{epoch}`
   (mis. `btc-updown-5m-1782480000`). `round_no = epoch` window;
   `round_no % 300 == 0` (5m) / `% 900 == 0` (15m). Cross-check fixture:
   `1782480000` → `2026-06-26T13:25:00Z`.
2. **Gamma discovery (market hidup)**: `GET /markets?closed=false&active=true`
   lalu filter **regex slug**. **Resolusi (market selesai)**: WAJIB `closed=true`
   (tanpa flag itu hasil selalu kosong → `--resolve-backfill` lapor `resolved:0`).
   Window ronde: `eventStartTime` → `endDate` (BUKAN `startDate` = tanggal
   listing). Header **`User-Agent` browser wajib**.
3. **FEE (KRITIS)**: market crypto up/down → `feesEnabled:true`,
   `feeType:"crypto_fees_v2"`, `rate:0.07`, `takerOnly:true`. **Asumsi zero-fee
   SALAH.** Semua `net_edge`/PnL/backtest/paper/live **harus mengurangi fee taker
   ~7%**. Implementasi: modul `signal/fees.py` pluggable, default konservatif
   `FEE_RATE=0.07`, `# TODO reverse-engineer base notional vs profit — calibrate G1`.
4. **Chainlink (harga live)**: BTC/USD **Data Feed**
   `0xc907E116054Ad103354f2D350FD2514433D57F6f` (Polygon). RPC failover terurut:
   `chainstack → publicnode → blastapi → blockpi`, **`User-Agent` browser** untuk
   RPC publik (403 tanpa UA). Catatan: **resolusi** market memakai Chainlink
   **Data Streams** (≠ Data Feeds) → `settlement_price` saat ini **best-effort**;
   adapter Data Streams menyusul (B2b) untuk presisi.
5. **CLOB WebSocket**: path **`/ws/market`** (BUKAN `/ws`). Market channel kirim
   **DUA bentuk**: (a) **LIST** = snapshot awal, (b) **DICT** = `price_change`.
   `side=BUY` → bids, `side=SELL` → asks. Keepalive: `ping_interval=None` +
   heartbeat aplikasi `"PING"` tiap **10 dtk** (server memutus ~45 dtk bila tak ada).
6. **Resolusi outcome**: `outcomes` & `outcomePrices` datang sebagai **JSON-string**
   → `json.loads`. Pemenang = index bernilai `"1"`: `["1","0"]`→**UP**,
   `["0","1"]`→**DOWN** (`outcomes` = `["Up","Down"]`). `umaResolutionStatus`
   **JANGAN** dijadikan syarat; cukup `closed==true && outcomePrices definitif`
   (tepat satu ≥0.99, sisanya ≤0.01). `resolution_source="gamma"`.
7. **Retensi `book_snapshots`**: tulis hanya saat **best berubah**, maks **1/dtk**
   bila best sama, **full-res 45 dtk terakhir** window
   (~16–20k baris/ronde; runway minggu-an). Schema kolom tetap (tanpa migrasi).
8. **SDK**: `py-clob-client` **diarsipkan Mei 2026** → gunakan **CLOB V2** (REST +
   signing **EIP-712** sendiri).
9. **Fixtures terkunci**: `tests/fixtures/gamma_resolved_markets.json` (+ fixtures
   book/round dari VPS) = **ground truth**, jangan regresi.

---

## 🧭 PROMPT 0 — Kickoff (tempel paling awal, sekali saja)

```
Kamu adalah engineer untuk proyek "5min-btc-polymarket". Sebelum menulis kode:
1. Baca file AGENTS.md dan SELURUH folder docs/ (01 sampai 11) serta
   .kiro/specs/btc-bot/. Ini adalah sumber kebenaran proyek.
2. Ringkas kembali ke saya dalam 10 bullet: tujuan, arsitektur berlapis,
   aturan MODE (readonly|backtest|paper|live), safety gates G0-G4, dan
   batasan keamanan (no secret di source, no order live sebelum G0-G2).
3. JANGAN menulis kode dulu. Tunggu saya menempel PROMPT berikutnya.

Konvensi yang wajib kamu patuhi sepanjang proyek:
- Python 3.11+, async-first (asyncio), type hints + mypy/pyright strict,
  lint ruff, format black.
- Semua uang/harga pakai decimal.Decimal (JANGAN float). Waktu UTC aware.
- Semua I/O eksternal di balik Protocol/adapter agar bisa di-mock.
- Test-first: tiap modul ada unit test; clock & RNG injectable (deterministik).
- Hormati MODE flag (default readonly). Live butuh LIVE_CONFIRMED=yes.
- Tulis ADR di docs/adr/ jika menyimpang dari blueprint.
```

---

# ════════════════════════════════════════════════
# FASE 0 — Scaffolding & Read-only Data  (Gate G0)
# ════════════════════════════════════════════════

## PROMPT 0.1 — Setup repo & tooling
```
Buat scaffolding proyek sesuai struktur di AGENTS.md §5 (src/btcbot/...).
Deliverable:
- pyproject.toml (pakai uv ATAU poetry) dengan deps awal: httpx, websockets,
  web3, eth-account, pydantic-settings, aiosqlite, structlog, prometheus-client,
  dan dev: pytest, pytest-asyncio, ruff, black, mypy, respx, freezegun.
- Struktur folder kosong: src/btcbot/{config,adapters,domain,exec,risk,data,backtest,app}
  dan tests/ yang mirror-nya.
- Config tooling: ruff.toml, mypy strict, .pre-commit-config.yaml, Makefile/justfile
  (target: lint, type, test, run-readonly).
- GitHub Actions CI: lint + type-check + test pada PR.
- README repo singkat (cara setup venv & run).
DoD: `make lint`, `make type`, `make test` jalan (test boleh kosong/placeholder).
Jangan implement logika bisnis dulu.
```

## PROMPT 0.2 — Settings, MODE gating, secrets
```
Implement src/btcbot/config/settings.py memakai pydantic-settings sesuai
docs/11-CONFIG_AND_SECRETS.md.
Deliverable:
- Class Settings memuat semua env var di docs/11 (mode, endpoints, strategy
  params, sizing/risk, infra). Pakai Decimal untuk harga/uang.
- Validator + method assert_live_ok() yang raise jika mode==live & live_confirmed!=yes.
- File .env.example (commit) sesuai docs/11. Tambah .env, *.db, secrets/ ke .gitignore.
- Helper get_settings() ber-cache.
DoD: unit test: default mode=readonly; live tanpa LIVE_CONFIRMED=yes raise error;
parsing Decimal benar. Tidak ada nilai secret asli di repo.
```

## PROMPT 0.3 — Clock adapter (deterministik)
```
Implement src/btcbot/adapters/clock.py sesuai docs/08 §8.1.
Deliverable: Protocol Clock; SystemClock (UTC now); SimClock dengan set()/advance().
DoD: unit test SimClock deterministik; semua waktu tz-aware UTC.
```

## PROMPT 0.4 — Gamma adapter (discovery market)
> ✅ VERIFIED (lihat VERIFIED REALITY #1,#2,#3): ganti placeholder/"harus
> diverifikasi" → filter **regex slug** `{asset}-updown-{5m|15m}-{epoch}`;
> discovery pakai `?closed=false&active=true`, resolusi WAJIB `?closed=true`;
> window ronde dari `eventStartTime`→`endDate` (BUKAN `startDate`); **parse field
> fee** (`feesEnabled/feeType=crypto_fees_v2/rate=0.07/takerOnly`); header
> `User-Agent` browser wajib; simpan satu respons asli sebagai fixture & jangan
> regresi `tests/fixtures/gamma_resolved_markets.json`.
```
Implement src/btcbot/adapters/gamma.py sesuai docs/04 §4.3 & docs/08 §8.2.
Deliverable:
- GammaClient (Protocol) + implementasi httpx async: next_btc5m_round() & get_round().
- Mapping respons -> domain Round (docs/07): condition_id, round_no, token_id_up/down,
  window_start/end, start_price, tick_size, min_order_size, status, resolved_outcome.
- CATATAN: endpoint Gamma harus diverifikasi dari dokumentasi resmi Polymarket
  terbaru. Jika belum pasti, buat client dengan base_url dari Settings + TODO,
  dan tulis test pakai respx dengan fixture JSON contoh.
DoD: unit test mapping pakai respx (mock HTTP). Tidak hit jaringan asli saat test.
```

## PROMPT 0.5 — CLOB WebSocket (market data)
> ✅ VERIFIED (lihat VERIFIED REALITY #5): path **`/ws/market`** (BUKAN `/ws`);
> market channel kirim **dua bentuk** — **LIST** = snapshot awal, **DICT** =
> `price_change`; `side=BUY`→bids, `side=SELL`→asks; keepalive `ping_interval=None`
> + heartbeat aplikasi `"PING"` tiap 10s (server memutus ~45s bila sepi); stale
> 30s → reconnect. Normalisasi pesan ke `BookState` per asset.
```
Implement src/btcbot/adapters/clob_ws.py sesuai docs/04 §4.4 & docs/08 §8.4.
Deliverable:
- ClobWS (Protocol) + impl: stream_market(token_ids) -> AsyncIterator[BookUpdate]
  dan stream_user() (boleh stub dulu, dipakai Fase 2/3).
- Reconnect exponential backoff, heartbeat/ping, deteksi STALE_MS -> emit event
  yang nanti dikonsumsi circuit breaker.
- Parsing pesan -> domain OrderBook/BookLevel (docs/07).
DoD: unit test dengan fake WebSocket server/mock: simulasikan update, disconnect,
reconnect, dan kondisi stale. Deterministik.
```

## PROMPT 0.6 — Chainlink price feed (price truth)
> ✅ VERIFIED (lihat VERIFIED REALITY #4): BTC/USD **Data Feed**
> `0xc907E116054Ad103354f2D350FD2514433D57F6f` (Polygon); RPC failover
> `chainstack → publicnode → blastapi → blockpi` + `User-Agent` browser (RPC
> publik 403 tanpa UA); gagal/timeout/HTTP/`price<=0`/stale → endpoint berikut;
> semua gagal → `AllRpcFailedError`. Catatan: **resolusi** market pakai Chainlink
> **Data Streams** (≠ Data Feeds) → `settlement_price` best-effort; adapter Data
> Streams menyusul (B2b).
```
Implement src/btcbot/adapters/chainlink.py sesuai docs/04 §4.6 & docs/08 §8.5.
Deliverable:
- PriceFeed (Protocol) + impl: price_now() & start_price(window_start) untuk
  BTC/USD di Polygon (selaras sumber resolusi market).
- CATATAN: cara baca Chainlink (feed address / data stream) harus diverifikasi.
  Jika belum pasti, abstraksikan di balik Protocol + impl placeholder yang
  membaca dari Settings.CHAINLINK_BTCUSD_SOURCE, plus FakePriceFeed untuk test.
DoD: unit test pakai FakePriceFeed; Decimal; tz-aware.
```

## PROMPT 0.7 — Store + Recorder (persistensi)
> ✅ VERIFIED (lihat VERIFIED REALITY #6,#7): retensi `book_snapshots` =
> write-on-change + throttle 1/dtk bila best sama + full-res 45 dtk terakhir
> (`BOOK_SAMPLE_MS`/`BOOK_FINEGRAIN_SEC`/`BOOK_PERSIST_MODE`); schema tetap (tanpa
> migrasi). **Resolution recorder** (`data/resolver.py`): resolusi WAJIB
> `closed=true`, `outcomes`/`outcomePrices` = JSON-string → `json.loads`, pemenang
> = index bernilai `"1"`, kolom additive `settlement_price`/`resolution_source`
> (`"gamma"`), backfill via `--resolve-backfill`. `umaResolutionStatus` bukan syarat.
```
Implement src/btcbot/data/store.py dan data/recorder.py sesuai docs/07 & docs/08 §8.12.
Deliverable:
- store.py: koneksi DB (aiosqlite, DB_URL dari Settings), migrasi/membuat tabel
  sesuai skema SQL docs/07, dan fungsi insert/query untuk rounds, book_snapshots,
  signals, orders, fills, round_results, equity_curve. Simpan kolom 'mode'.
- recorder.py: konsumsi stream Gamma+WSS+Chainlink, tulis book_snapshots & signals
  & rounds & resolusi. Tandai gap saat WSS putus.
DoD: unit test CRUD pakai SQLite in-memory; idempotent create tables.
```

## PROMPT 0.8 — CLI boot + runner readonly
```
Implement src/btcbot/app/cli.py sesuai docs/08 §8.14.
Deliverable:
- Boot sequence persis gaya referensi (connecting/authenticating/opening ws/
  loading interval-loader/trend/hedging -> [ ok ], lalu "all systems go.").
- Wiring dependency: inject adapters (gamma, clob_ws, chainlink, clock, store)
  ke recorder. Baca Settings; jika MODE!=readonly, untuk sekarang tetap jalankan
  recorder TANPA order.
- Loop readonly: temukan ronde aktif, rekam orderbook+harga+resolusi ke DB,
  cetak log per ronde (round_no, Δ, balance simulasi tetap).
- Graceful shutdown (SIGINT) + structlog JSON.
DoD: `make run-readonly` jalan end-to-end terhadap adapter mock/fixture tanpa
mengirim order; data masuk DB. Integration test happy-path memakai fake adapters.
```

## ✅ GATE G0 — verifikasi sebelum lanjut
```
Lakukan review G0:
1. Konfirmasi NOL jalur kode yang mengirim order (grep place_order/sign -> harus
   belum dipakai / di belakang MODE).
2. Jalankan bot mode readonly beberapa jam (atau replay fixture panjang) dan
   tunjukkan jumlah baris book_snapshots/signals/rounds yang terekam.
3. Pastikan lint+type+test hijau di CI.
Laporkan ringkasan dan update PROGRESS_TRACKER.md (tandai Fase 0 selesai).
```

---

# ════════════════════════════════════════════════
# FASE 1 — Backtest / Replay  (Gate G1)
# ════════════════════════════════════════════════

## PROMPT 1.1 — Interval loader (domain murni)
> ✅ VERIFIED (lihat VERIFIED REALITY #1): cadence epoch `%300==0` (5m) /
> `%900==0` (15m); slug `{asset}-updown-{5m|15m}-{epoch}`; cross-check fixture
> `1782480000` → `2026-06-26T13:25:00Z`.
```
Implement src/btcbot/domain/market.py sesuai docs/08 §8.6 & docs/05.
Deliverable: current_window(now), time_left(now), is_entry_window(now, T_ENTRY_SEC).
Murni (tanpa I/O), pakai Clock yang diinject.
DoD: unit test edge cases (sebelum/saat/sesudah window, batas T_ENTRY_SEC).
```

## PROMPT 1.2 — Signal engine (edge math)
> ✅ VERIFIED (lihat VERIFIED REALITY #3): `net_edge = p_win − ask_win −
> fee_per_share − expected_slippage`, dengan `fee_per_share` dari
> `crypto_fees_v2` **7% taker** (modul `signal/fees.py` pluggable, default
> `FEE_RATE=0.07`); σ (`sigma_left`) dikalibrasi di G1 dari realized vol.
> JANGAN biarkan asumsi zero-fee.
```
Implement src/btcbot/domain/signal.py sesuai docs/05 §5.1-5.3 & docs/08 §8.7.
Deliverable: compute(round, price_now, now, vol) -> Signal yang menghitung
Δ, sigma_left, z, p_win (CDF normal), ask_win, dan net_edge =
p_win - ask_win - fee_per_share - expected_slippage.
DoD: unit test numerik (z besar -> p_win->1; net_edge turun saat fee/slippage naik).
Fungsi murni & deterministik.
```

## PROMPT 1.3 — Strategy (entry/hedge/exit, never-fade)
> ✅ VERIFIED (lihat VERIFIED REALITY #3): `MIN_EDGE` wajib **> fee 7% +
> slippage** (entry hanya bila edge bersih lolos biaya nyata); taker-only;
> tetap **never-fade** & cap harga (`MIN_PRICE`/`MAX_PRICE`).
```
Implement src/btcbot/domain/strategy.py sesuai docs/05 §5.4-5.7 & docs/08 §8.8.
Deliverable: on_tick(signal, book, position) -> list[Decision]
(EnterOrder|Hedge|Exit|NoOp). Terapkan filter: time_left<=T_ENTRY_SEC,
|Δ|>=threshold, MIN_PRICE<=ask<=MAX_PRICE, net_edge>=MIN_EDGE. Hedge saat
p_win<P_EXIT atau book flip>=FLIP_RATIO. DILARANG fade / beli > MAX_PRICE.
DoD: unit test untuk tiap cabang keputusan + anti-pattern (harus NoOp).
```

## PROMPT 1.4 — Sizing (fractional Kelly + caps)
> ✅ VERIFIED (lihat VERIFIED REALITY #3): EV/Kelly dihitung **net-of-fee** —
> gunakan edge setelah fee taker 7% + slippage, bukan gross. Size 0 bila
> `net_edge <= MIN_EDGE`.
```
Implement src/btcbot/exec/sizing.py sesuai docs/06 §6.2 & docs/08 §8.9.
Deliverable: size(signal, bankroll, depth, limits) -> Decimal dengan fractional
Kelly (KELLY_FRACTION kecil) dibatasi MAX_NOTIONAL_ROUND, depth, min_order_size, tick.
DoD: property test: size tidak pernah > cap; tidak pernah > depth*FILL_SAFETY;
size 0 saat net_edge<=0.
```

## PROMPT 1.5 — Replay engine + fill model
> ✅ VERIFIED (lihat VERIFIED REALITY #3,#5,#6): fill model + **fee taker 7%** +
> slippage telusur level; settlement PnL pakai **label UP/DOWN dari Gamma**
> (`outcomePrices`); book input = recorded (sudah ternormalisasi LIST/DICT,
> retensi non-seragam → last-value-carried-forward).
```
Implement src/btcbot/backtest/replay.py sesuai docs/09 §9.3 & docs/08 §8.13.
Deliverable:
- Putar ulang book_snapshots+signals+rounds (dari DB/Parquet) memakai SimClock
  -> SignalEngine -> Strategy -> Sizer -> PaperOMS(fill model).
- Fill model REALISTIS: FOK/FAK terisi hanya jika ask<=harga & depth cukup;
  slippage menelusuri level book; latensi (keputusan t, fill t+latency);
  opsi kompetisi (hanya surplus size).
- Tulis round_results & equity_curve (mode=backtest).
DoD: backtest deterministik (seed tetap) menghasilkan PnL yang reproducible.
```

## PROMPT 1.6 — Laporan metrik & kalibrasi
> ✅ VERIFIED (lihat VERIFIED REALITY #3,#6): reliability curve dgn **label nyata
> Gamma**; ablation **wajib sertakan fee 7%**; headline **Net PnL setelah fee**;
> kalibrasi σ dari realized vol data terekam.
```
Tambahkan reporting di backtest (script/CLI) sesuai docs/09 §9.4.
Deliverable: cetak/ча simpan Net PnL, ROI, win-rate, distribusi net_edge saat
entry, reliability curve (p_win vs realized), max drawdown, varians,
sensitivitas grid (T_ENTRY_SEC x DELTA_THRESHOLD x MAX_PRICE), dan ablation
(dengan vs tanpa fee/slippage/latensi). Output tabel + (opsional) plot PNG.
DoD: jalankan pada data terekam Fase 0 dan tampilkan laporannya ke saya.
```

## ✅ GATE G1 — keputusan berbasis data
> ✅ VERIFIED (lihat VERIFIED REALITY #3,#6): keputusan edge harus **net fee 7% +
> slippage**, stabil lintas beberapa hari (anti-overfit), pakai **label Gamma**.
> Di sini juga kalibrasi `FEE_RATE` (reverse-engineer `crypto_fees_v2`) & σ.
```
Buat ringkasan G1: apakah net_edge > 0 STABIL di beberapa rentang parameter dan
beberapa hari data berbeda (bukan overfit)? Sertakan reliability curve & ablation.
Jika edge <= 0 setelah biaya, NYATAKAN dengan jujur dan usulkan: revisi strategi
atau berhenti. Update PROGRESS_TRACKER.md. JANGAN lanjut ke Fase 2 tanpa keputusan.
```

---

# ════════════════════════════════════════════════
# FASE 2 — Paper Trading  (Gate G2)
# ════════════════════════════════════════════════

## PROMPT 2.1 — Risk manager
```
Implement src/btcbot/risk/manager.py sesuai docs/06 & docs/08 §8.11.
Deliverable: check(order, state) -> Allow|Veto(reason); on_event(evt) untuk
kill-switch & circuit breaker (WSS down, price stale, clock drift, spread aneh);
should_halt(). Tegakkan semua limit di docs/06 §6.1.
DoD: unit test tiap limit (veto saat lampaui), kill-switch & circuit breaker
trigger benar; property test "tak pernah loloskan order > limit".
```

## PROMPT 2.2 — OMS mode paper
> ✅ VERIFIED (lihat VERIFIED REALITY #3): paper fills **mengurangi fee taker 7%**
> (sama dengan replay) agar paper konsisten net-of-fee dengan backtest.
```
Implement src/btcbot/exec/oms.py mode paper sesuai docs/08 §8.10.
Deliverable: submit(decision) -> OrderAck dengan simulasi fill realtime memakai
fill model yang sama dengan replay (slippage/latensi). WAJIB panggil
RiskManager.check() sebelum "submit". Idempotency client-order-id. Catat
orders/fills ke store (mode=paper).
DoD: unit/integration test: order ter-veto tidak tereksekusi; fill tercatat;
idempotent (retry tidak dobel).
```

## PROMPT 2.3 — Paper runner + ledger
> ✅ VERIFIED (lihat VERIFIED REALITY #3,#5): ledger PnL **net-of-fee 7%**; data
> live via WS `/ws/market` (LIST/DICT ternormalisasi). Log per ronde net-of-fee.
```
Implement src/btcbot/app/paper.py sesuai docs/08 §8.14 & docs/10 Fase 2.
Deliverable: loop realtime (adapters live data, MODE=paper) -> signal -> strategy
-> sizing -> risk -> paper OMS. Ledger PnL & equity_curve. Log per ronde persis
gaya referensi (round_no, fill @ price xSize, settles $1.00 +PnL, balance,
'book flipped' -> micro-hedge bila ada).
DoD: jalan realtime beberapa jam; output log mirip screenshot; PnL tercatat.
```

## PROMPT 2.4 — Reconciliation + alert dasar
> ✅ VERIFIED (lihat VERIFIED REALITY #3,#6): reconciliation **sertakan fee 7%**;
> resolusi posisi pakai label Gamma (`outcomePrices`); bandingkan PnL paper vs
> backtest harus konsisten **net-of-fee**.
```
Tambah rekonsiliasi sesuai docs/06 §6.5 & docs/09. Cocokkan order<->fill<->posisi
<->resolusi<->saldo tiap ronde; mismatch -> freeze + alert. Implement alert
sederhana ke ALERT_WEBHOOK_URL (Telegram/Discord) untuk kill-switch, circuit
breaker, drawdown, mismatch.
DoD: test mismatch men-trigger freeze; alert terkirim (mock webhook).
```

## ✅ GATE G2
```
Jalankan paper trading minimal beberapa RATUS ronde. Bandingkan PnL paper vs
prediksi backtest (harus konsisten). Pastikan nol mismatch reconciliation.
Laporkan & update PROGRESS_TRACKER.md. Jangan ke Fase 3 jika tidak konsisten.
```

---

# ════════════════════════════════════════════════
# FASE 3 — Live Micro-stakes  (Gate G3)
# ════════════════════════════════════════════════

> ⚠️ Mulai uang nyata. Verifikasi dulu semua item di docs/04 §4.8.

## PROMPT 3.1 — Signer EIP-712 (CLOB V2) + auth
> ✅ VERIFIED (lihat VERIFIED REALITY #8): **`py-clob-client` diarsipkan Mei 2026**
> → wajib **CLOB V2** (REST + signing EIP-712 sendiri). EIP-712 domain = Polygon.
```
Implement signing & auth CLOB V2 sesuai docs/04 §4.2 & §4.5.
Deliverable: Signer (Protocol) + impl eth-account untuk EIP-712 skema order V2;
derive API credentials dari signature; ClobClient REST (place_order/cancel/
get_orderbook/balances) memakai kredensial. Private key HANYA dari env/secret
manager; jangan log.
DoD: unit test signing terhadap vektor uji; auth header benar; tidak ada secret
ter-log. VERIFIKASI skema V2 dari dokumentasi resmi terbaru sebelum final.
```

## PROMPT 3.2 — OMS mode live
> ✅ VERIFIED (lihat VERIFIED REALITY #3): PnL live & fill tracking **net-of-fee
> taker 7%**; track fill via `stream_user()` WS (`/ws/market` infra yang sama).
```
Perluas exec/oms.py untuk mode live: submit order nyata via ClobClient
(FOK/FAK), track fill via stream_user() (WSS). WAJIB lewat RiskManager.
Idempotency, retry+backoff pada 429/5xx, cancel-on-shutdown.
DoD: integration test terhadap CLOB sandbox/mock; order ter-veto tidak terkirim;
retry tidak menduplikasi order.
```

## PROMPT 3.3 — Limit konservatif + gate live
```
Konfigurasikan live super-aman: MAX_NOTIONAL_ROUND $1-$5, MAX_DAILY_LOSS kecil,
MAX_CONSEC_LOSSES kecil. Tegakkan assert_live_ok() (LIVE_CONFIRMED=yes) di startup.
Pastikan kill-switch (manual file/CLI) & circuit breaker teruji end-to-end:
batalkan semua order saat trigger.
DoD: test: start live tanpa LIVE_CONFIRMED=yes -> menolak start; kill-switch
membatalkan order terbuka; circuit breaker menghentikan entry.
```

## PROMPT 3.4 — Monitoring & alerting
> ✅ VERIFIED (lihat VERIFIED REALITY #3): metrik PnL/balance & notifikasi
> **net-of-fee 7%**; pantau `ws_state` (keepalive `/ws/market`) & RPC failover.
```
Tambah observability sesuai docs/06 §6.7: Prometheus exporter (metrik: pnl,
balance, orders, fills, vetoes, latency, ws_state) + dashboard Grafana (JSON).
Heartbeat berkala + alert Telegram/Discord untuk semua event risk.
DoD: endpoint /metrics tersedia; alert terkirim saat event uji.
```

## PROMPT 3.5 — Live runner
```
Implement src/btcbot/app/live.py: orkestrasi mode live end-to-end (boot sequence,
data -> signal -> strategy -> sizing -> risk -> live OMS -> reconcile -> store).
Mulai dengan notional $1-$5/ronde. Logging audit immutable.
DoD: dry-run start (LIVE_CONFIRMED=no) menolak; dengan yes -> boot 'all systems
go.' dan siap trading micro-stake. Tunjukkan run terkontrol & PnL live tercatat.
```

## ✅ GATE G3
```
Setelah live micro-stakes berjalan terkontrol: laporkan PnL LIVE (bukan paper)
setelah biaya nyata, insiden risk (harus nol yang lolos), dan kualitas
reconciliation. Update PROGRESS_TRACKER.md. Scale-up DILARANG sampai G4.
```

---

# ════════════════════════════════════════════════
# FASE 4 — Hardening & Scale  (Gate G4)
# ════════════════════════════════════════════════

## PROMPT 4.1 — Ketahanan & deploy
> ✅ VERIFIED (lihat VERIFIED REALITY #4,#5): failover RPC nyata =
> `chainstack → publicnode → blastapi → blockpi` + `User-Agent` browser; WSS
> failover + pola keepalive `/ws/market` (`ping_interval=None` + heartbeat
> `"PING"` 10s, stale 30s → reconnect). Jangan kirim order ganda saat failover.
```
Tambah failover RPC & WSS (endpoint cadangan), Dockerfile + docker-compose,
systemd unit (restart=always), backup DB terjadwal, dan NTP check di startup.
DoD: matikan koneksi primer -> bot failover tanpa kirim order ganda; container
restart otomatis; backup teruji.
```

## PROMPT 4.2 — Tuning berbasis data live + ADR
```
Analisa data live: kalibrasi p_win nyata, slippage nyata, fill-rate. Usulkan
penyesuaian parameter (T_ENTRY_SEC, DELTA_THRESHOLD, MAX_PRICE, KELLY_FRACTION) dan
tulis ADR di docs/adr/. Jangan ubah perilaku tanpa ADR.
DoD: laporan tuning + ADR ter-commit.
```

## PROMPT 4.3 — Scale-up bersyarat
```
HANYA jika PnL live positif & stabil lintas beberapa hari: naikkan notional
secara bertahap dengan limit risk dinaikkan proporsional. Pantau drawdown ketat.
DoD: bukti PnL live positif sebelum tiap kenaikan ukuran; limit ter-update.
```

---

# 🔁 PROMPT UTILITAS (pakai kapan saja)

## Debug / error
```
Test/CI ini gagal: [tempel error]. Diagnosa akar masalah, perbaiki dengan
perubahan minimal, tambah regression test, dan jelaskan singkat. Patuhi konvensi
proyek (Decimal, async, type strict). Jangan mengubah scope lain.
```

## Review keamanan sebelum naik fase
```
Lakukan audit: (1) tidak ada secret hardcoded (grep), (2) semua order lewat
RiskManager, (3) MODE gating dihormati, (4) live butuh LIVE_CONFIRMED=yes,
(5) kill-switch & circuit breaker berfungsi. Laporkan temuan + perbaikan.
```

## Sinkron dokumen
```
Aku mengubah [modul/kontrak]. Perbarui docs terkait (04/05/06/07/08) dan
.kiro/specs agar konsisten. Tulis ADR jika ini keputusan arsitektur.
```

## Update progress tracker
```
Perbarui PROGRESS_TRACKER.md: tandai task yang selesai, isi tanggal & catatan,
update status GATE, dan tambahkan blocker/keputusan baru bila ada.
```



---

# ════════════════════════════════════════════════
# 📱 TELEGRAM CONTROL PLANE  (cross-cutting — lihat docs/12)
# ════════════════════════════════════════════════
> Bangun bertahap. T.1 (notifikasi) aman sejak Fase 0/2. T.2 (perintah read-only)
> & T.3 (aksi kontrol) di Fase 2/3. Semua perintah HARUS lewat whitelist.

## PROMPT T.1 — Notifier Telegram (push notification)
```
Implement notifikasi Telegram sesuai docs/12 §12.4 & §12.6.
Deliverable:
- adapters/telegram.py: TelegramController (impl Notifier) memakai
  python-telegram-bot v20+ (async). emit(BotEvent) non-blocking via asyncio.Queue
  + task pengirim terpisah (Telegram down TIDAK memblok core).
- Definisikan BotEvent (kind/text/severity/ts) dan event bus internal; core
  memancarkan event: boot, hasil ronde, hedge, kill, circuit breaker, drawdown,
  mismatch, heartbeat, error.
- Format pesan ala referensi (#round side @price xSize -> settles +PnL | balance).
- Config: TELEGRAM_ENABLED/BOT_TOKEN/NOTIFY_CHAT_ID/PER_ROUND_NOTIFY/HEARTBEAT_MIN
  (docs/11 addendum). Jika disabled/token kosong -> no-op, bot tetap jalan.
DoD: unit test format & queue; mock Telegram API (tanpa hit jaringan); test
"Telegram down -> core tetap jalan, pesan ter-drop aman"; token tidak ter-log.
```

## PROMPT T.2 — Perintah & tombol read-only
> ✅ VERIFIED (lihat VERIFIED REALITY #3): render hasil ronde/PnL di
> `/status`,`/pnl`,`/recent` **net-of-fee taker 7%** (jangan tampilkan gross).
```
Implement ControlFacade (app/control.py) + handler perintah Telegram read-only
sesuai docs/12 §12.5-12.6.
Deliverable:
- app/control.py: ControlFacade dengan status(), pnl(), positions(), recent(n).
- Handler Telegram: /start /help (menu + inline keyboard), /status /balance /pnl
  /positions /recent /config /mute /unmute.
- WHITELIST: tolak chat_id di luar TELEGRAM_ALLOWED_CHAT_IDS (abaikan + log).
- Inline keyboard menu utama (Status/PnL/Positions/Pause/Resume/Mute/KILL).
DoD: unit test whitelist (non-whitelist ditolak); test render menu & balasan
status; mock Telegram.
```

## PROMPT T.3 — Aksi kontrol (pause/resume/kill) + konfirmasi
> ✅ VERIFIED (lihat VERIFIED REALITY #3): semua angka P&L pada notifikasi/aksi
> render **net-of-fee taker 7%**.
```
Tambah aksi kontrol Telegram sesuai docs/12 §12.3 & docs/06 addendum.
Deliverable:
- ControlFacade.pause()/resume()/kill(reason)/set_mute(on) yang memanggil
  RiskManager (kill-switch & pause yang SAMA dengan CLI/file).
- /pause /resume /kill dengan KONFIRMASI 2-langkah (tombol "✅ Konfirmasi/❌ Batal"),
  anti-replay callback, dan AUDIT LOG tiap aksi.
- KILL membatalkan semua order terbuka + stop entry.
DoD: unit test: konfirmasi wajib sebelum eksekusi; non-whitelist tak bisa kill;
kill memicu RiskManager.kill_switch; aksi tercatat di audit log. Integrasi:
Telegram down tidak menonaktifkan kill-switch CLI/file.
```



---

## ADDENDUM (v1.2) — Perluasan PROMPT T.1 (Notifikasi P&L & Error)
Tambahkan ke implementasi T.1 (Notifier) sesuai `docs/12 §12.12`:
```
Perluas Notifier Telegram dengan notifikasi P&L & Error yang dapat dikonfigurasi:

A. P&L (otomatis):
- Event: trade_win, trade_loss, profit_milestone, equity_high, consec_loss,
  drawdown, daily_loss_warn, daily_summary.
- P&L tracker di ledger menghitung: PnL sesi, peak balance, drawdown, consec_loss,
  milestone terakhir; memancarkan event saat ambang terlewati.
- Hormati env: NOTIFY_PNL_PER_TRADE/WINS/LOSSES, NOTIFY_PROFIT_MILESTONE +
  PROFIT_MILESTONE_STEP, NOTIFY_NEW_EQUITY_HIGH, NOTIFY_DAILY_PNL_SUMMARY +
  DAILY_SUMMARY_TIME, ALERT_CONSEC_LOSSES, ALERT_DRAWDOWN_PCT, ALERT_DAILY_LOSS_PCT.
- Loss & alert risiko BYPASS /mute.

B. Error (butuh perbaikan):
- Petakan exception -> kategori di docs/12 §12.12.2 (WSS, auth, RPC, saldo USDC/MATIC,
  order ditolak, reconciliation mismatch, clock drift, config invalid, harga stale).
- Untuk ACTION_REQUIRED: kirim pesan berisi MASALAH + SARAN PERBAIKAN + tombol
  [Retry][Tetap Pause][Status], set action_required=true, dan trigger circuit
  breaker bila perlu.
- Hormati env: NOTIFY_ERRORS, NOTIFY_ERROR_MIN_SEVERITY, NOTIFY_ACTION_REQUIRED,
  ERROR_DEDUP_WINDOW_SEC (dedup anti-spam).

DoD tambahan: unit test pemicu tiap event P&L (milestone, consec loss, drawdown,
equity high); test mapping error->remediation + dedup; test loss/action_required
tidak ter-mute.
```



---

# ════════════════════════════════════════════════
# 🧠 STRATEGI (docs/13) & 🌐 MULTI-MARKET (docs/14)
# ════════════════════════════════════════════════
> Strategi: bangun fair-value engine dulu (sudah tercakup signal.py Fase 1).
> Multi-market: kerjakan di FASE 4, SETELAH BTC 5m profit live (G3). Aktifkan
> market satu per satu dengan validasi.

## PROMPT S.1 — Perkuat Fair-Value Engine (#1 taker)
```
Perkuat domain/signal.py menjadi fair-value engine penuh sesuai docs/13 §13.2-13.3:
- fair_up = N(d), d = (spot-start)/(sigma_asset*sqrt(time_left)); fair_down=1-fair_up.
- sigma_asset diestimasi dari realized vol jendela terbaru (injectable).
- Strategi #1: ambil saat (fair_side - best_ask - fee - slippage) >= MIN_EDGE,
  KAPAN SAJA dalam window (bukan hanya ekor). Pertahankan never-fade & cap harga.
- WAJIB: tambah kalibrasi (reliability curve) di laporan backtest (docs/09).
DoD: unit test fair-value & edge; laporan kalibrasi P_up vs hit-rate nyata.
```

## PROMPT S.2 (opsional, lanjutan) — Delta-Hedge Arb (#2)
```
Implement strategi #2 (docs/13 §13.4) sebagai modul strategi pluggable:
beli sisi Polymarket + hedge underlying perp di venue lain (adapter venue baru),
kelola basis risk, eksekusi 2-kaki. Mulai di paper. Hanya setelah #1 profit live.
DoD: backtest/paper menunjukkan varians turun & edge bersih; basis risk diukur.
```

## PROMPT S.3 (opsional, lanjutan) — Market Making (#3)
```
Implement strategi #3 (docs/13 §13.5): quote 2 sisi di sekitar fair value, skew
inventory, cancel/replace cepat saat referensi bergerak. Uji di paper; ukur
spread-capture vs adverse selection. Hanya jika latensi rendah & kalibrasi terbukti.
DoD: paper menunjukkan spread-capture > adverse selection; risk inventory terkendali.
```

## PROMPT M.1 — Market Registry & Config
> ✅ VERIFIED (lihat VERIFIED REALITY #1): cadence terverifikasi 5m=`%300`,
> 15m=`%900`; slug regex per-asset `{asset}-updown-{5m|15m}-{epoch}`
> (`btc`/`eth`/`sol`).
```
Implement domain/market_registry.py + MarketSpec (docs/14 §14.3). Baca daftar
market (asset,timeframe,enabled,weight,params) dari MARKETS_CONFIG (markets.yaml).
DoD: parsing config + test; default hanya BTC 5m enabled.
```

## PROMPT M.2 — MarketScanner + per-market Worker
> ✅ VERIFIED (lihat VERIFIED REALITY #1,#2): discovery per asset×timeframe pakai
> regex slug + `closed=false&active=true`; cadence `%300`/`%900`; tiap fee market
> di-parse (`crypto_fees_v2` 7%).
```
Generalisasi pipeline jadi multi-market (docs/14 §14.4):
- app/scanner.py (MarketScanner): discovery ronde tiap aset×timeframe via Gamma,
  spawn 1 app/worker.py (MarketWorker) per market enabled.
- Tiap worker pakai PriceFeed & sigma aset-nya; domain reusable (jangan duplikasi).
- Shared: RiskManager global, OMS, store, Telegram, clock.
- PriceFeed per aset (BTC/ETH/SOL) di adapters/chainlink.py.
DoD: 2+ market jalan paralel di paper; data ber-asset/timeframe; test scanner/worker.
```

## PROMPT M.3 — Risk multi-market & korelasi
> ✅ VERIFIED (lihat VERIFIED REALITY #3): semua limit & PnL per-market dihitung
> **net-of-fee 7%**; validasi edge per market wajib net biaya.
```
Perluas RiskManager (docs/14 §14.6 & docs/06 addendum v1.3): limit per-market,
per-asset, MAX_CORRELATED_DIRECTIONAL (BTC/ETH/SOL satu faktor risiko), global.
Kill/circuit breaker dua level (global & per-market). Gating likuiditas per market.
DoD: test tiap limit; korelasi cap mencegah all-in arah sama lintas aset.
```

## PROMPT M.4 — Rollout bertahap
```
Aktifkan market satu per satu (docs/14 §14.10): BTC15m -> ETH5m/15m -> SOL5m/15m.
Tiap market: kalibrasi sigma & param, lulus checklist edge (docs/13 §13.7) +
likuiditas SEBELUM live dengan ukuran nyata. PnL & metrik per market di Telegram.
DoD: tiap market punya laporan validasi sebelum diaktifkan live.
```
