$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "tools\run_strategy_dashboard.ps1")
