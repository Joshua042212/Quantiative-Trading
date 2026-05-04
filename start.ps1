$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find Python executable with fallback chain.
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
    Write-Host 'Please install Python or activate .venv' -ForegroundColor Red
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

Start-Sleep -Seconds 1

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host 'Development Environment Started' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ''
Write-Host 'Backend:  http://localhost:8000' -ForegroundColor Cyan
Write-Host 'Frontend: http://localhost:5173' -ForegroundColor Cyan
Write-Host ''
Write-Host 'Note: Two windows will open for backend and frontend processes.' -ForegroundColor Yellow
Write-Host 'Close either window to stop the corresponding service.' -ForegroundColor Yellow
Write-Host ''