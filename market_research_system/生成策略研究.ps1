$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

powershell -ExecutionPolicy Bypass -File .\tools\run_strategy_dashboard.ps1
