# CasinoBae Live Error -> OpenCode -> GitHub

Flow: WoW Lua error -> SavedVariables -> Windows sentinel -> incident JSON -> OpenCode -> dedicated branch/commit -> optional GitHub PR.

WoW addons cannot freely write arbitrary files or open outbound sockets. SavedVariables is the persistence boundary and is flushed by /reload or logout.

PowerShell:

    $env:CASINOBAE_SV_PATH="C:\Program Files (x86)\World of Warcraft\_anniversary_\WTF\Account\YOURACCOUNT\SavedVariables\Casinobabe.lua"
    $env:CASINOBAE_REPO="C:\Users\user\Documents\GitHub\Casinobabe"
    node scripts/casinobae-error-sentinel.cjs

The agent must fix root cause and test. Payment, trade acceptance, payout accounting, security and destructive data changes remain PR-gated.