# CasinoBae Autonomous Self-Healing

WoW error -> BugGrabber/CasinobabeErrorBus -> SavedVariables flush -> Windows sentinel -> local Ollama/Nemotron diagnosis -> OpenCode repair -> tests -> source/LIVE sync -> SHA verification -> Git commit/push/PR.

An addon cannot arbitrarily write files or open a raw socket. SavedVariables are therefore the bridge to the external watcher. An external process can observe the incident only after WoW flushes SavedVariables.

The local LLM is a funnel for language/diagnosis, not for winner, payout, gold, trade verification, accounting, or protected actions.

Start on Windows with:
`powershell -ExecutionPolicy Bypass -File .\scripts\casinobae-live-watch.ps1`

The watcher backs up the LIVE addon before copying validated source into it, then compares source/live SHA256.

A new Lua version still needs `/reload` or a restart to execute inside WoW.
