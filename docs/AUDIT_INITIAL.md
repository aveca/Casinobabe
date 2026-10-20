# Casinobabe — ÉTAT INITIAL (audit avant modification)

Date : 2026-10-19
Branche : `feat/fishing-oneclick-launcher`
But : socle pour le Casinobabe Control Center (mission "Control Center Windows").

Aucune modification de composant existant n'a été faite au moment de cet audit.

---

## 1. Composants réellement présents

| Composant | Fichier | Statut réel |
|---|---|---|
| Bot pêche | `scripts/fishing_bot.py` (89 KB) | **Existe, actif en code**. Lanceur : `--cli` (boucle) ou GUI tkinter interne. |
| Observateur pêche | `scripts/fishing_observer.py` (22 KB) | **Existe**. `--loop <sec>` pour le mode service. |
| Mémoire / apprentissage | `scripts/fishing_memory.py` (20 KB) | **Existe**, bibliothèque (JSONL + snapshots). |
| Focus broker | `scripts/focus_broker.py` | **Existe**. Bail de focus ~335 ms, arrière-plan. |
| Launcher 1-clic | `scripts/fishing-oneclick.ps1` (23 KB) | **Existe**. Lanceur canonique : sync addon → WoW → bot. |
| Launcher GUI existant | `scripts/casinoBabe_launcher.py` → `scripts/casinobabe_launcher.py` (14,6 KB) | **Existe**, tkinter. BASE À AMÉLIORER. |
| Bridge télémétrie | `bridge/bridge.mjs` (136 lignes) | **Existe**. Sens unique : addon → Supabase. Aucune commande. |
| Superviseur | `scripts/casinobae-supervisor.ps1` | **Existe**. Surveille **un seul** OS process : `casinobae-live-watch.ps1`. Pas les bots. |
| Sentinel live | `scripts/casinobae-live-sentinel.cjs` | **Existe**. Surveille des **fichiers** (SavedVariables WoW), pas un processus. |
| Watch PS | `scripts/casinobae-live-watch.ps1` | **Existe**. Boucle `node casinobae-live-sentinel.cjs --once` toutes les 5 s. |

### `Start-CasinobabeFishing.ps1`
**ABSENT du dépôt.** Recherche exhaustive : aucun fichier de ce nom dans l'arbre
et aucun script définissant la fonction `Start-CasinobabeFishing`. Son rôle est
assumé aujourd'hui par `scripts/fishing-oneclick.ps1`.

---

## 2. Bot casino : ABSENT (constat bloquant pour une partie de la mission)

**Il n'existe aucun processus hors-jeu "bot casino".**

