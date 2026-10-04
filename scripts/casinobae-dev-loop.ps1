param(
  [string]$RepoPath = $env:CASINOBAE_REPO,
  [string]$WowAddonPath = $env:CASINOBAE_WOW_ADDON,
  [string]$SavedVariablesPath = $env:CASINOBAE_SV_PATH,
  [switch]$Once
)

$ErrorActionPreference = "Stop"
if (-not $RepoPath) { $RepoPath = "C:\Users\user\Documents\GitHub\Casinobabe" }

if (-not $WowAddonPath) {
  $candidates = @(
    "C:\Program Files (x86)\World of Warcraft\_anniversary_\Interface\AddOns\Casinobabe",
    "C:\Program Files (x86)\World of Warcraft\_classic_\Interface\AddOns\Casinobabe",
    "$env:ProgramFiles\World of Warcraft\_anniversary_\Interface\AddOns\Casinobabe",
    "$env:ProgramFiles\World of Warcraft\_classic_\Interface\AddOns\Casinobabe"
  )
  $WowAddonPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $WowAddonPath) { throw "CASINOBAE_WOW_ADDON not found. Set it explicitly." }
if (-not (Test-Path $RepoPath)) { throw "Repo not found: $RepoPath" }

if (-not $SavedVariablesPath) {
  $wowRoot = Split-Path (Split-Path (Split-Path $WowAddonPath -Parent) -Parent) -Parent
  $accountRoot = Join-Path $wowRoot "WTF\Account"
  $sv = Get-ChildItem -Path $accountRoot -Filter "Casinobabe.lua" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($sv) { $SavedVariablesPath = $sv.FullName }
}

$env:CASINOBAE_REPO = $RepoPath
$env:CASINOBAE_SV_PATH = $SavedVariablesPath
$env:CASINOBAE_WOW_ADDON = $WowAddonPath

function Sync-LiveAddon {
  $source = Join-Path $RepoPath "runtime-addon\Casinobabe"
  if (-not (Test-Path $source)) { throw "Runtime source not found: $source" }
  if (-not (Test-Path $WowAddonPath)) { New-Item -ItemType Directory -Force -Path $WowAddonPath | Out-Null }
  robocopy $source $WowAddonPath /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $LASTEXITCODE" }
  Write-Host "[SYNC] WoW runtime <= repo" -ForegroundColor Cyan
}

function Test-LuaSyntax {
  $luac = Get-Command luac -ErrorAction SilentlyContinue
  if ($luac) {
    & $luac.Source -p (Join-Path $RepoPath "runtime-addon\Casinobabe\Casinobabe.lua")
    if ($LASTEXITCODE -ne 0) { throw "luac syntax check failed." }
    Write-Host "[TEST] luac syntax PASS" -ForegroundColor Green
  } else {
    $lua = Get-Content (Join-Path $RepoPath "runtime-addon\Casinobabe\Casinobabe.lua") -Raw
    if ($lua -match "\[\[INVALID_SENTINEL_PATTERN\]\]") { throw "Static Lua guard failed." }
    Write-Host "[TEST] static Lua guard PASS (luac unavailable)" -ForegroundColor Yellow
  }
}

function Github-Sync {
  Push-Location $RepoPath
  try {
    $branch = (git branch --show-current).Trim()
    if (-not $branch -or $branch -eq "main") { Write-Host "[GITHUB] main: no automatic push/PR" -ForegroundColor Yellow; return }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw "git diff --check failed." }
    git push -u origin $branch
    if ($LASTEXITCODE -ne 0) { throw "git push failed." }
    gh pr view --json number *> $null
    if ($LASTEXITCODE -ne 0) {
      gh pr create --base main --head $branch --fill
      if ($LASTEXITCODE -ne 0) { throw "gh pr create failed." }
    }
    Write-Host "[GITHUB] synchronized + PR ensured" -ForegroundColor Green
  } finally { Pop-Location }
}

Sync-LiveAddon
Test-LuaSyntax

$sentinel = Join-Path $RepoPath "scripts\casinobae-error-sentinel.cjs"
if (-not (Test-Path $sentinel)) { throw "Sentinel not found: $sentinel" }
Write-Host ""
Write-Host "CASINOBAE DEV LOOP ACTIVE" -ForegroundColor Green
Write-Host "Repo: $RepoPath"
Write-Host "WoW : $WowAddonPath"
Write-Host "SV  : $SavedVariablesPath"
Write-Host ""
Write-Host "Erreur WoW -> incident -> OpenCode -> test -> GitHub PR -> runtime sync."
Write-Host "Une correction chargee dans WoW necessite /reload."
Write-Host ""
& node $sentinel