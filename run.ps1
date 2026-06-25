# run.ps1 - PowerShell script for development tasks
# Usage: .\run.ps1 <command>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('help', 'install', 'lint', 'format', 'type', 'test', 'run-readonly', 'clean')]
    [string]$Command
)

function Show-Help {
    Write-Host "5min-btc-polymarket Development Commands" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\run.ps1 <command>" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Available commands:"
    Write-Host "  install        - Install dependencies (uv)"
    Write-Host "  lint           - Run ruff linter"
    Write-Host "  format         - Run black formatter"
    Write-Host "  type           - Run mypy type checker"
    Write-Host "  test           - Run pytest"
    Write-Host "  run-readonly   - Run bot in readonly mode (not implemented yet)"
    Write-Host "  clean          - Remove cache and build artifacts"
}

function Invoke-Install {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    uv sync --all-extras
}

function Invoke-Lint {
    Write-Host "Running linter..." -ForegroundColor Cyan
    uv run ruff check src tests
}

function Invoke-Format {
    Write-Host "Formatting code..." -ForegroundColor Cyan
    uv run black src tests
    uv run ruff check --fix src tests
}

function Invoke-Type {
    Write-Host "Type checking..." -ForegroundColor Cyan
    uv run mypy src tests
}

function Invoke-Test {
    Write-Host "Running tests..." -ForegroundColor Cyan
    uv run pytest
}

function Invoke-RunReadonly {
    Write-Host "Running readonly demo (no orders, fixture adapters)..." -ForegroundColor Cyan
    uv run python -m btcbot.app.cli --demo --max-rounds 3 --updates-per-round 3
}

function Invoke-Clean {
    Write-Host "Cleaning cache and build artifacts..." -ForegroundColor Cyan
    
    $dirs = @(
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build"
    )
    
    foreach ($dir in $dirs) {
        if (Test-Path $dir) {
            Remove-Item -Recurse -Force $dir
            Write-Host "  Removed $dir"
        }
    }
    
    # Remove __pycache__ directories recursively
    Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    
    # Remove .egg-info directories
    Get-ChildItem -Directory -Filter "*.egg-info" | Remove-Item -Recurse -Force
    
    Write-Host "Cache and build artifacts cleaned" -ForegroundColor Green
}

switch ($Command) {
    'help' { Show-Help }
    'install' { Invoke-Install }
    'lint' { Invoke-Lint }
    'format' { Invoke-Format }
    'type' { Invoke-Type }
    'test' { Invoke-Test }
    'run-readonly' { Invoke-RunReadonly }
    'clean' { Invoke-Clean }
}
