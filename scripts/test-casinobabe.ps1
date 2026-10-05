<#
.SYNOPSIS
CasinoBae Test Factory - Local Test Runner
.LICENSE
LUA_RUNTIME_UNAVAILABLE - Using Node.js mocks for WoW API validation

RUN INDIVIDUAL TESTS:
  node C:\Users\user\Documents\GitHub\Casinobabe\tests\unit\run-tests.cjs
  node C:\Users\user\Documents\GitHub\Casinobabe\tests\ui\ui-tests.cjs
  node C:\Users\user\Documents\GitHub\Casinobabe\tests\fuzz\fuzz-tests.cjs

THIS SCRIPT PROVIDES A SUMMARY OF TEST RESULTS.
#>

Write-Host "=== CasinoBae Test Factory - Local Mode ===" -ForegroundColor Cyan
Write-Host "LUA_RUNTIME_UNAVAILABLE: Using Node.js mocks for WoW APIs" -ForegroundColor Yellow
Write-Host ""

# Run unit tests and capture output
Write-Host "--- Running Unit Tests ---" -ForegroundColor Green
$unitOutput = powershell -NoProfile -Command "node C:\Users\user\Documents\GitHub\Casinobabe\tests\unit\run-tests.cjs 2>&1" -STA 2>&1
Write-Host $unitOutput

Write-Host "" 
Write-Host "--- Running UI Tests ---" -ForegroundColor Green
$uiOutput = powershell -NoProfile -Command "node C:\Users\user\Documents\GitHub\Casinobabe\tests\ui\ui-tests.cjs 2>&1" -STA 2>&1
Write-Host $uiOutput

Write-Host "" 
Write-Host "--- Running Fuzz Tests ---" -ForegroundColor Green
$fuzzOutput = powershell -NoProfile -Command "node C:\Users\user\Documents\GitHub\Casinobabe\tests\fuzz\fuzz-tests.cjs 2>&1" -STA 2>&1
Write-Host $fuzzOutput

Write-Host ""
Write-Host "=== Test Factory Complete ===" -ForegroundColor Cyan
Write-Host "See individual test output above for details." -ForegroundColor Yellow
Write-Host "LUA_RUNTIME_UNAVAILABLE: Full 75/75 baseline requires WoW client" -ForegroundColor DarkYellow