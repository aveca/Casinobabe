# CasinoBae Web Platform

## Current architecture

GitHub Pages serves the static web application under /site.

- `site/index.html`: public product site
- `site/demo.html`: browser-only interactive demo; simulated data is explicitly demo data
- `site/dashboard.html`: dealer cockpit and event monitor
- `site/sales.html`: installation/download page
- `site/guide.html`: installation and safety guide
- `site/faq.html`: FAQ
- `site/app.js`: browser event renderer
- `site/styles.css`: shared visual system

## Event bridge contract

The browser exposes:

```js
window.CasinoBae.receiveAddonEvent({
  name: "PLAYER_JOINED",
  state: "LOBBY_OPEN",
  payload: { name: "PlayerName" },
  time: "20:41:08"
});
```

The browser MUST treat the addon as the event source. It must not manufacture authoritative WoW events.

## Security boundary

A future backend must treat addon/browser input as untrusted. In particular, a client message such as `TRADE_SUCCESS`, `PAYOUT_SUCCESS` or `WIN` must not become authoritative merely because the client sent that string.

Critical state transitions require validation using only evidence available from the real WoW integration.

## Future backend

The current repository is GitHub Pages/static-first. A real authenticated API/database can be added later without changing the public UI contract. Until then, the website must never display a fictional "connected" production backend.

## Demo isolation

Browser demos may simulate UI state for product presentation. Demo state must remain visibly labelled DEMO ONLY and must never call real trade/payout paths.
