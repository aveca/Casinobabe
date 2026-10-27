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
# Garde-fou : le fichier STOP-Autonomy doit stopper toute (re)lance de boucle autonome,
# y compris le redemarrage du watcher par ce superviseur.
$StopMarker = 'C:\factory\control\STOP-Autonomy'
New-Item -ItemType Directory -Force -Path $StateDir,$LogsDir | Out-Null

function Test-StopAutonomy {
  param([string]$Marker)
  if (Test-Path $Marker -PathType Leaf) {
    $script:stopRequested = $true
    return $true
  }
  $script:stopRequested = $false
  return $false
}

function Write-State([hashtable]$state) {
  $state | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $StateFile
}

function Get-WatcherProcess {
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "casinobae-live-watch\.ps1" } |
    Select-Object -First 1
}

function Start-Watcher {
  # Ne jamais (re)demarrer le watcher quand l'arret autonome est demande.
  if ($script:stopRequested) {
    Write-State @{
      schema = 1
      status = "STOPPED_BY_STOP_MARKER"
      pid = $PID
      watcherPid = $null
      watcherRestarted = $false
      timestamp = (Get-Date).ToUniversalTime().ToString("o")
      reason = "STOP-Autonomy marker present at $script:StopMarker"
    }
    return $null
  }
  $args = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$Watcher,"-Mode","Watch")
  Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
}

function Supervisor-Once {
  # Garde-fou : si l'arret autonome est demande, on ne (re)lance rien.
  # L'appel est appele pour son effet de bord ($script:stopRequested) ; sa
  # valeur de retour est rangee pour ne pas polluer le pipeline de sortie.
  $null = Test-StopAutonomy $script:StopMarker
  if ($script:stopRequested) {
    Write-State @{
      schema = 1
      status = "STOPPED_BY_STOP_MARKER"
      pid = $PID
      watcherPid = $null
      watcherRestarted = $false
      timestamp = (Get-Date).ToUniversalTime().ToString("o")
      reason = "STOP-Autonomy marker present at $script:StopMarker"
    }
    return $false
  }

  # `watcherProc` (et non `$watcher`) : PowerShell est insensible a la casse,
  # donc `$watcher` est identique à `$Watcher` (le chemin du script) et son
  # assignation ecraseait le chemin par l'objet processus CIM.
  $watcherProc = Get-WatcherProcess
  $restarted = $false
  if (-not $watcherProc) {
    $proc = Start-Watcher
    $watcherPid = $proc.Id
    $restarted = $true
  } else {
    $watcherPid = [int]$watcherProc.ProcessId
  }

  Write-State @{
    schema = 1
    status = "RUNNING"
    pid = $PID
    watcherPid = $watcherPid
    watcherRestarted = $restarted
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
  }
  return $true
}

function Invoke-Supervisor {
  [CmdletBinding()]
  param(
    [switch]$Once,
    [int]$IntervalSeconds = 10
  )

  if ($Once) {
    Supervisor-Once | ConvertTo-Json
    exit 0
  }

  while (-not $script:stopRequested) {
    $null = Test-StopAutonomy $script:StopMarker
    if ($script:stopRequested) { break }
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

  Write-State @{
    schema = 1
    status = "EXITED"
    pid = $PID
    watcherPid = $null
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    reason = "boucle terminee (STOP-Autonomy marque present ou arret demande)"
  }
}

# Si le script est execute directement (et non dot-source), lancer le superviseur.
# En dot-source, seules les fonctions sont exposees ; aucune boucle n'est demarree,
# ce qui permet les tests unitaires de Test-StopAutonomy et Supervisor-Once.
if ($PSCommandPath -and $PSCommandPath -eq $MyInvocation.MyCommand.Path) {
  Invoke-Supervisor @PSBoundParameters
}
