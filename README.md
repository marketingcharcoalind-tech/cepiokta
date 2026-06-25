# 5min-btc-polymarket

Bot trading otomatis untuk market prediksi Polymarket "Bitcoin Up or Down — 5 menit".

**Strategi:** End-of-window settlement arbitrage ("take the already-settled side").

**Prioritas:** Infrastruktur untuk **mengukur edge secara jujur** melalui readonly → backtest → paper → live micro-stakes.

> ⚠️ **Disclaimer:** Proyek ini netral terhadap hasil. Edge nyata mungkin tipis/negatif. Sistem dirancang untuk mengukur, bukan mengasumsikan profit.

## Dokumentasi

Lihat folder `docs/` untuk spesifikasi lengkap:
- `01-PROJECT_CONTEXT.md` — Latar belakang & tujuan
- `02-ARCHITECTURE.md` — Arsitektur berlapis
- `05-STRATEGY_SPEC.md` — Math strategi (p_win, net_edge)
- `06-RISK_MANAGEMENT.md` — Safety gates & kill-switch
- `10-ROADMAP.md` — Fase 0–4 (ikuti berurutan)

## Quick Start

### Setup (menggunakan `uv` — recommended)

```bash
# Install uv (jika belum ada)
# Windows (PowerShell):
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Clone & setup
git clone <repo-url>
cd 5min-btc-polymarket-blueprint-v1.3
uv sync --all-extras
```

### Setup (alternatif: pip + venv)

```bash
python -m venv .venv

# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -e ".[dev]"
```

### Development

**Windows (PowerShell):**
```powershell
# Lint
.\run.ps1 lint

# Format code
.\run.ps1 format

# Type check
.\run.ps1 type

# Run tests
.\run.ps1 test

# Clean cache
.\run.ps1 clean
```

**Linux/Mac (Makefile):**
```bash
# Lint
make lint

# Format code
make format

# Type check
make type

# Run tests
make test

# Clean cache
make clean
```

### Pre-commit hooks (opsional)

```bash
pip install pre-commit
pre-commit install
# Sekarang ruff+black+mypy jalan otomatis sebelum commit
```

## Project Structure

```
src/btcbot/
  config/        # Settings & secrets loader
  adapters/      # I/O: gamma, clob, clob_ws, chainlink, clock
  domain/        # Pure logic: market, signal, strategy
  exec/          # OMS, sizing
  risk/          # Risk manager (veto, kill-switch, circuit breaker)
  data/          # Store, recorder (fase 0)
  backtest/      # Replay engine (fase 1)
  app/           # CLI, paper, live runner
tests/           # Mirror src structure
```

## Roadmap Status

- [ ] **Fase 0** — Scaffolding & Read-only (NO orders) → G0
- [ ] **Fase 1** — Backtest / Replay → G1
- [ ] **Fase 2** — Paper trading → G2
- [ ] **Fase 3** — Live micro-stakes ($1–$5) → G3
- [ ] **Fase 4** — Hardening & scale → G4

> **JANGAN loncat fase.** Setiap fase butuh test lulus sebelum lanjut.

## Mode Operasi

`MODE=readonly|backtest|paper|live` (default: `readonly`)

- `readonly` — Hanya rekam data, tidak ada order
- `backtest` — Replay data terekam
- `paper` — Trading simulasi realtime
- `live` — Uang nyata (butuh `LIVE_CONFIRMED=yes` + limits konservatif)

## Safety Rules (NON-NEGOTIABLE)

1. **Tidak ada order live sebelum Fase 0–2 selesai & ter-test**
2. **Tidak ada secret di source code** (private key, API secret, seed)
3. **Setiap order lewat Risk Manager** (bisa di-VETO)
4. **Kill-switch & circuit breaker wajib ada sebelum live**
5. **Order pertama: notional sangat kecil ($1–$5)**

## Contributing

- Type hints wajib (`mypy --strict`)
- Lint dengan `ruff`, format dengan `black`
- Test-first: unit test tiap modul
- CI hijau sebelum merge
- ADR di `docs/adr/` untuk keputusan besar

## License

(Tentukan lisensi sesuai kebutuhan)
