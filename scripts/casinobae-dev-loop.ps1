param([string]$RepoPath=$env:CASINOBAE_REPO,[string]$WowAddonPath=$env:CASINOBAE_WOW_ADDON,[string]$SavedVariablesPath=$env:CASINOBAE_SV_PATH,
  [string]$BugGrabberPath=$env:BUGGRABBER_SV_PATH)
$ErrorActionPreference="Stop"
if(-not $RepoPath){$RepoPath="C:\Users\user\Documents\GitHub\Casinobabe"}
if(-not $WowAddonPath){
  $candidates=@(
    "C:\Program Files (x86)\World of Warcraft\_anniversary_\Interface\AddOns\Casinobabe",
    "C:\Program Files (x86)\World of Warcraft\_classic_\Interface\AddOns\Casinobabe",
    "$env:ProgramFiles\World of Warcraft\_anniversary_\Interface\AddOns\Casinobabe",
    "$env:ProgramFiles\World of Warcraft\_classic_\Interface\AddOns\Casinobabe"
  )
  $WowAddonPath=$candidates|Where-Object{Test-Path $_}|Select-Object -First 1
}
if(-not $WowAddonPath){throw "WOW addon path not found. Set CASINOBAE_WOW_ADDON."}
if(-not(Test-Path $RepoPath)){throw "Repo not found: $RepoPath"}
if(-not $SavedVariablesPath -or -not $BugGrabberPath){
  $wowRoot=Split-Path (Split-Path (Split-Path $WowAddonPath -Parent) -Parent) -Parent
  $accountRoot=Join-Path $wowRoot "WTF\Account"
  if(-not $SavedVariablesPath){$sv=Get-ChildItem $accountRoot -Filter "Casinobabe.lua" -Recurse -ErrorAction SilentlyContinue|Select-Object -First 1;if($sv){$SavedVariablesPath=$sv.FullName}}
  if(-not $BugGrabberPath){$bg=Get-ChildItem $accountRoot -Filter "*.lua" -Recurse -ErrorAction SilentlyContinue|Where-Object{$_.Name -in @("BugGrabber.lua","!BugGrabber.lua")}|Select-Object -First 1;if($bg){$BugGrabberPath=$bg.FullName}}
}
if(-not $SavedVariablesPath -and -not $BugGrabberPath){throw "No Casinobabe or BugGrabber SavedVariables found. Set CASINOBAE_SV_PATH or BUGGRABBER_SV_PATH."}
$env:CASINOBAE_REPO=$RepoPath
$env:CASINOBAE_WOW_ADDON=$WowAddonPath
$env:CASINOBAE_SV_PATH=$SavedVariablesPath
$env:BUGGRABBER_SV_PATH=$BugGrabberPath

$source=Join-Path $RepoPath "runtime-addon\Casinobabe"
robocopy $source $WowAddonPath /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS|Out-Null
if($LASTEXITCODE -ge 8){throw "Initial runtime sync failed: $LASTEXITCODE"}
git -C $RepoPath diff --check
if($LASTEXITCODE -ne 0){throw "git diff --check failed"}

Write-Host ""
Write-Host "CASINOBAE DEV LOOP ACTIVE" -ForegroundColor Green
Write-Host "Repo: $RepoPath"
Write-Host "WoW : $WowAddonPath"
Write-Host "SV  : $SavedVariablesPath"
Write-Host "BG  : $BugGrabberPath"
Write-Host ""
Write-Host "Erreur WoW -> capture -> OpenCode -> test -> commit -> GitHub PR -> WoW sync" -ForegroundColor Cyan
Write-Host "Apres correction : /reload dans WoW pour charger le nouveau Lua." -ForegroundColor Yellow
Write-Host ""
node (Join-Path $RepoPath "scripts\casinobae-error-sentinel.cjs")