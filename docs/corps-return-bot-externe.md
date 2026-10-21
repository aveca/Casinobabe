# Retour au corps en bot EXTERNE (sans addon) — procédure validée

Résurrection **observée** le 21/10/2026 sur Colorabi (TBC Anniversary),
programme externe uniquement (capture d'écran + entrées simulées),
**sans addon chargé**.

## 1. Les trois causes racines (mesurées, pas supposées)

### 1.1 La carte du monde ouverte annule TOUTES les entrées
La carte du monde est une *frame* du client qui prend le focus clavier.
Tant qu'elle est ouverte : `Z+W` → **0,23 px** de défilement, clic droit →
**0,00 px** (et le clic droit sur la carte fait « zoom out »).
Carte fermée : `Z+W` 4 s → **36,19 px**, clic droit → **9,90 px**.

> **Règle** : la carte n'est ouverte que pour MESURER, puis refermée, et
> l'état « monde visible » est vérifié par capture avant toute action.

### 1.2 Un fantôme plonge et se coince au fond du lac
Sous l'eau, le fantôme descend et reste bloqué : ni `Z+W` ni le
clic-to-move ne le déplacent.

> **Règle** : maintenir **ESPACE** en permanence pendant le déplacement
> (`Z` + `W` + **ESPACE**) : le fantôme remonte à la surface et marche
> **sur** l'eau au lieu de plonger.

### 1.3 Les coordonnées de clic souris sont en ÉCRAN, la capture est en FENÊTRE
`capture_window()` renvoie l'image de la fenêtre (origine = `rect.left/top`,
ici -8,-8) ; `SetCursorPos` attend des coordonnées **écran**.
Un clic calculé sur les pixels de capture rate la cible de 8 px — assez
pour rater un bouton de 20 px de haut (« Accept » de « Resurrect now? »).

> **Règle** : `ecran = capture + (rect.left, rect.top)`.

## 2. Faits client mesurés (session 21/10)

| fait | valeur |
|---|---|
| raccourcis du perso **Colorabi** | `Z`=MOVEFORWARD, `Q`=TURNLEFT, `W`/`A`=NONE |
| cache du **compte** (non prioritaire) | `Z`=TOGGLEAUTORUN, `E`=ACTIONBUTTON5 |
| `E` | ce n'est **pas** une rotation (→ ACTIONBUTTON5) |
| lecture clavier du client | **raw input** : seules les touches réellement liées répondent |
| seule commande qui tourne | **la souris** (caméra), ~0,45 °/px, à déplacer **sous bail de focus** |
| bail de focus | à **tenir pendant l'action** (hors bail : 0,00 px) |
| listes déroulantes / boutons | répondent à un **appui maintenu** (mouse-down + ~0,6 s) |
| repère de cap | **minicarte** (verrouillée au nord) : `MinimapGeometry` (Hough) + `player_facing` |
| repère de position | **carte du monde** : joueur = flèche argentée + cercle bleu, cimetière = stèle violette |
| progression | distance en **pixels de carte** joueur ↔ cimetière (décroissante) |
| conversion | coordonnées normalisées (native, 1413) → pixels : cadre `x 350..2225`, `y 165..1440` — **validée** (le point natif tombe pile sur le cimetière) |
| interaction | clic droit sur le corps → dialogue « Resurrect now? » → **Accept** (clic maintenu, coords écran) |

## 3. Boucle de navigation (fermée, deux mesures indépendantes)

1. ouvrir la carte → mesurer cimetière (gabarit) + joueur (cercle bleu)
   → **bearing** (repère nord) et **distance** ;
2. fermer la carte et **vérifier** que le monde est visible ;
3. cap lu sur la **minicarte** → correction d'orientation à la **souris**
   (`dx = erreur / 0,45° par px`, sous bail de focus) ;
4. vol `Z` + `W` + **ESPACE** maintenus (3 s) ;
5. re-mesurer : la distance doit **baisser** ; sinon demi-tour ;
6. arrêt à ≤ 8 px → clic droit sur le corps → menu → Accept → vérifier
   l'état **vivant** (disparition du voile cyan : `cyan` 0,99 → 0,08).

## 4. Journal type d'une tentative

```
[1] carte fermee : True
    etat de vie : FANTOME (cyan=0.99)
    clic droit (1494,844) : diff locale= 37.26  -> MENU DETECTE
[accept] clic ecran (1301,335) -> cyan=0.08  ==> VIVANT OBSERVE
```

## 5. Pièges à ne pas refaire

* ne pas « calibrer » la carte : le cimetière est stable, la conversion est
  prouvée ;
* ne pas confondre le cimetière et le corps : le corps n'est **pas**
  dessiné sur la carte ni sur la minicarte (balayage complet fait) — le
  cimetière sert de **borne de progression**, la proximité se conclut par
  l'ouverture du menu « Resurrect now? » ;
* ne pas lancer de vol long sans direction fiable : la distance doit être
  mesurée avant/après ;
* ne pas laisser la carte ouverte entre deux actions ;
* ne pas clicking sans convertir en coordonnées écran.
