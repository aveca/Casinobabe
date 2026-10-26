# Casinobabe - Capital Demo Checklist (English)

Prerequisites: Casinobabe loaded, `/reload` after any runtime sync. Written for a
capital city (Stormwind / Orgrimmar / Silvermoon / Thunder Bluff). The addon never
moves the character - the player travels to the capital.

Legend: `[ ]` not run, `[x]` run and observed. A line is ticked only after the
player has executed it in game.

## 0. Loading
- [ ] `/reload` after the runtime sync; the addon loads with no Lua error.
- [ ] The panel auto-opens on first login - otherwise `/cb`.
- [ ] Minimap button appears; `/cb minimap` prints the LibDBIcon status.

## 1. Help and command surface
- [ ] `/cb help` prints the grouped list (panel / dealer / auto / info / ui / other).
- [ ] `/cb ?` and `/cb commands` print the same list.
- [ ] `/cb bogus` prints "Unknown command: /cb bogus" then points to `/cb help`.
- [ ] `/cb bogus some arg` echoes the argument it did not understand.
- [ ] `/cb` with no argument opens the panel again, no error.

## 2. Dealer flow (the actual casino)
- [ ] `/cb dealer on` -> "Dealer mode ENABLED", connection state advances to READY.
- [ ] `/cb dealer status` -> dealer, zone, context, prospects, sessions, auto-whisper.
- [ ] `/cb dealer zone` -> RealZone, SubZone, faction, locale, faction capital, CanAdvertise.
- [ ] `/cb dealer log` -> the dealer event log.
- [ ] `/cb dealer off` -> "Dealer mode DISABLED".

## 3. Games and rules (read-only, no gold risk)
- [ ] `/cb who auto` -> client language, faction and the /who query in use.
- [ ] `/cb who <text>` overrides it; `/cb who auto` restores the automatic query.
- [ ] Rules as announced in game: Normal 59+ x2 / 100 x3; High Risk 76+ x3 / 100 x4;
      Blackjack (beat the house); Roulette red/black x2, green x5; Dice over/under x2,
      seven x4; Lucky 7 (ends in 7 = x7).

## 4. Emotes and social interaction
- [ ] `/cb dealer emote` -> usage plus the full list of accepted tokens.
- [ ] `/cb dealer emote NOPE` -> "Unknown emote 'NOPE'." plus the accepted list.
- [ ] `/cb dealer emote WAVE` -> the character waves (real client emote, no chat spam).
- [ ] `/cb dealer emote BOW` / `CHEER` / `GASP` -> the matching native emote.

## 5. Show, announcements and anti-spam
- [ ] `/cb dealer show` -> the capital show runs with clean ASCII English text.
- [ ] `/cb dealer quickad` -> a short one-line advertisement.
- [ ] `/cb dealer showstatus` -> the current show status.
- [ ] `/cb dealer attract` -> exactly ONE channel announcement (typed command = consent).
- [ ] `/cb auto status` -> Auto / Dealer / Attract / Channel / Character.
- [ ] Anti-spam check: repeat `/cb dealer attract`; the addon refuses the same channel
      inside its cooldown instead of flooding chat.
- [ ] `/cbs` without an argument prints the show-engine help and lists available shows;
      an unknown show name is reported instead of failing silently.

## 6. State changes and error handling
- [ ] `/cb fx` -> fire effects on/off, with the state echoed back.
- [ ] `/cb resetstats` -> statistics and history cleared, with confirmation.
- [ ] `/cb intro` -> intro reset (panel auto-opens on the next /reload or login).
- [ ] `/cb reset` -> panel position reset to CENTER.
- [ ] Leave a round unanswered: after the no-response timeout the addon reports that
      the casino did not answer, takes back nothing, and records no result.
- [ ] `/cb dealer emote` with an invalid token: an error message, no Lua error raised.

## 7. Not part of this session
- Fishing (`/cbfish`) and the desktop launcher were **not** modified here.
- The live `CasinobabeFishing.lua` is newer than the repository copy; it was left
  untouched deliberately (see the session report).

## 8. Reproducibility
Every line above is offline-testable through `node tests/run-offline-harness.mjs`
(27 checks) except the parts that need the real client: emotes, show playback and
the actual gold movements. Those need a player action and stay `[ ]` until run.
