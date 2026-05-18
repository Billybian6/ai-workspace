@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%generate_intraday_research.ps1"
if errorlevel 1 (
  echo.
  echo Failed. Press any key to close.
  pause >nul
  exit /b %errorlevel%
)
if not "%NO_OPEN%"=="1" start "" "%ROOT%intraday_research.html"
exit /b 0
