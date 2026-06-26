# PROGRESS TRACKER — 5min-btc-polymarket

> Update file ini setiap menyelesaikan satu PROMPT (lihat PROMPT_GUIDE.md).
> Status: ⬜ belum · 🟦 sedang dikerjakan · ✅ selesai · ⛔ blocked · ⏭️ di-skip
>
> Mulai: `____-__-__`   |   Target G3 (live micro): `____-__-__`

---

## 🔑 Prasyarat (sebelum Fase 0)
| # | Item | Status | Catatan |
|---|------|:------:|---------|
| P1 | AI coding agent siap (Antigravity/Kiro/Codex/opencode/Cursor) | ⬜ | |
| P2 | Repo Git dibuat + blueprint disalin ke root | ⬜ | |
| P3 | Python 3.11+ + uv/poetry terpasang | ⬜ | |
| P4 | PROMPT 0 (kickoff) sudah ditempel & agent meringkas blueprint | ⬜ | |
| P5 | (untuk live nanti) wallet Polygon + USDC.e + RPC | ⬜ | jangan diisi sebelum Fase 3 |

---

## ▶️ Cara baca tabel
`Prompt` = nomor di PROMPT_GUIDE.md · `Modul` = file target · `DoD` = Definition of Done lulus? · isi `Tanggal` & `Catatan`.

---

## 🟢 FASE 0 — Scaffolding & Read-only Data  (Gate G0)
| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 0.1 | Setup repo & tooling | pyproject, CI, Makefile | ✅ | ✅ | 2026-06-25 | uv, ruff, black, mypy strict, GH Actions |
| 0.2 | Settings, MODE gating, secrets | config/settings.py, .env.example | ✅ | ✅ | 2026-06-25 | pydantic-settings; assert_live_ok() |
| 0.3 | Clock adapter | adapters/clock.py | ✅ | ✅ | 2026-06-25 | System+Sim, UTC aware, deterministik |
| 0.4 | Gamma adapter (discovery) | adapters/gamma.py | ✅ | ✅ | 2026-06-25 | httpx; respx tests; endpoint TODO verify |
| 0.5 | CLOB WebSocket (market data) | adapters/clob_ws.py | ✅ | ✅ | 2026-06-25 | reconnect/backoff/heartbeat/stale event |
| 0.6 | Chainlink price feed | adapters/chainlink.py | ✅ | ✅ | 2026-06-25 | Protocol + Fake; on-chain placeholder TODO |
| 0.7 | Store + Recorder | data/store.py, data/recorder.py | ✅ | ✅ | 2026-06-25 | aiosqlite; CRUD; gap marking |
| 0.8 | CLI boot + runner readonly | app/cli.py, app/demo.py | ✅ | ✅ | 2026-06-25 | boot seq + readonly loop; `run-readonly` demo |
| 0.7+ | Sizing + paper config (Phase 0.7) | exec/sizing.py, config | ✅ | ✅ | 2026-06-25 | KELLY_FRACTION + cap %bankroll + paper |

**GATE G0** — ✅ NOL order (verified: orders=0, fills=0; tidak ada place_order/sign/oms di src) · ✅ data terekam (replay fixture 1000 ronde) · ✅ CI hijau (121 tests)
> Status G0: ✅ LULUS (via replay fixture panjang) | Tanggal lulus: 2026-06-25
> Bukti: 1000 rounds, 5000 book_snapshots (3000 real + 2000 gap), signals=0 (komputasi Fase 1), orders=0, fills=0, mode=readonly.
> Catatan: Chainlink price_now sudah diimplementasikan (Data Feeds, eth_call read-only) → Δ kini terekam di signals. Soak-run nyata berjam-jam thd endpoint live (Gamma/CLOB) + verifikasi address feed (B1/B3) tetap disarankan; tidak memblok G0.

---

