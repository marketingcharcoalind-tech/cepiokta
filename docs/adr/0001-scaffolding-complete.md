# ADR 0001 — Scaffolding Complete

**Status:** ✅ Accepted  
**Date:** 2026-06-25  
**Context:** Fase 0, Task 1–2 (ROADMAP §10)

## Decision

Scaffolding proyek `5min-btc-polymarket` selesai dengan struktur berikut:

### Directory Structure
```
src/btcbot/
  config/        # Settings & secrets loader
  adapters/      # I/O: gamma, clob, clob_ws, chainlink, clock
  domain/        # Pure logic: market, signal, strategy
  exec/          # OMS, sizing
  risk/          # Risk manager
  data/          # Store, recorder
  backtest/      # Replay engine
  app/           # CLI, paper, live runner
tests/           # Mirror structure of src
```

### Tooling
- **Package manager:** `uv` (modern, fast Python package/project manager)
- **Dependencies:**
  - Runtime: `httpx`, `websockets`, `web3`, `eth-account`, `pydantic-settings`, `aiosqlite`, `structlog`, `prometheus-client`
  - Dev: `pytest`, `pytest-asyncio`, `ruff`, `black`, `mypy`, `respx`, `freezegun`
- **Linting:** `ruff` (fast, comprehensive Python linter)
- **Formatting:** `black` (opinionated code formatter)
- **Type checking:** `mypy` (strict mode)
- **CI:** GitHub Actions (lint + type + test on PR)

### Configuration Files
- `pyproject.toml` — Project metadata, dependencies, tool config
- `.pre-commit-config.yaml` — Pre-commit hooks (ruff + black + mypy)
- `.github/workflows/ci.yml` — CI pipeline
- `.env.example` — Environment variables template (secrets NOT committed)
- `.gitignore` — Excludes secrets, cache, DB files
- `Makefile` — Linux/Mac task runner
- `run.ps1` — Windows PowerShell task runner

### Development Commands
**Windows:**
```powershell
.\run.ps1 lint    # Ruff linter
.\run.ps1 format  # Black formatter + ruff --fix
.\run.ps1 type    # Mypy type check
.\run.ps1 test    # Pytest
.\run.ps1 clean   # Remove cache
```

**Linux/Mac:**
```bash
make lint
make format
make type
make test
make clean
```

### DoD Verification

✅ **`.\run.ps1 lint` passes** — No linting errors  
✅ **`.\run.ps1 type` passes** — Mypy strict mode (0 issues in 20 files)  
✅ **`.\run.ps1 test` passes** — Placeholder tests (2/2 passed)  
✅ **Directory structure** — All modules created with `__init__.py`  
✅ **Config tooling** — ruff, mypy, black, pre-commit configured  
✅ **CI** — GitHub Actions workflow ready  
✅ **Documentation** — README with setup instructions  

## Consequences

### Positive
- Struktur folder bersih, sesuai AGENTS.md §5
- Type safety enforced (mypy strict)
- Linting & formatting automated
- CI ensures code quality on every PR
- Cross-platform support (Windows PowerShell + Linux/Mac Makefile)

### Next Steps (Fase 0 remaining tasks)
- [ ] Task 3: `adapters/clock.py` (System + Sim)
- [ ] Task 4: `adapters/gamma.py` (discovery BTC 5m market)
- [ ] Task 5: `adapters/clob_ws.py` (stream orderbook)
- [ ] Task 6: `adapters/chainlink.py` (price feed)
- [ ] Task 7: `data/store.py` + `recorder.py`
- [ ] Task 8: `app/cli.py` (boot sequence + readonly runner)

## Notes
- No business logic implemented (as required — scaffolding only)
- All modules empty except placeholder `__init__.py`
- Secret management via env vars (`.env.example` provided, `.env` gitignored)
- MODE gating prepared (default `readonly`)
