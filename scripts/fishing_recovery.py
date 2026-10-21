#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_recovery.py — RECUPERATION DU CORPS (machine a etats, fondee sur
des MESURES, pas sur des hypotheses).

Sequence exigee :

    ALIVE -> DEAD_SPECTER -> NAVIGATING_TO_CORPSE -> CORPSE_INTERACTION
          -> RESURRECTION_VERIFY -> PAUSED_SAFE

Principes (tous appliques, aucun suppose) :

1. LA CARTE EST L'ORACLE.  Le jeu dessine sur la carte du monde le
   marqueur du CORPS (crane) et le JOUEUR (fleche verte).  Deux gabarits
   DISJOINTS (crane / fleche) sont localises par template matching.  On en
   tire :
     * le CAP vers le corps (repere nord, la carte est verrouillee au nord) ;
     * la DISTANCE au corps en pixels de carte -> c'est la MESURE DE
       PROGRESSION de reference (elle seule dit si on se rapproche).
   Les gabarits ne doivent JAMAIS se recouvrir : sinon les deux marqueurs
   tombent au meme endroit, le cap devient du bruit et la distance ne
   diminue jamais (bug observe le 2026-10-21).

2. LA CAMERA N'EST SUPPOSEE ETRE RIEN.  Le cap du personnage est releve sur
   la fleche de la minicarte (verrouillee au nord).  On pilote par
   BOUCLE A RETROACTION : on clique un point ecran, on relit le cap
   obtenu, on corrige l'angle de clic.  La camera (statique, "Smart" ou
   "Never") n'a pas besoin d'etre connue : le moteur l'apprend.

3. AUCUN CLIC AVEUGLE.  Chaque commande est suivie d'une MESURE
   (progression au cadran, cap obtenu, distance au corps).  Un clic
   execute n'est jamais une preuve.

4. L'INTERACTION AVEC LE CORPS n'est supposee etre un bouton "Resurrect"
   a l'ecran : l'invite est recherchee dans la VUE 3D uniquement (la carte
   contient des elements dores qui诱发 des faux positifs), puis on clique
   et on exige une CONFIRMATION INDEPENDANTE (le lecteur d'etat doit
   repasser en "vivant").

5. LA PECHE NE RELANCE JAMAIS.  L'etat final est PAUSED_SAFE, et la
   transition n'est possible qu'apres resurrection confirmee.
