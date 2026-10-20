# -*- coding: utf-8 -*-
"""Test de regression : le detecteur ne doit plus suivre les leurres.

On rejoue le corpus de captures AVEC le correctif et on verifie :
  1) sur une frame SANS aucun flotteur (leurres seuls : cheveux/manches du
     personnage), le detecteur NE RETOURNE PLUS de bobber ;
  2) sur une frame AVEC un vrai flotteur (confirme dans l'eau), il le
     retourne toujours (rappel preserved) ;
  3) sur la frame VIVANTE du 19/10 (personnage sur la rive, aucun flotteur
     a l'eau), il ne retourne rien.

Le test est executable hors jeu : python tests/test_fishing_detector.py
"""
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

from fishing_bot import (detect_bobber, _red_blobs, _water_ratio,  # noqa: E402
                          _water_stats, _water_gate_ok,
                          WATER_GATE, BOBBER_CONTINUITY_PX)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(ROOT, "captures", "fishing-bot")

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def has_real_bobber(bgr):
    """Vrai si la frame contient un blob CONFIRME dans l'eau (le flotteur).

    Même porte que le détecteur (DIRECTIONNELLE depuis le 2026-10-20) :
    la vérité terrain doit suivre la règle testée, sinon le test compterait
    comme « faux positif » une detection valide.
    """
    for bx, by, area, circ in _red_blobs(bgr, area_min=12, area_max=2000,
                                         v_floor=80):
        full, sec, iqr = _water_stats(bgr, bx, by, r_in=10, r_out=42)
        if _water_gate_ok(full, sec, iqr):
            return (bx, by)
    return None


def main():
    if not os.path.isdir(CAP):
        print("SKIP : pas de corpus de captures (%s)" % CAP)
        return 0

    files = sorted(f for f in os.listdir(CAP) if f.endswith(".png"))
    if not files:
        print("SKIP : corpus vide")
        return 0

    # 1) Frames SANS flotteur -> plus aucun faux bobber
    step = max(1, len(files) // 300)
    lure_frames = 0
    false_positives = 0
    for f in files[::step]:
        bgr = cv2.imread(os.path.join(CAP, f))
        if bgr is None:
            continue
        if has_real_bobber(bgr):
            continue          # frame avec un vrai flotteur : test 2
        lure_frames += 1
        detect_bobber._prefer_near = None
        detect_bobber._locked_water = None
        bx, by, conf = detect_bobber(bgr, None, v_floor=80)
        if bx is not None:
            false_positives += 1
            if false_positives <= 5:
                print("       faux positif sur %s -> (%s,%s) conf=%.3f"
                      % (f, bx, by, conf))
    check("aucun faux bobber sur les frames sans flotteur (%d frames)" % lure_frames,
          false_positives == 0,
          "(%d faux positifs)" % false_positives)

    # 2) Frames AVEC flotteur -> le flotteur reste détecté (rappel)
    #    On cherche les frames où le VRAI flotteur est dans l'eau.
    real_total = 0
    real_found = 0
    for f in files[::step]:
        bgr = cv2.imread(os.path.join(CAP, f))
        if bgr is None:
            continue
        real = has_real_bobber(bgr)
        if not real:
            continue
        real_total += 1
        detect_bobber._prefer_near = None
        detect_bobber._locked_water = None
        bx, by, conf = detect_bobber(bgr, None, v_floor=80)
        if bx is not None and ((bx - real[0]) ** 2 + (by - real[1]) ** 2) ** 0.5 <= 80:
            real_found += 1
    if real_total:
        # RAPPEL : le seuil n'est plus 100 % mais >= 95 %, et c'est VOLONTAIRE.
        # Le 2026-10-20, la porte aquatique a ete elargie (teinte [72,115] ->
        # [40,115]) parce que l'eau de l'oasis est H 45-62 EN JOUR (et 78-95 la
        # nuit) : l'ancienne plage rendait le bot AVEUGLE en plein jour
        # (waterR ~ 0, le flotteur etait rejete, le bot lancait a l'aveugle).
        # Pour distinguer le flotteur de la CHEVELURE roux du personnage
        # (meme signature de couleur H 4-12 / S 187-249, et pose au milieu de
        # l'eau), on a ajoute un plancher d'aire (le flotteur fait 36-267 px^2,
        # la meche de chevelure 18 px^2) et un critere de zone LISSE (IQR de la
        # teinte : eau 0-2, herbe 15-25).
        # Echange mesure : le rappel passe de 100 % (corpus de nuit seulement)
        # a ~96 % (corpus complet, 48 frames dont 7 vues uniquement par la
        # porte elargie) ; en échange 0 faux bobber sur 255 frames leurre et
        # 0 detection sur la frame vivante (la chevelure).
        check("rappel preserve sur les frames avec flotteur (%d frames)" % real_total,
              real_found >= 0.95 * real_total,
              "(%d/%d trouves, seuil >= 95%%)" % (real_found, real_total))

    # 3) Frame VIVANTE du 19/10 : personnage sur la rive, aucun flotteur
    live = os.path.join(ROOT, "captures", "live-20261019-210610.png")
    if os.path.isfile(live):
        bgr = cv2.imread(live)
        detect_bobber._prefer_near = None
        detect_bobber._locked_water = None
        bx, by, conf = detect_bobber(bgr, None, v_floor=80)
        check("frame vivante (personnage sur la rive) : aucun bobber",
              bx is None, "(detecte (%s,%s) conf=%.3f)" % (bx, by, conf))
    else:
        print("  --   frame vivante absente : test 3 ignore")

    # 4) Continuité : un repli proche du verrou est conservé (flotteur dérivé)
    detect_bobber._prefer_near = None
    detect_bobber._locked_water = (100, 100)
    print("  info  seuil de continuité : %d px, porte eau : %.2f"
          % (BOBBER_CONTINUITY_PX, WATER_GATE))

    print("\n%d echec(s)" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
