$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$crawlerCommand = "Set-Location '$projectRoot'; & '$pythonPath' backend/crawlers/data_crawler.py full-kline --continuous --period 10y --cycle-sleep 600 --min-delay 2 --max-delay 5"

Start-Process powershell -ArgumentList @(
  '-NoExit',
  '-ExecutionPolicy',
  'Bypass',
  '-Command',
  $crawlerCommand
)

Write-Host 'Started unified full-market data sync in a new window.'
