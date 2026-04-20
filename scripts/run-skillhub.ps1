param(
  [switch]$UseExistingBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$runtimeStorage = Join-Path $backendDir "runtime-storage"

if (-not (Test-Path $backendPython)) {
  throw "Backend virtual environment not found at $backendPython"
}

if (-not (Test-Path $runtimeStorage)) {
  New-Item -ItemType Directory -Path $runtimeStorage | Out-Null
}

$env:DATABASE_URL = "sqlite://"
$env:STORAGE_ROOT = $runtimeStorage
$env:FRONTEND_URL = "http://localhost:5173"
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "Running backend tests..."
& $backendPython $backendDir\tests\run_backend_tests.py
if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }

Write-Host "Running frontend type checks..."
Push-Location $frontendDir
try {
  npx tsc --noEmit
  if ($LASTEXITCODE -ne 0) { throw "Frontend type checks failed." }
  if ($UseExistingBuild) {
    if (-not (Test-Path (Join-Path $frontendDir "dist\\index.html"))) {
      throw "UseExistingBuild was set, but frontend/dist/index.html does not exist."
    }
    Write-Host "Using existing frontend build artifacts..."
  }
  else {
    Write-Host "Building frontend..."
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
  }
}
finally {
  Pop-Location
}

Write-Host "Migration files are prepared for persistent environments."
Write-Host "Local auto-run uses in-memory SQLite because file-backed SQLite is blocked in this environment."

Write-Host "Starting backend and frontend..."
$backendProcess = Start-Process -FilePath $backendPython -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $backendDir -PassThru
$frontendProcess = Start-Process -FilePath $backendPython -ArgumentList @("-m", "http.server", "5173", "--bind", "127.0.0.1", "--directory", (Join-Path $frontendDir "dist")) -WorkingDirectory $frontendDir -PassThru

Write-Host "Backend PID: $($backendProcess.Id)"
Write-Host "Frontend PID: $($frontendProcess.Id)"
Write-Host "SkillHub is starting on http://localhost:5173 and http://localhost:8000"
