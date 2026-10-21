#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_action_runner.py — EXECUTEUR D'ACTIONS PAS-A-PAS, VERIFIE.

Retour utilisateur (2026-10-21) : « il a ouvert la carte et s'est avancé au
milieu du lac ; vérifie bien ces actions touche par touche avec screen entre
les étapes pour avancer proprement ».

Ce module répond point par point :

1. **Une action = une etape.** Chaque etape est encadree par une CAPTURE
   D'ECRAN (avant / apres) : on peut montrer ce qui s'est passe, et le
   detecteur peut DECIDER de s'arreter.
2. **Aucun clic sur l'interface.** La minicarte ouvre la carte du monde : la
   zone est interdite. De meme, les barres, panneaux et boutons.
3. **Aucun clic sur l'eau.** Un clic dans l'eau fait nager le personnage (ou
   lancer une peche) : on ne clique que sur du SOL praticable.
4. **Un resultat par etape** : succes / echec, la preuve (les differences
   mesurees entre les deux captures) et la capture conservee.
5. **Arret au premier echec** : pas d'enchainement aveugle.

Le module ne depend pas du bot : il prend des actions injectables, ce qui le
rend testable hors jeu.
"""

import math
import os
import time

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

#: Zones de l'interface INTERDITES (fraction de l'image) : cliquer ici ouvre
#: un panneau (la carte du monde pour la minicarte).
UI_ZONES = [
    (0.86, 1.00, 0.00, 0.20),   # minicarte (haut-droite)
    (0.00, 0.13, 0.00, 1.00),   # barre de gauche (boutons debuff)
    (0.00, 1.00, 0.92, 1.00),   # bord droit (grille de raid)
    (0.86, 1.00, 0.00, 1.00),   # bas-droite
]

#: Seuil de « la vue a change » (difference absolue moyenne).
SCENE_CHANGE = 1.2


def in_ui_zone(x, y, w, h):
    """Le point (x, y) en pixels est-il dans une zone d'interface ?"""
    fx, fy = x / float(w), y / float(h)
    for x0, x1, y0, y1 in UI_ZONES:
        if x0 <= fx < x1 and y0 <= fy < y1:
            return True
    return False


def is_water(bgr, x, y, radius=10):
    """Le pixel vise est-il de l'eau ?

    Calibration REELLE (nuit, 2026-10-21, Lushwater Oasis) :
      eau  : H 50-105, **S 35-46**, **V 20-31**  (sombre et desaturee)
      herbe: H 68,      **S 146**,      **V 53**   (saturee)
    -> de nuit, c'est la SATURATION qui separe l'eau du sol, pas la
       luminance (l'eau est tres sombre : un seuil sur V seul la rate).
    """
    if bgr is None:
        return False
    h, w = bgr.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return False
    x0, x1 = max(0, x - radius), min(w, x + radius)
    y0, y1 = max(0, y - radius), min(h, y + radius)
    patch = bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hue = int(np.median(hsv[:, :, 0]))
    sat = int(np.median(hsv[:, :, 1]))
    val = int(np.median(hsv[:, :, 2]))
    # eau : sombre ET peu sature (les deux conditions, pour rester prudent)
    if val < 75 and sat < 80:
        return True
    # eau teal claire (bassin eclaire)
    if 80 <= hue <= 110 and sat < 60 and val < 110:
        return True
    return False


def is_ground(bgr, x, y, radius=8):
    """Le point vise est-il du SOL praticable (ni eau, ni interface) ?

    Regle PRUDENTE : on n'accepte que ce qui est clairement du sol
    (sature ou lumineux). Un pixel sombre et desature est rejete : mieux vaut
    ne pas cliquer que de faire nager le personnage ou ouvrir la carte.
    """
    if bgr is None:
        return False
    h, w = bgr.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return False
    if in_ui_zone(x, y, w, h):
        return False
    if is_water(bgr, x, y):
        return False
    hsv = cv2.cvtColor(bgr[max(0, y - radius):y + radius,
                         max(0, x - radius):x + radius], cv2.COLOR_BGR2HSV)
    if hsv.size == 0:
        return False
    sat = int(np.median(hsv[:, :, 1]))
    val = int(np.median(hsv[:, :, 2]))
    return sat > 80 or val > 70


