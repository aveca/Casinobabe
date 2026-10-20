# -*- coding: utf-8 -*-
"""Détection et confirmation des popups modaux WoW (StaticPopup).

PROBLÈME (19/10) : un popup modal bloque le JEU. Exemples réels capturés :
  - « Do you want to replace "Fishing Lure (+50 Fishing Skill)" with
    "Fishing Lure (+50 Fishing Skill)" ? »  -> [Yes] [No]
  - « TSM is missing important information from the TSM Desktop
    Application. »                            -> [Okay]
Tant que le popup est ouvert, aucune touche de pêche n'est traitée et le
flotteur n'est pas vu : la session est perdue. Il faut donc le confirmer A LA
SOURIS, sur le bon bouton.

Approche (validée sur les captures réelles du 19/10) :
  - un bouton de popup WoW est un RECTANGLE ROUGE SATURÉ, à bords nets,
    de largeur 150-260 px et de hauteur 14-30 px (mesuré : 185x22 sur le
    bouton « Okay » du dialogue TSM, 185x22 sur les boutons « Yes »/« No ») ;
  - il est situé dans la MOITIÉ HAUTE et CENTRALE de l'écran (les popups
    WoW sont centrés) ;
  - quand il y en a deux, c'est [Yes] [No] : on clique celui de GAUCHE.

DISCRIMINANT ANTI-FAUX-POSITIF : un popup de CONFIRMATION a 1 ou 2
boutons. Le menu « Options » du jeu (capture 20261016-155650) a lui aussi
des boutons rouges identiques (226x23) mais SIX d'un coup : on ne le
confirme JAMAIS (ce n'est pas une confirmation, c'est un menu).

DEUXIEME DISCRIMINANT (essentiel pour la securite) : le texte du bouton.
  - « Yes » / « No » (confirmation d'appat)  -> texte COURT  (ratio 0.12)
  - « Accept » / « Decline » (invitation de groupe) -> texte LONG (ratio 0.35)
Cliquer « Accept » sur une invitation de groupe serait une action non
voulue : on ne confirme donc QUE les popups a texte court, ceux-la
seulement.

On ne clique JAMAIS un bouton qui ne remplit pas TOUS ces critères : un
faux positif pourrait valider une action non voulue.

Fonctions :
  find_popup_buttons(img_bgr) -> [(x, y, w, h, cx, cy), ...]
  popup_confirm_buttons(img_bgr) -> boutons de confirmation d'appat
  choose_confirm_button(btns) -> le bouton à cliquer (gauche si 2)
"""
import cv2
import numpy as np

# Un bouton de popup WoW : rectangle rouge saturé, bords nets.
BTN_MIN_W, BTN_MAX_W = 120, 260
BTN_MIN_H, BTN_MAX_H = 14, 30
BTN_MIN_FILL = 0.55        # remplissage du rectangle par le rouge
BTN_MIN_S = 140            # saturation minimale du rouge
# Zone où un popup WoW peut se trouver (popups centrés, moitié haute).
ZONE_X = (0.30, 0.70)
ZONE_Y = (0.05, 0.50)
# Un popup de CONFIRMATION a 1 ou 2 boutons (Yes/No). Au-delà, c'est un
# menu du jeu (Options, etc.) : on ne touche à rien.
MAX_CONFIRM_BUTTONS = 2
# Largeur du TEXTE du bouton, en fraction de sa largeur. Mesuré sur les
# captures réelles du 19/10 :
#   « Yes » (remplacement d'appat)      -> 0.124
#   « No »                               -> 0.081
#   « Accept » (invitation de groupe)   -> 0.355
#   « Decline »                          -> 0.328
# On ne confirme donc qu'un texte COURT (< 0.25) : ce sont les boutons
# « Yes »/« Okay », pas « Accept ».
BTN_TEXT_MAX_RATIO = 0.25


def find_popup_buttons(img_bgr):
    """Boutons de popup détectés : [(x, y, w, h, cx, cy), ...]."""
    if img_bgr is None:
        return []
    h, w = img_bgr.shape[:2]
    if h < 200 or w < 200:
        return []
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168)) &
           (hsv[:, :, 1] > BTN_MIN_S) & (hsv[:, :, 2] > 60))
    mask = red.astype(np.uint8) * 255
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if not (BTN_MIN_W <= cw <= BTN_MAX_W):
            continue
        if not (BTN_MIN_H <= ch <= BTN_MAX_H):
            continue
        if area / float(cw * ch) < BTN_MIN_FILL:
            continue
        cx, cy = x + cw // 2, y + ch // 2
        if not (ZONE_X[0] * w <= cx <= ZONE_X[1] * w):
            continue
        if not (ZONE_Y[0] * h <= cy <= ZONE_Y[1] * h):
            continue
        out.append((int(x), int(y), int(cw), int(ch), int(cx), int(cy)))
    return out


def button_text_ratio(img_bgr, btn):
    """Fraction de la largeur du bouton occupée par son TEXTE (lettres claires).

    Mesuré sur captures réelles :
      « Yes »     -> 0.124      « No »      -> 0.081
      « Accept »  -> 0.355      « Decline » -> 0.328
      « Okay »    -> 0.205
    Un texte court => bouton affirmatif court (« Yes », « Okay »).
    """
    x, y, w, h = btn[0], btn[1], btn[2], btn[3]
    inner = img_bgr[y + 2:y + h - 2, x + 4:x + w - 4]
    if inner.size == 0:
        return 1.0
    gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    light = (gray > 165).astype(np.uint8)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(light, 8)
    xs = []
    for i in range(1, n):
        lx, ly, lw, lh, area = stats[i]
        if area >= 8:
            xs.append(lx + lw // 2)
    if not xs:
        return 1.0
    return (max(xs) - min(xs) + 1) / float(w)


def popup_confirm_buttons(img_bgr):
    """Boutons d'un popup de CONFIRMATION d'appât (1 ou 2). Vide sinon.

    Deux filets :
      - au-delà de 2 boutons rouges alignés, ce n'est pas une confirmation
        (menu Options du jeu) -> on ne clique RIEN ;
      - le texte doit être COURT (« Yes »/« Okay ») : une invitation de
        groupe (« Accept »/« Decline ») n'est JAMAIS confirmée par le bot.
    """
    btns = find_popup_buttons(img_bgr)
    if not btns or len(btns) > MAX_CONFIRM_BUTTONS:
        return []
    for b in btns:
        if button_text_ratio(img_bgr, b) > BTN_TEXT_MAX_RATIO:
            return []
    return btns


def choose_confirm_button(buttons):
    """Bouton à cliquer pour confirmer.

    1 bouton  -> lui-même (« Okay »).
    2 boutons -> celui de GAUCHE (« Yes », l'affirmatif ; « No » est à
                 droite). Confirmé sur la capture du 19/10 (dialogue de
                 remplacement d'appât : [Yes] [No]).
    """
    if not buttons:
        return None
    if len(buttons) == 1:
        return buttons[0]
    return min(buttons, key=lambda b: b[4])   # le plus à gauche


def popup_is_blocking(img_bgr):
    """Vrai si un popup de confirmation bloque l'écran."""
    return bool(popup_confirm_buttons(img_bgr))
