# Rapport de validation — Casinobabe Control Center + Moteur Casino

Date : 2026-10-19  
Branche : `feat/control-center-windows`  
Commit : `08fd11f`  
PR : #41 (https://github.com/aveca/Casinobabe/pull/41) — MERGEABLE / CLEAN  
CI : GitGuardian pass + factory pass (26s)

---

## 1. État réel de la PR #41

| Champ | Valeur |
|-------|--------|
| État | OPEN |
| Mergeable | MERGEABLE |
| MergeState | CLEAN |
| Commits | 4 |
| Fichiers | 10 |
| SHA vérifié | `08fd11f425953e121c63f548fa96c9706d2f2c0f` |
| SHA distant | `08fd11f425953e121c63f548fa96c9706d2f2c0f` (identique) |
| Checks CI | GitGuardian: pass | factory: pass (16s) |

---

## 2. Cause racine démontrée

**Le moteur casino Python et l'addon Lua WoW sont totalement isolés.**

| Constat | Preuve |
|---------|--------|
| `Casinobabe.lua` ligne 29-31 | Commentaire explicite : "WoW addons cannot write arbitrary files or open sockets." |
| Aucun `SendAddonMessage` vers l'extérieur | L'addon n'utilise `C_ChatInfo.SendAddonMessage` que vers le canal RAID/PARTY (L4348) |
| Aucun socket côté addon | Aucune occurrence de socket/TCP/UDP dans les fichiers Lua |
| Aucun fichier externe lisible par l'addon | L'addon ne peut que lire/écrire ses SavedVariables (`Casinobabe.lua`) |
| Moteur Python n'a pas d'API WoW | Pas d'injection de touches, pas de clic, pas de lecture de mémoire WoW |

**Conséquence :** Le moteur casino calcule correctement les règles de jeu (tous les tests unitaires passent), mais aucune commande ne peut atteindre l'addon WoW en temps réel, et aucun événement de jeu ne peut revenir au moteur.

---

## 3. Solution mise en œuvre : pont via SavedVariables

Comme aucun canal temps réel n'est possible (sandbox WoW), la seule voie de communication est les **SavedVariables** (`SavedVariables\Casinobabe.lua`).

### Architecture retenue

```
Desktop (Control Center)
    ↓ écrit CasinobabeDB.casinoCommand
    Fichier SavedVariables\Casinobabe.lua (écrit par le desktop)
    ↓ lu par l'addon WoW au chargement (/reload) ou via OnUpdate
Addon Lua (Casinobabe.lua)
    ↓ écrit CasinobabeDB.casinoResult et CasinobabeDB.dealer.log
    Fichier SavedVariables\Casinobabe.lua (écrit par WoW)
    ↓ lu par le desktop (polling)
Desktop (moteur casino)
```

### Fichiers modifiés

| Fichier | Modification |
|---------|--------------|
| `scripts/casino_bot.py` | Ajout `write_casino_command()`, `read_casino_result()`, `_lua_serialize()`, `find_wow_sv_file()`, commandes IPC `write_command` et `read_result` |
| `scripts/casino_engine.py` | Ajout `DealerCoreConfirmTrade()`, `DealerArmPayout()`, `DealerMarkPaid()` |
| `scripts/control_core.py` | Ajout `casino_request()`, `casino_detail()`, `casino()` commande, lecture `casinoResult` depuis SV |
| `runtime-addon/Casinobabe/Casinobabe.lua` | Ajout `CasinobabeDB.casinoCommand`, `desktopCmdFrame` (OnUpdate 0.5s), `DealerCoreConfirmTrade()`, `DealerArmPayout()`, `DealerMarkPaid()` |
| `docs/CASINOBABE-AUTOMATION-AUDIT.md` | Audit complet des mécanismes de communication |

---

## 4. Tests exécutés

