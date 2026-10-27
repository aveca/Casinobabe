<#
.SYNOPSIS
    Tests de regression pour le garde-fou STOP-Autonomie du superviseur Casinobabe.
    Verifie que le marqueur C:\factory\control\STOP-Autonomie est respecte :
    - marqueur present  -> Test-StopAutonomy retourne $true, Supervisor-Once ne lance
      aucun watcher (retourne $false).
    - marqueur absent   -> Test-StopAutonomy retourne $false.

.DESCRIPTION
    Ce script dot-source casinobae-supervisor.ps1 sans executer sa boucle (grâce à la
    garde de dot-source), puis appelle Test-StopAutonomy et Supervisor-Once de maniere
    isolee. Aucun processus watcher n'est lance pendant le test.

.NOTES
    Le fichier C:\factory\control\STOP-Autonomie est conserve tel quel ; seul un
    marqueur factice temporaire est cree puis supprime pour tester la branche
    "marqueur absent".
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Supervisor = Join-Path (Split-Path -Parent $PSScriptRoot) "casinobae-supervisor.ps1"
$RealMarker = 'C:\factory\control\STOP-Autonomy'
$FakeMarker = Join-Path $env:TEMP 'P0-test-stop-autonomy-marker'

$failures = 0
$passes = 0

function Expect-True($label, $value) {
  if ($value -eq $true) { $script:passes++; Write-Host ("PASS: {0}" -f $label) }
  else { $script:failures++; Write-Host ("FAIL: {0} (attendu $true, obtenu $false)" -f $label) }
}
function Expect-False($label, $value) {
  if ($value -eq $false) { $script:passes++; Write-Host ("PASS: {0}" -f $label) }
  else { $script:failures++; Write-Host ("FAIL: {0} (attendu $false, obtenu $true)" -f $label) }
}

# --- 1. Dot-source du superviseur (la boucle ne doit PAS demarrer) ---
Write-Host "=== Dot-source du superviseur (pas de boucle, pas de watcher) ==="
. $Supervisor
$watcherBefore = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match 'casinobae-live-watch\.ps1' } |
  Select-Object -First 1
if (-not $watcherBefore) {
  $script:passes++; Write-Host "PASS: aucun watcher casinobae demarre par le dot-source"
} else {
  $script:failures++; Write-Host "FAIL: un watcher casinobae etait demarre (pid $($watcherBefore.ProcessId))"
}

# --- 2. Marqueur factice PRESENT -> Test-StopAutonomy doit retourner $true ---
Write-Host ""
Write-Host "=== Marqueur factice PRESENT ==="
New-Item -ItemType File -Path $FakeMarker -Force | Out-Null
$result = Test-StopAutonomy $FakeMarker
Expect-True "Test-StopAutonomy retourne $true quand le marqueur existe" $result
Expect-True "Variable script:stopRequested vaut $true" $script:stopRequested

# --- 3. Marqueur factice ABSENT -> Test-StopAutonomy doit retourner $false ---
Write-Host ""
Write-Host "=== Marqueur factice ABSENT ==="
Remove-Item -Path $FakeMarker -Force -ErrorAction SilentlyContinue
$result = Test-StopAutonomy $FakeMarker
Expect-False "Test-StopAutonomy retourne $false quand le marqueur n'existe pas" $result
Expect-False "Variable script:stopRequested vaut $false" $script:stopRequested

# --- 4. Vrai marqueur PRESENT -> Supervisor-Once ne lance aucun watcher ---
Write-Host ""
Write-Host "=== Vrai marqueur STOP-Autonomie PRESENT ==="
$realMarkerExists = Test-Path $RealMarker -PathType Leaf
if ($realMarkerExists) {
  $result = Supervisor-Once
  Expect-False "Supervisor-Once retourne $false (marqueur present)" $result
  $watcherAfter = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'casinobae-live-watch\.ps1' } |
    Select-Object -First 1
  if (-not $watcherAfter) {
    $script:passes++; Write-Host "PASS: aucun watcher casinobae lance par Supervisor-Once avec marqueur present"
  } else {
    $script:failures++; Write-Host ("FAIL: watcher casinobae lance (pid {0})" -f $watcherAfter.ProcessId)
  }
} else {
  $script:failures++; Write-Host "FAIL: le vrai marqueur C:\factory\control\STOP-Autonomie est absent (impossible de verifier)"
}

# --- Nettoyage du marqueur factice si encore present ---
if (Test-Path $FakeMarker -PathType Leaf) { Remove-Item -Path $FakeMarker -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host ("=== RESULTAT : {0} reussi, {1} echec ===" -f $script:passes, $script:failures)
if ($script:failures -gt 0) { exit 1 }