## 🟡 FASE 1 — Backtest / Replay  (Gate G1)
| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 1.1 | Interval loader | domain/market.py | ⬜ | ⬜ | | |
| 1.2 | Signal engine (edge math) | domain/signal.py | ⬜ | ⬜ | | |
| 1.3 | Strategy (entry/hedge/exit) | domain/strategy.py | ⬜ | ⬜ | | |
| 1.4 | Sizing (Kelly + caps) | exec/sizing.py | ✅ | ✅ | 2026-06-25 | dikerjakan di Phase 0.7: KELLY_FRACTION + cap %bankroll/notional/depth |
| 1.5 | Replay engine + fill model | backtest/replay.py | ⬜ | ⬜ | | |
| 1.6 | Laporan metrik & kalibrasi | backtest reporting | ⬜ | ⬜ | | |

**GATE G1 — KEPUTUSAN EDGE (paling kritikal):**
- net_edge > 0 stabil lintas parameter? ⬜ Ya ⬜ Tidak
- Stabil lintas beberapa hari data (bukan overfit)? ⬜ Ya ⬜ Tidak
- Reliability curve terkalibrasi? ⬜ Ya ⬜ Tidak
- Edge bertahan setelah fee+slippage+latensi (ablation)? ⬜ Ya ⬜ Tidak

> **Hasil G1:** ⬜ LANJUT (edge terbukti) · ⬜ REVISI strategi · ⬜ STOP (edge ≤ 0)
> Net PnL backtest: ______ | ROI: ______ | Max DD: ______ | Tanggal: ______
> *(STOP adalah hasil yang valid & menyelamatkan modal — jangan paksakan.)*

---

## 🟠 FASE 2 — Paper Trading  (Gate G2)
| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 2.1 | Risk manager | risk/manager.py | ⬜ | ⬜ | | |
| 2.2 | OMS mode paper | exec/oms.py (paper) | ⬜ | ⬜ | | |
| 2.3 | Paper runner + ledger | app/paper.py | ⬜ | ⬜ | | |
| 2.4 | Reconciliation + alert | reconcile + alert | ⬜ | ⬜ | | |

**GATE G2** — ⬜ ≥ ratusan ronde paper · ⬜ PnL konsisten dgn backtest · ⬜ nol mismatch
> Status G2: ⬜ | Ronde paper: ______ | PnL paper: ______ | Tanggal lulus: ______

---

## 🔴 FASE 3 — Live Micro-stakes  (Gate G3)  ⚠️ UANG NYATA
**Checklist verifikasi API SEBELUM mulai (docs/04 §4.8):**
- ⬜ Base URL & versi CLOB V2 terbaru terverifikasi
- ⬜ Skema EIP-712 order V2 valid
- ⬜ Nama channel WSS & format pesan
- ⬜ Cara baca Chainlink BTC/USD di Polygon
- ⬜ Fee, tick size, min order size market BTC 5m
- ⬜ Restriksi geografis / kepatuhan akun dicek

| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 3.1 | Signer EIP-712 (CLOB V2) + auth | adapters/clob.py, Signer | ⬜ | ⬜ | | |
| 3.2 | OMS mode live | exec/oms.py (live) | ⬜ | ⬜ | | |
| 3.3 | Limit konservatif + gate live | risk/config | ⬜ | ⬜ | | |
| 3.4 | Monitoring & alerting | metrics/alert | ⬜ | ⬜ | | |
| 3.5 | Live runner | app/live.py | ⬜ | ⬜ | | |

**GATE G3** — ⬜ kill-switch teruji · ⬜ circuit breaker teruji · ⬜ LIVE_CONFIRMED gate · ⬜ reconciliation bersih
> Status G3: ⬜ | Notional/ronde: $____ | PnL LIVE: ______ | Insiden risk lolos: ____ | Tanggal: ______

---

## 🟣 FASE 4 — Hardening & Scale  (Gate G4)
| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| 4.1 | Ketahanan & deploy | Docker/systemd/failover | ⬜ | ⬜ | | |
| 4.2 | Tuning berbasis data live + ADR | docs/adr/ | ⬜ | ⬜ | | |
| 4.3 | Scale-up bersyarat | risk limits | ⬜ | ⬜ | | |

**GATE G4** — ⬜ PnL live positif & stabil lintas hari sebelum tiap kenaikan ukuran
> Status G4: ⬜ | Tanggal: ______

---

