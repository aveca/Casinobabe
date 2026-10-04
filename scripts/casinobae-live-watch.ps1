param(
  [string]$Repo = "",
  [string]$WowRoot = ""
)

$ErrorActionPreference = "Stop"

if (-not $Repo) {
  $Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

function Find-WowRoot {
  param([string]$Preferred)
  $candidates = @()
  if ($Preferred) { $candidates += $Preferred }
  $candidates += @(
    "C:\Program Files (x86)\World of Warcraft\_anniversary_",
    "C:\Program Files (x86)\World of Warcraft\_classic_",
    "$env:ProgramFiles\World of Warcraft\_anniversary_",
    "$env:ProgramFiles\World of Warcraft\_classic_"
  )
  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-Path (Join-Path $candidate "Interface\AddOns\Casinobabe\Casinobabe.lua")) {
      return $candidate
    }
  }
  throw "WoW root not found. Pass -WowRoot."
}

$root = Find-WowRoot $WowRoot
$addon = Join-Path $root "Interface\AddOns\Casinobabe"
$wtf = Join-Path $root "WTF\Account"

$env:CASINOBAE_REPO = (Resolve-Path $Repo).Path
$env:CASINOBAE_WOW_ROOT = $root
$env:CASINOBAE_WOW_ADDON = $addon
$env:CASINOBAE_POLL_MS = "1200"

$sv = Get-ChildItem -Path $wtf -Filter "Casinobabe.lua" -Recurse -File -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
$bg = Get-ChildItem -Path $wtf -Filter "!BugGrabber.lua" -Recurse -File -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName

if ($sv.Count -gt 0) { $env:CASINOBAE_SV_PATH = $sv[0] }
if ($bg.Count -gt 0) { $env:BUGGRABBER_SV_PATH = $bg[0] }

Write-Host "============================================"
Write-Host "CasinoBae 24/7 LIVE SELF-HEALING"
Write-Host "============================================"
Write-Host ("Repo : " + $env:CASINOBAE_REPO)
Write-Host ("WoW  : " + $root)
Write-Host ("Addon: " + $addon)
Write-Host ("SV   : " + $env:CASINOBAE_SV_PATH)
Write-Host ("BG   : " + $env:BUGGRABBER_SV_PATH)
Write-Host ""
Write-Host "Capture -> local LLM diagnosis -> OpenCode -> tests -> LIVE sync -> SHA verify"
Write-Host "WoW still requires /reload or restart to execute newly copied Lua."
Write-Host ""

node (Join-Path $Repo "scripts\casinobae-live-sentinel.cjs")
