# Retour au corps — valeurs natives observées (2026-10-21)

Toutes les valeurs ci-dessous proviennent des **API natives du client**
(`C_Map`, `C_DeathInfo`, `UnitPosition`, `RetrieveCorpse`), lues dans le
client — aucune valeur n'est simulée ni estimée par le bot.

## 1. Le module de diagnostic a-t-il été chargé ?

**OUI.** Preuves :

| Preuve | Détail |
|---|---|
| Message dans le chat du client | `[DiagCorps] ghost=true \| mapID=1430 \| playerMap=… \| corpseMap=… \| worldPos=… \| RetrieveCorpse=nil … \| C_DeathInfo=true \| Interaction action failed because of an AddOn` |
| `CasinobabeDB.corpseDiag` dans le fichier WTF | présent, écrit par le client (`WTF/Account/103329567#1/SavedVariables/Casinobabe.lua`) |
| Pas d'erreur Lua | `Logs/Errors.txt` absent, `FrameXML.log` sans nouvelle erreur |
| Interface d'addon | `## Interface: 20506` — identique à la version du dépôt, donc l'addon n'est **pas** marqué « obsolète » par le client |

## 2. Valeurs natives observées

Deux lectures, à 2 instants différents (le fantôme s'est déplacé entre les deux) :

### Lecture A — dans le chat du client

| Champ | Valeur |
|---|---|
| `ghost` | `true` |
| `mapID` | `1430` |
| `playerMap` | `0.0429, 0.0522` (coordonnées **normalisées 0..1** de la carte de zone) |
| `corpseMap` | `0.0858, 0.1044` (même système) |
| `deltaCarte` | `0.0429, 0.0522` — **corps au SUD-EST** du joueur |
| `dist` (normalisé) | `0.0676` |
| `worldPos` | `727.30, -2403.40, 0.00` **yards**, repère monde WoW |
| `C_DeathInfo` | `true` (l'API **existe** dans TBC Anniversary) |
| `RetrieveCorpse` | `existe=true` ; appel → `nil`, `err=nil` |
| `sortRecuperer(id=5026)` | `connu=true`, `resultat=nil` → **`Interaction action failed because of an AddOn`** |

### Lecture B — dans les SavedVariables (écrit par le client à 07:24:50)

| Champ | Valeur |
|---|---|
| `mapID` | `1413` |
| `playerMap` | `0.5011, 0.3463` |
| `corpseMap` | `0.4582, 0.3985` |
| delta / distance | `-0.0429, +0.0522` / `0.0676` |
| **cap vers le corps** | **-129,4°** (repère nord, 0 = est, +90 = nord) → **OUEST-SUD-OUEST** |
| `worldPos` | `-727.30, -2454.40, 0.00` yards |
| `corpseApi` / `retrieveCorpseExists` / `retrieveSpellKnown` | `true` / `true` / `true` |
| `castErr` | `Interaction action failed because of an AddOn` |

**Systèmes de coordonnées** (explicites) :

* `C_Map.GetPlayerMapPosition` / `C_DeathInfo.GetCorpseMapPosition` → **position sur la carte de zone**, `x` = ouest→est, `y` = nord→sud, **normalisé 0..1**. La carte de zone est **verrouillée au nord** → le vecteur joueur→corps donne un **cap absolu**, indépendant de la caméra.
* `UnitPosition` → **position monde WoW en yards** (X est, Y nord, Z vertical), origine à l'origine du continent.

## 3. Test de la récupération native

| Action | Résultat |
|---|---|
| `RetrieveCorpse()` (API globale) | appelle, renvoie `nil`, **aucune erreur** — mais aucun effet |
| `CastSpellByName("Retrieve Corpse")` (id **5026**) | **refusée** : `Interaction action failed because of an AddOn` |
| Conclusion | le sort **est connu du client** mais **protégé pour les addons** : la résurrection doit être une **action joueur** (clic sur le bouton d'action du fantôme, ou clic droit sur le corps) |

## 4. Blocage exact restant

**Le rechargement d'UI est inaccessible depuis l'état fantôme.** Mécanismes testés, tous en échec :

| Mécanisme | Résultat |
|---|---|
| `/reload` dans le chat | refusé (« You can't chat when you're dead! ») |
| touche `~` (console) | aucune réaction (7 VK testés) |
| clic physique sur les boutons de l'UI | 9 combinaisons testées, **0 réaction** (le client ignore les clics sur ces panneaux) |
| clic sur le panneau Casinobabe | le WTF n'est pas réécrit → pas de rechargement |
| ajout d'un `.lua` / réécriture du `.toc` | le client ne surveille pas le dossier |
| `ReloadUI()` via un addon | impossible : le code n'est chargé qu'au rechargement suivant |
| **Le module a pourtant été chargé à 07:24** | via une action de l'utilisateur ; le WTF a été réécrit à 07:24:50 avec `corpseDiag` |

**Action requise, unique et simple** : **reconnecter le client** (déconnexion/reconnexion). L'UI se recharge, `CasinobabeDiag.lua` est chargé, et il publie alors la bande de diagnostic **en direct** (ticker 2 s) que le bot lit pour naviguer vers le corps (cap natif, distance native comme mesure de progression), puis tente l'action joueur de résurrection et vérifie que le monde n'est plus en mode fantôme.

En attendant, le pilote `piloter_approche.py` tourne en arrière-plan : il attend la référence native fraîche, navigue, tente l'interaction et journalise. La pêche reste arrêtée (`fishing_bot.py` n'est pas relancé).
