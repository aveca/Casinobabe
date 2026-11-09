# Navigation V2 — constats de terrain et état réel

Document de référence : ce qui a été **mesuré** en jeu le 2026-10-21, ce qui
fonctionne, ce qui bloque, et la règle à retenir.

> Aucun résultat ci-dessous n'est simulé. Les mesures proviennent de captures
> réelles du client (Lushwater Oasis, WoW Classic 1.13 « anniversary »,
> 2547×1440, zone de l'oasis).

---

## 1. Ce qui est établi (mesuré)

| Constat | Mesure | Conséquence |
|---|---|---|
| La minicarte est **verrouillée au nord** et **centrée sur le joueur** | capture du 2026-10-21 : le « N » en haut, la flèche du joueur au centre | la minicarte ne donne **aucune position absolue** ; elle donne le **cap** et les **cibles suivies** (anneaux colorés) |
| Le client **ignore les touches de direction injectées** | `SendInput` (état clavier réel) : **0,00 px** de déplacement ; `WM_KEYDOWN/WM_KEYUP` : **0,00 px** | le déplacement **au clavier est impossible** |
| `W` n'est **pas** la touche d'avance | `bindings-cache.wtf` : `bind W ACTIONBUTTON1`, `bind UP NONE` | les raccourcis par défaut sont trompeurs : il faut **lire** les bindings du compte joué |
| Le déplacement réel est l'**auto-run** | `bind Z TOGGLEAUTORUN` + clic droit au sol → **0,53 à 0,96 px** de minicarte observés | mécanique retenue pour la navigation |
| Un clic **ne re-cible pas** l'auto-run | clics à 90° et 270° → déplacement identique (cap −135° dans les deux cas) | il faut **re-armer** l'auto-run avant chaque nouvelle cible |
| **L'odomètre de minicarte dérive** | **7,05 px en 10 s** sur un personnage **immobile**, alors que la vue 3D **ne défile pas** (0,00 px/s) | l'animation de l'eau biaise la corrélation de phase : **la minicarte n'est pas un odomètre fiable** |
| La dérive « 0,000 px » mesurée le 2026-10-20 était un **faux négatif** | l'animation de l'eau était momentanément immobile | toute mesure de dérive doit être **croisée** avec la vue 3D |
| La **touche de lancer configurée** n'était pas liée à l'action de pêche | compte en jeu `103329567#1` : `2`→ACTIONBUTTON7, `X`→ACTIONBUTTON10 ; config : `castKey=2` | **0 capture** : le bot pressait la mauvaise touche |

## 2. La cause des « 0 capture » — démontrée et corrigée

```
compte en jeu  : 103329567#1
  touche 2     -> ACTIONBUTTON7   (une autre action)
  touche X     -> ACTIONBUTTON10 (l'action de pêche)
configuration : castKey = "2"      <-- mauvaise touche
```

Le bot envoyait donc une action sans rapport : le flotteur n'apparaissait
jamais (`CAST_UNCONFIRMED` × 13), et aucune prise n'était possible.

**Correctif** : `scripts/fishing_controls.py` lit les bindings **réels** de
tous les comptes (lecture seule, aucune modification), identifie le compte en
jeu (SavedVariables le plus récent) et **résout** la touche de lancer. Le bot
journalise `CAST_KEY_RESOLVED` et utilise la touche résolue.

**Vérifié en jeu** après correctif : `CAST key=X` puis
`CATCH_DETECTED biteDiff=15.8 whiteFrac=0.045` (une vraie éclaboussure).

## 3. La navigation réelle — ce qui bloque aujourd'hui

Le mécanisme est en place et fonctionne *mécaniquement* (déplacement observé,
étalonnage, garde-fous), **mais l'arrivée à un repère n'est pas validée**, pour
une raison maintenant identifiée et mesurée :

> **La position n'est pas exploitable.** L'odomètre de minicarte dérive de
> 7,05 px en 10 s au repos. Or c'est exactement l'ordre de grandeur des
> déplacements que l'on cherche à mesurer (0,5 à 1 px par clic).

C'est pourquoi le contrôleur refuse aujourd'hui de piloter un déplacement :

```
NAV_RESULT status=unreliable_position
  reason="derive de l'odometre 4.24 px (seuil 1.5) : position non
          exploitable, aucun deplacement pilote"
  travelled=0.0  turns=0
```

**C'est le comportement voulu** : mieux vaut ne pas bouger que croire à une
position fausse. Le garde-fou est couvert par un test
(`test_unreliable_position_guard`).

### 3.1 La pièce manquante, précisément

Il faut une **source de position fiable** et la **conversion direction monde →
direction écran** :

1. **Source de position** : la vue 3D. Quand le personnage avance, le monde
   défile ; l'odomètre serait alors construit sur la vue 3D
   (`world_displacement()` est **déjà implémenté** dans
   `scripts/fishing_minimap.py`, et mesure 0,00 px/s au repos ✓) et non sur la
   minicarte.
2. **Orientation de la caméra** : la flèche du joueur, au centre de la
   minicarte, donne le cap en repère nord (la minicarte est verrouillée au
   nord). C'est la conversion direction monde → direction écran (point à
   cliquer) qui manque.
3. **Preuve d'arrivée** : deux signaux **indépendants** à combiner —
   le marqueur de cible vu à moins de la tolérance du centre de la minicarte
   (mesure de couleur, **insensible au biais de l'eau** ✓) et la position
   mesurée par la vue 3D.

### 3.2 Amorçage de la carte

Sans déplacement, la carte ne contient que la position courante : aucun banc
n'est atteignable. Un mode d'**exploration vers un point** a été ajouté
(`targetMode=map` → point d'exploration, distance et reproductibilité
configurables, test couvert ✓), dont l'arrivée sera prouvée par l'odomètre
une fois la source de position fiabilisée.

## 4. Règles mémorisées (à ne pas réapprendre)

1. **Ne jamais supposer un raccourci** : lire `bindings-cache.wtf` du compte
   **réellement joué**, et le résoudre au démarrage
   (`fishing_controls.resolve_cast_key`).
2. **Une position n'est une position que si elle est corroborée** : croiser la
   minicarte et la vue 3D ; sinon, ne rien piloter.
3. **Un clic ne re-cible pas l'auto-run** : le re-cibler explicitement.
4. **Un compteur n'est pas une preuve** : un clic, une animation ou un
   flotteur disparu ne sont pas une capture (cf. `fishing_metrics.py`).
5. **Toute anomalie mesurée devient un test** : les sept constats ci-dessus
   sont verrouillés par des tests (33/33 navigation, 34/34 commandes,
   22/22 mesure des captures, 40/40 compétences).

## 5. État des tests

| Suite | Résultat |
|---|---|
| `pytest tests` (worktree V2) | **89 passed / 1 skipped** |
| `tests/test_fishing_navigation.py` | 33/33 |
| `tests/test_fishing_controls.py` | 34/34 |
| `tests/test_fishing_skills.py` | 40/40 |
| `tests/test_fishing_metrics.py` | 22/22 |
| `npm test` (harnais Lua + pipeline synthétique) | PASS |
