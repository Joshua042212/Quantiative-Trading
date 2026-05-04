# setup_scheduler.ps1
# Run this script as Administrator to create a daily 20:00 scheduled task.

$ErrorActionPreference = "Stop"

$TaskName = "StockDB_DailyUpdate"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "backend\scheduled_tasks.py"
$LogDir = Join-Path $ProjectRoot "backend\logs"

if (-not (Test-Path -Path $PythonExe)) {
    Write-Error "Python executable not found: $PythonExe"
    exit 1
}

if (-not (Test-Path -Path $Script)) {
    Write-Error "Scheduler script not found: $Script"
    exit 1
}

if (-not (Test-Path -Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "Created log directory: $LogDir"
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "backend\scheduled_tasks.py --backfill-days 5" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At "20:00"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Daily stock data update via backend/scheduled_tasks.py" -ErrorAction Stop | Out-Null
}
catch {
    Write-Error "Failed to register scheduled task. Run PowerShell as Administrator and try again. Details: $($_.Exception.Message)"
    exit 1
}

$createdTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $createdTask) {
    Write-Error "Task registration did not complete successfully. Please run PowerShell as Administrator and try again."
    exit 1
}

Write-Host ""
Write-Host "================================================================"
Write-Host "Task created successfully: $TaskName"
Write-Host "================================================================"
Write-Host "Schedule       : Daily at 20:00"
Write-Host "Script         : $Script"
Write-Host "Python         : $PythonExe"
Write-Host "Working dir    : $ProjectRoot"
Write-Host "Catch-up       : StartWhenAvailable enabled"
Write-Host "Log path       : $LogDir\scheduled_YYYYMMDD.log"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now:"
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "  Check status:"
Write-Host "    Get-ScheduledTask -TaskName '$TaskName' | Select-Object TaskName, State"
Write-Host ""
Write-Host "  Remove task:"
Write-Host "    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host "================================================================"
