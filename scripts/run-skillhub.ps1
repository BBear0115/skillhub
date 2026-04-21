Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$frontendNpm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$dataRoot = Join-Path $backendDir "data"
$storageRoot = Join-Path $backendDir "storage"
$backendStdoutLog = Join-Path $backendDir ".skillhub-backend.stdout.log"
$backendStderrLog = Join-Path $backendDir ".skillhub-backend.stderr.log"
$frontendStdoutLog = Join-Path $frontendDir ".skillhub-frontend.stdout.log"
$frontendStderrLog = Join-Path $frontendDir ".skillhub-frontend.stderr.log"
$startupTimeoutSeconds = 15
if ($env:SKILLHUB_START_TIMEOUT_SECONDS) {
  $startupTimeoutSeconds = [int]$env:SKILLHUB_START_TIMEOUT_SECONDS
}

if (-not (Test-Path $backendPython)) {
  throw "Backend virtual environment not found at $backendPython"
}

if ($null -eq $frontendNpm) {
  throw "npm.cmd was not found in PATH"
}

$frontendNodeModules = Join-Path $frontendDir "node_modules"
if (-not (Test-Path $frontendNodeModules)) {
  throw "Frontend dependencies not found at $frontendNodeModules"
}

if (-not (Test-Path $dataRoot)) {
  New-Item -ItemType Directory -Path $dataRoot | Out-Null
}

if (-not (Test-Path $storageRoot)) {
  New-Item -ItemType Directory -Path $storageRoot | Out-Null
}

$env:DATABASE_URL = "sqlite:///./data/skillhub.db"
$env:STORAGE_ROOT = $storageRoot
$env:FRONTEND_URL = "http://localhost:5173"
$env:PYTHONDONTWRITEBYTECODE = "1"

function Wait-ForUrl {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [Parameter(Mandatory = $true)]
    [System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds = 20
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) {
      throw "Process exited before $Url became available."
    }

    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        return
      }
    }
    catch {
      Start-Sleep -Milliseconds 500
    }
  }

  throw "Timed out waiting for $Url"
}

function Test-UrlReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url
  )

  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
  }
  catch {
    return $false
  }
}

function Test-PortListening {
  param(
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  $connections = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {
    $_.LocalAddress -eq "127.0.0.1" -and $_.LocalPort -eq $Port
  }
  return $null -ne $connections
}

Write-Host "Validating backend import..."
& $backendPython -c "import app.main; print('backend-import-ok')"
if ($LASTEXITCODE -ne 0) { throw "Backend import validation failed." }

Write-Host "Using local SQLite database at backend/data/skillhub.db"
Write-Host "Resolved backend storage root: $storageRoot"
Write-Host "Frontend URL: $($env:FRONTEND_URL)"
Write-Host "Startup timeout: ${startupTimeoutSeconds}s"
Write-Host "Backend logs: $backendStdoutLog / $backendStderrLog"
Write-Host "Frontend logs: $frontendStdoutLog / $frontendStderrLog"
Write-Host "Starting frontend with the Vite development server."

Write-Host "Starting backend and frontend..."
Remove-Item -LiteralPath $backendStdoutLog, $backendStderrLog, $frontendStdoutLog, $frontendStderrLog -ErrorAction SilentlyContinue

$backendReady = Test-UrlReady -Url "http://127.0.0.1:8000/health"
$frontendReady = Test-UrlReady -Url "http://127.0.0.1:5173/"
$backendStarted = $false
$frontendStarted = $false

if ($backendReady) {
  Write-Host "Backend already available on http://127.0.0.1:8000/health, reusing existing process."
}
elseif (Test-PortListening -Port 8000) {
  throw "Port 8000 is already in use by another process, and SkillHub backend is not responding on /health."
}
else {
  $backendProcess = Start-Process -FilePath $backendPython -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $backendDir -RedirectStandardOutput $backendStdoutLog -RedirectStandardError $backendStderrLog -PassThru
  $backendStarted = $true
  Write-Host "Started backend process PID $($backendProcess.Id)"
}

if ($frontendReady) {
  Write-Host "Frontend already available on http://127.0.0.1:5173/, reusing existing process."
}
elseif (Test-PortListening -Port 5173) {
  throw "Port 5173 is already in use by another process, and SkillHub frontend is not responding."
}
else {
  $frontendProcess = Start-Process -FilePath $frontendNpm.Source -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173") -WorkingDirectory $frontendDir -RedirectStandardOutput $frontendStdoutLog -RedirectStandardError $frontendStderrLog -PassThru
  $frontendStarted = $true
  Write-Host "Started frontend process PID $($frontendProcess.Id)"
}

try {
  if ($backendStarted) {
    Wait-ForUrl -Url "http://127.0.0.1:8000/health" -Process $backendProcess -TimeoutSeconds $startupTimeoutSeconds
  }
  if ($frontendStarted) {
    Wait-ForUrl -Url "http://127.0.0.1:5173/" -Process $frontendProcess -TimeoutSeconds $startupTimeoutSeconds
  }
}
catch {
  if ($backendStarted -and -not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id -Force }
  if ($frontendStarted -and -not $frontendProcess.HasExited) { Stop-Process -Id $frontendProcess.Id -Force }
  if (Test-Path $backendStdoutLog) { Write-Host "`nBackend stdout:"; Get-Content $backendStdoutLog }
  if (Test-Path $backendStderrLog) { Write-Host "`nBackend stderr:"; Get-Content $backendStderrLog }
  if (Test-Path $frontendStdoutLog) { Write-Host "`nFrontend stdout:"; Get-Content $frontendStdoutLog }
  if (Test-Path $frontendStderrLog) { Write-Host "`nFrontend stderr:"; Get-Content $frontendStderrLog }
  throw
}

if ($backendStarted) {
  Write-Host "Backend PID: $($backendProcess.Id)"
}
if ($frontendStarted) {
  Write-Host "Frontend PID: $($frontendProcess.Id)"
}
Write-Host "SkillHub is starting on http://localhost:5173 and http://localhost:8000"
