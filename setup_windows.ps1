$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot\services\geometry-api
try {
  if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Membuat virtual environment...' -ForegroundColor Cyan
    $created = $false
    try {
      & py -3.11 -m venv .venv
      if ($LASTEXITCODE -eq 0) { $created = $true }
    } catch {}

    if (-not $created) {
      & py -3 -m venv .venv
    }
  }

  if (-not (Test-Path '.venv\Scripts\python.exe')) {
    throw 'Virtual environment gagal dibuat. Jalankan py --list untuk mengecek Python.'
  }

  & .\.venv\Scripts\python.exe -m pip install --upgrade pip
  & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  Write-Host 'Setup selesai. Jalankan .\run_windows.ps1' -ForegroundColor Green
} finally {
  Pop-Location
}
