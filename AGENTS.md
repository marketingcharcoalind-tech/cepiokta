# AGENTS.md — Aturan untuk AI Coding Agent

> File ini WAJIB dibaca lebih dulu oleh AI coding agent (Antigravity, Kiro,
> Codex, opencode, Cursor, Claude Code). Berisi aturan operasi, urutan kerja,
> dan batasan keamanan yang TIDAK BOLEH dilanggar.

---

## 0. Misi

Bangun `5min-btc-polymarket`: bot yang trading market **Polymarket BTC Up/Down
5 menit**. Strategi inti = *end-of-window settlement arbitrage* ("ambil sisi
yang sudah hampir pasti"). Lihat `docs/05-STRATEGY_SPEC.md`.

**Sasaran utama BUKAN profit, melainkan infrastruktur yang bisa MENGUKUR edge
secara jujur** lalu trading hanya jika edge terbukti positif setelah biaya.

---

## 1. Batasan Keamanan (NON-NEGOTIABLE)

1. **NEVER** menulis kode yang submit order live ke uang nyata sampai semua
   gate ROADMAP fase 0–2 selesai dan lulus test.
2. **NEVER** hardcode private key, API secret, atau seed phrase di source code.
   Semua secret via environment variable / secret manager. Lihat
   `docs/11-CONFIG_AND_SECRETS.md`.
3. Setiap modul eksekusi WAJIB di belakang flag `MODE` =
   `readonly | backtest | paper | live`. Default = `readonly`.
4. `live` mode WAJIB lewat Risk Manager yang bisa mem-VETO order.
5. Implementasikan **kill-switch** & **circuit breaker** SEBELUM live mode.
6. Order live pertama harus dibatasi keras: max notional per ronde sangat kecil
   ($1–$5), max daily loss kecil, dan butuh konfirmasi eksplisit (env
   `LIVE_CONFIRMED=yes`).

---

## 2. Urutan Kerja (ikuti ROADMAP, jangan loncat)

Kerjakan `docs/10-ROADMAP.md` secara berurutan:

- **Fase 0 — Scaffolding & Read-only data**: koneksi Gamma + CLOB WSS +
  Chainlink, rekam orderbook & hasil resolusi ke DB. TANPA order.
- **Fase 1 — Backtest/Replay**: harness yang memutar ulang data terekam,
  hitung **edge bersih** (setelah fee + slippage + asumsi kompetisi).
- **Fase 2 — Paper trading**: simulasi order realtime, fill model realistis,
  ledger PnL. (Inilah yang menghasilkan output mirip screenshot.)
- **Fase 3 — Live micro-stakes**: signing EIP-712 (CLOB V2), order kecil,
  risk manager penuh, monitoring.
- **Fase 4 — Hardening & scale**: observability, alerting, deploy, tuning.

> Jangan menulis kode fase N+1 sebelum fase N punya test yang lulus.

---

## 3. Konvensi Kode

- **Bahasa default**: Python 3.11+ (boleh usul TypeScript/Rust jika argumen
  latensi kuat — tulis ADR di `docs/`). Lihat `docs/03-TECH_STACK.md`.
- Async-first (`asyncio`). Jangan blocking I/O di event loop.
- Type hints wajib + `mypy`/`pyright` strict. Lint `ruff`. Format `black`.
- Struktur paket: domain-driven, satu modul = satu tanggung jawab. Lihat
  `docs/08-MODULE_SPECS.md` untuk kontrak interface.
- Semua I/O eksternal (API, WSS, DB, clock) di balik **interface/adapter**
  supaya bisa di-mock saat test.
- Config via Pydantic Settings; tidak ada "magic number" tersebar.
- Logging terstruktur (JSON) dengan `round_id`, `market_id`, `mode`.

## 4. Testing

- Setiap modul punya unit test. Integrasi pakai data terekam (fixture).
- Determinisme: clock & RNG harus injectable.
- Backtest harus reproducible (seed tetap). Lihat
  `docs/09-TESTING_AND_BACKTESTING.md`.
- CI: lint + type-check + test harus hijau sebelum merge.

## 5. Struktur Repo Target (yang harus agent buat)

```
src/btcbot/
  config/        # settings, secrets loader
  adapters/
    gamma.py     # REST market discovery
    clob.py      # CLOB V2 REST + order signing
    clob_ws.py   # WSS market & user channel
    chainlink.py # BTC/USD price truth
    clock.py     # injectable time
  domain/
    models.py    # entities (Round, Order, Fill, Position, ...)
    market.py    # window/interval logic (interval-loader)
    signal.py    # trend / edge calc
    strategy.py  # entry/exit/hedge decisions
  exec/
    oms.py       # order management
    sizing.py    # position sizing
  risk/
    manager.py   # veto, kill-switch, circuit breaker
  data/
    store.py     # DB (SQLite/Postgres)
    recorder.py  # fase 0 recorder
  backtest/
    replay.py    # fase 1 harness
  app/
    paper.py     # fase 2 runner
    live.py      # fase 3 runner
    cli.py       # boot sequence ala screenshot
tests/
```

## 6. Definisi Selesai (Definition of Done) per task

- Kode + type hints + docstring.
- Unit test lulus.
- Tidak ada secret hardcoded.
- Mode flag dihormati (default readonly).
- Update dokumen relevan jika kontrak berubah.

## 7. Jika Ragu

- Sumber kebenaran API: `docs/04-API_INTEGRATION.md`. Jika API berubah
  (CLOB V2 baru), verifikasi ke dokumentasi resmi Polymarket sebelum koding.
- Jangan mengarang endpoint. Jika tidak yakin, buat adapter + TODO + test mock,
  dan minta verifikasi manusia.



---

## ADDENDUM (v1.1) — Telegram Control Plane
- Bot punya lapisan kontrol/monitoring via **Telegram** (lihat `docs/12-TELEGRAM_INTEGRATION.md`).
- **Aturan keamanan tambahan (NON-NEGOTIABLE):**
  - `TELEGRAM_BOT_TOKEN` = secret (perlakukan seperti private key; jangan log).
  - Perintah kontrol HANYA dari `TELEGRAM_ALLOWED_CHAT_IDS` (whitelist).
  - `/kill`, `/pause`, ganti MODE wajib konfirmasi 2-langkah + audit log.
- **Aturan arsitektur:** Telegram bersifat AUXILIARY/best-effort. Notifikasi via
  `asyncio.Queue` (non-blocking). Jika Telegram down, core trading TETAP jalan.
- Telegram & CLI sama-sama lewat `ControlFacade` (`app/control.py`); jangan
  biarkan Telegram menyentuh domain/exec/risk langsung.
- Tambah modul: `adapters/telegram.py`, `app/control.py` ke struktur repo §5.



---

## ADDENDUM (v1.3) — Strategi & Multi-Market
- **Strategi**: SATU fair-value engine, 3 keluarga monetisasi (lihat
  `docs/13-STRATEGY_PLAYBOOK.md`). Default = #1 Fair-Value Taker. #2 delta-hedge &
  #3 market making menyusul, hanya setelah level bawah profit live.
- **Multi-market**: dukung BTC/ETH/SOL × {5m,15m} via config (lihat
  `docs/14-MULTI_MARKET_SCALING.md`). Generalisasi pipeline jadi MarketScanner +
  per-market Worker; domain tetap MURNI & reusable. Tambah market = ubah config,
  bukan kode.
- **Aturan keras tambahan**:
  - JANGAN aktifkan market baru tanpa lulus validasi edge (docs/13 §13.7) + likuiditas.
  - Hormati limit korelasi (BTC/ETH/SOL = satu faktor risiko).
  - Tambah modul: `domain/market_registry.py`, `app/scanner.py`, `app/worker.py`.
- Urutan bangun: single-market BTC 5m dulu (Fase 0–3), multi-market = Fase 4
  (lihat PROMPT M.*).
