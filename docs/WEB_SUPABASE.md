# CasinoBae — Supabase live bridge

Project: `vgeoprcuzkugaecbpbjg`

The browser dashboard is connected to Supabase Realtime. The WoW addon remains authoritative for game state and protected actions.

## Security boundary

- The publishable key is safe for the browser because database RLS is enabled.
- Direct client writes to the CasinoBae tables are not allowed.
- `casino-create-session` creates an isolated session and returns a one-time capability token.
- `casino-addon-event` accepts events only when the session token hashes to a valid session.
- The service-role key exists only inside Supabase Edge Functions.
- Never put the service-role/secret key in the website or addon.

## Event contract

A bridge calls:

```js
window.CasinoBae.receiveAddonEvent({
  name: "PLAYER_JOINED",
  state: "LOBBY_OPEN",
  actor: "Dealer",
  payload: { name: "Player-Realm" },
  time: "14:32:10"
});
```

Supported semantic events include:

- `STATE`
- `PLAYER_JOINED`
- `PLAYER_LEFT`
- `ROLL`
- `RESULT`
- `ACTION_REQUIRED`
- `ACTION_CONFIRMED`
- `SESSION_CLOSED`

The dashboard persists those events and receives subsequent events through Supabase Realtime.

## WoW boundary

The WoW addon cannot be treated as an unrestricted web client. Blizzard's addon security model restricts protected actions and addon execution. CasinoBae therefore never claims a trade, invite, payout, or other protected action succeeded merely because the website requested it.

When a protected action needs player input, the addon enters `ACTION_REQUIRED` and resumes only after an authoritative in-game event confirms the action.

A separate local bridge/companion process is required for fully automatic WoW-to-web transport; the website contract is ready for that bridge.
