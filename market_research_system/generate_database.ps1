$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python .\tools\akshare_warehouse.py backfill --preset probe --days 30
if ($LASTEXITCODE -ne 0) {
  throw "akshare_warehouse.py probe failed with exit code $LASTEXITCODE"
}

python .\tools\research_db.py ingest --all
if ($LASTEXITCODE -ne 0) {
  throw "research_db.py ingest --all failed with exit code $LASTEXITCODE"
}

python .\tools\research_db.py summary
if ($LASTEXITCODE -ne 0) {
  throw "research_db.py summary failed with exit code $LASTEXITCODE"
}
