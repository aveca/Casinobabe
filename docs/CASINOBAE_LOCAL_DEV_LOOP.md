# CasinoBae — Local WoW Addon Dev Loop

## One-command workflow

Run from PowerShell in the repository:

`powershell -ExecutionPolicy Bypass -File .\scripts\casinobae-dev-loop.ps1`

Then keep playing WoW. The local loop watches Casinobabe SavedVariables for new captured Lua errors, creates an incident, launches OpenCode, validates the fix, synchronizes `runtime-addon/Casinobabe` into the live WoW addon folder, pushes the agent branch, and ensures a GitHub PR exists.

## Required local tools

- Node.js
- OpenCode CLI (`opencode` on PATH)
- Git + authenticated `origin`
- GitHub CLI (`gh`) authenticated for PR creation

## Paths

The PowerShell launcher tries `_anniversary_` and `_classic_` automatically. Explicit overrides are supported with `CASINOBAE_REPO`, `CASINOBAE_WOW_ADDON`, and `CASINOBAE_SV_PATH`.

## WoW limitation

The WoW addon sandbox cannot freely write arbitrary files or open an outbound socket. Errors are therefore captured into `CasinobabeErrorBus` SavedVariables. WoW flushes SavedVariables on `/reload` or logout, so the loop removes screenshot/copy-paste work but a reload is still required to flush a newly captured runtime error.

The corrected files are synced automatically to the live addon directory after the agent finishes. WoW loads the new Lua on the next `/reload`.

## Safety

The agent fixes root causes and runs validation. Payment, payout, trade acceptance, security, and destructive-data changes remain PR-gated and are never auto-merged by this loop.
