[CmdletBinding()]
param([string]$TaskName = "CasinoBae Supervisor")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Supervisor = Join-Path $Repo "scripts\casinobae-supervisor.ps1"
if (-not (Test-Path $Supervisor)) { Write-Error "Supervisor not found"; exit 2 }
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Argument = '-NoProfile -ExecutionPolicy Bypass -File "' + $Supervisor + '"'
$action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Argument
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
Write-Output ("REGISTERED " + $TaskName)
exit 0
