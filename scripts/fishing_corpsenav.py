"""Navigation du fantome vers le corps, EN MESURE.

Trois grandeurs, trois sources, aucun postulat :

  * DIRECTION FRAICHE : vecteur crane->joueur lu sur la carte du monde
    (gabarits, score 1,0 mesure).  Repere = celui de la carte.
  * ORIENTATION DU PERSONNAGE : fleche de la minicarte, repere NORD
    (la minicarte est verrouillee au nord).
  * REFERENCE NATIVE : C_Map / C_DeathInfo via l'addon (repere NORD,
    position du corps FIXE).  Elle se rafraichit a chaque franchissement de
    zone (le module republie sur PLAYER_ENTERING_WORLD).

La carte n'etant pas verrouillee au nord, on ne MELANGE JAMAIS les deux
referentiels : on utilise la carte pour la direction immediate, et la
reference native (des qu'elle est fraiche) pour recaler l'orientation.

Progression : distance en pixels de carte entre crane et joueur, ET
distance native quand elle est disponible.
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
import fishing_mapread as FMR  # noqa: E402
from fishing_process import as_bgr  # noqa: E402

KEY_FORWARD = 0x5A      # Z = MOVEFORWARD (cache du personnage)
KEY_TURN_LEFT = 0x51    # Q = TURNLEFT
KEY_TURN_RIGHT = 0x45   # E = TURNRIGHT (defaut client)
KEY_MAP = 0x4D


def _n180(a):
    return (a + 180.0) % 360.0 - 180.0


class CorpsePilot(object):
    """Pilote le fantome vers le corps, en verifiant chaque pas."""

    def __init__(self, executor, log=None, config=None):
        self.ex = executor
        self.log = log or (lambda *a, **k: None)
        self.cfg = dict(config or {})
        self.map_open = False
        self.turn_sign = None
        self.native = None          # derniere reference native lue
        self.native_stamp = 0.0
        self.last_distance = None
        self.best_distance = None
        self.turn_per_tap = 18.0    # rotation empirique d'un appui

    # -- primitives ------------------------------------------------------
    def _kd(self, vk):
        import ctypes
        f = 0x00000001 | (vk << 16) | (1 << 30)
        ctypes.windll.user32.SendMessageW(self.ex.actions._win(), 0x0100, vk, f)

    def _ku(self, vk):
        import ctypes
        f = 0x00000001 | (vk << 16) | (1 << 31)
        ctypes.windll.user32.SendMessageW(self.ex.actions._win(), 0x0101, vk, f)

    def tap(self, vk, hold=0.12):
        self._kd(vk)
        time.sleep(hold)
        self._ku(vk)
        time.sleep(0.2)

    def forward(self, seconds=1.2):
        self._kd(KEY_FORWARD)
        time.sleep(seconds)
        self._ku(KEY_FORWARD)
        time.sleep(0.35)

    # -- carte -----------------------------------------------------------
    def _map_toggle(self, want_open):
        for _ in range(3):
            img = self.ex.screen.grab(force=True)
            if img is None:
                return
            from fishing_map import map_is_open
            if map_is_open(as_bgr(img)) == want_open:
                return
            self.ex.actions.key("M")
            time.sleep(float(self.cfg.get("mapWait", 2.6)))

    def read_map(self):
        """Etat sur la carte : (bearing, distance_px) ou None."""
        self._map_toggle(True)
        img = self.ex.screen.grab(force=True)
        self._map_toggle(False)
        if img is None:
            return None
        corps, joueur, dbg = FMR.read_map_markers_tpl(as_bgr(img))
        if not corps or not joueur:
            self.log("MAP_MARKERS_MISSING", **(dbg or {}))
            return None
        bearing, dist = FMR.direction_to_corpse(corps, joueur)
        if bearing is None:
            return None
        return {"bearingDeg": bearing, "distancePx": dist,
                "corpse": corps, "player": joueur, "debug": dbg}

    # -- cap du personnage (minicarte, repere NORD) -----------------------
    def read_heading(self):
        try:
            from fishing_minimap import MinimapGeometry, player_facing
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

    # -- reference native ------------------------------------------------
    def read_native(self, wtf_path):
        """Reference native (repere NORD, corps fixe). Fraiche ? on le voit
        grace au decalage du playerMap."""
        out = FD.read_wtf_payload(wtf_path)
        if not out or "playerMap" not in out or "corpseMap" not in out:
            return None
        data = {"playerMap": out["playerMap"], "corpseMap": out["corpseMap"],
                "mapID": out.get("mapID"), "worldX": out.get("worldX"),
                "worldY": out.get("worldY")}
        dx = data["corpseMap"][0] - data["playerMap"][0]
        dy = data["corpseMap"][1] - data["playerMap"][1]
        data["bearingDeg"] = math.degrees(math.atan2(-dy, dx))
        data["distance"] = math.hypot(dx, dy)
        fresh = (self.native is None
                 or data["playerMap"] != self.native["playerMap"])
        data["fresh"] = fresh
        if fresh:
            self.native = data
            self.native_stamp = time.time()
        return data

    # -- calibration du signe de rotation (APPRENTISSAGE) ----------------
    def calibrate_turn(self):
        if self.turn_sign is not None:
            return self.turn_sign
        h0 = self.read_heading()
        if h0 is None:
            return None
        self.tap(KEY_TURN_LEFT, 0.15)
        time.sleep(0.5)
        h1 = self.read_heading()
        if h1 is None:
            return None
        delta = _n180(h1 - h0)
        self.turn_sign = 1 if delta > 0 else -1
        self.turn_per_tap = max(6.0, min(60.0, abs(delta)))
        self.log("TURN_CALIBRATED", deltaDeg=round(delta, 1),
                 sign=self.turn_sign, perTap=round(self.turn_per_tap, 1))
        return self.turn_sign

    # -- un pas ------------------------------------------------------------
    def step(self, state, max_turns=10):
        """Tourne vers la direction visee puis avance.

        La direction visee est celle de la CARTE (repere carte).  Pour la
        traduire en rotation reelle, on utilise le fait que le personnage
        se deplace DANS LE REPERE CARTE : l'ecart entre la direction visee
        et la direction ACTUELLE du personnage (lue sur la carte, elle aussi)
        donne la rotation a effectuer.  On mesure la direction actuelle du
        personnage par le vecteur joueur->corps sur la carte (le
        personnage etant entre le crane et ... hmm).
        """
        return None

    # -- boucle ------------------------------------------------------------
    def approach(self, wtf_path, max_steps=30, on_step=None):
        state = self.read_map()
        if state is None:
            return {"ok": False, "reason": "marqueurs de carte illisibles"}
        native = self.read_native(wtf_path)
        self.log("CORPSE_REF", mapBearingDeg=round(state["bearingDeg"], 1),
                 distancePx=round(state["distancePx"], 1),
                 nativeBearingDeg=(round(native["bearingDeg"], 1)
                                   if native else None),
                 nativeDist=(round(native["distance"], 4) if native else None),
                 nativeFresh=bool(native and native.get("fresh")))
        best = state["distancePx"]
        for i in range(max_steps):
            state = self.read_map()
            if state is None:
                return {"ok": False, "reason": "carte illisible", "state": state}
            native = self.read_native(wtf_path)
            # direction visee, dans le repere de la carte
            target = state["bearingDeg"]
            # orientation actuelle du personnage, lue sur la carte : le
            # vecteur joueur->corps donne la direction vers le corps, pas
            # l'orientation.  On utilise donc l'ecart entre la direction
            # carte et la direction NATIVE (repere nord) quand la
            # reference native est fraiche ; sinon on avance droit devant
            # en reevaluant a chaque pas.
            if native and native.get("fresh"):
                offset = _n180(native["bearingDeg"] - target)
                head = self.read_heading()
                if head is not None:
                    turn = _n180((target + offset) - head)
                else:
                    turn = 0.0
            else:
                turn = 0.0
            if self.turn_sign is None:
                self.calibrate_turn()
            if abs(turn) > 8.0 and self.turn_sign is not None:
                want_right = (turn > 0) == (self.turn_sign > 0)
                key = KEY_TURN_RIGHT if want_right else KEY_TURN_LEFT
                taps = min(max_turns, int(abs(turn) / self.turn_per_tap) + 1)
                for _ in range(taps):
                    self.tap(key, 0.12)
            before = state["distancePx"]
            self.forward(float(self.cfg.get("forwardSeconds", 1.3)))
            after = self.read_map()
            if after is None:
                return {"ok": False, "reason": "carte illisible apres le pas"}
            d1 = after["distancePx"]
            closer = d1 < before
            if closer:
                best = min(best, d1)
            self.log("CORPSE_STEP", step=i, before=round(before, 1),
                     after=round(d1, 1), closer=bool(closer),
                     turn=round(turn, 1))
            if on_step:
                on_step(i, after, closer)
            if d1 <= float(self.cfg.get("arrivePx", 16.0)):
                return {"ok": True, "state": after, "distancePx": d1}
        return {"ok": False, "reason": "budget de pas epuise", "best": best}
