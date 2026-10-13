# MODE PÊCHE — ASSISTANT (CasinobabeFishing.lua)

Ajouté le 2026-10-13. Client cible : WoW Classic TBC Anniversary (Interface 20506).

## Ce que fait le module

- **Bouton** `MODE PÊCHE — ASSISTANT` (barre autonome, ancrée au panneau Casinobabe si présent, draggable, position sauvegardée).
- **États réels observés** : INACTIF → PRÊT AU LANCER → PÊCHE EN COURS → PRISE À RÉCUPÉRER (LOOT_OPENED) → PRISE ENREGISTRÉE (CHAT_MSG_LOOT) / PAUSE-ERREUR / COMBAT DÉTECTÉ / PERSONNAGE MORT.
- **Comptage de butin** : prises confirmées (CHAT_MSG_LOOT), objets ciblés (« Délices déviants », matching par nom insensible casse/accents + itemId extrait du lien `|Hitem:...|h`), déduplication 1 s, historique horodaté 200 entrées, totaux par zone.
- **Tableau de bord** : état, durée session, prises, cibles, échecs, doublons ignorés, butin de type inconnu, combats, morts, dernier butin, nombre d'erreurs Lua (bus d'erreurs Casinobabe).
- **Export** JSON / CSV affiché dans le chat (les addons ne peuvent pas écrire de fichiers).
- **Spots** : registre par zone (GetZoneText + GetPlayerMapPosition si dispo), statistiques observées, intervalle d'apparition **uniquement si ≥ 2 observations confirmées** et toujours étiqueté « confiance faible ». Aucune heure d'apparition inventée.
- **Reset** avec confirmation (StaticPopup ou `/cbfish reset confirm`).
- **Sons** : 51402 (cible obtenue), 43918 (prise normale) — ids déjà éprouvés côté casino.

## Ce que le module ne fait PAS (volontairement)

- Aucun lancer, clic, déplacement, butin, résurrection automatique. Aucune simulation d'entrée clavier/souris (vérifié par test `no_input_simulation`).
- **Aucune détection de la touche** : l'API des addons 2.5.x n'expose aucun événement de touche. Le module affiche la consigne : observer le flotteur et cliquer manuellement. `PRISE À RÉCUPÉRER` n'apparaît que sur `LOOT_OPENED` (signal fiable).
- Aucune heure d'apparition prétendue exacte.

## Slash

`/cbfish` ou `/cbf` : `on | off | pause | resume | status | reset confirm | export [csv] | spots | targets | target add/remove <nom>`

## Liste de cibles par défaut (⚠ non vérifiée en jeu, ajustable)

`Délice déviant`, `Délices déviants`, `Poisson-dévie`, `Deviate Delight`, `Deviate Fish`.
Le premier butin d'un objet donné résout son itemId via le lien de butin ; les matchs suivants utilisent l'id.

## Tests (hors ligne — ne prouvent PAS le fonctionnement client réel)

```
node tests/run-fishing-harness.mjs   # 44/44 PASS, exit 0
npm test                             # suite complète, exit 0
```

Transitions testées : activation, cast observé, LOOT_OPENED, butin cible FR, dédup, non-cible, langue inconnue, « poisson échappé », combat, mort, réanimation, pause, export JSON/CSV, reset sans puis avec confirmation, persistance, cibles dynamiques, arrêt, absence d'API d'entrée simulée, zéro erreur mock.

## Reste à valider en jeu

- Le sort « Pêche » en frFR sur le client TBC Anniversary (le module accepte aussi « Fishing »).
- Les textes d'erreur FR (« s'est échappé », etc.) du client réel.
- Le nom exact des objets « Délices déviants » sur le serveur réellement joué.
- Apparence/position de la barre à l'écran.
