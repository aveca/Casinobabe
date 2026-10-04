# CasinoBae Autonomous Self-Healing

Source of truth: `runtime-addon/Casinobabe/`. The WoW LIVE directory is a deployment target only.

Required loop:
CAPTURE ERROR -> ROOT CAUSE -> SEARCH ALL REFERENCES -> FIX -> TEST -> SYNC LIVE -> SHA VERIFY -> COMMIT -> PUSH -> PR -> CONTINUE.

Use BugGrabber/SavedVariables/CasinobabeErrorBus instead of asking for screenshots when text is available. SavedVariables are the persistence boundary; an external watcher sees the error after WoW flushes them on /reload, logout, or client shutdown.

The local LLM is advisory for diagnosis and natural language. It must never decide winners, payouts, gold transfers, trade verification, accounting, or protected-action bypass. These stay deterministic.

Dealer identity is dynamic: dealer mode means the currently logged-in character. Never hard-code a dealer character name.

DEMO is isolated: TEST_PLAYER only; no real chat, whispers, trades, rolls, gold, accounting, or economic statistics.

Never claim stable while a captured runtime error remains unresolved. After each fix, audit the crashed symbol and related callbacks, timers, UI handlers, and event paths.

Required verification: Lua syntax when available, git diff --check, targeted regression checks, nil/global/callback scans, DEMO verification, source/live SHA256 equality.

When no incident exists, continue safe preventive audits and regression work.
