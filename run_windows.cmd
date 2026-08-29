@echo off
setlocal
cd /d "%~dp0services\geometry-api"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment belum ada. Jalankan setup_windows.cmd dulu.
  pause
  exit /b 1
)
echo Development OS Milestone 2.5.12 ^-^> http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
