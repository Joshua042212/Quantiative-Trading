$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Resolve Python executable across common setups (.venv, py launcher, python in PATH).
$pythonExe = $null
$pythonArgs = @()

if (Test-Path "$projectRoot\.venv\Scripts\python.exe") {
  $pythonExe = "$projectRoot\.venv\Scripts\python.exe"
} elseif ((Get-Command py -ErrorAction SilentlyContinue) -and (py -3 --version 2>$null)) {
  $pythonExe = 'py'
  $pythonArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $pythonExe = 'python'
} else {
  Write-Host 'ERROR: Could not find Python executable' -ForegroundColor Red
  Write-Host 'Please install Python or create .venv first.' -ForegroundColor Red
  exit 1
}

$backendCommand = "Set-Location '$projectRoot'; "
if ($pythonArgs.Count -gt 0) {
  $backendCommand += "& '$pythonExe' $($pythonArgs -join ' ') -m uvicorn backend.main:app --reload --host localhost --port 8000"
} else {
  $backendCommand += "& '$pythonExe' -m uvicorn backend.main:app --reload --host localhost --port 8000"
}

$frontendCommand = "Set-Location '$projectRoot'; npm.cmd run dev"

Start-Process powershell -ArgumentList @(
  '-NoExit',
  '-ExecutionPolicy',
  'Bypass',
  '-Command',
  $backendCommand
)

Start-Sleep -Seconds 1

Start-Process powershell -ArgumentList @(
  '-NoExit',
  '-ExecutionPolicy',
  'Bypass',
  '-Command',
  $frontendCommand
)

Write-Host 'Backend: http://localhost:8000'
Write-Host 'Frontend: http://localhost:5173 (or 5174 if 5173 is occupied)'
