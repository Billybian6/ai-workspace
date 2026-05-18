$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

python .\tools\akshare_warehouse.py backfill --preset probe --days 30
if ($LASTEXITCODE -ne 0) {
  throw "akshare_warehouse.py probe failed with exit code $LASTEXITCODE"
}
python .\tools\build_strategy_dashboard.py
if ($LASTEXITCODE -ne 0) {
  throw "build_strategy_dashboard.py failed with exit code $LASTEXITCODE"
}
python .\tools\research_db.py ingest --latest strategy
if ($LASTEXITCODE -ne 0) {
  throw "research_db.py ingest strategy failed with exit code $LASTEXITCODE"
}
python .\tools\backfill_index_minutes.py --source cache --index all
if ($LASTEXITCODE -ne 0) {
  throw "backfill_index_minutes.py cache backfill failed with exit code $LASTEXITCODE"
}
python .\tools\build_intraday_research.py
if ($LASTEXITCODE -ne 0) {
  throw "build_intraday_research.py failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Done."
Write-Host "  Page: .\strategy_dashboard.html"
Write-Host "  Data: .\data\cache\strategy_dashboard.json"
Write-Host "  Database: .\data\research_warehouse.sqlite"
Write-Host "  Minute bars: .\data\research_warehouse.sqlite table index_minute_bars"
Write-Host "  Intraday page: .\intraday_research.html"
