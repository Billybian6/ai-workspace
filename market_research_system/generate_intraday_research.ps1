$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python .\tools\backfill_index_minutes.py --source cache --index all
if ($LASTEXITCODE -ne 0) {
  throw "backfill_index_minutes.py cache backfill failed with exit code $LASTEXITCODE"
}

python .\tools\build_intraday_research.py
if ($LASTEXITCODE -ne 0) {
  throw "build_intraday_research.py failed with exit code $LASTEXITCODE"
}
