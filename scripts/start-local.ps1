$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Load .env.local into process environment
Get-Content (Join-Path $Root ".env.local") | ForEach-Object {
  if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
    [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
  }
}

$ApiPython = Join-Path $Root ".venv-api\Scripts\python.exe"
$WorkerPython = Join-Path $Root ".venv-worker\Scripts\python.exe"

if (-not (Test-Path $ApiPython)) {
  throw "Missing .venv-api. Create it first and install shared/ + backend/requirements.txt."
}
if (-not (Test-Path $WorkerPython)) {
  throw "Missing .venv-worker. Create it first and install shared/ + worker/requirements.txt."
}

$LogDir = Join-Path $Root "local_data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "Starting SongScribe API on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$apiProc = Start-Process -FilePath $ApiPython `
  -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
  -WorkingDirectory (Join-Path $Root "backend") `
  -RedirectStandardOutput (Join-Path $LogDir "api.out.log") `
  -RedirectStandardError (Join-Path $LogDir "api.err.log") `
  -PassThru

Write-Host "Starting SongScribe ML Worker ..." -ForegroundColor Cyan
$workerProc = Start-Process -FilePath $WorkerPython `
  -ArgumentList "-m", "app.local_worker" `
  -WorkingDirectory (Join-Path $Root "worker") `
  -RedirectStandardOutput (Join-Path $LogDir "worker.out.log") `
  -RedirectStandardError (Join-Path $LogDir "worker.err.log") `
  -PassThru

Write-Host "Starting SongScribe Frontend on http://localhost:5173 ..." -ForegroundColor Cyan
$feProc = Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c", "npm run dev" `
  -WorkingDirectory (Join-Path $Root "frontend") `
  -RedirectStandardOutput (Join-Path $LogDir "frontend.out.log") `
  -RedirectStandardError (Join-Path $LogDir "frontend.err.log") `
  -PassThru

Write-Host ""
Write-Host "SongScribe is running!" -ForegroundColor Green
Write-Host "  Frontend : http://localhost:5173"
Write-Host "  API      : http://127.0.0.1:8000/api/health"
Write-Host "  PIDs     : API=$($apiProc.Id), Worker=$($workerProc.Id), Frontend=$($feProc.Id)"