Recherches effectuées (agent d'exploration, lecture seule) :
- `casino_bot.py`, `casino_supervisor`, `casino_controller`, `dealer_bot`,
  `*blackjack*` → **0 résultat**.
- `git log --all --name-only -- '*casino*bot*' '*dealer_bot*' '*blackjack*'` → **vide**.
- `state/casino-*.json`, `state/*dealer*.json` → **aucune configuration**.
- `scripts/fishing-oneclick.ps1` et `scripts/casinobabe_launcher.py` → **aucune
  référence** à un processus casino.
- `bridge/bridge.mjs` → **aucune** commande entrante, uniquement POST de télémétrie.

Ce qui existe côté casino, c'est **uniquement du Lua embarqué dans l'addon WoW**
(`runtime-addon/Casinobabe/Casinobabe.lua`, fonctions `Dealer*` / `Casino*`)
plus des **harnais de test** (`scripts/test_casino_harness.py`,
`scripts/test_casino_lua_harness.py`, `scripts/test_casino_harness.lua`) qui ne
font aucun loop de jeu.

**Conséquence mission :** le Control Center doit afficher le bot casino comme
*absent / non vérifiable*, ne doit RIEN démarrer pour lui, et ne doit pas
proposer de contrôles qui n'ont aucun processus derrière. La section Casino est
implémentée en lecture seule (état de l'addon lu depuis les logs/SavedVariables),
sans commande de démarrage.

---

## 3. `window_guard` : ABSENT

`grep -ri "window_guard|windowGuard"` sur tout le dépôt → **0 résultat**.

Analogue le plus proche existant (à généraliser, pas à réinventer) :
- `scripts/focus_broker.py:98-130` (`_force_foreground`), `:188-206` (bail).
- `scripts/fishing_bot.py` : `user32.GetForegroundWindow()` lignes 348, 870,
  1448, 1532 ; événement `FOCUS_LOST` / `FOCUS_BACK` / `FOCUS_LOST_CYCLE`.
- `scripts/fishing-oneclick.ps1:87-162` : P/Invoke `FindWindowW`,
  `IsIconic`, `ShowWindow(SW_RESTORE)`, `SetForegroundWindow`, `MoveWindow`.

→ Le module `window_guard` doit être **créé** par cette mission.

---

## 4. Mécanismes de contrôle réels (ce qui est réellement actionnable)

### 4.1 Verrou anti-doublon (existe, à réutiliser tel quel)
`scripts/fishing_observer.py:474` `acquire_single_instance(pid_path=None)` et
`:495` `release_single_instance(pid_path=None)`.

- Écrit le PID dans le fichier, refus (retour `False` → `sys.exit(2)`) si un
  PID valide est déjà vivant.
- Liveness vérifiée par `_pid_running()` (ligne 456) via
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `QueryFullProcessImageNameW`,
  et **nom du binaire doit contenir "python"**.
- `fishing_bot.py:1759-1765` réutilise ce mécanisme avec son propre chemin :
  `state/fishing-bot<instance>.pid`.

→ C'est le mécanisme anti-doublon **officiel**. Le Control Center doit
l'utiliser tel quel et ne pas créer de verrou concurrent.

### 4.2 START
`python scripts/fishing_bot.py --cli`
(sortie `2` = déjà une instance vivante).

### 4.3 STOP
- **Pas de commande externe.** Le bot s'arrête sur :
  - `F9` (`emergency_stop_pressed()`) = arrêt **définitif**, vérifié à chaque
    itération de la boucle (lignes 1439-1441, 1782).
  - `Ctrl+C` (`KeyboardInterrupt`, ligne 1785).
- Le Control Center doit donc faire un arrêt **propre et vérifié** : d'abord
  F9 (via le mécanisme existant), puis `CTRL_BREAK`/terminate en dernier
  recours, puis contrôle du PID. **Aucune injection de touche n'est autorisée**
  par la mission → le plan est `terminate()`, jamais de clavier injecté.

### 4.4 PAUSE / REPRISE — **le PAUSE existant est cosmétique**
`casinobabe_launcher.py:128-131` :
```python
def pause(self):
    self.paused = not self.paused
    self._log("PAUSED" if self.paused else "RESUMED")
```
→ **Ce code ne fait rien au processus.** Le bot n'a aucun canal de pause
externe. Les états `PAUSED` du bot (`fishing_bot.py:168, 888-921, 1112-1173,
1507-1549`) sont **internes et automatiques** :
- `FOCUS_LOST` → PAUSED (WoW perd le premier plan),
- `CURSOR_SKIP` / divergence curseur → suspension temporaire
  (`_manual_suspend()`, ligne 1409) : le **processus reste vivant et garde le
  verrou PID**, il n'envoie plus d'entrée et reprend seul après
  `manualSuspendSec` (120 s).

**Conséquence mission :** la mission dit « mettre en pause ou reprendre **lorsque
le mécanisme existant le permet** ». Ici il ne le permet pas au sens commande
externe. Deux options honnêtes, et il faut le documenter :
1. Pause **logique** côté Control Center = arrête de piloter le bot, sans tuer
   le processus (état affiché = "suspendu par l'utilisateur"), le bot restant
   maître de ses propres pauses.
2. Pause **processus** = suspendre le thread Windows (NtSuspendProcess),
   réversible et sans input. Réalisable, mais à considérer comme une
   modification du comportement du bot.

→ Décision : implémenter (1) comme comportement par défaut et documenter que le
verrou PID et le comportement du bot sont préservés ; ne pas introduire de
suspension OS non validée sans demande explicite.

### 4.5 Heartbeat / preuve de progrès
Journal : `reports/fishing-bot-log.jsonl` (44,7 Mo), une ligne JSON par
événement, `{ts, event, state, ...}` (`fishing_bot.py:717-725`).

Événements de **progrès** observés (échantillon des 20 000 dernières lignes) :
`WATCH` (10 358), `CURSOR_SKIP` (3 877), `CAST_NOT_SENT` (1 753),
`WAITING_FOR_BITE` (670), `CAST` (667), `WOW_MINIMIZED` (661),
`BOBBER_LOCKED` (431), `CYCLE_SUMMARY` (97), `CLICK_BOBBER` (14),
`CATCH_DETECTED` (11), `VERIFY_OK` (6).

→ Un processus vivant **sans** `WATCH`/`CAST`/`CYCLE_SUMMARY` récent = processus
**bloqué**, pas actif. C'est exactement la distinction demandée par la mission.

### 4.6 Configuration
`state/fishing-bot-config.json` — 16 clés, toutes numériques ou "castKey".
Chargée par `load_json(CONFIG_FILE, DEFAULT_CONFIG)` avec fusion
`dict(DEFAULT_CONFIG, **config)`. **Aucune clé inconnue ne doit être écrasée**
(exigence mission) → sauvegarde par lecture-modification-écriture du JSON.

### 4.7 Journal persistant
`reports/fishing-bot-log.jsonl` + `reports/launcher.log` + `state/mem-*.jsonl`.
Réf. aussi `reports/live-errors.json`, `reports/live-status.json` (sentinel).

---

## 5. État machine de référence (lua, pour la lecture du bot casino)

`runtime-addon/Casinobabe/Casinobabe.lua` :
- `NEW → CONTACTED → {INVITED, GAME_SELECTED, CANCELLED}`, etc. (ligne 2095).
- `CLOSED` et `CANCELLED` sont terminaux (`{}`).
- `ERROR` seulement atteignable depuis `TRADE_PENDING` / `PAYOUT_PENDING`.
- Codes d'erreur `ERR_*` (ligne 2115).
- Timeouts watchdog : `CONTACTED 300`, `INVITED 180`, `TRADE_PENDING 180`,
  `TRADE_VERIFIED 180`, `STAKE_CONFIRMED 600`, `GROUPED 600`,
  `GAME_SELECTED 600`, `ROLLING 600`, `RESOLVED 600`,
  `PAYOUT_PENDING 300`, `ERROR 60`.
- Core : `DealerCoreGetStatus/Transition/CreateSession/ActiveSession/
  CalculatePayout/ValidateBet/WatchdogTick/CheckIntegrity/CheckTimeouts/...`.

---

## 6. État des processus au moment de l'audit

| Élément | Valeur |
|---|---|
| `state/fishing-bot.pid` | `16752` — **PID mort** (verrou résiduel / périmé) |
| `state/fishing-observer.pid` | `14644` — à vérifier |
| Processus python bot/observer | **aucun** |
| WoW `WowClassic.exe` | **PID 2588**, titre "World of Warcraft" |
| Superviseur (`casinobae-live-watch.ps1`) | non trouvé dans la liste Win32 |
| Tâche planifiée "CasinoBae Supervisor" | à vérifier |

→ Le Control Center doit savoir distinguer **verrou résiduel** (fichier PID
présent, processus mort) de **verrou actif**. C'est un cas de test explicite
de la mission.

---

## 7. Décisions de conception issues de l'audit

1. **Ne pas créer un second superviseur.** Le Control Center ne fait que
   *lire* l'état (fichiers PID, journaux) et *ordonner* les scripts existants.
   Il ne remplace ni `fishing-oneclick.ps1`, ni `casinobabe_launcher.py`,
   ni `casinobae-supervisor.ps1`.
2. **Réutiliser `ProcessManager`** de `casinobabe_launcher.py` comme base, en le
   remplaçant par une version testable sans GUI (`control_core.py`) et en
   corrigeant le PAUSE cosmétique.
3. **Créer `scripts/window_guard.py`** (absent) et en faire une dépendance
   obligatoire de toute action du Control Center susceptible de toucher au
   bureau.
4. **Bot casino : lecture seule, aucun démarrage**, affichage explicite
   « non présent / non vérifiable ».
5. Secrets : `bridge/config.json` contient `sessionToken`. Ne jamais l'afficher
   ni le journaliser → masquage par liste de clés.
