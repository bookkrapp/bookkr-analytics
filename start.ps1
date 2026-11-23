# Start Bookr Analyst locally: syncs data if none exists, then serves the app at http://localhost:8000
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating Python environment..."
    python -m venv (Join-Path $backend ".venv")
    & $py -m pip install -q -r (Join-Path $backend "requirements.txt")
}
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "Building web app..."
    Push-Location (Join-Path $root "frontend"); npm install --no-audit --no-fund; npm run build; Pop-Location
}
if (-not (Test-Path (Join-Path $root "data\analytics.duckdb"))) {
    Write-Host "First run: syncing data from Neon..."
    Push-Location $backend; & $py -m bookr_analyst.sync; Pop-Location
}
Write-Host "Bookr Analyst running at http://localhost:8000"
& $py -m uvicorn bookr_analyst.api:app --port 8000 --app-dir $backend
