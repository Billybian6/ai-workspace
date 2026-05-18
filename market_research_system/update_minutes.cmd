@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%generate_minutes.ps1"
if errorlevel 1 (
  echo.
  echo Failed. Press any key to close.
  pause >nul
  exit /b %errorlevel%
)
echo.
echo Done. Press any key to close.
if not "%NO_PAUSE%"=="1" pause >nul
exit /b 0
