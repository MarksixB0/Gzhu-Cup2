@echo off
setlocal
cd /d "%~dp0apps\desktop"

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm not found in PATH.
  pause
  exit /b 1
)

echo.
echo  Starting XinBan desktop app...
echo  Electron starts the backend automatically (uses .venv\Scripts\uvicorn.exe).
echo.

rem If the app is already running, quit it from the tray first, otherwise the
rem single-instance lock just focuses the old window (with the old code).
call npm start

pause
