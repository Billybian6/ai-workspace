$ErrorActionPreference = "Stop"

$SystemRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $SystemRoot

python .\tools\akshare_warehouse.py backfill --preset probe --days 30
if ($LASTEXITCODE -ne 0) {
  throw "akshare_warehouse.py probe failed with exit code $LASTEXITCODE"
}
python .\tools\update_congestion.py
if ($LASTEXITCODE -ne 0) {
  throw "update_congestion.py failed with exit code $LASTEXITCODE"
}
python .\tools\build_market_dashboard.py
if ($LASTEXITCODE -ne 0) {
  throw "build_market_dashboard.py failed with exit code $LASTEXITCODE"
}
python .\tools\research_db.py ingest --latest daily
if ($LASTEXITCODE -ne 0) {
  throw "research_db.py ingest daily failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Daily dashboard generated:"
Write-Host "  Page: .\dashboard.html"
Write-Host "  Reports: .\outputs\reports"
Write-Host "  Database: .\data\research_warehouse.sqlite"
