#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_ghost.py — Pilotage du FANTOME vers le corps, en MESURE.

DEUX SOURCES, UN SEUL REPÈRE (le NORD) :

  * la CARTE DU MONDE (fenêtre séparée, position fixe, verrouillée au
    nord) donne la DIRECTION vers le corps et la DISTANCE (progression) ;
    le marqueur du corps est le CRÂNE ORANGE (gabarit de silhouette,
    score 1,0 mesuré, indépendant du fond de carte) et le joueur est la
    FLÈCHE de la carte (position, score 0,99995 mesuré) ;
  * la MINICARTE (verrouillée au nord) donne le CAP du personnage :
    `player_facing` lit la pointe de la flèche du joueur.

La flèche de la CARTE ne donne PAS le cap (elle pointe toujours au
nord) : le cap vient donc de la minicarte — c'est mesuré, pas supposé.

VITESSE : la carte reste ouverte et épinglée (une lecture = une capture
+ 0,4 s de détection au lieu de ~6 s), et le vol (Z + W) dure plusieurs
secondes par pas.

FAITS MESURÉS EN JEU (TBC Anniversary, perso Colorabi, 2026-10-21) :
  * Z = MOVEFORWARD ; Z + W = VOL (le fantôme vole) ;
  * le clic droit au sol = 0 px (inopérant sur un fantôme) ;
  * les entrées n'atteignent le client QUE si la fenêtre du jeu a le
    focus (sinon 0,00 px) : on passe par focus_broker.focus_lease ;
  * E n'est pas une touche de rotation (elle est liée à une action) :
    on tourne donc à gauche, et l'amplitude réelle est MESURÉE.
