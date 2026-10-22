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
  # FIX 2026-10-20 : `& node $Sentinel --once` ecrivait la SORTIE STANDARD du
  # sentinel dans le pipeline de la fonction. La fonction renvoyait donc un
  # TABLEAU (la sortie + le code de retour) : `$code` valait `System.Object[]`
  # et la comparaison `$code -ne 0` etait toujours vraie -> le watcher
  # journalisait un faux "WATCHER_SENTINEL_EXIT code=System.Object[]" toutes
  # les 5 s (1,9 Mo de logs/live-errors.log) alors que le sentinel, lui,
  # tournait correctement. On capture la sortie a part et on ne renvoie QUE
  # le code de sortie.
  $output = & node $Sentinel --once 2>&1
  $code = $LASTEXITCODE
  if ($code -ne 0 -and $output) {
    $detail = ($output | Out-String).Trim()
    if ($detail.Length -gt 400) { $detail = $detail.Substring(0, 400) }
    Write-Host ("[watcher] sentinel code={0} {1}" -f $code, $detail)
  }
  return $code
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
