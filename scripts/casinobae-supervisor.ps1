[CmdletBinding()]
param(
  [switch]$Once,
  [int]$IntervalSeconds = 10
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Watcher = Join-Path $Repo "scripts\casinobae-live-watch.ps1"
$StateDir = Join-Path $Repo "state"
$LogsDir = Join-Path $Repo "logs"
$StateFile = Join-Path $StateDir "supervisor-state.json"
New-Item -ItemType Directory -Force -Path $StateDir,$LogsDir | Out-Null

function Write-State([hashtable]$state) {
  $state | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $StateFile
}

function Get-WatcherProcess {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "casinobae-live-watch\.ps1" } |
    Select-Object -First 1
}

function Start-Watcher {
  $args = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$Watcher,"-Mode","Watch")
  Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
}

function Supervisor-Once {
  $watcher = Get-WatcherProcess
  $restarted = $false
  if (-not $watcher) {
    $proc = Start-Watcher
    $watcherPid = $proc.Id
    $restarted = $true
  } else {
    $watcherPid = [int]$watcher.ProcessId
  }

  Write-State @{
    schema = 1
    status = "RUNNING"
    pid = $PID
    watcherPid = $watcherPid
    watcherRestarted = $restarted
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
  }
}

if ($Once) {
  Supervisor-Once | ConvertTo-Json
  exit 0
}

while ($true) {
  try {
    Supervisor-Once
  } catch {
    Add-Content -Path (Join-Path $LogsDir "live-errors.log") -Value ("[{0}] SUPERVISOR_ERROR {1}" -f (Get-Date).ToUniversalTime().ToString("o"), $_.Exception.Message)
    Write-State @{
      schema = 1
      status = "DEGRADED"
      pid = $PID
      timestamp = (Get-Date).ToUniversalTime().ToString("o")
      error = $_.Exception.Message
    }
  }
  Start-Sleep -Seconds ([Math]::Max(2, $IntervalSeconds))
}
