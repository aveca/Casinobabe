# CasinoBae — Audit d'automatisation et diagnostic de bout en bout

**Date :** 2026-10-19  
**Dépôt :** aveca/Casinobabe  
**Branche :** feat/control-center-windows  
**SHA :** 6b0da0d  
**PR :** #41 (MERGEABLE / CLEAN)

---

## 1. Inventaire des composants existants

| Composant | Fichier | Rôle | Communique avec |
|-----------|---------|------|-----------------|
| Bot pêche | `scripts/fishing_bot.py` | Lance des touches et clics dans la fenêtre WoW via `SendMessageW` | Fenêtre WoW directement (injection de messages Windows) |
| Moteur casino | `scripts/casino_bot.py` | Calcule les règles de jeu, gère les sessions, les mises, les paiements | Control Center via named pipe |
| IPC casino | `scripts/casino_ipc.py` | Canal nommé pipe authentifié par jeton | Control Center ↔ moteur casino |
| Control Center | `scripts/control_center.py` | Interface Windows de supervision | Moteur casino via IPC |
| Addon Lua | `runtime-addon/Casinobabe/Casinobabe.lua` | Gère les commandes slash, whispers, trades, rolls, paiements en jeu | Client WoW uniquement (sandboxé) |
| Bridge télémétrie | `bridge/bridge.mjs` | Lit les SavedVariables WoW, POST vers Supabase | Fichier SV → Supabase (unidirectionnel) |
| Sentinel live | `scripts/casinobae-live-sentinel.cjs` | Lit les SavedVariables WoW, détecte les erreurs | Fichier SV → rapports (unidirectionnel) |

---

## 2. Comparaison pêche vs casino

