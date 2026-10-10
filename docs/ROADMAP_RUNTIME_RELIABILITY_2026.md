# Casinobabe — Runtime Reliability and Product Integrity Roadmap

## Strategic role
A WoW companion product and event-driven systems test bed, not a claim of unrestricted autonomous gameplay. Trust depends on real runtime state and a clear boundary between demo, addon-observed events and actions requiring the client.

## P0 — Capture failures; prevent false success
- Never show an in-game action as successful without an authoritative addon/client event.
- Preserve explicit ACTION_REQUIRED for protected or human-required actions.
- Capture Lua errors with timestamp, addon version, session context and bounded error details where available.
- Redact sensitive player data; do not upload unrestricted chat or SavedVariables.
- Reproduce production errors in the offline harness before repair.

## P1 — Reliable test-to-live chain
- Maintain representative Lua 5.1/Classic-compatible harness tests.
- Cover nil values, event order, missing APIs, invalid format arguments, stale session state and reload behavior.
- Record candidate commit and addon artifact SHA-256 for each live validation.
- Distinguish local test, CI, merge, deployment, file sync and in-game runtime evidence.
- No production-ready claim until the exact target client/version is tested.

## P2 — Safe local companion bridge
- Keep the Windows bridge separate from website/backend.
- Require explicit user approval for physical input or live-client actions.
- Use authenticated, bounded messages and replay protection.
- Fail closed when bridge/client state is uncertain.
- Do not bypass Blizzard protections or report unsupported operations as complete.

## P3 — Product validation
- Measure repeat usage of lobby, dealer cockpit and session flow.
- Track real event delivery, completed sessions, crashes and support burden.
- Validate willingness to pay before expanding monetization.

## Release gates
1. Reproducible harness evidence.
2. CI tied to candidate commit.
3. Correct packaged artifact and SHA-256 provenance.
4. Explicit target-client validation.
5. No unresolved P0 runtime error.

**Status:** proposal only; does not certify addon stability or live-client behavior.
