# ✅ Scaffolding Complete — Fase 0 (Tasks 1-2)

**Date:** 2026-06-25  
**Status:** Ready for Task 3+

## What Was Built

### 1. Project Structure
```
5min-btc-polymarket-blueprint-v1.3/
├── src/btcbot/              # Main source code
│   ├── config/              # Settings loader (empty)
│   ├── adapters/            # External I/O (empty)
│   ├── domain/              # Pure logic (empty)
│   ├── exec/                # OMS, sizing (empty)
│   ├── risk/                # Risk manager (empty)
│   ├── data/                # Store, recorder (empty)
│   ├── backtest/            # Replay engine (empty)
│   └── app/                 # Runners (empty)
├── tests/                   # Mirror structure
├── docs/                    # Blueprint docs (01-14)
│   └── adr/                 # Architecture decisions
├── .github/workflows/       # CI pipeline
├── pyproject.toml           # Dependencies & config
├── .env.example             # Secrets template
├── run.ps1                  # Windows dev commands
├── Makefile                 # Linux/Mac dev commands
└── README.md                # Setup guide
```

### 2. Dependencies Installed (via `uv`)
**Runtime:**
- `httpx` — Async HTTP client (REST APIs)
- `websockets` — WebSocket client (CLOB WSS)
- `web3`, `eth-account` — Ethereum/Polygon wallet & EIP-712 signing
- `pydantic-settings` — Config management
- `aiosqlite` — Async SQLite
- `structlog` — Structured JSON logging
- `prometheus-client` — Metrics export

**Dev:**
- `pytest`, `pytest-asyncio` — Testing
- `ruff` — Linting
- `black` — Formatting
- `mypy` — Type checking (strict)
- `respx` — Mock HTTP
- `freezegun` — Mock time

### 3. Tooling Configured
- **Ruff:** Fast linter with 30+ rule families (E, F, I, ANN, B, etc.)
- **Black:** Code formatter (100 char line length)
- **Mypy:** Strict type checking enabled
- **Pre-commit:** Auto-run ruff+black+mypy before commits
- **CI:** GitHub Actions (lint + type + test on PR)

### 4. Development Commands

**Windows (PowerShell):**
```powershell
.\run.ps1 lint      # ✅ All checks passed
.\run.ps1 type      # ✅ Success: 0 issues in 20 files
.\run.ps1 test      # ✅ 2/2 passed
.\run.ps1 format    # Format & auto-fix
.\run.ps1 clean     # Remove cache
```

**Linux/Mac:**
```bash
make lint / make type / make test / make format / make clean
```

## DoD Verification ✅

- [x] `.\run.ps1 lint` passes — No linting errors
- [x] `.\run.ps1 type` passes — Mypy strict (0 issues, 20 files)
- [x] `.\run.ps1 test` passes — 2 placeholder tests pass
- [x] Directory structure created per AGENTS.md §5
- [x] All modules have `__init__.py`
- [x] Config files: pyproject.toml, .gitignore, .env.example
- [x] Tooling: ruff, black, mypy, pre-commit, CI
- [x] README with setup instructions
- [x] No business logic (scaffolding only, as required)

## Safety Compliance ✅

- [x] `.gitignore` excludes `.env`, `*.key`, `secrets/`, `*.db`
- [x] `.env.example` has placeholders (no real secrets)
- [x] MODE flag prepared (default `readonly`)
- [x] No order execution code (Fase 0 is read-only only)

## Next Steps (Remaining Fase 0 Tasks)

From `docs/10-ROADMAP.md` and `.kiro/specs/btc-bot/tasks.md`:

- [ ] **Task 3:** `adapters/clock.py` — SystemClock + SimClock + tests
- [ ] **Task 4:** `adapters/gamma.py` — Discover BTC 5m rounds + mock tests
- [ ] **Task 5:** `adapters/clob_ws.py` — Stream orderbook + reconnect + stale detection
- [ ] **Task 6:** `adapters/chainlink.py` — BTC/USD price feed (acuan resolusi)
- [ ] **Task 7:** `data/store.py` + `recorder.py` — Persist rounds/book/signals
- [ ] **Task 8:** `app/cli.py` — Boot sequence + readonly runner (NO orders)

**Gate G0:** Bot jalan berhari-hari merekam data; nol order; test adapter hijau.

## Notes

- **Package manager:** Using `uv` (modern, fast; fallback to pip if needed)
- **Platform:** Cross-platform (Windows PowerShell + Linux/Mac Makefile)
- **Type safety:** Mypy strict mode enforced
- **Testing:** pytest + pytest-asyncio for async tests
- **CI:** GitHub Actions ready (auto-runs on PR)
- **No `make` on Windows:** Use `.\run.ps1` instead

## How to Start Developing

```powershell
# 1. Install dependencies (if not done yet)
.\run.ps1 install

# 2. Activate pre-commit (optional)
uv run pre-commit install

# 3. Start implementing Task 3 (clock.py)
# Create: src/btcbot/adapters/clock.py
# Create: tests/adapters/test_clock.py

# 4. Run tests after each module
.\run.ps1 test

# 5. Commit with confidence (pre-commit hooks will lint/type/format)
git add .
git commit -m "feat: implement SystemClock + SimClock (Fase 0 Task 3)"
```

---

**Scaffolding selesai. Siap untuk Task 3+. Jangan loncat fase! 🚀**