## 📊 Status Ringkas (isi cepat)
```
Fase 0 [##########] 8/8     G0: ✅ LULUS (replay fixture)
Fase 1 [#         ] 1/6     G1: belum   (edge terbukti? belum) — sizing done early
Fase 2 [          ] 0/4     G2: belum
Fase 3 [          ] 0/5     G3: belum
Fase 4 [          ] 0/3     G4: belum
```

---

## ⛔ Blockers / Risiko Aktif
| # | Deskripsi | Sejak | Dampak | Rencana | Status |
|---|-----------|-------|--------|---------|--------|
| B1 | CLOB **REST** V2 (order/signing) belum diverifikasi | 2026-06-25 | blokir Fase 3 (live) | cek docs resmi Polymarket (docs/04 §4.8) | 🟦 |
| B1-ws | CLOB **WS market** parser + keepalive — RESOLVED | 2026-06-26 | — | path `/ws/market`; parse LIST snapshot + `price_change`; keepalive: ping_interval=None + heartbeat "PING" 10s + stale 30s reconnect | ✅ |
| B2b | Adapter Chainlink **Data Streams** BTC/USD (akurasi harga akhir-window) | 2026-06-25 | akurasi resolusi/edge; sumber resolusi asli market | bangun di Fase 1 (lihat task lanjutan) | 🟦 |
| F1-fee | Reverse-engineer formula `crypto_fees_v2` → masukkan ke net_edge | 2026-06-25 | market berbiaya; edge harus net setelah fee | Fase 1 (signal/sizing) | 🟦 |
| B2 | Chainlink BTC/USD price_now — RESOLVED (Data Feeds reader + RPC failover) | 2026-06-25 | — | ChainlinkDataFeed (eth_call read-only) + retry/staleness/sanity; FailoverPriceSource primary+fallbacks, UA browser | ✅ |
| B3 | Gamma discovery — RESOLVED (slug-based + window benar + fee parsed) | 2026-06-25 | — | regex slug `asset-updown-tf-epoch`; window dari eventStartTime/endDate (bukan startDate); query end_date window + UA browser | ✅ |

## 🧠 Decision Log (ADR ringkas)
| Tgl | Keputusan | Alasan | ADR file |
|-----|-----------|--------|----------|
| 2026-06-25 | Gamma discovery berbasis **slug** `asset-updown-tf-epoch`, bukan teks judul/durasi startDate | startDate = tanggal listing (~24j sebelum) → bug filter durasi lama menolak semua ronde; epoch slug = window_end andal | — |
| 2026-06-25 | Market up/down 5m/15m **BERBIAYA**: `feesEnabled=true`, `feeType=crypto_fees_v2`, `feeSchedule{exponent,rate,takerOnly,rebateRate}` di-parse ke model | net_edge wajib memperhitungkan fee (Fase 1) | — |
| 2026-06-25 | Resolusi market via **Chainlink Data Streams** (`resolutionSource`), bukan Data Feeds | basis-risk: sumber harga akhir-window harus = sumber resolusi (B2b) | — |
| 2026-06-25 | `outcomePrices` Gamma **STALE** untuk market cepat → tidak dipakai sbg harga | harga live dari order book CLOB | — |
| 2026-06-26 | WS market: book snapshot = JSON **array** (per token); `price_change` = dict (`price_changes[]`, BUY→bid/SELL→ask, size 0 hapus). Endpoint `/ws/market`. Maintain BookState per asset; best_bid=max(bids), best_ask=min(asks) | fix crash `.get()` pada list; harga live akurat dari order book | — |
| 2026-06-26 | WS keepalive: server tak balas ping protokol → `ping_interval=None`/`ping_timeout=None` (matikan keepalive library) + heartbeat "PING" aplikasi tiap 10s (task terpisah) + stale 30s → reconnect | fix `1011 keepalive ping timeout` (mati ~45s meski data mengalir); konfig `WS_APP_PING_SECONDS`/`WS_STALE_SECONDS` | — |
| 2026-06-26 | RPC failover: primary `POLYGON_RPC_URL` (chainstack) + fallbacks publik (publicnode/blastapi/blockpi). UA browser WAJIB (RPC publik 403 tanpa UA). Gagal = exception/HTTP/JSON-RPC/price<=0/stale>120s → endpoint berikutnya; semua gagal → AllRpcFailedError (Δ=None+gap) | RPC tunggal down → bot buta; failover otomatis | — |
| 2026-06-26 | Retensi book: write-on-change + throttle 1s + fine-grain 45s akhir-window; default `BOOK_PERSIST_MODE=changes`. Order book in-mem tetap penuh; hanya persistensi di-throttle. Schema tetap (tanpa migrasi) | ~333 baris/dtk (~6 GB/hari) mayoritas duplikat depth-jitter → soak berhari/minggu | — |
| | | | |

