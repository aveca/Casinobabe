# -*- coding: utf-8 -*-
"""Test du detecteur de popup bloquant, sur les captures REELLES.

On verifie :
  1) les frames SANS popup ne declenchent AUCUNE detection (pas de faux
     positif -> on ne clique jamais par erreur) ;
  2) les frames AVEC popup (dialogue TSM « Okay », dialogue de
     remplacement d'appat « Yes/No ») sont detectees ;
  3) sur le dialogue a deux boutons, c'est bien celui de GAUCHE (« Yes »)
     qui est choisi.
"""
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

from fishing_popup import (find_popup_buttons, popup_confirm_buttons,  # noqa: E402
                             choose_confirm_button)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(ROOT, "captures", "fishing-bot")

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    files = sorted(glob.glob(os.path.join(CAP, "*.png")))
    if not files:
        print("SKIP : pas de captures")
        return 0

    # Frames de REFERENCE identifiees a la main (zoom sur l'ecran) :
    #   - popup TSM « Okay »          : 1 bouton rouge 185x22
    #   - popup remplacement d'appat  : 2 boutons rouges 185x22
    ref_one = ["20261019-203650-bite.png"]
    ref_two = ["20261019-210511-bite.png", "20261019-212509-bite.png"]

    print("=== 1) Bouton unique (dialogue « Okay ») ===")
    for name in ref_one:
        p = os.path.join(CAP, name)
        if not os.path.isfile(p):
            continue
        bgr = cv2.imread(p)
        btns = find_popup_buttons(bgr)
        check("%s : popup detecte (1 bouton)" % name, len(btns) == 1,
              "(trouve %d)" % len(btns))
        if btns:
            b = choose_confirm_button(btns)
            print("       bouton choisi : centre=(%d,%d)" % (b[4], b[5]))

    print()
    print("=== 2) Deux boutons (« Yes » / « No ») ===")
    for name in ref_two:
        p = os.path.join(CAP, name)
        if not os.path.isfile(p):
            continue
        bgr = cv2.imread(p)
        btns = find_popup_buttons(bgr)
        check("%s : 2 boutons detectes" % name, len(btns) == 2,
              "(trouve %d)" % len(btns))
        if len(btns) == 2:
            b = choose_confirm_button(btns)
            gauche = min(btns, key=lambda x: x[4])
            check("%s : on clique « Yes » (gauche)" % name,
                  b[4] == gauche[4],
                  "(choisi x=%d, gauche x=%d)" % (b[4], gauche[4]))
            # verifie que le bouton choisi est bien celui de gauche, et que
            # l'autre est bien a droite du cadre.
            print("       gauche=(%d,%d)  droite=(%d,%d)" %
                  (btns[0][4], btns[0][5], btns[1][4], btns[1][5]))

    print()
    print("=== 3) Pas de faux positif sur les frames sans popup ===")
    # On prend toutes les frames SAUVEGARDEES hors des references popup :
    # un popup bloque la fenetre, donc seules les frames de snapshots de
    # 'bite'/'dip'/'miss' peuvent en contenir.
    suspects = [f for f in files
                 if os.path.basename(f) not in ref_one + ref_two]
    step = max(1, len(suspects) // 400)
    false_pos = []
    n = 0
    for p in suspects[::step]:
        bgr = cv2.imread(p)
        if bgr is None:
            continue
        n += 1
        btns = popup_confirm_buttons(bgr)
        if btns:
            false_pos.append((os.path.basename(p), btns))
    check("aucun faux positif sur %d frames" % n, not false_pos,
          "('%s')" % false_pos[:3])

    print()
    print("=== 4) Le menu « Options » du jeu n'est PAS un popup ===")
    # Capture 20261016-155650 : menu Options (6 boutons rouges empiles).
    for name in ("20261016-155650-miss.png", "20261017-192724-miss.png"):
        p = os.path.join(CAP, name)
        if not os.path.isfile(p):
            continue
        bgr = cv2.imread(p)
        raw = find_popup_buttons(bgr)
        conf = popup_confirm_buttons(bgr)
        check("%s : menu Options rejete (%d boutons bruts, 0 confirmation)" %
              (name, len(raw)), not conf, "(confirme %d)" % len(conf))

    print()
    print("=== 5) L'invitation de groupe n'est JAMAIS confirmee ===")
    # Capture 20261015-214218 : « Graydiggan invites you to a group »
    # -> boutons [Accept] [Decline]. Cliquer Accept serait une action
    # non voulue : le bot doit l'ignorer.
    p = os.path.join(CAP, "20261015-214218-miss.png")
    if os.path.isfile(p):
        bgr = cv2.imread(p)
        raw = find_popup_buttons(bgr)
        conf = popup_confirm_buttons(bgr)
        check("invitation de groupe : 2 boutons bruts detectes", len(raw) == 2,
              "(trouve %d)" % len(raw))
        check("invitation de groupe : AUCUNE confirmation (texte long)",
              not conf, "(confirme %d)" % len(conf))

    print()
    print("%d echec(s)" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
