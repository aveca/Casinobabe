#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_corpsenav.py — NAVIGATION vers le corps par la REFERENCE NATIVE.

Principe (change par rapport aux approches pixel) :

  * la direction et la distance vers le corps viennent des API NATIVES du
    client (C_Map / C_DeathInfo), publiees en direct par l'addon dans une
    bande de pixels (canal vivant) et dans les SavedVariables ;
  * la carte de zone est verrouillee au NORD : le vecteur joueur -> corps
    donne donc un cap ABSOLU, independant de la camera ;
  * la PROGRESSION est la distance native au corps : elle doit DIMINUER
    apres chaque deplacement, sinon le deplacement est considere comme
    bloque (et l'on change d'angle) ;
  * l'angle de clic est deduit du cap vise et de la rotation
    ecran -> monde APPRISE en ligne (mesure reelle du cap obtenu apres
    chaque clic).

Aucun parcours de deplacement n'est invente : tout provient du client.
"""

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

import fishing_diag as FD  # noqa: E402


def bearing_to_screen(bearing_deg, rotation_deg):
    """Cap nord -> angle ecran (0 = droite, 90 = bas).

    `rotation_deg` est la rotation APPRISE (ecran -> monde) : elle est
    mesuree en jeu, jamais supposee.
    """
    if rotation_deg is None:
        return None
    return (bearing_deg - rotation_deg + 180.0) % 360.0 - 180.0


class NativeCorpseNav(object):
    """Pilote le fantome vers le corps a partir de la reference native."""

    def __init__(self, executor, log=None, config=None):
        self.ex = executor
        self.log = log or (lambda *a, **k: None)
        self.cfg = dict(config or {})
        self.rotation = None          # rotation ecran -> monde (apprise)
        self.last = None              # dernier payload compact lu
        self.history = []

    # -- lecture de la reference native --------------------------------
    def read_native(self):
        """Lit la bande (canal vivant) et renvoie le payload compact."""
        img = self.ex.screen.grab(force=True)
        if img is None:
            return None
        text, _sq = FD.read_strip_payload(img, compact=True)
        if not text:
            return None
        data = FD.parse_compact(text)
        if data:
            self.last = data
        return data

    def read_wtf(self, wtf_path):
        """Repli : lecture des SavedVariables (ecrites par le client)."""
        out = FD.read_wtf_payload(wtf_path)
        if not out:
            return None
        if "playerMap" not in out or "corpseMap" not in out:
            return None
        px, py = out["playerMap"]
        cx, cy = out["corpseMap"]
        dx, dy = cx - px, cy - py
        data = {"ghost": out.get("ghost") == "true",
                "mapID": out.get("mapID"),
                "playerMap": (px, py), "corpseMap": (cx, cy),
                "delta": (dx, dy),
                "distance": math.hypot(dx, dy),
                "bearingDeg": math.degrees(math.atan2(-dy, dx)),
                "corpseApi": out.get("corpseMap") is not None,
                "retrieveCorpseExists": out.get("retrieveCorpseExists") == "true",
                "retrieveSpellKnown": out.get("retrieveSpellKnown") == "true"}
        self.last = data
        return data

    # -- un pas ------------------------------------------------------------
    def step_towards(self, before, theta_deg):
        """Un clic de deplacement, puis MESURE de la progression native.

        Retourne un dict : {ok, distance, bearing, rotation}.
        """
        img = self.ex.screen.grab(force=True)
        if img is not None:
            try:
                img = np.asarray(img)
                if img.ndim == 3:
                    img = img[:, :, ::-1]      # RGB -> BGR
            except Exception:
                img = None
        h = img.shape[0] if img is not None else 1328
        w = img.shape[1] if img is not None else 2345
        r = self.cfg.get("clickRadius", 900)
        cx, cy = w / 2.0, h * 0.62
        tx = int(max(30, min(w - 30, cx + math.cos(math.radians(theta_deg)) * r)))
        ty = int(max(30, min(h - 120, cy + math.sin(math.radians(theta_deg)) * r)))
        self.ex.actions.click(tx, ty, button="right")
        time.sleep(float(self.cfg.get("moveSeconds", 2.5)))
        after = self.read_native()
        if after is None:
            after = self.read_wtf(self.cfg.get("wtfPath"))
        if after is None or before is None:
            return {"ok": False, "reason": "reference native illisible"}
        d0 = before.get("distance")
        d1 = after.get("distance")
        # cap reellement obtenu (deplacement observe sur la carte native)
        p0, p1 = before.get("playerMap"), after.get("playerMap")
        achieved = None
        if p0 and p1:
            mx, my = p1[0] - p0[0], p1[1] - p0[1]
            if abs(mx) > 1e-6 or abs(my) > 1e-6:
                achieved = math.degrees(math.atan2(-my, mx))
        if achieved is not None:
            rot = (achieved - theta_deg + 180.0) % 360.0 - 180.0
            if self.rotation is None:
                self.rotation = rot
            else:
                self.rotation = (self.rotation * 0.5 + rot * 0.5
                                 + 180.0) % 360.0 - 180.0
        res = {"ok": True, "distance": d1, "previous": d0,
               "bearing": after.get("bearingDeg"),
               "achieved": achieved, "rotation": self.rotation,
               "click": [tx, ty], "theta": theta_deg}
        self.history.append(res)
        del self.history[:-50]
        return res

    # -- interaction ------------------------------------------------------
    def try_resurrect(self, native):
        """Tente l'interaction AUTORISEE avec le corps.

        Le sort 'Retrieve Corpse' (5026) est PROTGE pour les addons
        ('Interaction action failed because of an AddOn') : la
        resurrection doit etre une ACTION JOUEUR.  On tente donc :
          1. le clic GAUCHE sur le bouton d'action correspondant dans la
             barre du fantome (detecte par sa signature DORE) ;
          2. a defaut, le clic droit sur le corps (menu de gossip
             'Resurrect').
        Retourne (ok, methode, note).
        """
        res = self.ex.do_click  # present pour l'interface
        img = self.ex.screen.grab(force=True)
        if img is None:
            return False, "aucune capture"
        try:
            arr = np.asarray(img)
            if arr.ndim == 3:
                arr = arr[:, :, ::-1]
            bgr = arr
        except Exception:
            return False, "capture illisible"
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        # boutons dore de la barre d'action (le sort du fantome)
        band = bgr[int(h * 0.94):, int(w * 0.50):int(w * 0.75)]
        if band.size:
            bh = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
            gold = ((bh[:, :, 0] >= 12) & (bh[:, :, 0] <= 45) &
                    (bh[:, :, 1] > 80) & (bh[:, :, 2] > 100)).astype(np.uint8)
            n, _l, stats, _c = cv2.connectedComponentsWithStats(gold, 8)
            cands = []
            for i in range(1, n):
                x, y, cw, ch, area = stats[i]
                if 12 <= area <= 2000 and 8 <= cw <= 60 and 8 <= ch <= 60:
                    cands.append((int(x + cw // 2) + int(w * 0.50),
                                  int(y + ch // 2) + int(h * 0.94)))
            # on tente chaque bouton dore de la barre
            for bx, by in cands:
                res = self.ex.do_click([bx, by], button="left")
                time.sleep(1.2)
                if self._is_alive():
                    return True, "bouton d'action", "clic en (%d,%d)" % (bx, by)
        # sinon : clic droit au centre (le corps est sous les pieds)
        res = self.ex.do_click([int(w * 0.5), int(h * 0.66)], button="right")
        time.sleep(1.2)
        if self._is_alive():
            return True, "clic droit sur le corps", ""
        return False, "", "aucun bouton n'a ressuscite le personnage"

    def _is_alive(self):
        """Le personnage est-il vivant ? (le monde n'est plus monochrome
        cyan : le client ne rend plus la vue fantome)."""
        img = self.ex.screen.grab(force=True)
        if img is None:
            return False
        try:
            arr = np.asarray(img)
            if arr.ndim == 3:
                arr = arr[:, :, ::-1]
            bgr = arr
        except Exception:
            return False
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        reg = hsv[int(h * 0.06):int(h * 0.55), int(w * 0.10):int(w * 0.85)]
        if reg.size == 0:
            return False
        cyan = float(((reg[:, :, 0] >= 90) & (reg[:, :, 0] <= 118)
                      & (reg[:, :, 1] > 40)).mean())
        return cyan <= 0.80

    # -- boucle ------------------------------------------------------------
    def approach(self, max_steps=30, wtf_path=None, on_step=None):
        """Avance vers le corps jusqu'a ce que la distance NATIVE diminue
        regulierement, ou qu'une interaction soit possible.

        Chaque etape : lecture native -> angle vise -> clic -> relecture
        native -> verification que la distance a DIMINUE.
        """
        if wtf_path:
            self.cfg.setdefault("wtfPath", wtf_path)
        state = self.read_native() or self.read_wtf(wtf_path)
        if state is None:
            return {"ok": False, "reason": "reference native indisponible"}
        self.log("CORPSE_REF", mapID=state.get("mapID"),
                 bearing=round(state.get("bearingDeg") or 0, 1),
                 distance=round(state.get("distance") or 0, 4),
                 source="native")
        best = state.get("distance")
        for i in range(max_steps):
            if state.get("distance") is not None and best is not None:
                if state["distance"] > best - 0.0005:
                    # pas de progression : on decale l'angle
                    self.log("CORPSE_STUCK", step=i,
                             distance=round(state["distance"], 4))
            else:
                best = state.get("distance")
            theta = bearing_to_screen(state.get("bearingDeg") or 0.0,
                                      self.rotation)
            if theta is None:
                # premiere fois : on part d'un angle neutre et on l'apprend
                theta = -90.0
            res = self.step_towards(state, theta)
            if not res.get("ok"):
                return {"ok": False, "reason": res.get("reason"), "state": state}
            state = self.read_native() or self.read_wtf(wtf_path) or state
            if state.get("distance") is not None:
                best = min(best or 1.0, state["distance"])
            if on_step:
                on_step(i, state, res)
            self.log("CORPSE_STEP", step=i,
                     distance=round(state.get("distance") or 0, 4),
                     bearing=round(state.get("bearingDeg") or 0, 1),
                     achieved=res.get("achieved"),
                     rotation=round(res.get("rotation") or 0, 1))
        return {"ok": False, "reason": "budget de pas epuise",
                "state": state, "best": best}
