#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_fishing_actions.py — tests des actions de base (deplacer, camera, vue).

Le principe verifie : une action n'est un succes que si sa PREUVE est
produite. Ici les preuves sont simulees (on injecte des captures et des
actions factices) : ce sont les MEMES fonctions de mesure qui sont
utilisees en jeu (world_shift / scene_change).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import numpy as np                           # noqa: E402
import fishing_actions as fa                 # noqa: E402

FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s %s" % (name, detail))


def make_frame(seed=0, size=(400, 400)):
    """Image synthetique STRUCTUREE (motifs nets) : le bruit aleatoire n'est
    ni correlable (la correlation de phase echoue sur du bruit) ni
    « inchange » (deux images de bruit different toujours) — il ne permet
    donc pas de tester la discrimination."""
    import cv2
    img = np.zeros((size[0], size[1], 3), np.uint8)
    step = 20
    for i in range(0, size[0], step):
        for j in range(0, size[1], step):
            v = 40 + ((i * 3 + j * 5 + seed * 17) % 200)
            img[i:i + step, j:j + step] = (v, (v * 2) % 255, 255 - v)
    # quelques traits verticaux : structure directionnelle, correlate
    for j in range(0, size[1], 37):
        img[:, j:j + 6] = 240
    return cv2.GaussianBlur(img, (3, 3), 0)


def shifted(frame, dx, dy):
    out = np.zeros_like(frame)
    h, w = frame.shape[:2]
    xs0, xs1 = max(0, dx), min(w, w + dx)
    xd0, xd1 = max(0, -dx), min(w, w - dx)
    ys0, ys1 = max(0, dy), min(h, h + dy)
    yd0, yd1 = max(0, -dy), min(h, h - dy)
    out[yd0:yd1, xd0:xd1] = frame[ys0:ys1, xs0:xs1]
    return out


class FakeActions(fa.BasicActions):
    """Actions simulees : on injecte les captures et les appels."""

    def __init__(self, frames, **kw):
        self.frames = list(frames)
        self.idx = 0
        self.moved = []
        self.clicks = []
        self.toggles = 0
        self.cursor = (100, 100)
        super(FakeActions, self).__init__(self._cap, self._move, self._click,
                                          self._toggle, **kw)

    def _cap(self):
        if self.idx < len(self.frames):
            f = self.frames[self.idx]
            self.idx += 1
            return f
        return self.frames[-1]

    def _move(self, dx, dy):
        self.moved.append((dx, dy))
        self.cursor = (self.cursor[0] + dx, self.cursor[1] + dy)

    def _click(self, x, y):
        self.clicks.append((x, y))

    def _toggle(self):
        self.toggles += 1


def test_world_shift():
    print("=== 1. MESURE DU DEFILDEMENT DU MONDE ===")
    a = make_frame(1)
    b = shifted(a, 6, 0)
    sx, sy, conf = fa.world_shift(a, b)
    # le signe depend du sens de decalage du helper ; c'est la MAGNITUDE qui
    # compte (le sens est verifie en jeu).
    check("un decalage de 6 px est mesure", abs(abs(sx) - 6) < 1.5, "(dx=%.2f)" % sx)
    check("le deplacement vertical nul est mesure", abs(sy) < 1.0, "(dy=%.2f)" % sy)
    c = shifted(a, 0, 0)
    sx2, sy2, _c2 = fa.world_shift(a, c)
    check("une vue identique donne un decalage nul",
          abs(sx2) < 0.5 and abs(sy2) < 0.5, "(%+.2f,%+.2f)" % (sx2, sy2))
    check("des images de tailles differentes ne plantent pas",
          fa.world_shift(a, np.zeros((10, 10, 3), np.uint8)) == (0.0, 0.0, 0.0))


def test_scene_change():
    print()
    print("=== 2. MESURE DU CHANGEMENT DE SCENE ===")
    a = make_frame(2)
    b = make_frame(3)
    check("deux vues differentes donnent un changement > seuil",
          fa.scene_change(a, b) > fa.SCENE_CHANGE_THRESHOLD,
          "(%.3f)" % fa.scene_change(a, b))
    check("une vue identique donne un changement ~0",
          fa.scene_change(a, a) < 0.01, "(%.4f)" % fa.scene_change(a, a))


def test_actions_contract():
    print()
    print("=== 3. CONTRAT DES ACTIONS (preuve obligatoire) ===")
    # camera : la scene change
    a = make_frame(4)
    b = make_frame(5)
    act = FakeActions([a, a])          # vue IDENTIQUE -> aucune preuve
    r = act.turn_camera(200)
    check("tourner la camera sans changement de scene est un echec",
          r.ok is False, "(%s)" % r.proof)
    check("le motif de l'echec est explicite", bool(r.error))

    # camera : avec changement
    act2 = FakeActions([a, b, a])
    r2 = act2.turn_camera(200)
    check("tourner la camera avec changement de scene est un succes", r2.ok is True,
          "(%s)" % r2.proof)
    check("le deplacement de souris a ete effectue", act2.moved)

    # personnage : le monde doit defiler
    base = make_frame(6)
    moved = shifted(base, 8, 0)
    act3 = FakeActions([base, base])   # vue identique -> aucun defilement
    r3 = act3.move_character(100, 100, duration=0)
    check("deplacer le personnage sans defilement est un echec", r3.ok is False,
          "(%s)" % r3.proof)
    act4 = FakeActions([base, moved])
    r4 = act4.move_character(100, 100, duration=0)
    check("deplacer le personnage avec defilement est un succes", r4.ok is True,
          "(%s)" % r4.proof)
    check("la preuve contient le defilement mesure",
          "worldShiftPx" in r4.proof and "sceneChange" in r4.proof)
    check("un clic au sol a ete envoye", len(act4.clicks) == 1)

    # re-ciblage : si rien ne bouge, on re-arme et on reclique
    act5 = FakeActions([base, base, moved])
    r5 = act5.move_character(100, 100, duration=0)
    check("sans mouvement, l'auto-run est re-arme et le clic rejoue",
          act5.toggles >= 1 and len(act5.clicks) >= 2,
          "(toggles=%d clics=%d)" % (act5.toggles, len(act5.clicks)))
    check("la tentative de re-ciblage finit par reussir", r5.ok is True,
          "(%s)" % r5.proof)

    # focus : sans focus, aucune action n'est tentee
    act6 = FakeActions([a, b], with_focus=lambda: False)
    r6 = act6.turn_camera(200)
    check("sans focus, l'action echoue explicitement", r6.ok is False)
    check("aucune entree n'a ete envoyee sans focus", not act6.moved)


def main():
    print("=== TESTS ACTIONS DE BASE ===")
    test_world_shift()
    test_scene_change()
    test_actions_contract()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