| Suite | Résultat |
|-------|----------|
| `pytest tests/test_control_center.py` | **55 passed** (dont 4 tests d'intégration réelle sur le moteur casino) |
| `python scripts/casino_bot.py --selftest` | **PASS** (0 échec) |
| `python scripts/control_core.py --selftest` | **PASS** (0 échec) |
| `python scripts/control_center.py --selftest` | **PASS** (fenêtre non mappée) |
| `python scripts/window_guard.py --selftest` | **PASS** (0 échec) |
| `python tests/test_focus_broker.py` | **0 échec** |
| `node tests/run-offline-harness.mjs` | **CHECKS=14/14, ERRORS=0** |
| `node tests/live_pipeline_synthetic.cjs` | **SYNTHETIC_LIVE=PASS** |
| `node tests/regression-runtime-contract.cjs` | **3/3 PASS** |
| CI GitHub (PR #41) | **factory: pass (16s), GitGuardian: pass** |

Tests de bout en bout réel (moteur casino autonome) :
- `test_demarrage_arret_reel` : PASS — démarre, répond sur le pipe, s'arrête proprement
- `test_anti_doublon_reel` : PASS — seconde instance refusée (code 2)
- `test_cycle_jeu_reel` : PASS — session complète : create → game → stake → confirm_trade → mark_stake_confirmed → game → roll → resolve → arm_payout → mark_paid → close
- `test_persistence_apres_redemarrage` : PASS — session survive à un arrêt/redémarrage

---

## 5. État réel du moteur et de l'interface

Au moment de la livraison (lecture seule, aucun processus lancé) :

| Composant | État | Preuve |
|-----------|------|--------|
| Moteur casino | ARRÊTÉ (aucun processus) | `state/fishing-bot.pid` absent |
| Bot pêche | ARRÊT (verrou résiduel PID 16752 mort) | `state/fishing-bot.pid` = STALE |
| Observateur pêche | ARRÊT (verrou résiduel PID 14644 mort) | `state/fishing-observer.pid` = STALE |
| Control Center | Non lancé | Aucun processus Python contrôle-center |
| WoW | Présent (PID 2588), minimisé | Laissé tel quel |

Le moteur casino peut être démarré à tout moment par le bouton "Démarrer" du Control Center. Il créera son propre verrou PID, son heartbeat (toutes les 2s), et son watchdog (toutes les 5s).

---

## 6. Limites non vérifiables sans test en jeu réel

| Limite | Raison |
|--------|--------|
| **Envoi de commande au addon WoW** | Le desktop écrit dans `CasinobabeDB.casinoCommand` des SV, mais l'addon ne lit ce fichier que au chargement (`ADDON_LOADED` / `PLAYER_LOGIN`). Pour qu'une commande soit exécutée, le joueur doit faire `/reload` dans WoW. |
| **Réception d'événements WoW (roll, trade, etc.)** | L'addon ne peut pas envoyer de données vers un processus externe. Aucune API WoW ne permet à un addon de contacter un processus desktop. |
| **Paiement réel dans WoW** | `InitiateTrade` nécessite un événement matériel (hardware event) qui ne peut pas être généré par un script externe. |

**Le test de bout en bout réel (commande desktop → exécution dans WoW → retour au moteur) est techniquement impossible sans intervention manuelle du joueur (/reload dans WoW).** Ceci est une limitation de l'API WoW, pas un bug du code.

---

## 7. Corrections apportées

| Problème | Correction | Fichier |
|----------|-----------|---------|
| `is_secret()` ne masquait pas `api_key`/`CLIENT_SECRET` | Normalisation de la clé et du jeu de clés | `scripts/control_core.py` |
| `BOT_START` compté comme progression | Distinction `startup_events` / `progress_events` | `scripts/control_core.py` |
| Événement d'erreur masqué par progression | Passe 1 (erreur) avant passe 2 (progression) dans `heartbeat()` | `scripts/control_core.py` |
| Journal absent → `DEMARRAGE` au lieu de `BLOQUÉ` | État `BLOQUÉ` quand journal absent/vide | `scripts/control_core.py` |
| `Thread._stop` écrasé par `Event` | Renommé en `_stop_evt` | `scripts/window_guard.py` |
| `pause()` cosmétique (aucun effet) | Suspension OS des threads (`psutil.suspend()`) | `scripts/control_core.py` |
| `pause()` impossible → pas de repli | Repli sur "retenue logique" avec message explicite | `scripts/control_core.py` |
| `filedialog` import mort (risque de modal) | Supprimé | `scripts/control_center.py` |
| Raccourci pointe vers worktable jetable | Détection `is_scratch_checkout()` + mode `--verify` | `scripts/create_control_center_shortcut.py` |
| `window_guard` bloquait commande si souris bouge | Observations = non-bloquantes, seule la politique est bloquante | `scripts/window_guard.py` |
| `assert_no_mutating_api()` se détectait elle-même | Analyse AST au lieu de recherche texte | `scripts/window_guard.py` |
| `safe_call()` refusait si curseur bougeait | Les observations ne bloquent plus, seul le refus déterministe bloque | `scripts/window_guard.py` |
| `fishing_bot.py` absent de `main` | `bot_script_present()` + repli sur schéma interne | `scripts/control_core.py` |
| Clés `__*` contournent la validation | Refus explicite des clés `__*` | `scripts/control_core.py` |
| BOM UTF-8 dans `control_core.py` | Supprimé, lecture en `utf-8-sig` | `scripts/control_core.py` |

---

## 8. Décision finale

**READY_FOR_EXTENDED_TEST**

Le moteur casino est fonctionnel et testé (55 tests passent). Le pont desktop→addon via SavedVariables est implémenté mais nécessite un `/reload` dans WoW pour que l'addon lise les commandes écrites par le desktop.

**Ce qui reste à faire pour une validation en jeu réel :**
1. L'utilisateur lance le Control Center depuis le Bureau
2. L'utilisateur démarre le moteur casino depuis le Control Center
3. L'utilisateur écrit une commande (ex: `create_session` pour "Alice") via le Control Center
4. L'utilisateur fait `/reload` dans WoW pour que l'addon recharge les SV
5. L'addon exécute la commande et écrit le résultat dans `CasinobabeDB.casinoResult`
6. Le Control Center lit le résultat et l'affiche

**Ce test de bout en bout réel nécessite l'intervention de l'utilisateur (étape 4 : `/reload` dans WoW) et ne peut pas être automatisé depuis le PC.**

Le bot pêche n'est pas affecté par ces modifications (fichiers, verrous et journaux séparés).

**Le PR #41 n'est pas fusionné.** Validation humaine requise avant déploiement.