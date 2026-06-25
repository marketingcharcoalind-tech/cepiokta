# 03 — Tech Stack

## 3.1 Pilihan Bahasa
| Opsi | Kapan dipilih | Catatan |
|---|---|---|
| **Python 3.11+** (DEFAULT) | Iterasi cepat, ekosistem trading/data kaya | Async cukup untuk latensi detik. Rekomendasi awal. |
| TypeScript/Node | Tim lebih kuat di TS; tooling web3 matang | ethers.js bagus untuk signing EIP-712. |
| Rust/Go | Butuh latensi sub-100ms serius, kompetisi ketat | Overhead dev tinggi; lakukan hanya jika fase 4. |

> Default blueprint = **Python**. Jika menyimpang, tulis ADR.

## 3.2 Library Inti (Python)
| Kebutuhan | Library |
|---|---|
| Async runtime | `asyncio` (+ `uvloop` di Linux) |
| HTTP REST | `httpx` (async) |
| WebSocket | `websockets` atau `aiohttp` |
| Wallet / signing EIP-712 | `web3.py` + `eth-account` |
| Polymarket CLOB | SDK resmi **CLOB V2** (verifikasi nama/versi terbaru) — jangan pakai `py-clob-client` V1 yang diarsipkan kecuali hanya untuk referensi struktur |
| Config | `pydantic-settings` |
| DB | `sqlite3`/`aiosqlite` (dev) → `asyncpg`+Postgres (prod); ORM opsional `SQLAlchemy` |
| Logging | `structlog` (JSON) |
| Metrics | `prometheus-client` |
| Test | `pytest`, `pytest-asyncio`, `freezegun`/clock palsu, `respx` (mock httpx) |
| Lint/format/types | `ruff`, `black`, `mypy`/`pyright` |
| Decimal money | `decimal.Decimal` (JANGAN float untuk uang/harga) |

## 3.3 Infrastruktur
- **Chain**: Polygon (USDC.e untuk modal, sedikit MATIC untuk gas).
- **RPC node**: provider andal (mis. layanan RPC Polygon) + fallback.
- **Host**: VPS Linux dekat infra Polymarket; Docker; systemd `restart=always`.
- **Time**: NTP wajib.
- **Secrets**: `.env` (dev) → secret manager / KMS (prod). Jangan commit.

## 3.4 Dev Tooling
- `uv` atau `poetry` untuk dependency & venv.
- `pre-commit` (ruff+black+mypy) sebelum commit.
- `Makefile`/`justfile`: `make lint`, `make test`, `make run-readonly`.
- CI (GitHub Actions): lint + type + test pada tiap PR.

## 3.5 Aturan Numerik (penting)
- Semua harga/ukuran/uang pakai `Decimal`. Konversi ke float HANYA untuk
  metrik/plot.
- Tick size & sizing harus mengikuti aturan market Polymarket (cek via Gamma).
- Timestamp selalu UTC, aware datetime.



---

## ADDENDUM (v1.1) — Telegram Control Plane
Lihat `docs/12-TELEGRAM_INTEGRATION.md` untuk detail.
Tambahan dependency:
- **python-telegram-bot** v20+ (async) — notifikasi & tombol kontrol. Alternatif: `aiogram` v3.
Catatan: Telegram bersifat AUXILIARY (best-effort). Bot harus tetap jalan jika Telegram down.
