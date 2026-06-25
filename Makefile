.PHONY: help install lint type test run-readonly clean

help:
	@echo "5min-btc-polymarket Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  install        - Install dependencies (uv or pip)"
	@echo "  lint           - Run ruff linter"
	@echo "  format         - Run black formatter"
	@echo "  type           - Run mypy type checker"
	@echo "  test           - Run pytest"
	@echo "  run-readonly   - Run bot in readonly mode (not implemented yet)"
	@echo "  clean          - Remove cache and build artifacts"

install:
	uv sync --all-extras

lint:
	uv run ruff check src tests

format:
	uv run black src tests
	uv run ruff check --fix src tests

type:
	uv run mypy src tests

test:
	uv run pytest

run-readonly:
	uv run python -m btcbot.app.cli --demo --max-rounds 3 --updates-per-round 3

clean:
	@if exist .pytest_cache rmdir /s /q .pytest_cache 2>nul
	@if exist .mypy_cache rmdir /s /q .mypy_cache 2>nul
	@if exist .ruff_cache rmdir /s /q .ruff_cache 2>nul
	@for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
	@if exist dist rmdir /s /q dist 2>nul
	@if exist build rmdir /s /q build 2>nul
	@for /d %%d in (*.egg-info) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
	@echo Cache and build artifacts cleaned
