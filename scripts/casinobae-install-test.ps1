param(
  [string]$RepoPath=$env:CASINOBAE_REPO,
  [string]$WowRoot=$env:CASINOBAE_WOW_ROOT,
  [switch]$SkipBugGrabber
)
$ErrorActionPreference="Stop"
if(-not $RepoPath){$RepoPath=(Resolve-Path ".").Path}
if(-not(Test-Path $RepoPath)){throw "Repo not found: $RepoPath"}

function Find-WowRoot {
  if($WowRoot -and (Test-Path $WowRoot)){return $WowRoot}
  $c=@(
    "C:\Program Files (x86)\World of Warcraft\_anniversary_",
    "C:\Program Files (x86)\World of Warcraft\_classic_",
    "$env:ProgramFiles\World of Warcraft\_anniversary_",
    "$env:ProgramFiles\World of Warcraft\_classic_"
  )
  return ($c|Where-Object{Test-Path $_}|Select-Object -First 1)
}

$WowRoot=Find-WowRoot
if(-not $WowRoot){throw "WoW root not found. Set CASINOBAE_WOW_ROOT."}
$addonPath=Join-Path $WowRoot "Interface\AddOns\Casinobabe"
if(-not(Test-Path $addonPath)){throw "Casinobabe addon folder not found: $addonPath"}
$accountRoot=Join-Path $WowRoot "WTF\Account"

if(-not $SkipBugGrabber){
  $existing=Get-ChildItem $addonPath -Directory -ErrorAction SilentlyContinue|Where-Object{$_.Name -match "BugGrabber"}|Select-Object -First 1
  if(-not $existing){
    $zip=Join-Path $env:TEMP "!BugGrabber-v12.1.0.zip"
    $url="https://www.curseforge.com/wow/addons/bug-grabber/download/8907587/file"
    Write-Host "[install] downloading BugGrabber v12.1.0..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    if(-not(Test-Path $zip)){throw "BugGrabber download failed"}
    $tmp=Join-Path $env:TEMP "casinobae-buggrabber"
    if(Test-Path $tmp){Remove-Item $tmp -Recurse -Force}
    Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    $candidate=Get-ChildItem $tmp -Recurse -Filter "!BugGrabber.toc" -File|Select-Object -First 1
    if(-not $candidate){throw "Downloaded archive does not contain !BugGrabber.toc"}
    Copy-Item $candidate.Directory.FullName (Join-Path $addonPath $candidate.Directory.Name) -Recurse -Force
    Write-Host "[install] BugGrabber installed: $($candidate.Directory.Name)" -ForegroundColor Green
  }else{
    Write-Host "[install] BugGrabber already installed: $($existing.Name)" -ForegroundColor Green
  }
}

$bgSv=Get-ChildItem $accountRoot -Filter "*.lua" -Recurse -ErrorAction SilentlyContinue|Where-Object{$_.Name -in @("BugGrabber.lua","!BugGrabber.lua")}|Select-Object -First 1
$cbSv=Get-ChildItem $accountRoot -Filter "Casinobabe.lua" -Recurse -ErrorAction SilentlyContinue|Select-Object -First 1

Write-Host ""
Write-Host "CASINOBAE LIVE DEV CHECK" -ForegroundColor Green
Write-Host "WoW root : $WowRoot"
Write-Host "Addon    : $addonPath"
Write-Host "BugGrabber SV: $($bgSv.FullName)"
Write-Host "Casinobabe SV: $($cbSv.FullName)"

if(-not $bgSv){Write-Host "[check] BugGrabber SavedVariables not present yet — normal before first save." -ForegroundColor Yellow}
if(-not $cbSv){Write-Host "[check] Casinobabe SavedVariables not present yet — normal before first save." -ForegroundColor Yellow}

$sentinel=Join-Path $RepoPath "scripts\casinobae-error-sentinel.cjs"
if(-not(Test-Path $sentinel)){throw "Sentinel missing: $sentinel"}
node --check $sentinel
if($LASTEXITCODE -ne 0){throw "Sentinel syntax check failed"}
Write-Host "[check] Node sentinel syntax PASS" -ForegroundColor Green

$runtime=Join-Path $RepoPath "runtime-addon\Casinobabe\Casinobabe.lua"
if(Get-Command luac -ErrorAction SilentlyContinue){
  & luac -p $runtime
  if($LASTEXITCODE -ne 0){throw "Lua syntax check failed"}
  Write-Host "[check] Lua syntax PASS" -ForegroundColor Green
}else{
  Write-Host "[check] luac unavailable — OpenCode should run the repo's available Lua/static tests." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "SETUP COMPLETE" -ForegroundColor Green
Write-Host "Start the live loop with: scripts\casinobae-dev-loop.ps1"
Write-Host "WoW must be reloaded after runtime changes are synchronized." -ForegroundColor Yellow