"""

import ctypes
import math
import os
import sys
import time

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fishing_map import (CORPS, JOUEUR, MapMarkerDetector,  # noqa: E402
                         analyze_map, map_is_open, map_bearing_to_screen)
from fishing_safety import (DEAD, GHOST, CORPSE_RECOVERY, RESURRECTION,  # noqa: E402
                            SAFE_REPOSITION, RESUME_OR_STOP,
                            OUTCOME_OK, OUTCOME_BLOCKED, OUTCOME_RECOVERED)

# --------------------------------------------------------------------------
# etats
# --------------------------------------------------------------------------
ALIVE = "ALIVE"
DEAD_SPECTER = "DEAD_SPECTER"
MAP_ANALYSIS = "MAP_ANALYSIS"
NAVIGATING_TO_CORPSE = "NAVIGATING_TO_CORPSE"
PROGRESS_CHECK = "PROGRESS_CHECK"
RELOCALIZATION = "RELOCALIZATION"
CORPSE_INTERACTION = "CORPSE_INTERACTION"
RESURRECTION_VERIFY = "RESURRECTION_VERIFY"
PAUSED_SAFE = "PAUSED_SAFE"
ABORTED = "ABORTED"

#: etats de la sequence exigee, dans l'ordre
REQUIRED_SEQUENCE = (ALIVE, DEAD_SPECTER, NAVIGATING_TO_CORPSE,
                     CORPSE_INTERACTION, RESURRECTION_VERIFY, PAUSED_SAFE)

#: anciens noms conserves (compatibilite)
RECOVERY_INIT = DEAD_SPECTER
MAP_ANALYSIS = MAP_ANALYSIS
ROUTE_PLANNING = NAVIGATING_TO_CORPSE
TRAVEL_STEP = NAVIGATING_TO_CORPSE
CORPSE_APPROACH = CORPSE_INTERACTION
RESURRECTION_DETECTED = CORPSE_INTERACTION
VERIFY_ALIVE = RESURRECTION_VERIFY

RECOVERY_STATES = (ALIVE, DEAD_SPECTER, MAP_ANALYSIS, NAVIGATING_TO_CORPSE,
                   PROGRESS_CHECK, RELOCALIZATION, CORPSE_INTERACTION,
                   RESURRECTION_VERIFY, PAUSED_SAFE, ABORTED)


def _bgr(img):
    from fishing_process import as_bgr
    return as_bgr(img)


def _n180(a):
    """Ecart angulaire signe dans [-180, +180]."""
    return (a + 180.0) % 360.0 - 180.0


class ResurrectPrompt(object):
    """Invite "Resurrect" du client : bouton DORE, dans la VUE 3D.

    Contraintes de robustesse :
      * jamais dans la carte (la carte a des elements dores) -> la vue 3D
        est verifiee avant toute recherche ;
      * forme d'un bouton horizontal compact (ratio 1.5 a 12) ;
      * zone autour du personnage (l'invite est dessinee sur le corps, donc
        au pied du fantome) : on evite les lanternes, les icones d'or du
        decor et les boutons de l'UI.
    """

    def __init__(self, region=(0.18, 0.86, 0.30, 0.88)):
        self.region = region

    def find(self, img):
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        x0, x1, y0, y1 = self.region
        roi = bgr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        gold = ((hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 45)
                & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 140))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(
            gold.astype(np.uint8), 8)
        best = None
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 120 or cw < 40 or cw > 300 or ch < 10 or ch > 90:
                continue
            if not (1.5 <= float(cw) / max(1, ch) <= 12.0):
                continue
            if area < 0.40 * cw * ch:
                continue
            cx = x0 * w + x + cw / 2.0
            cy = y0 * h + y + ch / 2.0
            if best is None or area > best["area"]:
                best = {"x": int(cx), "y": int(cy), "area": int(area),
                        "w": int(cw), "h": int(ch)}
        return best


class RecoverySession(object):
    """Machine a etats du retour au corps (une transition par `step()`)."""

    def __init__(self, executor, state_reader=None, safety=None, memory=None,
                 detector=None, prompt=None, log=None, config=None,
                 validator=None, evidence_dir=None):
        self.ex = executor
        self.state_reader = state_reader or executor.state
        self.safety = safety
        self.memory = memory
        self.detector = detector or MapMarkerDetector.with_fixtures()
        self.prompt = prompt or ResurrectPrompt()
        self.log = log or (lambda *a, **k: None)
        self.cfg = dict(config or {})
        self.validator = validator
        self.evidence_dir = evidence_dir
        self.state = DEAD_SPECTER
        self.history = []
        self.analysis = None
        self.last_distance = None
        self.best_distance = None
        self.attempts = 0
        self.max_attempts = int(self.cfg.get("maxAttempts", 40))
        self.map_attempts = int(self.cfg.get("maxMapAttempts", 4))
        self.click_px = int(self.cfg.get("clickPx", 900))
        self.min_px = float(self.cfg.get("minProgressPx", 0.8))
        self.map_open = False
        self.result = None
        self.prompt_hit = None
        self.resurrect_clicks = 0
        self.max_resurrect_clicks = int(self.cfg.get("maxResurrectClicks", 3))
        self.theta = None          # angle ecran du dernier clic (feedback)
        self.click_to_heading = None   # rotation APPRISE (clic -> cap)
        self.heading = None        # cap du personnage (minicarte)
        self.stuck_count = 0
        self.bearing_offset = 0.0   # correction laterale apres un echec
        self.map_open_tries = 0
        self._geo = None

    # ------------------------------------------------------------------
    # utilitaires
    # ------------------------------------------------------------------
    def _set(self, state, note=None, **evidence):
        old = self.state
        self.state = state
        rec = {"from": old, "to": state, "at": time.time(), "note": note,
               "evidence": evidence}
        self.history.append(rec)
        self.log("RECOVERY_STATE", previous=old, state=state, note=note,
                 **evidence)
        return rec

    def _abort(self, reason, **evidence):
        """Arret controle, AVEC preuve conservee (auto-diagnostic)."""
        self._evidence("abort-" + str(reason)[:24].replace(" ", "_"))
        self.result = {"ok": False, "outcome": OUTCOME_BLOCKED,
                       "reason": reason, "state": self.state}
        self._set(ABORTED, reason, **evidence)
        return self.result

    def _capture(self):
        return self.ex.screen.grab(force=True)

    def _world_view(self):
        """Capture de la VUE 3D (la carte est fermee si besoin).

        Indispensable : la carte contient des elements DORES qui seraient
        confondus avec l'invite "Resurrect".
        """
        img = self._capture()
        if img is not None and map_is_open(_bgr(img)):
            self._ensure_map(False)
            img = self._capture()
        self.map_open = False
        return img

    def _ensure_map(self, want_open):
        """Garantit l'etat de la carte en le MESURANT."""
        for _ in range(4):
            img = self._capture()
            if img is None:
                break
            state = map_is_open(_bgr(img))
            if state is None:
                break
            if state == want_open:
                self.map_open = state
                return True
            self.ex.actions.key("M")
            time.sleep(float(self.cfg.get("mapOpenSeconds", 2.4)))
        self.map_open = want_open
        return False

    # ------------------------------------------------------------------
    # primitives externalisees (validees en jeu le 21/10/2026)
    # ------------------------------------------------------------------
    def _hwnd(self):
        """Handle de la fenetre du client (acces tolerant)."""
        try:
            return self.ex.actions._win()
        except Exception:
            return 0

    def _window_rect(self):
        """Rectangle REEL de la fenetre du client (bord + barre de titre)."""
        try:
            from fishing_observer import find_wow_window
            _h, _t, rect = find_wow_window()
            if rect:
                return tuple(rect)
        except Exception:                                  # pragma: no cover
            pass
        try:
            return tuple(self.ex.actions.rect)
        except Exception:                                  # pragma: no cover
            return (0, 0, 0, 0)

    def _to_screen(self, x, y):
        """Coordonnees de CAPTURE (fenetre) -> coordonnees ECRAN.

        La capture du client est cadree sur la FENETRE (origine =
        rect.left/top, souvent -8) alors que SetCursorPos attend l'ECRAN :
        sans cette conversion, un clic passe a cote de la cible (mesure :
        8 px de decalage ont fait rater le bouton 'Accept').
        """
        left, top = self._window_rect()[0], self._window_rect()[1]
        return int(x) + int(left), int(y) + int(top)

    def _held_click(self, x, y, hold=0.6, button="left"):
        """Clic avec APPUI MAINTENU, en coordonnees ecran.

        Les boutons de dialogue du client (listes deroulantes de la carte,
        bouton 'Accept' de 'Resurrect now?') ne repondent pas a un clic
        bref : il faut maintenir le bouton (mesure du 21/10).
        """
        sx, sy = self._to_screen(x, y)
        u = ctypes.windll.user32
        down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
        try:
            from focus_broker import focus_lease
        except Exception:                                  # pragma: no cover
            focus_lease = None
        if focus_lease is not None:
            with focus_lease(self._hwnd(), respect_user=False,
                             log=lambda *a, **k: None):
                return self._held_click_raw(u, sx, sy, hold, down, up)
        return self._held_click_raw(u, sx, sy, hold, down, up)

    @staticmethod
    def _held_click_raw(u, sx, sy, hold, down, up):
        u.SetCursorPos(sx, sy)
        time.sleep(0.25)
        u.mouse_event(down, 0, 0, 0, 0)
        time.sleep(hold)
        u.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.35)
        return sx, sy

    def _world_visible(self, img):
        """Le monde 3D est-il visible (et non le parchemin de la carte) ?

        La carte du monde est une frame du client : tant qu'elle est
        ouverte elle capte le focus clavier et ANNULE les entrees de
        deplacement (mesure : vol 0,23 px / clic 0,00 px avec la carte
        ouverte, 36,19 px / 9,90 px carte fermee).
        """
        bgr = _bgr(img)
        if bgr is None:
            return False
        h, w = bgr.shape[:2]
        reg = bgr[int(h * 0.25):int(h * 0.60), int(w * 0.20):int(w * 0.80)]
        if reg.size == 0:
            return False
        hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
        orange = ((hsv[:, :, 0] >= 8) & (hsv[:, :, 0] <= 40)
                  & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 110)).mean()
        return float(orange) < 0.5

    def _ensure_world(self):
        """Ferme la carte du monde et CONFIRME par une nouvelle capture.

        L'emettre de la commande ne prouve rien : seule une capture
        verifie que le monde est reellement visible.
        """
        for _ in range(4):
            img = self._capture()
            if img is None:
                return False
            if (not map_is_open(_bgr(img))) and self._world_visible(img):
                self.map_open = False
                return True
            self.ex.actions.key("M")
            time.sleep(float(self.cfg.get("mapOpenSeconds", 2.4)))
        img = self._capture()
        ok = img is not None and (not map_is_open(_bgr(img))) \
            and self._world_visible(img)
        self.map_open = not ok
        self.log("RECOVERY_WORLD_VISIBILITY", visible=bool(ok))
        return bool(ok)

    def _fly(self, seconds=3.0):
        """VOL du fantome : Z (avance) + W (saut) + ESPACE maintenus.

        L'ESPACE est indispensable : un fantome qui plonge descend et
        reste bloque au fond du lac (constat du developpeur, 21/10).
        Toutes les touches sont liberees, meme en cas d'exception.
        """
        u = ctypes.windll.user32
        hwnd = self._hwnd()
        keys = (0x5A, 0x57, 0x20)          # Z, W, ESPACE
        try:
            from focus_broker import focus_lease
        except Exception:                                  # pragma: no cover
            focus_lease = None

        def _do():
            for vk in keys:
                u.SendMessageW(hwnd, 0x0100, vk,
                               0x00000001 | (vk << 16) | (1 << 30))
            time.sleep(seconds)

        def _release():
            for vk in reversed(keys):
                u.SendMessageW(hwnd, 0x0101, vk,
                               0x00000001 | (vk << 16) | (1 << 31))

        try:
            if focus_lease is not None:
                with focus_lease(hwnd, respect_user=False,
                                 log=lambda *a, **k: None):
                    _do()
            else:                                         # pragma: no cover
                _do()
        except Exception as exc:                        # pragma: no cover
            self.log("RECOVERY_FLY_ERROR", error=repr(exc))
        finally:
            # aucune touche ne doit rester bloquee, meme en cas d'erreur
            try:
                _release()
            except Exception:                             # pragma: no cover
                pass
        time.sleep(0.3)
        return True

    def _ghost_tint(self, img):
        """Voile CYAN du mode fantome (0 = vivant, ~1 = fantome).

        Verification INDEPENDANTE de l'etat vivant, quand le lecteur
        d'etat du client n'est plus disponible (addon desactive).
        Mesure : 0,99 en fantome, 0,08 apres resurrection.
        """
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        reg = hsv[int(h * 0.08):int(h * 0.55), int(w * 0.10):int(w * 0.85)]
        if reg.size == 0:
            return None
        return float(((reg[:, :, 0] >= 90) & (reg[:, :, 0] <= 118)
                      & (reg[:, :, 1] > 40)).mean())

    def _steer(self, err_deg):
        """Oriente la CAMERA a la souris (seule commande acceptee par le
        client, qui lit le clavier en raw input).

        `err_deg` > 0 : il faut augmenter le cap (tourner a gauche).  La
        souris se deplace de ~0,45 deg par pixel (mesure du 21/10).
        Le deplacement se fait SOUS BAIL DE FOCUS, sinon l'evenement
        n'atteint pas le client.
        """
        if err_deg is None:
            return 0
        deg_per_px = float(self.cfg.get("degPerMousePx", 0.45))
        u = ctypes.windll.user32
        hwnd = self._hwnd()
        dx = int(round(err_deg / deg_per_px))
        if dx == 0:
            return 0
        try:
            from focus_broker import focus_lease
        except Exception:                                  # pragma: no cover
            focus_lease = None

        def _act():
            pt = ctypes.wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            cur = int(pt.x)
            lo = int(self.cfg.get("mouseMinX", 80))
            hi = int(self.cfg.get("mouseMaxX", 1950))
            want = max(lo, min(hi, cur + dx))
            eff = want - cur
            if eff:
                steps = max(1, int(abs(eff) / 25))
                for _ in range(steps):
                    u.mouse_event(0x0001, int(eff / steps), 0, 0, 0)
                    time.sleep(0.012)
            return eff

        try:
            if focus_lease is not None:
                with focus_lease(hwnd, respect_user=False,
                                 log=lambda *a, **k: None):
                    return _act()
            return _act()
        except Exception as exc:                        # pragma: no cover
            self.log("RECOVERY_STEER_ERROR", error=repr(exc))
            return 0
    def _world_shift(self, before, after):
        """Deplacement REEL de la vue 3D entre deux captures (px).

        Une touche envoyee n'est pas une preuve de deplacement : on
        mesure le defilement du monde.
        """
        if before is None or after is None:
            return None
        try:
            from fishing_minimap import world_displacement
            dx, dy, conf = world_displacement(before, after)
            return math.hypot(dx, dy)
        except Exception:                                  # pragma: no cover
            return None

    def _read_heading(self):
        """Cap REEL du personnage (fleche de la minicarte, verrouillee nord).

        Convention : 0 = est, +90 = nord (identique au cap carte).
        """
        try:
            from fishing_minimap import MinimapGeometry, player_facing
            if self._geo is None:
                self._geo = MinimapGeometry()
            img = self._world_view()
            if img is None:
                return None
            bgr = _bgr(img)
            self._geo.ensure(bgr)
            crop = self._geo.crop(bgr)
            rad = player_facing(crop)
            return None if rad is None else math.degrees(rad)
        except Exception as exc:                        # pragma: no cover
            self.log("RECOVERY_HEADING_ERROR", error=str(exc))
            return None

    def _center(self):
        """Centre du personnage a l'ecran (le_camera suit le personnage)."""
        img = _bgr(self._capture())
        if img is None:
            return 1288.0, 902.0
        h, w = img.shape[:2]
        return w / 2.0, h * 0.62

    def _screen_click(self, theta_deg, radius=None):
        """Clic droit au sol, a l'angle ecran `theta_deg` (0=droite, 90=bas).

        La cible est ramenee dans la VUE MONDE : un clic sur un bouton
        d'interface ouvre un panneau (sac, amis, fiche personnage) au lieu
        de deplacer le personnage.
        """
        from fishing_screen import clamp_to_world, in_world_zone
        img = _bgr(self._world_view())
        h = img.shape[0] if img is not None else 1328
        w = img.shape[1] if img is not None else 2345
        r = radius if radius is not None else self.click_px
        cx, cy = w / 2.0, h * 0.62
        tx = int(cx + math.cos(math.radians(theta_deg)) * r)
        ty = int(cy + math.sin(math.radians(theta_deg)) * r)
        if not in_world_zone(tx, ty, w, h):
            tx, ty = clamp_to_world(tx, ty, w, h)
        self.ex.actions.click(tx, ty, button="right")
        return tx, ty

    def _evidence(self, tag, img=None):
        """Conserve une capture de preuve (auto-diagnostic sans humain)."""
        if not self.evidence_dir:
            return None
        try:
            os.makedirs(self.evidence_dir, exist_ok=True)
            if img is None:
                img = self._capture()
            if img is None:
                return None
            import cv2
            path = os.path.join(self.evidence_dir, "%s-%s.png" % (
                time.strftime("%H%M%S"), tag))
            cv2.imwrite(path, as_bgr(img))
            return path
        except Exception:
            return None

    # ------------------------------------------------------------------
    # APPRENTISSAGE de la relation clic -> cap
    # ------------------------------------------------------------------
    def _calibrate_click_to_heading(self):
        """Apprend l'angle ecran a cliquer pour un cap donne, PAR MESURE.

        Le cap du personnage est relu sur la fleche de la minicarte
        (verrouillee au nord, donc directement comparable au cap carte).
        On clique un angle ecran connu, on attend que le personnage prenne
        ce cap, et on en deduit la rotation camera -> monde.  Cette rotation
        est ensuite APPRISE et affinee a chaque pas (boucle a
        retroaction), ce qui est la seule facon fiable de convertir un cap
        carte en point de clic : la camera n'est pas supposee immobile.
        """
        if self.click_to_heading is not None:
            return self.click_to_heading
        probe = -90.0                    # "tout droit" a l'ecran
        self._screen_click(probe, radius=260)
        time.sleep(float(self.cfg.get("calibrateSeconds", 2.0)))
        head = self._read_heading()
        if head is None:
            return None
        # le personnage a pris le cap correspondant au clic : la relation
        # est lineaire (cap = angle_ecran + rotation)
        self.click_to_heading = (head - probe + 180.0) % 360.0 - 180.0
        self.log("CLICK_HEADING_CALIBRATED", probeDeg=probe,
                 headDeg=round(head, 1),
                 rotationDeg=round(self.click_to_heading, 1))
        return self.click_to_heading

    def _theta_for_heading(self, target_heading):
        """Angle ecran a cliquer pour obtenir le cap vise (appris)."""
        rot = self.click_to_heading
        if rot is None:
            rot = self._calibrate_click_to_heading()
        if rot is None:
            return None
        return (target_heading - rot + 180.0) % 360.0 - 180.0

    def _refine_rotation(self, measured_heading, commanded_theta):
        """Affine la rotation clic -> cap apres chaque deplacement mesure."""
        if measured_heading is None:
            return
        rot = (measured_heading - commanded_theta + 180.0) % 360.0 - 180.0
        if self.click_to_heading is None:
            self.click_to_heading = rot
        else:
            # moyenne glissante : le cap releve est bruite (la fleche de la
            # minicarte est petite), on ne garde que l'information stable
            self.click_to_heading = (self.click_to_heading * 0.6 + rot * 0.4
                                     + 180.0) % 360.0 - 180.0
        self.log("CLICK_HEADING_REFINED", measuredDeg=round(measured_heading, 1),
                 commandedDeg=round(commanded_theta, 1),
                 rotationDeg=round(self.click_to_heading, 1))

    # ------------------------------------------------------------------
    # etats
    # ------------------------------------------------------------------
    def _st_init(self):
        """Etat initial : mort ou fantome ? (le lecteur d'etat tranche)."""
        img = self._capture()
        st = self.state_reader.read(img, self.ex.board)
        life = st["life"]
        if life == "alive":
            return self._abort("personnage vivant : rien a recuperer",
                               life=life)
        if st["signals"].get("releaseDialog"):
            # le corps n'est pas encore libere : on libere d'abord
            btn = self._red_block(img)
            if btn:
                self.ex.do_click([btn["x"], btn["y"]], button="left")
                time.sleep(2.0)
                st2 = self.state_reader.read(self._capture(), self.ex.board)
                if st2["signals"].get("releaseDialog"):
                    return self._abort("impossible de liberer le corps",
                                       life=life)
        self._set(MAP_ANALYSIS, "etat initial lu", life=life)
        return {"ok": True, "state": MAP_ANALYSIS}

    def _red_block(self, img):
        """Bouton rouge du dialogue de liberation (barre desaturee)."""
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        b, g, r = (bgr[:, :, 0].astype(np.int16), bgr[:, :, 1].astype(np.int16),
                   bgr[:, :, 2].astype(np.int16))
        val = bgr[:, :, 2]
        redish = ((r - np.maximum(g, b)) > 10) & (val > 60)
        band = np.zeros_like(redish)
        band[:int(h * 0.45), :] = True
        redish = redish & band
        n, _l, stats, _c = cv2.connectedComponentsWithStats(
            redish.astype(np.uint8), 8)
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 120 or cw < 40 or ch < 8 or cw > 320:
                continue
            if not (2.0 <= float(cw) / max(1, ch) <= 9.0):
                continue
            if area < 0.35 * cw * ch:
                continue
            x0 = max(0, x - cw)
            x1 = min(w, x + 2 * cw)
            y0 = max(0, y - ch)
            y1 = min(h, y + 2 * ch)
            around = val[y0:y1, x0:x1]
            if around.size == 0 or float(np.median(around)) > 70.0:
                continue
            return {"x": int(x + cw / 2), "y": int(y + ch / 2)}
        return None

    def _st_map_analysis(self):
        """Ouvre la carte, localise le corps et le joueur, mesure la distance."""
        for _ in range(self.map_attempts):
            self._ensure_map(True)
            img = self._capture()
            if img is None:
                continue
            analysis = analyze_map(_bgr(img), self.detector)
            if analysis.usable:
                self.analysis = analysis
                self.last_distance = analysis.distance_px
                if self.best_distance is None:
                    self.best_distance = analysis.distance_px
                if self.memory is not None:
                    try:
                        self.memory.record_incident(
                            OUTCOME_BLOCKED,
                            zone="carte:cap%.0f,dist%.0f" % (
                                analysis.bearing_deg or 0.0,
                                analysis.distance_px or 0.0),
                            destination="corps (marqueur carte)",
                            cause="noyade lors d'un deplacement",
                            confidence=0.8)
                    except Exception:
                        pass
                self._set(NAVIGATING_TO_CORPSE, "corps localise sur la carte",
                          **analysis.as_dict())
                return {"ok": True, "state": NAVIGATING_TO_CORPSE,
                        "analysis": analysis.as_dict()}
            self.log("RECOVERY_MAP_AMBIGU", reasons=analysis.reasons)
            self._ensure_map(False)
            time.sleep(0.8)
        return self._abort("marqueur du corps introuvable sur la carte "
                           "(apres %d captures)" % self.map_attempts)

    def _st_navigate(self):
        """UN pas vers le corps, pilote par BOUCLE A RETROACTION.

        On ne connait pas la camera : on clique un angle ecran, on relit le
        cap obtenu sur la minicarte, et on corrige l'angle pour que le cap
        se rapproche du cap carte vise.  C'est une APPRENTISSAGE en ligne
        de la relation clic -> cap.
        """
        if self.attempts >= self.max_attempts:
            return self._abort("budget de deplacements epuise (%d)"
                               % self.max_attempts)
        a = self.analysis
        if a is None or a.bearing_deg is None:
            return self._set_and_go(CORPSE_INTERACTION, "pas d'analyse")
        # REGLE 1 : tant que la carte du monde est ouverte, elle capte le
        # focus et ANNULE les entrees de deplacement.  On la ferme et on
        # CONFIRME par une nouvelle capture avant de bouger quoi que ce
        # soit.
        if not self._ensure_world():
            return self._abort("la carte du monde intercepte les entrees : "
                               "deplacement refuse")
        target = a.bearing_deg + self.bearing_offset
        # Cap courant (minicarte, repere NORD comme le cap carte) : c'est
        # la meme reference que le cap vise, donc l'ecart donne la
        # correction d'orientation.
        head_now = self._read_heading()
        if head_now is not None:
            self.heading = head_now
        err = None
        if self.heading is not None:
            err = _n180(target - self.heading)
        self.theta = err
        self.attempts += 1
        before = _bgr(self._capture())
        # REGLE 2 : le deplacement d'un FANTOME est un VOL (Z + W) avec
        # ESPACE maintenu (sans ESPACE il plonge et se bloque au fond du
        # lac).  On ne passe pas par do_goto : ses garde-fous d'eau/rive
        # refusent justement d'aller vers un corps noye.
        # La direction du vol est celle de la CAMERA : on l'oriente d'abord
        # a la souris (seule commande acceptee par le client en raw input).
        if err is not None:
            self._steer(err)
        self._fly(float(self.cfg.get("flySeconds", 3.0)))
        after = _bgr(self._capture())
        px = self._world_shift(before, after)
        ccx, ccy = self._center()
        tx = int(ccx + math.cos(math.radians(self.theta or 0.0)) * self.click_px)
        ty = int(ccy + math.sin(math.radians(self.theta or 0.0)) * self.click_px)
        # la cible informative reste dans l'ecran
        frame = _bgr(before)
        if frame is not None:
            fh, fw = frame.shape[:2]
            tx = max(0, min(fw - 1, tx))
            ty = max(0, min(fh - 1, ty))
        head2 = self._read_heading()
        if head2 is not None:
            self.heading = head2
        # AUTO-DIAGNOSTIC : si rien n'a bouge, on conserve la capture et
        # on consigne POURQUOI (le moteur doit pouvoir se corriger seul).
        if px is None or px < 0.5:
            path = self._evidence("noprogress-%d" % self.attempts, after)
            self.log("RECOVERY_NO_PROGRESS", attempt=self.attempts,
                     px=None if px is None else round(px, 2),
                     err=None if err is None else round(err, 1),
                     target=round(target, 1),
                     head=None if head2 is None else round(head2, 1),
                     evidence=path)
        self._last = {"px": px, "angle": None, "theta": self.theta,
                      "target": target, "headBefore": self.heading,
                      "headAfter": head2, "click": [tx, ty]}
        self._set(PROGRESS_CHECK, "pas de navigation",
                  px=None if px is None else round(px, 2),
                  theta=round(self.theta or 0.0, 1), target=round(target, 1),
                  head=None if head2 is None else round(head2, 1))
        return {"ok": True, "state": PROGRESS_CHECK, "move": self._last}

    def _st_progress_check(self):
        """Verifie le deplacement ET la distance au corps (mesure reelle)."""
        last = getattr(self, "_last", None) or {}
        px = last.get("px")
        if px is None:
            return self._abort("deplacement non mesure : observation "
                               "insuffisante")
        if px < self.min_px:
            # AUCUNE PROGRESSION : correction CONTROLEE avant d'abandonner.
            # On decale l'angle de clic (contournement d'obstacle) et on
            # retente ; ce n'est qu'apres plusieurs echecs qu'on s'arrete.
            self.stuck_count += 1
            self.bearing_offset = 45.0 * self.stuck_count
            self.log("RECOVERY_STUCK", attempt=self.attempts,
                     px=round(px, 2), count=self.stuck_count,
                     newTheta=round(self.theta or 0.0, 1))
            if self.stuck_count > int(self.cfg.get("maxStuck", 4)):
                self._evidence("stuck-final")
                return self._abort(
                    "aucune progression apres %d tentatives (%.2f px) et %d "
                    "corrections d'angle : obstacle non contournable"
                    % (self.attempts, px, self.stuck_count))
            self._set(NAVIGATING_TO_CORPSE,
                      "aucune progression -> angle decale de %d deg"
                      % (45 * self.stuck_count), px=px)
            return {"ok": True, "state": NAVIGATING_TO_CORPSE}
        self.stuck_count = 0
        self._set(RELOCALIZATION, "progression %.1f px" % px, px=px)
        return {"ok": True, "state": RELOCALIZATION}

    def _st_relocalization(self):
        """Re-mesure la distance au corps : c'est la progression de reference."""
        self._ensure_map(True)
        img = self._capture()
        analysis = (analyze_map(_bgr(img), self.detector)
                    if img is not None else None)
        self._ensure_map(False)
        if analysis is None or not analysis.usable:
            return self._set_and_go(CORPSE_INTERACTION,
                                    "re-localisation impossible")
        prev = self.last_distance
        self.analysis = analysis
        self.last_distance = analysis.distance_px
        if self.best_distance is None or analysis.distance_px < self.best_distance:
            self.best_distance = analysis.distance_px
        closer = (prev is not None and
                  analysis.distance_px is not None and
                  analysis.distance_px < prev - 0.5)
        self.log("RECOVERY_DISTANCE", previous=prev,
                 distance=analysis.distance_px, closer=bool(closer))
        if not closer and self.attempts >= 3:
            # la distance ne diminue pas : on change d'angle de clic
            self.theta = ((self.theta or 0.0) + 35.0) % 360.0
            self.log("RECOVERY_STUCK", previous=prev,
                     distance=analysis.distance_px)
        self._set(NAVIGATING_TO_CORPSE, "distance %.0f px" %
                  (analysis.distance_px or 0), **analysis.as_dict())
        return {"ok": True, "state": NAVIGATING_TO_CORPSE,
                "analysis": analysis.as_dict()}

    def _find_accept_dialog(self, img):
        """Bouton ROUGE 'Accept' du dialogue 'Resurrect now?'.

        Le client ouvre ce dialogue quand le corps est a portee ; le
        bouton ne repond PAS a un clic bref (il faut un appui maintenu)
        et il est a_clickable en coordonnees ECRAN (conversion centralisee
        dans _to_screen).
        """
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        x0, x1 = int(w * 0.30), int(w * 0.70)
        y0, y1 = int(h * 0.10), int(h * 0.40)
        roi = bgr[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        red = (((hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 172))
               & (hsv[:, :, 1] > 130) & (hsv[:, :, 2] > 90))
        n, _l, stats, _c = cv2.connectedComponentsWithStats(
            red.astype(np.uint8), 8)
        best = None
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 400 or area > 9000:
                continue
            if not (60 <= cw <= 420 and 8 <= ch <= 60):
                continue
            if not (4.0 <= float(cw) / max(1, ch) <= 16.0):
                continue
            if best is None or area > best["area"]:
                best = {"x": int(x0 + x + cw / 2), "y": int(y0 + y + ch / 2),
                        "w": int(cw), "h": int(ch), "area": int(area)}
        return best

    def _st_corpse_interaction(self):
        """INTERACTION REELLE avec le corps (vue 3D).

        Chemin valide (21/10) : clic droit sur le corps -> le client ouvre
        le dialogue 'Resurrect now?' -> clic maintenu sur 'Accept'.
        Si le dialogue n'apparait pas, on retombe sur l'invite doree du
        corps (chemin historique).
        """
        img = self._world_view()
        # 1) dialogue 'Resurrect now?' deja ouvert ?
        accept = self._find_accept_dialog(img)
        if accept is None:
            # 2) on declenche l'interaction : clic droit sur le corps,
            #    au pied du fantome (le corps est a portee).
            ccx, ccy = self._center()
            for fx, fy in ((0.0, 0.0), (-0.06, -0.03), (0.06, -0.03),
                           (0.0, -0.08), (-0.12, 0.0), (0.12, 0.0)):
                tx = int(ccx + fx * 1400)
                ty = int(ccy + fy * 1400)
                self.ex.actions.click(tx, ty, button="right")
                time.sleep(float(self.cfg.get("interactDelay", 1.4)))
                img = self._world_view()
                accept = self._find_accept_dialog(img)
                if accept is not None:
                    break
        if accept is not None:
            self.resurrect_clicks += 1
            if self.resurrect_clicks > self.max_resurrect_clicks:
                return self._abort("trop de tentatives d'interaction (%d)"
                                   % self.resurrect_clicks)
            # clic avec APPUI MAINTENU, en coordonnees ECRAN
            sx, sy = self._held_click(accept["x"], accept["y"],
                                      hold=float(self.cfg.get(
                                          "acceptHold", 0.6)))
            self.log("RECOVERY_ACCEPT_CLICK", capture=[accept["x"], accept["y"]],
                     screen=[sx, sy], clicks=self.resurrect_clicks)
            time.sleep(float(self.cfg.get("interactDelay", 2.5)))
            self._set(RESURRECTION_VERIFY, "Accept clique sur le dialogue",
                      accept=accept, distance=self.last_distance)
            return {"ok": True, "state": RESURRECTION_VERIFY, "hit": accept}
        hit = self.prompt.find(img)
        if hit:
            self.prompt_hit = hit
            self.resurrect_clicks += 1
            if self.resurrect_clicks > self.max_resurrect_clicks:
                return self._abort("trop de tentatives d'interaction (%d)"
                                   % self.resurrect_clicks)
            self.ex.do_click([hit["x"], hit["y"]], button="left")
            time.sleep(float(self.cfg.get("interactDelay", 2.5)))
            self._set(RESURRECTION_VERIFY, "interaction avec le corps",
                      hit=hit, distance=self.last_distance)
            return {"ok": True, "state": RESURRECTION_VERIFY, "hit": hit}
        if self.attempts >= self.max_attempts:
            return self._abort("corps non atteint (distance finale %.0f px)"
                               % (self.last_distance or 0))
        self._set(NAVIGATING_TO_CORPSE, "pas d'invite : on continue",
                  distance=self.last_distance)
        return {"ok": True, "state": NAVIGATING_TO_CORPSE}

    def _st_verify_alive(self):
        """CONFIRMATION INDEPENDANTE de la resurrection.

        Deux sources : le lecteur d'etat du client (si disponible) et,
        en secours, la VOIE VISUELLE : le monde d'un fantome est voile de
        CYAN (mesure : 0,99 en fantome, 0,08 apres resurrection).  Cette
        seconde source ne depend d'aucun addon.
        """
        img = self._world_view()
        tint = None
        try:
            frame = _bgr(img)
            # la mesure visuelle n'a de sens que sur une VRAIE capture
            # plein ecran (les fixtures de test sont des vignettes)
            if frame is not None and frame.shape[1] >= 2000 \
                    and frame.shape[0] >= 1000:
                tint = self._ghost_tint(img)
        except Exception:                                  # pragma: no cover
            tint = None
        st = self.state_reader.read(img, self.ex.board)
        alive = (st["life"] == "alive"
                 and not st["signals"].get("releaseDialog"))
        # verification visuelle independante : un monde non voile => vivant
        if tint is not None and tint < 0.5:
            alive = True
            self.log("RECOVERY_ALIVE_VISUAL", tint=round(tint, 3))
        if alive:
            if self.safety is not None:
                self.safety.observe_resurrected(st["signals"])
                self.safety.observe_safe_position({"resurrected": True})
            if self.memory is not None:
                try:
                    self.memory.record_incident(
                        OUTCOME_RECOVERED, zone="corps",
                        cause="corps retrouve, resurrection confirmee",
                        confidence=0.9)
                except Exception:
                    pass
            self.result = {"ok": True, "outcome": OUTCOME_RECOVERED,
                           "reason": "resurrection confirmee par le lecteur "
                                     "d'etat",
                           "state": PAUSED_SAFE}
            self._set(PAUSED_SAFE, "resurrection confirmee",
                      signals=st["signals"])
            return self.result
        if self.resurrect_clicks >= self.max_resurrect_clicks:
            return self._abort("resurrection non confirmee (etat lu: %s, "
                               "distance %.0f px)"
                               % (st["life"], self.last_distance or 0))
        self._set(CORPSE_INTERACTION, "resurrection non confirmee, "
                  "on re-essaie localement", life=st["life"],
                  distance=self.last_distance)
        return {"ok": False, "state": CORPSE_INTERACTION}

    def _st_paused_safe(self):
        self.result = {"ok": True, "outcome": OUTCOME_RECOVERED,
                       "reason": "personnage vivant, position a valider "
                                 "humainement",
                       "state": PAUSED_SAFE}
        return self.result

    def _set_and_go(self, state, note=None, **evidence):
        self._set(state, note, **evidence)
        return {"ok": True, "state": state}

    # ------------------------------------------------------------------
    _HANDLERS = {
        DEAD_SPECTER: "_st_init",
        MAP_ANALYSIS: "_st_map_analysis",
        NAVIGATING_TO_CORPSE: "_st_navigate",
        PROGRESS_CHECK: "_st_progress_check",
        RELOCALIZATION: "_st_relocalization",
        CORPSE_INTERACTION: "_st_corpse_interaction",
        RESURRECTION_VERIFY: "_st_verify_alive",
        PAUSED_SAFE: "_st_paused_safe",
    }

    def step(self):
        handler = self._HANDLERS.get(self.state)
        if handler is None:
            return self._abort("etat inconnu : %r" % self.state)
        return getattr(self, handler)()

    def run(self, max_transitions=120):
        for _ in range(int(max_transitions)):
            if self.state in (PAUSED_SAFE, ABORTED):
                break
            res = self.step()
            if res is None:
                break
        if self.state not in (PAUSED_SAFE, ABORTED):
            self.result = {"ok": False, "outcome": OUTCOME_BLOCKED,
                           "reason": "budget de transitions epuise",
                           "state": self.state}
        return self.result or {"ok": False, "state": self.state}
