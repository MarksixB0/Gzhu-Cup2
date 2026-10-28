@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\uvicorn.exe" (
  echo [ERROR] .venv\Scripts\uvicorn.exe not found.
  echo Create the env first, then retry.
  pause
  exit /b 1
)

echo.
echo  Starting backend at http://127.0.0.1:8000
echo  Keep this window open. Press Ctrl+C to stop.
echo.
".venv\Scripts\uvicorn.exe" gzhu_cup2.entrypoints.api:app --host 127.0.0.1 --port 8000

echo.
echo Backend stopped.
pause
