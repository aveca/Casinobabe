# Rapport de mission P0 — Stopper les consoles visibles et corriger le garde-fou d'autonomie

**Date** : 2026-10-27  
**Auteur** : agent OpenCode (OpenCode Desktop, `opencode-cli.exe serve --service`, pid 19448)  
**Statut** : **BLOQUÉ en attente de validation** (PR #46 ouverte, mergeabilité/CI non confirmées)  
**PR dédiée (cible)** : https://github.com/aveca/Casinobabe/pull/46  
**PR #45 (large, conservée)** : https://github.com/aveca/Casinobabe/pull/45 (mergeable: false, portée trop large — 45 commits, ~30 fichiers non liés)

---

## 1. État initial et preuves datées

### Contexte
Le poste subit des apparitions intempestives de fenêtres de console (`cmd.exe` hébergé en onglet **Windows Terminal**) pendant WoW Classic, générant des flashs et des risques de vol de focus en plein écran.

### Preuves issues des logs `C:\factory\logs\P0-console-watch\` (enregistreur de processus actif)

Fichier `console-2026-10-27.jsonl` (jour de l'incident) :
- Événements de création de processus console : **4 220**
- Dont créés **pendant que `WowClassic.exe` tournait** : **1 750**
- Dont 1 750 avec `opencode-cli.exe` dans l'ascendance : **611**
- Répartition par type : 2 683 `conhost.exe`, 1 174 `powershell.exe`, 273 `cmd.exe`, 33 `WindowsTerminal.exe`, 31 `OpenConsole.exe`, 24 `wscript.exe`, 2 `cscript.exe`

Exemple d'événement console pendant le jeu (pid de jeu 28684) :
```
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "python3 -c \"import urllib.request ... https://sargasses-martinique.com/ ...\""
ancestry : powershell.exe < opencode-cli.exe(19448) < <gone>(4084)
```

### Processus actifs au début de la mission (vérifiés via `Get-CimInstance Win32_Process`)

| PID | Nom | Ligne de commande | Action |
|-----|-----|-------------------|--------|
| 34740 | powershell.exe | `casinobae-supervisor.ps1` | **Arrêté** |
| 30500 | powershell.exe | `casinobae-live-watch.ps1 -Mode Watch` | **Arrêté** |
| 25204 | powershell.exe | `watch-consoles2.ps1` | **Arrêté** |
| 5144 | powershell.exe | `no-console-flash.ps1` (en erreur depuis le 25/10) | **Arrêté** |
| 19448 | opencode-cli.exe | `serve --service` | Conservé |
| WowClassic.exe | — | — | Conservé (pas touché) |

Le garde-fou anti-flash `no-console-flash.ps1` était **cassé depuis le 25/10** (erreur à chaque itération : le paramètre `$pid` collisionne avec la variable système PowerShell en lecture seule `$PID`). Il ne protégeait plus rien.

---

## 2. Cause démontrée du non-respect de `STOP-Autonomie`

Le fichier `C:\factory\control\STOP-Autonomie` existe depuis le 2026-10-23T05:16:34.

Deux mécanismes le violaient :

1. **`casinobae-supervisor.ps1`** (dépôt Casinobabe) : relançait `casinobae-live-watch.ps1` toutes les 10 s dans une boucle `while ($true)` **sans jamais consulter** le marqueur `STOP-Autonomie`. Le fichier de contrôle était ignoré par ce superviseur.

2. **`github-bridge.cjs`** (`C:\factory\control\`) : `autonomyTick()` vérifiait déjà le garde-fou, mais `pollOnce()` (traitement des missions en file GitHub, déclenché toutes les 30 s par la tâche planifiée `Factory-Control-Runner`) ne le vérifiait pas.

### Correction de la garde
Le superviseur Casinobabe vérifie désormais le marqueur au démarrage et à chaque itération. Si présent :
- aucun watcher n'est (re)lancé,
- le superviseur s'arrête proprement (état `STOPPED_BY_STOP_MARKER`),
- une collision de variable `$watcher`/`$Watcher` (PowerShell insensible à la casse) qui écrasait le chemin du watcher est corrigée,
- le code d'exécution est isolé derrière une garde de dot-source pour permettre les tests unitaires sans lancer la boucle.

`github-bridge.cjs` : `pollOnce()` retourne immédiatement (aucun mission exécutée) quand `STOP-Autonomie` est présent.

---

## 3. Fichiers modifiés et raisons

| Fichier | Dépôt / emplacement | Modification | PR |
|---------|---------------------|--------------|-----|
| `scripts/casinobae-supervisor.ps1` | `aveca/Casinobabe` | Garde-fou `STOP-Autonomie` + fix collision variable + isolation code d'exécution | PR #46 |
| `scripts/ops/test-stop-autonomy.ps1` | `aveca/Casinobabe` | Tests de régression (7/7) | PR #46 |
| `control/github-bridge.cjs` | `C:\factory\control` (pas de dépôt git) | `pollOnce()` vérifie `STOP-Autonomie` | **non versionné** |
| `run_test_with_preview.js` | `C:\factory` (pas de dépôt git) | `spawn(node, vite-local, {windowsHide:true, stdio:pipe})` au lieu de `spawn('npx',...,{shell:true,stdio:inherit})` | **non versionné** |
| `local-tools/no-console-flash.ps1` | `C:\Users\user\local-tools` (pas de dépôt git) | Renommage `$pid` → `$processId` (collision avec `$PID` système) qui cassait le garde-fou | **non versionné** |

Les 3 fichiers situés sous `C:\factory` et `C:\Users\user\local-tools` n'appartiennent à aucun dépôt Git local (aucune racine `.git` trouvée) : ils ne peuvent pas faire l'objet d'une PR. Ils sont documentés ici comme corrections locales.

---

## 4. Tests exécutés et résultats

### Tests PowerShell (réels, exécutés)

`scripts/ops/test-stop-autonomy.ps1` — **7/7 réussis** :
1. Dot-source du superviseur ne lance ni boucle ni watcher ✓
2. Marqueur factice présent → `Test-StopAutonomy` retourne `$true` ✓
3. `script:stopRequested` vaut `$true` ✓
4. Marqueur factice absent → `Test-StopAutonomy` retourne `$false` ✓
5. `script:stopRequested` vaut `$false` ✓
6. Vrai marqueur `C:\factory\control\STOP-Autonomie` présent → `Supervisor-Once` retourne `$false` ✓
7. Aucun watcher `casinobae-live-watch.ps1` lancé par `Supervisor-Once` avec marqueur présent ✓

### Vérifications processus
- PID 30500, 34740, 25204 : **arrêtés et vérifiés disparus** (pas de redémarrage) ✓
- WowClassic et opencode-cli (19448) : **conservés** (pas touchés) ✓

### Tests Node.js
- `node --check run_test_with_preview.js` : **OK** ✓
- `node --check github-bridge.cjs` : **OK** ✓
- Test fonctionnel du lancement Vite (équivalent du script corrigé : `node vite.js preview --port 4173 --host` avec fenêtre cachée) : le serveur démarre, répond sur le port 4173, s'arrête proprement à la fin du script ; aucun processus `vite` orphelin créé par le test (2 processus `vite` pré-existants identifiés, sans lien avec ce test) ✓

### Syntax PowerShell
- `casinobae-supervisor.ps1` : OK ✓
- `test-stop-autonomy.ps1` : OK (prouvé par l'exécution réussie) ✓
- `no-console-flash.ps1` : OK ✓

### Non vérifié (limites)
- ❌ **Zéro flash de console** : ne peut pas être affirmé sans observation probante. Le watcher de focus (`watch-consoles2.ps1`) est arrêté ; `no-console-flash.ps1` est arrêté (cassé). Aucune mesure avant/après des fenêtres visibles n'a pu être effectuée.
- ❌ **Test pendant une session de jeu** : non effectué (interdiction d'interrompre une partie en cours).
- ❌ **PR #46 mergeable / CI verte** : non confirmé (revue automatique Codex échouée sur limite d'usage ; pas de preuve que la CI passe).
- ❌ **Tâche planifiée `FactoryV3-Supervisor`** (`f3.py supervise --interval 5`, état `Running`) : non vérifiée pour le respect de `STOP-Autonomie`. Risque résiduel.
- ❌ Les 3 corrections locales (hors dépôt git) ne passent par aucun test CI.

---

## 5. Mesures avant/après

| Indicateur | Avant (27/10, logs) | Après (missions appliquées) |
|------------|---------------------|-----------------------------|
| Superviseur casinobae actif | Oui (pid 34740) | Non (arrêté + garde-fou) |
| Watcher casinobae actif | Oui (pid 30500) | Non (arrêté, pas de redémarrage) |
| watch-consoles2 actif | Oui (pid 25204) | Non (arrêté) |
| Garde-fou `STOP-Autonomie` respecté par le superviseur | Non | Oui |
| Garde-fou `STOP-Autonomie` respecté par github-bridge `pollOnce()` | Non | Oui (modification locale) |
| Garde-fou anti-flash `no-console-flash.ps1` opérationnel | Non (cassé depuis 25/10) | Corrigé, mais instance arrêtée |

---

## 6. Redémarrages automatiques

- **Superviseur Casinobabe** : neutralisé (garde-fou respecté, boucle sort quand le marqueur est présent).
- **Watcher Casinobabe** : arrêté, ne sera pas relancé par le superviseur corrigé.
- **watch-consoles2.ps1** : arrêté.
- **github-bridge** : `pollOnce()` ne s'exécute plus quand `STOP-Autonomie` est présent. Cependant, la tâche planifiée `Factory-Control-Runner` (toutes les 30 s) peut relancer `github-bridge.cjs --loop` ; avec le marqueur présent, `pollOnce()` est une no-op et `autonomyTick()` vérifie déjà le garde-fou.
- **`FactoryV3-Supervisor`** (`f3.py`, tâche planifiée `Running`) : **non vérifié** — risque résiduel possible.
- Les processus `opencode-cli` (service OpenCode) et WowClassic sont conservés et peuvent continuer à lancer des consoles individuelles via leurs outils shell normaux (ceux-ci ne sont pas ciblés par cette mission).

---

## 7. Commit, PR et rapport GitHub

- Branche ciblée : `agent/ops/P0-console-guard-cible` (dépôt `aveca/Casinobabe`, depuis `main`)
- Commit : `d659ae3` (2 fichiers : `scripts/casinobae-supervisor.ps1`, `scripts/ops/test-stop-autonomy.ps1`)
- PR dédiée : https://github.com/aveca/Casinobabe/pull/46
- PR #45 (large, conservée, non fusionnée) : https://github.com/aveca/Casinobabe/pull/45

---

## 8. Bilan final

| Point | Statut |
|-------|--------|
| 1. Processus effectivement arrêtés | Pid 34740 (superviseur), 30500 (watcher), 25204 (watch-consoles2), 5144 (no-console-flash cassé) — **vérifiés disparus** |
| 2. Redémarrages automatiques neutralisés | Superviseur casinobabe (gardé-fou ajouté) ; github-bridge `pollOnce()` (garde-fou ajouté). `FactoryV3-Supervisor` **non vérifié** |
| 3. Correctifs réellement appliqués | casinobae-supervisor.ps1 (PR #46), test-stop-autonomy.ps1 (PR #46), github-bridge.cjs (local), run_test_with_preview.js (local), no-console-flash.ps1 (local) |
| 4. Tests réussis / échoués | 7/7 tests de régression réussis ; syntaxe PowerShell OK ; `node --check` OK ; test Vite preview fonctionnel. Aucun échec. |
| 5. Preuves de réduction des fenêtres visibles | **Non vérifié** (pas de mesure avant/après, watchers arrêtés) |
| 6. Commit, PR, rapport GitHub | Commit `d659ae3` ; PR #46 ouverte ; ce rapport dans `reports/MISSION-P0-CONSOLE-GUARD.md` |
| 7. Blocages restants / prochaine action | - PR #46 mergeabilité/CI non confirmées<br>- 3 corrections locales non versionnées (pas de dépôt git)<br>- `FactoryV3-Supervisor` non vérifié<br>→ Prochaine action : vérifier mergeabilité de PR #46, versionner les 3 corrections locales dans un dépôt adapté, vérifier `FactoryV3-Supervisor` |

**Verdict** : Le diagnostic est établi et les correctifs principaux sont appliqués et testés. La clôture opérationnelle est **bloquée** en attendant la validation de PR #46 (mergeabilité + CI) et la vérification des risques résiduels listés ci-dessus.
