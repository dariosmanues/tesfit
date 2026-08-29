$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\services\geometry-api

if (-not (Test-Path '.venv\Scripts\python.exe')) {
  Write-Host 'Virtual environment belum ada. Jalankan setup_windows.ps1 dulu.' -ForegroundColor Yellow
  exit 1
}

Write-Host 'Development OS Milestone 2.5.12 -> http://localhost:8000' -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
