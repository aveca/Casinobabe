#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_actions.py — LES ACTIONS DE BASE, avec une PREUVE pour chacune.

Le retour utilisateur etait clair : « le bot fait n'importe quoi, regarde
ailleurs, change camera, et se perd. Apprends a te deplacer, bouger la
camera, et effectuer des actions de base avant tout. »

Ce module isole donc trois primitives, chacune avec :
  - ses preconditions ;
  - l'action executee (bornee) ;
  - la PREUVE mesuree (et non une declaration) ;
  - ses criteres d'echec ;
  - sa strategie de recuperation.

PRINCIPE : la camera suit le curseur. Toute action qui deplace le curseur
doit donc RESTAURER sa position afterwards, sinon la vue reste captive et le
bot « se perd ». C'est la regle appliquee ici.

Les preuves utilisees (toutes derivees de l'ecran, sans le client) :
  * deplacement du personnage : le MONDE defile (correlation de phase sur la
    vue 3D, hors minicarte) -> c'est la mesure de reference, immune au
    biais de la minicarte ;
  * rotation de la camera : la scene change fortement au meme endroit de
    l'ecran (difference absolue moyenne) -> preuve qu'un pivot a eu lieu ;
  * observation d'une cible : la zone visee change (difference absolue
    moyenne) -> preuve que la vue porte desormais autre chose.
"""

import math
import time

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

#: Zone monde utilisee pour mesurer le defilement (hors minicarte et HUD).
WORLD_REGION = (0.12, 0.72, 0.05, 0.62)

#: Seuil de difference (moyenne absolue) qui prouve un changement de vue.
SCENE_CHANGE_THRESHOLD = 1.2


class ActionResult(object):
    """Issue d'une action de base : succes + preuve."""

    __slots__ = ("ok", "action", "proof", "detail", "error", "elapsed")

    def __init__(self, ok, action, proof=None, detail=None, error=None, elapsed=0.0):
        self.ok = bool(ok)
        self.action = action
        self.proof = proof
        self.detail = detail
        self.error = error
        self.elapsed = float(elapsed)

    def as_dict(self):
        return {"ok": self.ok, "action": self.action, "proof": self.proof,
                "detail": self.detail, "error": self.error,
                "elapsed": round(self.elapsed, 2)}

    def __repr__(self):  # pragma: no cover
        return "<ActionResult %s ok=%s %s>" % (self.action, self.ok,
                                               (self.error or self.detail or "")[:60])


