---
description: Install and validate BugGrabber plus the CasinoBae live repair loop
agent: build
subagent: false
---

Run the CasinoBae local live-dev setup completely.

1. Inspect the current repository and discover the local WoW installation; do not guess paths.
2. Run `powershell -ExecutionPolicy Bypass -File .\\scripts\\casinobae-install-test.ps1`.
3. Install !BugGrabber if missing. The target is the current BugGrabber release compatible with Classic TBC 2.5.5/2.5.6.
4. Verify !BugGrabber and the Casinobabe SavedVariables locations.
5. Run Node syntax validation, Lua syntax/static checks, and every available addon test already in the repository.
6. Create a temporary synthetic BugGrabber-style incident containing a Casinobabe stack/locals and verify the sentinel can turn it into an incident for Nemotron without any screenshot.
7. Do not change gameplay/accounting/payment logic during setup.
8. If something fails, diagnose and fix the setup itself, rerun the tests, and continue until green.
9. Finish by printing the exact command to start the 24/7 local loop.

Important: the point of this command is to remove screenshot/copy-paste debugging. Future runtime errors should come from !BugGrabber text into the sentinel/OpenCode pipeline.
