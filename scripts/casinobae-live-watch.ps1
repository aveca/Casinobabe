[CmdletBinding()]
param(
  [ValidateSet("Watch","Once")]
  [string]$Mode = "Watch",
  [int]$IntervalSeconds = 5
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Sentinel = Join-Path $Repo "scripts\casinobae-live-sentinel.cjs"

if (-not (Test-Path $Sentinel)) {
  Write-Error "Sentinel not found: $Sentinel"
  exit 2
}

function Invoke-Sentinel {
  & node $Sentinel --once
  return $LASTEXITCODE
}

if ($Mode -eq "Once") {
  exit (Invoke-Sentinel)
}

while ($true) {
  try {
    $code = Invoke-Sentinel
    if ($code -ne 0) {
      $log = Join-Path $Repo "logs\live-errors.log"
      Add-Content -Path $log -Value ("[{0}] WATCHER_SENTINEL_EXIT code={1}" -f (Get-Date).ToUniversalTime().ToString("o"), $code)
    }
  } catch {
    $log = Join-Path $Repo "logs\live-errors.log"
    Add-Content -Path $log -Value ("[{0}] WATCHER_ERROR {1}" -f (Get-Date).ToUniversalTime().ToString("o"), $_.Exception.Message)
  }
  Start-Sleep -Seconds ([Math]::Max(1, $IntervalSeconds))
}
