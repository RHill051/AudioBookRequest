# Runs the same checks as the GitHub CI workflows locally.
# Requires uv: https://docs.astral.sh/uv/getting-started/installation/
# PostgreSQL migration check is skipped (requires a running Postgres instance).

$ErrorActionPreference = "Stop"

function Run-Step {
    param([string]$Name, [scriptblock]$Block)
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "PASSED: $Name" -ForegroundColor Green
}

# check-format.yaml
Run-Step "ruff format check" { uv run ruff format --check app/ }
Run-Step "djlint template check" { uv run djlint templates/ }

# build.yaml
Run-Step "basedpyright type check" { uv run basedpyright }
Run-Step "jinjax parameter check" {
    uv run app/util/test_jinjax.py templates -g content base_url json_regexp audible_regions version changelog getattr -f toJSstring
}
Run-Step "alembic upgrade (SQLite)" { uv run alembic upgrade heads }
Run-Step "alembic schema check (SQLite)" { uv run alembic check }
Run-Step "alembic downgrade (SQLite)" { uv run alembic downgrade base }
Run-Step "alembic upgrade again (SQLite)" { uv run alembic upgrade heads }
Run-Step "alembic schema check again (SQLite)" { uv run alembic check }

Write-Host "`nAll checks passed!" -ForegroundColor Green
Write-Host "(PostgreSQL migration check skipped - requires a running Postgres instance)" -ForegroundColor Yellow