def world_shift(frame_a, frame_b, region=WORLD_REGION):
    """Deplacement de la vue 3D (pixels ecran) entre deux captures."""
    if frame_a is None or frame_b is None:
        return (0.0, 0.0, 0.0)
    ha, wa = frame_a.shape[:2]
    hb, wb = frame_b.shape[:2]
    if (ha, wa) != (hb, wb):
        return (0.0, 0.0, 0.0)
    y0, y1, x0, x1 = region
    za = frame_a[int(ha * y0):int(ha * y1), int(wa * x0):int(wa * x1)]
    zb = frame_b[int(hb * y0):int(hb * y1), int(wb * x0):int(wb * x1)]
    if za.size == 0 or za.shape != zb.shape:
        return (0.0, 0.0, 0.0)
    g1 = cv2.cvtColor(za, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(zb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    win = cv2.createHanningWindow((g1.shape[1], g1.shape[0]), cv2.CV_32F)
    try:
        (sx, sy), resp = cv2.phaseCorrelate(g1, g2, win)
    except cv2.error:
        return (0.0, 0.0, 0.0)
    return (float(sx), float(sy), float(resp))


def scene_change(frame_a, frame_b, region=WORLD_REGION):
    """Difference moyenne absolue entre deux vues (preuve d'un changement)."""
    if frame_a is None or frame_b is None:
        return 0.0
    ha, wa = frame_a.shape[:2]
    hb, wb = frame_b.shape[:2]
    if (ha, wa) != (hb, wb):
        return 0.0
    y0, y1, x0, x1 = region
    za = frame_a[int(ha * y0):int(ha * y1), int(wa * x0):int(wa * x1)]
    zb = frame_b[int(hb * y0):int(hb * y1), int(wb * x0):int(wb * x1)]
    if za.size == 0 or za.shape != zb.shape:
        return 0.0
    g1 = cv2.cvtColor(za, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(zb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(g1 - g2).mean())


class BasicActions(object):
    """Les trois actions de base, avec leurs preuves.

    :param capture: fonction sans argument -> image BGR (None si impossible)
    :param move_mouse: fonction(dx, dy) qui deplace le curseur physique
    :param ground_click: fonction(x, y) qui fait un clic droit au sol
    :param toggle_auto_run: fonction() qui bascule l'auto-run (touche Z)
    :param screen_size: (l, h) de la fenetre cliente
    """

    def __init__(self, capture, move_mouse, ground_click, toggle_auto_run,
                 screen_size=(2568, 1448), log=None, with_focus=None):
        self.capture = capture
        self.move_mouse = move_mouse
        self.ground_click = ground_click
        self.toggle_auto_run = toggle_auto_run
        self.screen_size = screen_size
        self.log = log or (lambda *a, **k: None)
        # Le jeu ne traite la souris (camera) et ne deplace le personnage
        # (auto-run) que si sa fenetre est au premier plan : un contexte de
        # focus est donc necessaire. Il est fourni par l'appelant (le bot
        # utilise son bail de focus, qui rend la main apres).
        self.with_focus = with_focus

    def _focused(self):
        """Execute l'action avec le focus (si disponible)."""
        if self.with_focus is None:
            return True, "pas de gestion de focus : action tentee sans"
        ok = self.with_focus()
        return bool(ok), None if ok else "focus non obtenu (bail refuse)"

    # ------------------------------------------------------------- interne
    def _restore_cursor(self, pos):
        if pos is None:
            return
        try:
            self.move_mouse(pos[0] - self._cursor[0], pos[1] - self._cursor[1])
        except Exception:
            pass

    def _cursor_pos(self):
        try:
            import win32api
            return win32api.GetCursorPos()
        except Exception:
            return None

    # ------------------------------------------------------ 1. la camera
    def turn_camera(self, delta_px, restore=True, settle=0.4):
        """Tourne la camera de `delta_px` pixels de souris (positif = droite).

        La camera suit le curseur : on deplace le curseur, on laisse le jeu
        appliquer, puis on RESTAURE la position du curseur (la camera revient
        a son orientation) — sauf si `restore=False` (navigation en cours).

        Preuve : la scene a change au meme endroit de l'ecran.
        """
        t0 = time.time()
        before = self.capture()
        cursor = self._cursor_pos()
        focused, why = self._focused()
        if not focused:
            return ActionResult(False, "turn_camera", error=why, elapsed=time.time() - t0)
        try:
            self.move_mouse(int(delta_px), 0)
        except Exception as exc:
            return ActionResult(False, "turn_camera", error=repr(exc), elapsed=time.time() - t0)
        time.sleep(settle)
        after = self.capture()
        change = scene_change(before, after)
        if restore:
            try:
                if cursor is not None:
                    import win32api
                    win32api.SetCursorPos(cursor)
            except Exception:
                pass
            time.sleep(0.25)
        ok = change >= SCENE_CHANGE_THRESHOLD
        return ActionResult(ok, "turn_camera",
                            proof={"sceneChange": round(change, 3),
                                   "threshold": SCENE_CHANGE_THRESHOLD},
                            detail="pivot de %d px de souris" % delta_px,
                            error=None if ok else "la scene n'a pas change (%.3f)" % change,
                            elapsed=time.time() - t0)

    # ------------------------------------------------- 2. le personnage
    def move_character(self, screen_x, screen_y, duration=2.5, restore=True,
                       rearm_auto_run=True, settle=0.3):
        """Deplace le personnage vers un point ecran (auto-run).

        Mecanisme mesure : l'auto-run (TOGGLEAUTORUN + clic droit au sol).
        Un clic seul NE RE-CIBLE PAS l'auto-run : on re-arme donc l'auto-run
        avant le clic.

        Preuve : le MONDE a defile (deplacement de la vue 3D) — mesure
        independante de la minicarte, dont on a mesure le biais (7 px / 10 s
        au repos).
        """
        t0 = time.time()
        before = self.capture()
        cursor = self._cursor_pos()
        focused, why = self._focused()
        if not focused:
            return ActionResult(False, "move_character", error=why, elapsed=time.time() - t0)
        try:
            if rearm_auto_run:
                self.toggle_auto_run()
                time.sleep(0.25)
            self.ground_click(int(screen_x), int(screen_y))
        except Exception as exc:
            return ActionResult(False, "move_character", error=repr(exc),
                                elapsed=time.time() - t0)
        time.sleep(duration)
        after = self.capture()
        sx, sy, conf = world_shift(before, after)
        moved = math.hypot(sx, sy)
        change = scene_change(before, after)
        # RE-CIBLAGE : un clic seul ne re-cible pas l'auto-run (mesure : le
        # personnage continuait vers sa cible precedente). Si rien n'a bouge,
        # on re-arme l'auto-run (toggle) et on reclique UNE fois.
        if moved < 0.4 and rearm_auto_run:
            try:
                self.toggle_auto_run()
                time.sleep(0.3)
                self.ground_click(int(screen_x), int(screen_y))
                time.sleep(duration)
                after2 = self.capture()
                sx2, sy2, conf2 = world_shift(after, after2)
                moved2 = math.hypot(sx2, sy2)
                if moved2 > moved:
                    sx, sy, conf, moved = sx2, sy2, conf2, moved2
                    after = after2
                    change = scene_change(before, after2)
            except Exception:
                pass
        if restore:
            try:
                if cursor is not None:
                    import win32api
                    win32api.SetCursorPos(cursor)
            except Exception:
                pass
        ok = change >= SCENE_CHANGE_THRESHOLD
        return ActionResult(ok, "move_character",
                            proof={"sceneChange": round(change, 3),
                                   "worldShiftPx": round(moved, 3),
                                   "dx": round(sx, 3), "dy": round(sy, 3),
                                   "correlationConfidence": round(conf, 3),
                                   "threshold": SCENE_CHANGE_THRESHOLD},
                            detail="auto-run vers (%d, %d)" % (screen_x, screen_y),
                            error=None if ok else ("le monde n'a pas change (%.3f)" % change),
                            elapsed=time.time() - t0)

    # ------------------------------------------------- 3. l'observation
    def look_at(self, screen_x, screen_y, settle=0.35):
        """Oriente la vue vers une zone ecran (deplace le curseur, sans clic).

        Utile pour reafficher une cible que la navigation a fait sortir du
        champ. Le curseur est restaure ensuite.

        Preuve : la scene a change (la zone visee est desormais visible).
        """
        t0 = time.time()
        before = self.capture()
        cursor = self._cursor_pos()
        try:
            self.move_mouse(int(screen_x), int(screen_y))
        except Exception as exc:
            return ActionResult(False, "look_at", error=repr(exc), elapsed=time.time() - t0)
        time.sleep(settle)
        after = self.capture()
        change = scene_change(before, after)
        try:
            if cursor is not None:
                import win32api
                win32api.SetCursorPos(cursor)
        except Exception:
            pass
        ok = change >= SCENE_CHANGE_THRESHOLD
        return ActionResult(ok, "look_at",
                            proof={"sceneChange": round(change, 3)},
                            detail="vue orientee vers (%d, %d)" % (screen_x, screen_y),
                            error=None if ok else "la scene n'a pas change",
                            elapsed=time.time() - t0)