## 🔬 Hasil Pengukuran Edge (diisi dari G1/G2/G3)
| Sumber | Net PnL | ROI | Win-rate | Max DD | Catatan |
|--------|---------|-----|----------|--------|---------|
| Backtest (G1) | | | | | |
| Paper (G2) | | | | | |
| Live (G3) | | | | | |



---

## 📱 TELEGRAM CONTROL PLANE (cross-cutting — docs/12)
| Prompt | Tugas | Modul utama | Status | DoD ✔ | Tanggal | Catatan |
|:------:|-------|-------------|:------:|:-----:|---------|---------|
| T.1 | Notifier Telegram (push) | adapters/telegram.py | ⬜ | ⬜ | | bangun di Fase 0/2 |
| T.2 | Perintah & tombol read-only | app/control.py + handler | ⬜ | ⬜ | | Fase 2 |
| T.3 | Aksi kontrol (pause/resume/kill) | control + risk | ⬜ | ⬜ | | Fase 2/3, sebelum live |

**Setup Telegram (sebelum T.1):**
- ⬜ Buat bot via @BotFather → dapat TELEGRAM_BOT_TOKEN
- ⬜ Dapatkan chat_id (mis. @userinfobot) → isi ALLOWED_CHAT_IDS & NOTIFY_CHAT_ID
- ⬜ Isi env Telegram di .env (jangan commit token)

**Gate Telegram** — ⬜ whitelist berfungsi · ⬜ konfirmasi KILL 2-langkah · ⬜ Telegram down tidak hentikan trading · ⬜ token tidak ter-log



---

## 📊 Notifikasi P&L & Error (bagian dari T.1 — docs/12 §12.12)
- ⬜ Notif menang/kalah tiap trade (P&L) — `NOTIFY_PNL_WINS/LOSSES`
- ⬜ Milestone profit + equity high baru
- ⬜ Alert kalah beruntun (+auto-pause), drawdown, peringatan dini daily loss
- ⬜ Ringkasan harian/sesi
- ⬜ Notif error "ACTION REQUIRED" + saran perbaikan + tombol cepat
- ⬜ Dedup error (anti-spam) & loss/error bypass /mute
- ⬜ Test: pemicu event P&L + mapping error->remediation



---

## 🧠 STRATEGI (docs/13)
| Prompt | Tugas | Status | DoD ✔ | Catatan |
|:------:|-------|:------:|:-----:|---------|
| S.1 | Fair-value engine (#1 taker) + kalibrasi | ⬜ | ⬜ | inti, di Fase 1 |
| S.2 | Delta-hedge arb (#2) | ⬜ | ⬜ | opsional, setelah #1 live |
| S.3 | Market making (#3) | ⬜ | ⬜ | opsional, butuh latensi rendah |

## 🌐 MULTI-MARKET (docs/14 — kerjakan di Fase 4, setelah BTC 5m live)
| Prompt | Tugas | Status | DoD ✔ | Catatan |
|:------:|-------|:------:|:-----:|---------|
| M.1 | Market registry + MarketSpec config | ⬜ | ⬜ | |
| M.2 | MarketScanner + per-market Worker | ⬜ | ⬜ | PriceFeed per aset |
| M.3 | Risk multi-market & korelasi | ⬜ | ⬜ | cap korelasi BTC/ETH/SOL |
| M.4 | Rollout bertahap (validasi per market) | ⬜ | ⬜ | enable satu per satu |

**Status aktivasi market (centang saat lulus validasi edge+likuiditas):**
- ⬜ BTC 5m (WAJIB pertama) · ⬜ BTC 15m · ⬜ ETH 5m · ⬜ ETH 15m · ⬜ SOL 5m · ⬜ SOL 15m