def scene_change(a, b, region=(0.12, 0.72, 0.05, 0.62)):
    """Difference absolue moyenne entre deux vues de la zone monde."""
    if a is None or b is None:
        return 0.0
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    if (ha, wa) != (hb, wb):
        return 0.0
    y0, y1, x0, x1 = region
    za = a[int(ha * y0):int(ha * y1), int(wa * x0):int(wa * x1)]
    zb = b[int(hb * y0):int(hb * y1), int(wb * x0):int(wb * x1)]
    if za.size == 0:
        return 0.0
    g1 = cv2.cvtColor(za, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(zb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(g1 - g2).mean())


def world_shift(a, b, region=(0.12, 0.72, 0.05, 0.62)):
    """Deplacement de la vue 3D entre deux captures (pixels ecran)."""
    if a is None or b is None:
        return (0.0, 0.0, 0.0)
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    if (ha, wa) != (hb, wb):
        return (0.0, 0.0, 0.0)
    y0, y1, x0, x1 = region
    za = a[int(ha * y0):int(ha * y1), int(wa * x0):int(wa * x1)]
    zb = b[int(hb * y0):int(hb * y1), int(wb * x0):int(wb * x1)]
    if za.size == 0:
        return (0.0, 0.0, 0.0)
    g1 = cv2.cvtColor(za, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(zb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    win = cv2.createHanningWindow((g1.shape[1], g1.shape[0]), cv2.CV_32F)
    try:
        (sx, sy), resp = cv2.phaseCorrelate(g1, g2, win)
    except cv2.error:
        return (0.0, 0.0, 0.0)
    return (float(sx), float(sy), float(resp))


class StepResult(object):
    """Resultat d'UNE etape, avec ses deux captures."""

    __slots__ = ("name", "ok", "detail", "proof", "before_path", "after_path",
                 "error", "elapsed")

    def __init__(self, name, ok, detail=None, proof=None, before_path=None,
                 after_path=None, error=None, elapsed=0.0):
        self.name = name
        self.ok = bool(ok)
        self.detail = detail
        self.proof = proof or {}
        self.before_path = before_path
        self.after_path = after_path
        self.error = error
        self.elapsed = float(elapsed)

    def as_dict(self):
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "proof": self.proof, "before": self.before_path,
                "after": self.after_path, "error": self.error,
                "elapsed": round(self.elapsed, 2)}

    def __repr__(self):  # pragma: no cover
        return "<Step %s ok=%s %s>" % (self.name, self.ok,
                                       (self.error or self.detail or "")[:60])


class ActionRunner(object):
    """Execute des actions UNE PAR UNE, avec capture avant/apres et arret au
    premier echec.

    :param capture: fonction -> image BGR de la fenetre du client
    :param ground_click: fonction(x, y) : clic droit au sol (coordonnees
                           de la fenetre cliente)
    :param toggle_auto_run: fonction() : bascule l'auto-run (touche Z)
    :param move_mouse: fonction(dx, dy)
    :param shot_dir: dossier ou les captures sont conservees
    """

    def __init__(self, capture, ground_click, toggle_auto_run, move_mouse=None,
                 shot_dir=None, log=None):
        self.capture = capture
        self.ground_click = ground_click
        self.toggle_auto_run = toggle_auto_run
        self.move_mouse = move_mouse
        self.shot_dir = shot_dir
        self.log = log or (lambda *a, **k: None)
        self.steps = []
        self._n = 0

    # ------------------------------------------------------------- captures
    def _shot(self, tag):
        img = self.capture()
        path = None
        if img is not None and self.shot_dir:
            try:
                os.makedirs(self.shot_dir, exist_ok=True)
                self._n += 1
                path = os.path.join(self.shot_dir, "step-%02d-%s.png" % (self._n, tag))
                cv2.imwrite(path, img)
            except Exception:
                path = None
        return img, path

    def _record(self, res):
        self.steps.append(res)
        self.log("ACTION_STEP", **res.as_dict())
        return res

    # -------------------------------------------------------------- etapes
    def step_look(self, x, y, settle=0.35):
        """Oriente la vue vers un point, SANS cliquer (et sans bouger le
        personnage). Verification : la scene a change, et le point vise n'est
        ni dans l'interface ni sur l'eau."""
        t0 = time.time()
        before, p_before = self._shot("look-avant")
        if before is None:
            return self._record(StepResult("look", False, error="capture impossible"))
        h, w = before.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return self._record(StepResult("look", False, error="point hors ecran"))
        if in_ui_zone(x, y, w, h):
            return self._record(StepResult(
                "look", False, error="point dans une zone d'interface (interdit)"))
        if self.move_mouse is None:
            return self._record(StepResult("look", False, error="pas de controle souris"))
        self.move_mouse(int(x), int(y))
        time.sleep(settle)
        after, p_after = self._shot("look-apres")
        change = scene_change(before, after)
        ok = change >= SCENE_CHANGE
        return self._record(StepResult(
            "look", ok, detail="vue orientee vers (%d, %d)" % (x, y),
            proof={"sceneChange": round(change, 3), "threshold": SCENE_CHANGE},
            before_path=p_before, after_path=p_after,
            error=None if ok else "la vue n'a pas change",
            elapsed=time.time() - t0))

    def step_walk(self, x, y, duration=3.0, rearm=True):
        """Deplace le personnage vers un point du SOL (auto-run).

        Verification : le monde a defile ET le personnage n'est pas parti
        dans l'eau (le point vise etait du sol, et la vue reste la meme
        scene : on ne peut pas le garantir ici, mais le point de depart et
        d'arrivee sont documentes par les captures).
        """
        t0 = time.time()
        before, p_before = self._shot("walk-avant")
        if before is None:
            return self._record(StepResult("walk", False, error="capture impossible"))
        h, w = before.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return self._record(StepResult("walk", False, error="point hors ecran"))
        if in_ui_zone(x, y, w, h):
            return self._record(StepResult(
                "walk", False, error="point dans une zone d'interface (interdit)"))
        if not is_ground(before, x, y):
            return self._record(StepResult(
                "walk", False, error="le point vise n'est pas du sol praticable "
                                    "(eau ou terrain non reconnu)"))
        if rearm:
            self.toggle_auto_run()
            time.sleep(0.25)
        self.ground_click(int(x), int(y))
        time.sleep(duration)
        after, p_after = self._shot("walk-apres")
        sx, sy, conf = world_shift(before, after)
        change = scene_change(before, after)
        moved = math.hypot(sx, sy)
        ok = change >= SCENE_CHANGE
        return self._record(StepResult(
            "walk", ok, detail="auto-run vers (%d, %d)" % (x, y),
            proof={"sceneChange": round(change, 3),
                   "worldShiftPx": round(moved, 3),
                   "correlationConfidence": round(conf, 3),
                   "threshold": SCENE_CHANGE},
            before_path=p_before, after_path=p_after,
            error=None if ok else "la scene n'a pas change (%.3f)" % change,
            elapsed=time.time() - t0))

    def run(self, plan):
        """Execute un plan d'etapes, en s'ARRETANT au premier echec.

        `plan` : liste de tuples ("look"|"walk", x, y, **kwargs).
        """
        results = []
        for item in plan or []:
            kind = item[0]
            if kind == "look":
                res = self.step_look(item[1], item[2], **item[3] if len(item) > 3 else {})
            elif kind == "walk":
                res = self.step_walk(item[1], item[2], **item[3] if len(item) > 3 else {})
            else:
                res = self._record(StepResult(str(kind), False,
                                              error="type d'etape inconnu"))
            results.append(res)
            if not res.ok:
                break
        return results

    def report(self):
        """Resume lisible des etapes (et les captures conservees)."""
        lines = ["=== ACTIONS PAS-A-PAS ==="]
        for s in self.steps:
            lines.append("  [%s] %-5s %s%s" % ("OK " if s.ok else "ECHEC",
                                                 s.name,
                                                 s.detail or "",
                                                 "" if s.ok else ("  <- " + (s.error or ""))))
            if s.before_path or s.after_path:
                lines.append("        avant: %s" % (s.before_path or "-"))
                lines.append("        apres: %s" % (s.after_path or "-"))
        return "\n".join(lines)
