# Casinobabe Control Center

Application Windows unique pour superviser le bot de peche et evaluer le
composant casino, sans passer par l'addon comme interface de controle.

Audit de l'existant : [`docs/AUDIT_INITIAL.md`](AUDIT_INITIAL.md).

---

## 1. Lancement

```powershell
# Interface complete, sans fenetre console
pythonw scripts\control_center.py

# Equivalent depuis PowerShell
python scripts\control_center.py
```

Raccourci Bureau (cree sans toucher a l'existant) :

```powershell
python scripts\create_control_center_shortcut.py           # creer
python scripts\create_control_center_shortcut.py --force   # recreer
python scripts\create_control_center_shortcut.py --remove  # supprimer
```

Le raccourci cree s'appelle `Casinobabe Control Center.lnk` et pointe vers
`pythonw.exe` + `scripts/control_center.py`. Il ne remplace **pas**
`Casinobabe Launcher.lnk` (ancien launcher, conserve).

### Prerequis

| Element | Etat verifie sur cette machine |
|---|---|
| Python | 3.11.9 |
| tkinter | present (inclus avec Python Windows) |
| psutil | 7.2.2 — requis pour la pause/reprise OS et la detection par ligne de commande |
| pywin32 | present — requis **seulement** pour le raccourci Bureau |
| WoW | facultatif : l'application fonctionne sans WoW |

`psutil` est la seule dependance a ajouter pour disposer de PAUSE/REPRISE.
Sans elle, tout le reste fonctionne et la pause bascule en « retenue
logique » avec un message explicite.

---

## 2. Ce qui est reellement pilote

| Composant | Lanceur existant utilise | Fichier PID | Journal de progression |
|---|---|---|---|
| Bot Peche | `python scripts/fishing_bot.py --cli` | `state/fishing-bot.pid` | `reports/fishing-bot-log.jsonl` |
| Observateur Peche | `python scripts/fishing_observer.py --loop 60` | `state/fishing-observer.pid` | sortie standard |
| Bridge Telemetrie | `node bridge/bridge.mjs` | detection par ligne de commande | sortie standard |
| Superviseur Sentinel | `scripts/casinobae-supervisor.ps1` | detection par ligne de commande | `state/supervisor-state.json` |
| Bot Casino | **absent** | — | — |

Le Control Center **n'invente aucun superviseur**. Il lit l'etat reel et
ordonne les scripts deja presents. Le verrou anti-doublon officiel
(`fishing_observer.acquire_single_instance`) est reutilise tel quel : aucun
verrou concurrent n'est cree.

---

## 3. Les 4 vues

### Tableau de bord

Une carte par composant, avec pastille d'etat, detail explicite, PID,
chemin du binaire, etat du verrou, dernier evenement de progression et sa
faconette, derniere erreur. Plus un panneau « Environnement » : fenetre WoW
(detectee / minimisee), fenetre au premier plan, politique du garde-fou.

 Etats possibles :

| Etat | Signification |
|---|---|
| `ARRETE` | aucun processus (verrou eventuellement residuel signale) |
| `DEMARRAGE` | processus vivant, mais seulement des evenements de demarrage |
| `ACTIF` | processus vivant **et** progression recente dans le journal |
| `EN PAUSE` | suspendu par le Control Center, ou bot en pause interne |
| `BLOQUE` | processus vivant mais **aucune progression** : ce n'est pas « actif » |
| `ERREUR` | le bot lui-meme signale `ERROR` / `FATAL_ERROR` / `DEAD` |
| `ABSENT` | le composant n'existe pas dans le depot |
| `NON VERIFIABLE` | l'etat ne peut pas etre etabli (ex. pas de psutil) |

C'est la distinction exigee par la mission : **un processus vivant n'est pas un
bot qui travaille**. Le seuil est `heartbeat_stale_sec` (45 s pour le bot).

### Controles

`Demarrer` / `Pause` / `Reprendre` / `Arreter` / `Redemarrer` par composant,
avec le motif de blocage affiche a droite quand un bouton est desactive.

- **START** refuse si un processus est deja vivant (anti-doublon). Message
  exact : `refus anti-doublon : un processus est deja vivant (PID …)`.
- **START** nettoie un verrou residuel **uniquement si le PID est mort**, et le
  dit dans le message.
- **PAUSE** suspend les threads du processus (`psutil.suspend()`). Le processus
  reste vivant, **garde son verrou PID** (donc aucun doublon possible) et
  n'envoie plus aucune entree. Aucune touche n'est injectee.
- **REPRISE** fait l'inverse.
- **STOP** : `terminate()`, attente `stopTimeoutSec` (10 s), puis `kill()`.
  Verification du PID et liberation du verrou.
- **REDEMARRER** : arret propre verifie, puis relance. Si l'echec d'arret est
  refuse, aucun redemarrage n'est tente.

Aucune de ces commandes n'utilise le clavier, la souris, ni une API de fenetre.

### Journaux & diagnostics

- Lecture en direct avec rafraichissement automatique.
- Filtres : composant, niveau (`DEBUG`/`INFO`/`WARN`/`ERROR`), periode
  (`5 MIN`/`1 H`/`24 H`), erreurs seulement.
- Les evenements sont colores par niveau via des tags `Treeview`.
- `Exporter le diagnostic` ecrit un JSON complet dans `reports/`.

### Parametres

- **Bot peche** : les 21 parametres de `fishing_bot.DEFAULT_CONFIG`, avec
  bornes, valeur courante, et refus des parametres verrouille
  (`detectScale`, `castKey`).
- **Control Center** : `refreshSec`, `stopTimeoutSec`, `startTimeoutSec`,
  `autoScrollLogs`, `maxLogLines` (fichier `state/control-center.json`).
- **Bot casino** : volontairement vide, avec la raison affichee.

Garantie d'ecriture : lecture-modification-ecriture du JSON existant, donc
**aucune cle inconnue n'est ecrasee**. Une valeur invalide est refusee et
**rien n'est ecrit** du tout.

---

## 4. Securite

- **Aucun secret n'est affiche ni journalise.** Le rapport de diagnostic passe
  par `mask_tree()` qui masque toute valeur dont la cle ressemble a un secret
  (`sessionToken`, `api_key`, `CLIENT_SECRET`, `Authorization`, ...).
  Verifie sur le vrai `bridge/config.json` : le token n'apparait pas dans
  l'export.
- **Aucune API payante.** Tout est local. Aucun modele IA n'est utilise
  (aucune fonction IA n'est necessaire).
- **`window_guard`** (`scripts/window_guard.py`) est cree par cette mission :
  il n'existait pas (audit, section 3). Toute commande passe par
  `safe_call()`, sur trois piliers :
  1. **Refus deterministe avant execution** : une action interdite par la
     politique (vol de focus, minimisation/fermeture de WoW, injection
     clavier/souris, deplacement du curseur) n'est **jamais executee**.
  2. **Preuve statique** : `assert_no_mutating_api()` analyse l'AST de
     `window_guard.py`, `control_core.py` et `control_center.py` et prouve
     qu'aucune API Win32 mutante (`SetCursorPos`, `SetForegroundWindow`,
     `ShowWindow`, `SendInput`, ...) n'y est appelee. Ce test est dans la
     suite pytest.
  3. **Observations** : ce qui bouge sur le bureau pendant une commande
     (souris deplacee, focus WoW, fenetre deplacee) est consigne et affiche,
     mais **ne bloque jamais**. Un echantillonnage avant/apres ne peut pas
     attribuer un changement a l'application : traiter cela comme une
     violation produisait des refus a tort (constate le 2026-10-19 :
     « Demarrer » refuse en boucle tant que l'utilisateur bougeait sa souris).
- **Aucune action en jeu.** Le Control Center n'envoie aucune entree a WoW et
  ne ferme jamais WoW. A noter : sur cette machine, WoW est actuellement
  **minimisee** et le Control Center le laisse dans cet etat.
- **Fermeture propre** : fermer la fenetre arrete le suivi et rend la main aux
  bots ; aucun processus n'est tue, aucun fichier supprime.

---

## 5. Tests

```powershell
python -m pytest tests\test_control_center.py -v   # 48 tests + 1 skip
python scripts\window_guard.py     --selftest       # garde-fou
python scripts\control_core.py     --selftest       # logique + modes complet/reduit
python scripts\control_center.py   --selftest       # fenetre NON mappee
```

Regression des suites existantes :

```powershell
python tests\test_focus_broker.py
node tests\run-offline-harness.mjs
node tests\live_pipeline_synthetic.cjs
node tests\regression-runtime-contract.cjs
```

Les commandes START / PAUSE / STOP / anti-doublon sont exercees sur
`tests/fake_bot_target.py`, un processus **inoffensif** qui reproduit la
structure du bot (verrou PID + journal de progression) sans ouvrir de fenetre,
sans envoyer d'entree et sans regarder WoW. **Le vrai bot de peche n'est jamais
lance par les tests.**

L'auto-test de l'interface utilise `withdraw()` et aucun `mainloop()` : la
fenetre n'est jamais mappee sur l'ecran, donc le test ne vole pas le focus.

---

## 6. Etat reel verifie au moment de la livraison

Lecture seule, aucun processus lance :

```
Bot peche : ARRETE   (verrou residuel PID 16752, processus mort)
Bot casino: ABSENT
Observateur : ARRETE (verrou residuel PID 14644, processus mort)
Bridge : ARRETE      Supervisor : ARRETE
WoW : present (PID 2588), minimisee = True  -> laissee telle quelle
```

---

## 7. Limites declarees

| Limite | Pourquoi | Contournement |
|---|---|---|
| **Pas de bot casino a piloter** | aucun processus n'existe dans le depot (audit, section 2) | rien a faire : le Control Center l'affiche `ABSENT` et ne propose aucune commande |
| **PAUSE = suspension OS** | `fishing_bot.py` n'a aucun canal de pause externe ; ses `PAUSED` sont internes et automatiques | suspension des threads : reversible, conserve le verrou PID, n'envoie rien |
| **Pause de l'observateur / du bridge** | aucun mecanisme de pause dans ces scripts | bouton desactive + raison affichee |
| **STOP ne peut pas faire le `bot.stop()` gracieux** | le bot s'arrete sur F9 ou Ctrl+C, et injecter une touche est interdit | `terminate()` puis `kill()` ; le verrou est nettoye et verifie |
| **Le superviseur existant n'est pas etendu** | `casinobae-supervisor.ps1` ne surveille que `casinobae-live-watch.ps1` | laisse tel quel : le Control Center n'est pas un second superviseur |
| **Etat du casino non lisible depuis l'addon** | le sentinel n'a jamais produit de `state/live-*.json` sur cette machine | affiche `ABSENT`, sans inventer de donnee |

---

## 8. Arborescence

```
scripts/
  window_guard.py              garde-fou desktop (nouveau)
  control_core.py              toute la logique, sans GUI
  control_center.py            la fenetre Windows
  create_control_center_shortcut.py
tests/
  fake_bot_target.py           processus de test inoffensif
  test_control_center.py       48 tests
docs/
  AUDIT_INITIAL.md             etat initial avant modification
  CONTROL_CENTER.md            ce document
```

Le ancien `scripts/casinobabe_launcher.py` est **conserve et inchange** :
`ProcessManager` a servi de reference pour la gestion de processus, mais son
`pause()` etait cosmetique (un simple booleen, aucune action sur le processus).