| Aspect | Bot pêche | Moteur casino |
|--------|-----------|---------------|
| Interaction avec WoW | `SendMessageW` (WM_KEYDOWN, WM_RBUTTONDOWN/UP) directement dans la fenêtre WoW | Aucun. L'addon Lua fonctionne indépendamment |
| Canal de contrôle | Injection de messages Windows dans la fenêtre du jeu | Named pipe `\\.\pipe\casinobabe-casino` (desktop uniquement) |
| État partagé | Aucun (le bot lit l'écran et clique) | Journal JSONL + fichier d'état JSON |
| Dépendance WoW | Doit que WoW soit lancé avec une fenêtre visible | Aucun (tourne indépendamment de WoW) |
| Peut fonctionner sans WoW | Non | Oui |

**Constat :** La pêche interagit directement avec le client WoW via injection de messages Windows. Le casino passe par un addon Lua sandboxé qui ne peut ni ouvrir de socket, ni lire de fichier arbitraire, ni recevoir de commande d'un processus extérieur.

---

## 3. Chaîne de commande — traçage complet

### Chaîne souhaitée (commande desktop → action dans WoW → résultat revenant au moteur)

```
Control Center (desktop)
    → nommé pipe \\.\pipe\casinobabe-casino
    → moteur casino Python (calcule le résultat)
    → ??? (aucune sortie vers WoW)
    → addon Lua Casinobabe (ne reçoit rien)
    → joueur dans WoW (ne reçoit aucune commande du moteur)
    → /roll dans le chat WoW (généré par le joueur, pas par le moteur)
    → ??? (le moteur ne reçoit pas le roll)
```

**La chaîne s'interrompt entre le moteur Python et l'addon Lua.** Il n'existe aucun mécanisme pour que :
1. Le moteur envoie une commande à l'addon (ex: "crée une session pour Alice")
2. L'addon envoie un événement au moteur (ex: "Alice a fait /roll 45")

### Ce qui existe déjà (mais ne résout pas le problème)

| Mécanisme | Direction | Latence | Utilisable pour le casino ? |
|-----------|-----------|---------|------------------------------|
| SavedVariables `CasinobabeDB` | addon → desktop (lecture du fichier par le desktop) | Élevée (dépend du flush WoW : /reload, logout) | Partiellement (lecture seulement) |
| SavedVariables `showTrigger` | desktop → addon (écriture dans le fichier SV) | Élevée (lu seulement au PLAYER_LOGIN) | Partiellement (écriture seulement, pas temps réel) |
| Named pipe `\\.\pipe\casinobabe-casino` | desktop ↔ desktop (contrôle center ↔ moteur) | ~ms | Non (pas de lien avec l'addon) |
| SendChatMessage / InviteUnit / InitiateTrade | addon → WoW client (in-game uniquement) | ~ms (temps réel) | Non (l'addon ne peut pas recevoir du desktop) |

---

## 4. Cause racine

**Il n'existe aucun mécanisme de communication temps réel entre le moteur casino Python et l'addon Lua Casinobabe.**

Preuves :

1. **L'addon Lua est sandboxé** (Casinobabe.lua, lignes 29-31) :  
   `"WoW addons cannot write arbitrary files or open sockets."`  
   L'addon ne peut pas lire de fichier externe, ouvrir un socket, ou recevoir un named pipe.

2. **L'addon n'a aucun code de réception de commandes externes.**  
   Toutes les entrées de l'addon viennent des APIs WoW en jeu : `SendChatMessage` (whispers), `InitiateTrade`, `CHAT_MSG_SYSTEM` (rolls), `InviteUnit`. Aucune de ces API n'est connectée au moteur Python.

3. **Le moteur Python met les actions en attente (`pending_actions`) mais rien ne les lit côté addon.**  
   Chaque commande du moteur (create_session, game, stake, roll, resolve, arm_payout, mark_paid, close) ajoute une entrée dans `session.pending_actions`, mais aucune fonction de l'addon ne vide cette file.

4. **Les SavedVariables sont la seule voie addon↔desktop**, mais ce sont des fichiers lus/écrits par WoW à son rythme (logout, /reload, ou périodique). Ce n'est pas un canal temps réel.

---

## 5. Ce qui fonctionne déjà (preuves)

| Test | Résultat | Preuve |
|------|----------|--------|
| Moteur casino démarre et répond sur le named pipe | PASS | `ping` → `{'ok': True, 'pong': True, 'pid': 6484}` |
| Moteur casino répond au status | PASS | `status` → état `ACTIF`, heartbeat à 2s |
| Watchdog détecte les timeouts | PASS | `CONTACTED` depuis 360s → flaggé |
| Sessions créées, mises armées, rolls résolus | PASS | 56 assertions du selftest, toutes vertes |
| Anti-doublon (une seule instance) | PASS | Seconde instance refuse (code 2) |
| Persistence après redémarrage | PASS | Session restaurée après stop/start |
| IPC authentifié (jeton 32 octets) | PASS | Mauvais jeton refusé (`AUTH_FAILED`) |
| Aucun socket TCP dans le module IPC | PASS | Seul `AF_PIPE` (named pipe Windows) est utilisé |

---

## 6. Ce qui ne fonctionne pas (et pourquoi)

| Fonctionnalité | Statut | Raison |
|----------------|--------|--------|
| Contrôler le jeu depuis le desktop (whisper JOIN, sélection de jeu, mise, /roll) | ❌ Impossible | L'addon Lua ne peut pas recevoir de commande d'un processus externe (sandbox WoW) |
| Recevoir le résultat d'un /roll dans le moteur | ❌ Impossible | L'addon ne peut pas envoyer d'événement au moteur Python |
| Automatiser un cycle de jeu complet en jeu | ❌ Impossible | Aucun pont entre le moteur et l'addon |
| Supervision en temps réel de l'état du bot dans WoW | ❌ Impossible | Les SavedVariables ne sont pas lues en temps réel par le moteur |

---

## 7. Solution : pont via SavedVariables

La seule voie de communication addon ↔ desktop qui traverse la sandbox WoW est les **SavedVariables** (`WTF\Account\*\*\SavedVariables\Casinobabe.lua`).

### Architecture proposée

```
Desktop (Control Center)
    ↓ écrit CasinobabeDB.casinoCommand (ex: {action="roll", player="Alice", roll=45})
    Fichier SavedVariables\Casinobabe.lua (écrit par le desktop, lu par WoW)
    ↓ lit au prochain événement WoW (PLAYER_LOGIN, ou via un frame OnUpdate si ajouté)
Addon Lua (Casinobabe.lua)
    ↓ écrit CasinobabeDB.dealer.log (événements: JOIN, GAME_SELECTED, STAKE_ARMED, ROLL, RESOLVED, PAYOUT_ARMED, PAID)
    Fichier SavedVariables\Casinobabe.lua (écrit par WoW, lu par le desktop)
    ↓ lit le desktop (polling toutes les N secondes)
Desktop (moteur casino)
```

### Avantages
- Ne nécessite aucune modification du client WoW (pas de socket, pas d'injection)
- Utilise le mécanisme existant des SavedVariables
- Bidirectionnel (dans les deux sens, via écriture/lecture alternée)

### Inconvénients
- **Latence élevée** : dépend du flush des SavedVariables par WoW (logout, /reload, ou écriture périodique automatique)
- **Pas de temps réel** : ne convient pas pour des interactions rapides (roll immédiat après une mise)
- **Pas de garantie d'écriture immédiate** : WoW décide quand flusher les SV

---

## 8. Plan d'implémentation

### Phase 1 : Pont desktop → addon (écriture de commandes dans les SV)

1. Ajouter dans `CasinobabeDB` un champ `casinoCommand` (table avec `action`, `player`, `params`, `requestId`, `timestamp`)
2. Dans l'addon, ajouter un frame `OnUpdate` qui vérifie périodiquement si `CasinobabeDB.casinoCommand` a changé
3. Si oui, exécuter la commande correspondante (`DealerCreateSession`, `DealerGame`, `DealerStake`, `DealerRecord`, etc.)
4. Marquer la commande comme traitée (`processed: true`) pour éviter les doublons

### Phase 2 : Pont addon → desktop (lecture des événements dans les SV)

1. Le moteur casino lit périodiquement le fichier `SavedVariables\Casinobabe.lua`
2. Extraire `CasinobabeDB.dealer.log` (entrées depuis la dernière lecture)
3. Mettre à jour l'état des sessions dans le moteur Python
4. Déclencher les actions automatiques (payout, close, etc.) selon les règles du moteur

### Phase 3 : Tests de bout en bout

1. Lancer le moteur casino
2. Lancer WoW, se connecter
3. Écrire une commande dans les SV depuis le desktop
4. Vérifier que l'addon l'exécute dans WoW
5. Vérifier que l'addon écrit le résultat dans les SV
6. Vérifier que le moteur lit le résultat et met à jour l'état

---

## 9. État réel au moment du diagnostic

| Élément | État |
|---------|------|
| Moteur casino | Actif (PID 6484), heartbeat toutes les 2s, watchdog actif, aucune session |
| Control Center | Ouvert (PID 26052), voit le moteur comme ACTIF |
| Bot pêche | Arrêté (non lancé) |
| WoW | Connecté (PID 2588), fenêtre "World of Warcraft" |
| Addon Lua | Présent dans `Interface\AddOns\Casinobabe\` (380 KB) |
| SavedVariables | Aucun fichier `Casinobabe.lua` trouvé dans `WTF\Account\*\*\SavedVariables\` (WoW n'a pas encore flushed son état, ou le compte n'est pas chargé) |
| Verrous PID | Tous nettoyés (aucun processus mort ne bloque) |

---

## 10. Conclusion

Le moteur casino Python et l'addon Lua Casinobabe sont **deux systèmes totalement isolés**. Le moteur calcule les règles de jeu correctement (tous les tests unitaires et d'intégration passent), mais il ne peut pas interagir avec le client WoW car les addons sont sandboxés.

Pour rendre le système fonctionnel de bout en bout, il faut implémenter un pont via les SavedVariables (Phase 1 et 2 ci-dessus). Cela permettra au desktop d'envoyer des commandes à l'addon et de lire les événements du jeu, sans violer le sandbox de WoW.

**Aucune transaction réelle, aucun paiement, aucune action de jeu n'a été effectuée pendant ce diagnostic.** Tous les tests ont été exécutés sur le moteur en isolation ou sur des processus de test inoffensifs.