"""

import ctypes
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

import fishing_mapread as FMR  # noqa: E402
from fishing_process import as_bgr  # noqa: E402

user32 = ctypes.windll.user32

KEY_FORWARD = 0x5A      # Z = MOVEFORWARD
KEY_JUMP = 0x57         # W : vole (avec Z)
KEY_TURN_LEFT = 0x51    # Q = TURNLEFT
KEY_TURN_RIGHT = 0x45   # E (non lie sur ce perso : ACTIONBUTTON5)

MAP_TITLE = "World Map"
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def _n180(a):
    return (a + 180.0) % 360.0 - 180.0


class GhostPilot(object):
    """Fait avancer le fantome vers le corps, en verifiant chaque pas."""

    def __init__(self, executor, log=None, config=None):
        self.ex = executor
        self.log = log or (lambda *a, **k: None)
        self.cfg = dict(config or {})
        self.hwnd = None
        self.map_open = False
        self.q_delta = None        # variation de cap (degres) par appui Q
        self.e_delta = None        # idem pour E
        self.heading = None        # cap courant deduit du deplacement
        self.moved = 0.0
        self.key_left = KEY_TURN_LEFT
        self.key_right = KEY_TURN_RIGHT
        self.last_player = None
        self.best = None
        self.mouse_deg_per_px = 0.30    # degres de cap par pixel de souris

    # -- focus (indispensable : sans lui, 0,00 px) --------------------------
    def _win(self):
        if self.hwnd is None:
            self.hwnd = self.ex.actions._win()
        return self.hwnd

    def focus(self):
        try:
            from focus_broker import focus_lease
        except Exception:                                  # pragma: no cover
            return True
        try:
            with focus_lease(self._win(), respect_user=False,
                             log=lambda *a, **k: None):
                return True
        except Exception:                                  # pragma: no cover
            return False

    def _with_focus(self, action):
        """Execute une action EN TENANT le bail de focus.

        Indispensable : le focus clavier n'appartient qu'a une fenetre et
        le client perd le focus des qu'il change (constat : un vol lance
        hors du bail ne deplace que 0,00 px).  Le bail doit donc etre
        tenu pendant TOUTE l'action, pas seulement avant.
        """
        try:
            from focus_broker import focus_lease
        except Exception:                                  # pragma: no cover
            action()
            return
        try:
            with focus_lease(self._win(), respect_user=False,
                             log=lambda *a, **k: None):
                action()
        except Exception:                                  # pragma: no cover
            action()

    # -- clavier -------------------------------------------------------------
    def _kd(self, vk):
        user32.SendMessageW(self._win(), 0x0100, vk,
                            0x00000001 | (vk << 16) | (1 << 30))

    def _ku(self, vk):
        user32.SendMessageW(self._win(), 0x0101, vk,
                            0x00000001 | (vk << 16) | (1 << 31))

    def tap(self, vk, n=1, hold=0.10):
        def action():
            for _ in range(max(1, n)):
                self._kd(vk)
                time.sleep(hold)
                self._ku(vk)
                time.sleep(0.14)
        self._with_focus(action)

    def fly(self, seconds=4.0):
        """Z + W : le fantome VOL vers l'avant (bail de focus tenu)."""
        def action():
            self._kd(KEY_FORWARD)
            self._kd(KEY_JUMP)
            time.sleep(seconds)
            self._ku(KEY_JUMP)
            self._ku(KEY_FORWARD)
            time.sleep(0.2)
        self._with_focus(action)

    # -- carte ---------------------------------------------------------------
    def _pin_map(self):
        m = user32.FindWindowW(None, MAP_TITLE)
        if m:
            user32.SetWindowPos(m, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        return m

    def ensure_map(self):
        from fishing_map import map_is_open
        for _ in range(3):
            img = self.ex.screen.grab(force=True)
            if img is None:
                return False
            if map_is_open(as_bgr(img)):
                self.map_open = True
                self._pin_map()
                return True
            self.focus()
            self.tap(0x4D, 1, 0.10)
            time.sleep(2.4)
        return False

    def read(self):
        """Une capture -> etat carte (corps, joueur, cap carte, distance).

        Le CAP du personnage n'est PAS lu sur la carte (la fleche de la
        carte pointe toujours au nord) : on le deduit de la direction du
        DEPLACEMENT mesure entre deux lectures (le fantome vole dans
        son axe de marche).  Deux reperes, un seul : le NORD.
        """
        img = self.ex.screen.grab(force=True)
        if img is None:
            return None
        bgr = as_bgr(img)
        st = FMR.map_state(bgr, last_player=self.last_player)
        if st is None:
            self.log("MAP_STATE_MISSING")
            return None
        prev = self.last_player
        self.last_player = st["player"]
        # cap deduit du deplacement mesure
        if prev is not None:
            dx = st["player"]["cx"] - prev["cx"]
            dy = st["player"]["cy"] - prev["cy"]
            if math.hypot(dx, dy) >= 3.0:
                self.heading = math.degrees(math.atan2(-dy, dx))
                self.moved = math.hypot(dx, dy)
        st["headingDeg"] = self.heading
        if self.heading is None:
            st["turnErrorDeg"] = None
        else:
            st["turnErrorDeg"] = _n180(st["bearingDeg"] - self.heading)
        return st

    # -- SOURIS (la seule maniere de tourner : la camera) --------------------
    # Le client lit la souris en RAW INPUT : une touche de rotation
    # (TURNLEFT) n'est pas traitee, mais la souris, elle, oriente la
    # camera, et le fantome suit la camera en vol.
    # Le curseur doit rester HORS du cadre de la carte (qui occupe
    # x 365..2213), sinon l'UI de la carte capte la souris.
    MOUSEEVENTF_MOVE = 0x0001
    SAFE_X = (5, 340)

    def _cursor(self):
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def _raw_move(self, dx):
        steps = max(1, int(abs(dx) / 20))
        for _ in range(steps):
            user32.mouse_event(self.MOUSEEVENTF_MOVE, int(dx / steps), 0, 0, 0)
            time.sleep(0.012)

    def _move_mouse(self, dx_total):
        """Deplace le curseur de dx_total px en restant dans la zone sure.

        Renvoie le deplacement EFFECTIF (celui fait dans la zone ou la
        camera reagit) : un retour de curseur fait sur la carte ne
        tourne rien et ne doit pas polluer l'apprentissage.
        """
        cur, cy = self._cursor()
        lo, hi = self.SAFE_X
        if not (lo <= cur <= hi):
            # le curseur est sur le cadre de la carte : on le ramene
            # d'abord (sans effet camera), puis on applique le mouvement
            cur = lo if cur < lo else hi
            self._raw_move(cur - self._cursor()[0])
        want = cur + int(dx_total)
        want = max(lo, min(hi, want))
        eff = want - cur
        if eff:
            self._raw_move(eff)
        return eff

    def _facing(self, bgr=None):
        """Cap du personnage lu sur la MINICARTE (repere nord).

        Retourne None si la minicarte n'est pas lisible (elle est masquee
        quand la carte du monde est ouverte).
        """
        try:
            from fishing_minimap import MinimapGeometry, player_facing
            if bgr is None:
                img = self.ex.screen.grab(force=True)
                if img is None:
                    return None
                bgr = as_bgr(img)
            geo = MinimapGeometry()
            geo.detect(bgr)
            crop = geo.crop(bgr)
            rad = player_facing(crop)
            return None if rad is None else math.degrees(rad)
        except Exception as exc:                        # pragma: no cover
            self.log("HEADING_ERROR", error=str(exc))
            return None

    # -- calibration (APPRENTISSAGE) ----------------------------------------
    def _probe_mouse(self, dx=-120):
        """Mesure la rotation (degres) produite par un deplacement de
        souris de dx px : c'est la sensibilite reelle de la camera."""
        self.read()                       # P0
        self.focus()
        self.fly(2.0)
        st = self.read()                  # P1 -> cap de la direction A
        if st is None or st["headingDeg"] is None:
            return None
        h0 = st["headingDeg"]
        self._move_mouse(dx)
        self.focus()
        self.fly(2.0)
        st2 = self.read()                 # P2 -> cap de la direction B
        if st2 is None or st2["headingDeg"] is None:
            return None
        return _n180(st2["headingDeg"] - h0)

    def calibrate_mouse(self):
        deg = self._probe_mouse()
        if deg is None or abs(deg) < 3.0:
            return None
        # -120 px de souris -> deg degres de cap
        self.mouse_deg_per_px = deg / -120.0
        self.log("MOUSE_CALIBRATED", degPerPx=round(self.mouse_deg_per_px, 4),
                 deltaDeg=round(deg, 1))
        return self.mouse_deg_per_px

    # -- correction d'orientation (SOURIS) -----------------------------------
    def steer(self, err_deg):
        """Oriente le personnage vers la direction visee.

        err_deg > 0 : il faut tourner a DROITE dans le repere nord, donc
        decaler la camera vers la GAUCHE (le cap diminue quand la camera
        tourne a gauche... mesure fait : -120 px de souris = -54 deg).
        """
        if err_deg is None or abs(err_deg) < float(self.cfg.get("aimTolDeg", 10.0)):
            return 0
        sens = self.mouse_deg_per_px
        if not sens:
            return 0
        dx = int(round(err_deg / sens))
        moved = self._move_mouse(dx)
        self.log("STEER", errDeg=round(err_deg, 1), dx=dx, moved=moved)
        return moved
    def _probe(self, vk, taps=5):
        """Mesure la rotation reelle produite par une touche."""
        self.read()                      # position de reference
        self.focus()
        self.fly(2.0)
        st = self.read()                 # cap deduit du deplacement
        if st is None or st["headingDeg"] is None:
            return None
        h0 = st["headingDeg"]
        self.tap(vk, taps)
        self.focus()
        self.fly(2.0)
        st2 = self.read()
        if st2 is None or st2["headingDeg"] is None:
            return None
        return {"delta": _n180(st2["headingDeg"] - h0), "n": taps}

    def calibrate(self):
        """Apprend par MESURE la rotation reelle par appui de Q et de E."""
        r = self._probe(KEY_TURN_LEFT)
        if r is not None:
            self.q_delta = r["delta"] / float(r["n"])
        r2 = self._probe(KEY_TURN_RIGHT)
        if r2 is not None:
            self.e_delta = r2["delta"] / float(r2["n"])
        self.log("TURN_CALIBRATED",
                 qDegParAppui=(None if self.q_delta is None
                               else round(self.q_delta, 2)),
                 eDegParAppui=(None if self.e_delta is None
                               else round(self.e_delta, 2)))
        return self.q_delta is not None or self.e_delta is not None

    # -- correction d'orientation --------------------------------------------
    def turn(self, err_deg):
        """Corrige l'orientation. err > 0 : il faut augmenter le cap
        (tourner a gauche dans le repere nord, 0 = est / +90 = nord)."""
        tol = float(self.cfg.get("aimTolDeg", 14.0))
        if err_deg is None or abs(err_deg) <= tol:
            return 0
        q = self.q_delta or 0.0
        e = self.e_delta or 0.0
        # on utilise en priorite la touche qui rapproche le plus vite
        options = []
        if abs(q) > 1.0:
            options.append((abs(q), self.key_left, q))
        if abs(e) > 1.0:
            options.append((abs(e), self.key_right, e))
        if not options:
            return 0
        options.sort(reverse=True)
        for _per, key, delta in options:
            if (err_deg > 0) == (delta > 0):
                n = max(1, min(14, int(round(abs(err_deg) / abs(delta)))))
                self.focus()
                self.tap(key, n)
                return n
        # aucune touche ne tourne dans le bon sens : on fait le tour par
        # l'autre sens avec la touche la plus rapide
        _per, key, delta = options[0]
        reste = 360.0 - abs(err_deg)
        n = max(1, min(22, int(round(reste / abs(delta)))))
        self.focus()
        self.tap(key, n)
        return n

    # -- correction d'orientation --------------------------------------------
    def turn(self, err_deg):
        """Corrige l'orientation. err > 0 : il faut augmenter le cap
        (tourner a gauche dans le repere nord, 0 = est / +90 = nord)."""
        tol = float(self.cfg.get("aimTolDeg", 12.0))
        if err_deg is None or abs(err_deg) <= tol:
            return 0
        q = self.q_delta or 0.0
        e = self.e_delta or 0.0
        # on preferne la touche qui rapproche le plus vite
        if abs(q) > 1.0:
            # Q augmente le cap si q > 0
            if (err_deg > 0) == (q > 0):
                n = max(1, min(12, int(round(err_deg / abs(q)))))
                self.focus()
                self.tap(self.key_left, n)
                return n
            # sinon on tente E
            if abs(e) > 1.0 and ((err_deg > 0) == (e > 0)):
                n = max(1, min(12, int(round(err_deg / abs(e)))))
                self.focus()
                self.tap(self.key_right, n)
                return n
            # E ne tourne pas : on fait le tour par la gauche
            n = max(1, min(20, int(round((360.0 - abs(err_deg)) / abs(q))))
                    if err_deg < 0 else
                    max(1, min(20, int(round(abs(err_deg) / abs(q))))))
            self.focus()
            self.tap(self.key_left, n)
            return n
        return 0

    # -- un pas ---------------------------------------------------------------
    def step(self, state, aim=True):
        """Oriente puis vole.  Renvoie le deplacement de souris reellement
        applique (pour apprendre la sensibilite en ligne)."""
        dx = 0
        if aim:
            err = state.get("turnErrorDeg")
            if err is not None and self.mouse_deg_per_px:
                want = int(round(err / self.mouse_deg_per_px))
                dx = self._move_mouse(want)
                self.log("STEER", errDeg=round(err, 1), want=want, moved=dx)
            elif err is not None:
                self.turn(err)
        # vol adaptatif : long quand on est loin, court quand on approche
        dist = state.get("distancePx") or 999.0
        far = float(self.cfg.get("flySeconds", 4.5))
        near = float(self.cfg.get("flySecondsNear", 1.2))
        span = float(self.cfg.get("nearSpanPx", 40.0))
        secs = far if dist > span else max(0.5, near * dist / span)
        self.focus()
        self.fly(secs)
        return {"ok": True, "dx": dx, "h0": state.get("headingDeg")}

    def _learn_mouse(self, dx, h0, h1):
        """Apprentissage en ligne de la sensibilite camera (degres/px)."""
        if not dx or h0 is None or h1 is None:
            return
        actual = _n180(h1 - h0)
        if abs(actual) < 2.0:
            return
        meas = actual / float(dx)
        old = self.mouse_deg_per_px or meas
        self.mouse_deg_per_px = 0.6 * old + 0.4 * meas
        self.log("MOUSE_LEARN", mesure=round(meas, 4),
                 retenu=round(self.mouse_deg_per_px, 4))

    # -- boucle ----------------------------------------------------------------
    def approach(self, max_steps=40, on_step=None):
        if not self.ensure_map():
            return {"ok": False, "reason": "carte impossible a ouvrir"}
        # Le calibrage n'est pas bloquant : la sensibilite de la camera
        # s'affine en ligne a chaque pas (voir _learn_mouse).
        if self.calibrate_mouse() is None:
            self.log("MOUSE_CALIB_FAILED", defaut=self.mouse_deg_per_px)
        best = None
        for i in range(max_steps):
            st = self.read()
            if st is None:
                return {"ok": False, "reason": "carte illisible"}
            d0 = st["distancePx"]
            if best is None:
                best = d0
            self.log("CORPSE_REF", step=i, distancePx=round(d0, 1),
                     bearingDeg=round(st["bearingDeg"], 1),
                     headingDeg=(None if st["headingDeg"] is None
                                 else round(st["headingDeg"], 1)),
                     turnErrorDeg=(None if st["turnErrorDeg"] is None
                                   else round(st["turnErrorDeg"], 1)))
            if d0 <= float(self.cfg.get("arrivePx", 12.0)):
                return {"ok": True, "state": st, "distancePx": d0}
            res = self.step(st)
            st2 = self.read()
            if st2 is None:
                return {"ok": False, "reason": "carte illisible apres le pas"}
            self._learn_mouse(res.get("dx"), res.get("h0"),
                              st2.get("headingDeg"))
            d1 = st2["distancePx"]
            closer = d1 < d0
            if closer:
                best = min(best, d1)
            else:
                # pas de progression : on tourne d'un angle fixe et on
                # retente (repli homing, aucune hypothese sur l'obstacle)
                self.log("CORPSE_NOT_CLOSER", step=i, d0=round(d0, 1),
                         d1=round(d1, 1))
                self.focus()
                self.tap(self.key_left, 3)
            if on_step:
                on_step(i, st2, closer)
            self.log("CORPSE_STEP", step=i, d0=round(d0, 1), d1=round(d1, 1),
                     closer=bool(closer))
        return {"ok": False, "reason": "budget de pas epuise", "best": best}
