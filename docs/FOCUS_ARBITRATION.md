# Focus : pourquoi 2 bots de pêche simultanés ne marchent pas, et la solution

## Le fait physique

Windows n'accorde le focus clavier **qu'à une seule fenêtre**. `SendInput` /
`keybd_event` n'atteignent que la fenêtre au premier plan.

`fishing_bot.py` applique déjà cette règle en sécurité :

- `send_key()` et `send_escape()` vérifient `GetForegroundWindow() == hwnd`
  juste **avant** l'envoi, et renvoient `False` sinon ;
- la boucle principale bascule en `PAUSED` / `FOCUS_LOST` quand la fenêtre
  ciblée perd le focus (`INPUT_PAUSED_FOCUS_LOST`), pour ne jamais envoyer une
  touche dans une autre application.

Conséquence directe : avec deux clients WoW ouverts, un seul peut avoir le
focus. Le bot du client non actif resterait en `FOCUS_LOST` indéfiniment. Deux
bots qui « tournent en même temps » ne sont donc pas un problème de
configuration, c'est une impossibilité.

C'est pourquoi `fishing-oneclick.ps1` **refuse** `-BotInstances "1,2"` par
défaut : mieux vaut un refus explicite qu'un bot qui fait semblant de
fonctionner.

## Observation clé pour la conception

La capture d'écran (`PrintWindow` sur le HWND WoW) fonctionne **sans focus**.
Le focus n'est nécessaire qu'aux **moments d'envoi d'entrée** :

| Phase du cycle pêche | Besoin du focus |
|---|---|
| Attente du flotteur (jusqu'à `maxBiteWaitSec`, 32 s) | non (capture suffit) |
| Détection de la touche | non (capture) |
| Envoi de `castKey` (lancer) | **oui**, ~100 ms |
| Clic après morsure | **oui**, ~150 ms |

Le besoin de focus est donc **court et creux**, pas continu. C'est ce qui rend
une arbitration possible.

## Solution proposée : bail de focus par action

Un **focus broker** cross-process, au lieu d'un focus « détenu » en continu :

1. Un mutex nommé global Windows (`Global\CasinobabeFocus`) sérialise les
   entrées entre tous les processus.
2. Juste **avant** chaque `SendInput`, le bot acquiert le bail, appelle
   `SetForegroundWindow(hwnd)`, envoie, puis relâche.
3. Un délai d'acquisition borné (ex. 2 s) évite qu'un client affame l'autre.

Bénéfice : plusieurs bots peuvent entrelacer leurs actions sur plusieurs
fenêtres, chacune avec son propre bail au moment d'envoyer.

## Ce que ça ne change pas

La sécurité existante reste en place : le bail est **acquis puis relâché**, et
`send_key()` continue de vérifier le focus réel avant d'envoyer. Si le bail
n'est pas obtenu, aucune touche n'est envoyée (échec = cycle gelé, jamais une
mauvaise fenêtre).

## Points à trancher avant implémentation

1. **Seuil de confiance focus.** Aujourd'hui, focus perdu = pause. Avec bail, la
   pause devient « attente de bail ». Faut-il garder une pause si le focus est
   perdu *après* l'envoi (par exemple l'utilisateur ouvre le chat) ? → oui,
   recommandé : le bail ne couvre que l'envoi, pas la surveillance d'état.
2. **Priorité.** Un client en cycle de morsure ne doit pas être préempté entre la
   détection et le clic (faux négatif). → bail **non préemptible** pendant le
   couple détection → envoi.
3. **Portée.** Le même broker devra arbitrer la pêche **et** le dealer casino
   quand il existera : une seule autorité de focus, plusieurs consommateurs.

## Statut

**IMPLÉMENTÉ le 2026-10-17** : `scripts/focus_broker.py`, branché sur
`send_key()`, `send_escape()`, `_click()` et le repositionnement du curseur.

Mesures réelles (`scripts/selftest_focus_lease.py`) :

| | valeur |
|---|---|
| maintien du focus | **48 ms** |
| prêt total (repos + restitution) | **335 ms** |
| fenêtre utilisateur rendue ensuite | **oui** |
| deference si l'utilisateur est actif | **oui**, refus immédiat (0 ms) |

### La contrainte découverte : ne JAMAIS minimiser WoW

Une fenêtre minimisée renvoie `GetWindowRect = (-32000,-32000,-31972,-31840)`,
soit **28x160 px**. `PrintWindow` y met une image illisible : le détecteur ne
voit rien, le bot accumule des `WATCH_MISS` sans comprendre pourquoi et
`observedSec` reste à 0.

`capture_window()` refuse maintenant explicitement ce cas
(`FENETRE_REDUITE` / `FENETRE_TROP_PETITE`) et le bot journalise
`WOW_MINIMIZED` une fois puis gèle au lieu de boucler à l'aveugle.

**Recouvrir WoW avec une autre fenêtre est sans effet** (capture vérifiée
fraîche en arrière-plan : 7-9 % de pixels changent entre deux captures, sans
focus). **Le réduire, si.**

### Pourquoi pas d'entrée 100 % en arrière-plan

Deux voies testées :

- `SendInput` / `keybd_event` : ne touchent que la fenêtre au premier plan.
- `WM_INPUT` posté dans la file de messages de la fenêtre (`SendMessage`, qui
  n'exige pas le focus) : le message est bien traité, mais **WoW l'ignore tant
  qu'il n'a pas le focus**.

Donc : entrée sans focus = impossible avec un client DirectX. Seules solutions
réelles : être au premier plan (avec prêt court), ou faire jouer l'addon Lua
lui-même (`CastSpellByName("Fishing")`) pour supprimer tout besoin d'entrée
externe — piste à explorer, la détection de la morsure en Lua reste le point
dur.