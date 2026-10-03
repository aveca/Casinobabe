# CasinoBae Local Bridge

Transport: WoW addon -> SavedVariables -> local Node watcher -> Supabase -> Dealer Cockpit.

WoW addons cannot freely open arbitrary HTTP sockets, so this bridge does not bypass Blizzard's addon security model. SavedVariables are persisted by WoW on logout/reload; therefore this transport is not zero-latency.

Setup:
1. Create a Dealer Cockpit session.
2. Copy its bridge token.
3. Copy config.example.json to config.json.
4. Set sessionToken and the actual WTF/.../SavedVariables/CasinoBae.lua path.
5. Run node bridge.mjs.

Never commit config.json. The token is a session capability secret.
