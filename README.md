# CasinoBae

CasinoBae is a World of Warcraft companion platform combining a web application and a World of Warcraft addon.

## MVP
Site: https://aveca.github.io/Casinobabe/

Demo: https://aveca.github.io/Casinobabe/demo.html

Page de vente: https://aveca.github.io/Casinobabe/sales.html

Dépôt: https://github.com/aveca/Casinobabe

## MVP livré
- Lobby et découverte par whispers
- Annonces WoW
- Parties /rand basées sur les résultats réellement observés
- Fin de manche et gagnant
- ACTION_REQUIRED pour les actions humaines
- Dashboard web responsive / Dealer Cockpit
- Démo interactive
- Guide d’installation + FAQ
- Sitemap + robots.txt
- Contrat d’événements documenté dans docs/WEB_PLATFORM.md
- Workflow GitHub Pages

## Installation addon
Copier addon/CasinoBae/ dans World of Warcraft/_retail_/Interface/AddOns/CasinoBae/, activer l’addon, puis utiliser /cb lobby.

## Transparence
Le site de démo peut simuler son interface pour présentation. Le jeu WoW, lui, ne doit jamais fabriquer un succès d’action Blizzard. Le bridge navigateur↔addon doit utiliser une intégration réellement autorisée.

Voir docs/MVP.md pour le périmètre exact.

## Live backend (Supabase)

CasinoBae now has a dedicated Supabase backend in project `vgeoprcuzkugaecbpbjg`:

- Dealer Cockpit: `site/dashboard.html`
- Persistent sessions, players and event log
- Supabase Realtime for live event updates
- Edge Functions: `casino-create-session` and `casino-addon-event`
- RLS enabled on all CasinoBae tables
- Direct browser writes are blocked; event ingestion uses a session capability token
- Backend contract: `docs/WEB_SUPABASE.md`

### Important WoW boundary

The website/backend is live, but the WoW addon is still the authoritative source for in-game state and protected actions. Blizzard's addon environment restricts protected operations, so CasinoBae does not pretend that a web request completed a trade, invite, payout or other protected action. A separate local companion/bridge is required for fully automatic WoW-to-web transport; the web contract is ready for it.

