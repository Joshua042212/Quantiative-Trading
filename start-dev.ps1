$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendCommand = "Set-Location '$projectRoot'; C:/Python314/python.exe -m uvicorn backend.main:app --reload --host localhost --port 8000"
$frontendCommand = "Set-Location '$projectRoot'; npm run dev"

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
