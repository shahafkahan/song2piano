$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
  Copy-Item ".env.example" ".env"
}

$hasGpu = $false
try {
  $null = & nvidia-smi 2>$null
  if ($LASTEXITCODE -eq 0) { $hasGpu = $true }
} catch {
  $hasGpu = $false
}

if ($hasGpu) {
  Write-Host "Detected NVIDIA GPU — launching Docker Compose with CUDA overlay..." -ForegroundColor Green
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build @args
} else {
  Write-Host "No NVIDIA GPU detected — launching CPU Docker Compose..." -ForegroundColor Yellow
  docker compose -f docker-compose.yml up --build @args
}
