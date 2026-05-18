@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%generate_strategy.ps1"
if errorlevel 1 (
  echo.
  echo Failed. Press any key to close.
  pause >nul
  exit /b %errorlevel%
)
if not "%NO_OPEN%"=="1" start "" "%ROOT%strategy_dashboard.html"
exit /b 0
