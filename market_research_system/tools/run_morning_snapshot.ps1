$ErrorActionPreference = "Stop"

$SystemRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $SystemRoot

python .\tools\akshare_warehouse.py backfill --preset probe --days 30
if ($LASTEXITCODE -ne 0) {
  throw "akshare_warehouse.py probe failed with exit code $LASTEXITCODE"
}
python .\tools\build_morning_dashboard.py
if ($LASTEXITCODE -ne 0) {
  throw "build_morning_dashboard.py failed with exit code $LASTEXITCODE"
}
python .\tools\research_db.py ingest --latest morning
if ($LASTEXITCODE -ne 0) {
  throw "research_db.py ingest morning failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Morning snapshot generated:"
Write-Host "  Page: .\morning_dashboard.html"
Write-Host ("  Report: .\outputs\reports\morning_" + (Get-Date -Format 'yyyy-MM-dd') + ".md")
Write-Host "  Database: .\data\research_warehouse.sqlite"
