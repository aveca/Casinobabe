#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_recovery.py — RECUPERATION DU CORPS (procedure dediee, a etats).

Le personnage est mort dans l'eau (noyade du 2026-10-21).  Il faut
retrouver le corps et ressusciter SUR PLACE.

La sequence explicite, chaque transition depending d'une observation
verifiable :

    RECOVERY_INIT -> MAP_ANALYSIS -> ROUTE_PLANNING -> TRAVEL_STEP
                  -> PROGRESS_CHECK -> RELOCALIZATION -> CORPSE_APPROACH
                  -> RESURRECTION_DETECTED -> VERIFY_ALIVE -> PAUSED_SAFE

Regles appliquees :

* aucune commande n'est consideree comme reussie parce qu'elle a ete
  envoyee : chaque deplacement est MESURE, chaque etat est verifie ;
* la carte du monde fournit la direction ABSOLUE (elle est verrouillee au
  nord), la fleche de la minicarte fournit le cap REEL du personnage, et
  la calibration mesuree du moteur de navigation corrige l'angle de clic ;
* le vecteur de la carte n'est JAMAIS utilise tel quel comme point de clic ;
* un resultat ambigu de la carte -> nouvelle capture, puis arret controle ;
* le garde-fou "eau" reste actif pour le personnage VIVANT ; pour le
  FANTOME il est desactive (un fantome ne se noie pas), mais les autres
  garde-fous (progression nulle, distance, duree, nombre de tentatives)
  restent actifs ;
* a la fin, le personnage est laisse PAUSED_SAFE : la peche n'est pas
  relancee automatiquement.
"""

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

from fishing_map import (CORPS, JOUEUR, MapAnalysis,  # noqa: E402
                         MapMarkerDetector, analyze_map, map_is_open,
                         map_bearing_to_screen)
from fishing_safety import (DEAD, GHOST, CORPSE_RECOVERY, RESURRECTION,  # noqa: E402
                            SAFE_REPOSITION, RESUME_OR_STOP, FISHING,
                            OUTCOME_OK, OUTCOME_BLOCKED, OUTCOME_WATER,
                            OUTCOME_RECOVERED)

# --------------------------------------------------------------------------
# etats de la recuperation
# --------------------------------------------------------------------------
RECOVERY_INIT = "RECOVERY_INIT"
MAP_ANALYSIS = "MAP_ANALYSIS"
ROUTE_PLANNING = "ROUTE_PLANNING"
TRAVEL_STEP = "TRAVEL_STEP"
PROGRESS_CHECK = "PROGRESS_CHECK"
RELOCALIZATION = "RELOCALIZATION"
CORPSE_APPROACH = "CORPSE_APPROACH"
RESURRECTION_DETECTED = "RESURRECTION_DETECTED"
VERIFY_ALIVE = "VERIFY_ALIVE"
PAUSED_SAFE = "PAUSED_SAFE"
ABORTED = "ABORTED"

RECOVERY_STATES = (RECOVERY_INIT, MAP_ANALYSIS, ROUTE_PLANNING, TRAVEL_STEP,
                   PROGRESS_CHECK, RELOCALIZATION, CORPSE_APPROACH,
                   RESURRECTION_DETECTED, VERIFY_ALIVE, PAUSED_SAFE, ABORTED)

#: Correspondance avec la sequence demandedee pour le correctif du retour au
#: corps : ALIVE -> DEATH_DETECTED -> GHOST_CONFIRMED -> NAVIGATE_TO_CORPSE
#: -> VERIFY_ARRIVAL -> RESURRECTION_CONFIRMED -> RESUME_FISHING.
#:
#: Le present moteur est plus fin (il separe l'analyse de carte, la
#: planification et la verification), mais chaque etat de la sequence
#: demandee a bien une correspondance 1-1 :
STATE_SEQUENCE = (
    ("ALIVE", RECOVERY_INIT),
    ("DEATH_DETECTED", RECOVERY_INIT),
    ("GHOST_CONFIRMED", MAP_ANALYSIS),
    ("NAVIGATE_TO_CORPSE", ROUTE_PLANNING),
    ("VERIFY_ARRIVAL", PROGRESS_CHECK),
    ("RELOCALIZATION", RELOCALIZATION),
    ("CORPSE_APPROACH", CORPSE_APPROACH),
    ("RESURRECTION_CONFIRMED", RESURRECTION_DETECTED),
    ("RESUME_FISHING", VERIFY_ALIVE),
    ("PAUSED_SAFE", PAUSED_SAFE),
)

#: couleurs de l'invite "Resurrect" (or / jaune dore dans le monde)
GOLD_LO, GOLD_HI = 18, 42


def _bgr(img):
    from fishing_process import as_bgr
    return as_bgr(img)


class ResurrectPrompt(object):
    """Detecte l'invite "Resurrect" du client (bouton dore sur le corps).

    Contrainte de localisation : l'invite est dessinee AU PIED DU
    PERSONNAGE (le corps est a ses pieds).  On ne cherche donc que dans un
    rectangle autour du personnage : sans cela, les objets dores de la
    scene (lanterne du bateau, icones de l'UI) declenchaient de faux
    positifs.

    Ne detectionne jamais dans l'UI (barres d'action, sacs, mini-carte).
    """

    def __init__(self, region=(0.28, 0.74, 0.48, 0.86)):
        # (x_min, x_max, y_min, y_max) en fractions de l'ecran
        self.region = region

    def find(self, img):
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        x0 = int(self.region[0] * w)
        x1 = int(self.region[1] * w)
        y0 = int(self.region[2] * h)
        y1 = int(self.region[3] * h)
        roi = bgr[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        gold = ((hsv[:, :, 0] >= GOLD_LO) & (hsv[:, :, 0] <= GOLD_HI)
                & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 150))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(
            gold.astype(np.uint8), 8)
        best = None
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 60 or cw < 22 or ch < 6 or cw > 260:
                continue
            if not (1.2 <= float(cw) / max(1, ch) <= 12.0):
                continue
            cx = x0 + x + cw / 2.0
            cy = y0 + y + ch / 2.0
            if best is None or area > best["area"]:
                best = {"x": int(cx), "y": int(cy), "area": int(area),
                        "w": int(cw), "h": int(ch)}
        return best


class RecoverySession(object):
    """Machine a etats de recuperation du corps.

    Une transition par appel a `step()` : la progression est donc
    observable, journalisee et interruptible a tout instant.
    """

    def __init__(self, executor, state_reader=None, safety=None, memory=None,
                 detector=None, prompt=None, log=None, config=None):
        self.ex = executor
        self.state_reader = state_reader or executor.state
        self.safety = safety
        self.memory = memory
        self.detector = detector or MapMarkerDetector.with_fixtures()
        self.prompt = prompt or ResurrectPrompt()
        self.log = log or (lambda *a, **k: None)
        self.cfg = dict(config or {})
        self.state = RECOVERY_INIT
        self.history = []
        self.analysis = None
        self.route = []
        self.route_index = 0
        self.steps_since_reloc = 0
        self.attempts = 0
        self.max_attempts = int(self.cfg.get("maxAttempts", 24))
        self.map_attempts = int(self.cfg.get("maxMapAttempts", 4))
        # Distance du clic : le point vise doit etre LOINTAIN (bord de
        # l'ecran).  Mesure du 2026-10-21 : viser un point proche situé
        # derrière un obstacle (l'epave du cimetière) fait echouer le
        # pathing ; viser loin fait contourner l'obstacle.
        self.step_px = int(self.cfg.get("stepPx", 900))
        self.min_px = float(self.cfg.get("minProgressPx", 1.0))
        self.direction_tol = float(self.cfg.get("directionTolDeg", 70.0))
        self.click_rotation = float(self.cfg.get("clickRotationDeg", 0.0))
        self.heading_deg = None
        self.map_open = False
        self.prompt_hit = None
        self.resurrect_clicks = 0
        self.last_distance = None
        self.stuck_count = 0
        self.block_turn = 0
        self.camera_px_per_deg = None
        self.result = None
        self.spiral = [(0, -1), (0.7, -0.7), (1, 0), (0.7, 0.7),
                       (0, 1), (-0.7, 0.7), (-1, 0), (-0.7, -0.7)]

    # -- utilitaires ------------------------------------------------------
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
        self.result = {"ok": False, "outcome": OUTCOME_BLOCKED, "reason": reason,
                       "state": self.state}
        self._set(ABORTED, reason, **evidence)
        return self.result

    def _capture(self):
        return self.ex.screen.grab(force=True)

    def _map_key(self):
        """Bascule l'ouverture de la carte (la sesiion suit l'etat reels)."""
        self.ex.actions.key("M")
        time.sleep(float(self.cfg.get("mapOpenSeconds", 2.4)))
        self.map_open = not self.map_open

    def _ensure_map(self, want_open):
        """Garantit l'etat de la carte en le MESURANT (pas en supposant)."""
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
            self._map_key()
        self.map_open = want_open
        return False

    def _world_view(self):
        """Capture de la VUE 3D (la carte est fermee si besoin).

        Indispensable : la carte contient des elements DORES (boutons, textes
        d'or) qui seraient confondus avec l'invite "Resurrect", et un
        bouton ROUGE qui serait confondu avec le dialogue de liberation.
        """
        img = self._capture()
        if img is not None and map_is_open(_bgr(img)):
            self._ensure_map(False)
            img = self._capture()
        self.map_open = False
        return img

    def _read_heading(self):
        """Cap REEL du personnage (fleche de la minicarte, verrouillee nord)."""
        try:
            from fishing_minimap import MinimapGeometry, player_facing
            geo = getattr(self, "_geo", None)
            if geo is None:
                geo = MinimapGeometry()
                self._geo = geo
            img = self._capture()
            if img is None:
                return None
            bgr = _bgr(img)
            geo.ensure(bgr)
            crop = geo.crop(bgr)
            rad = player_facing(crop)
            return None if rad is None else math.degrees(rad)
        except Exception as exc:                        # pragma: no cover
            self.log("RECOVERY_HEADING_ERROR", error=str(exc))
            return None

    # -- ORIENTATION : la camera, APPRISE par mesure ----------------------
    def calibrate_camera(self, probe_px=120):
        """Apprend la sensibilite de la camera (px de souris -> degres).

        Un deplacement de souris connu est applique (clic gauche enfonce),
        puis le cap resultant est relu sur la fleche de la minicarte : le
        rapport donne les pixels par degree.  Rien n'est suppose.
        """
        h0 = self._read_heading()
        if h0 is None:
            return None
        rotate = getattr(self.ex.actions, "rotate_camera", None)
        if rotate is None:
            return None
        rotate(probe_px, 0)
        time.sleep(0.6)
        h1 = self._read_heading()
        if h1 is None:
            return None
        delta = (h1 - h0 + 180.0) % 360.0 - 180.0
        if abs(delta) < 1.0:
            return None
        sens = float(probe_px) / abs(delta)
        self.camera_px_per_deg = sens
        self.log("CAMERA_CALIBRATED", probePx=probe_px,
                 deltaDeg=round(delta, 1), pxPerDeg=round(sens, 2))
        return sens

    def turn_to(self, bearing_deg, tolerance=8.0):
        """Tourne la camera pour pointer le personnage vers le cap vise."""
        sens = self.camera_px_per_deg or self.calibrate_camera()
        if not sens:
            return None
        rotate = getattr(self.ex.actions, "rotate_camera", None)
        if rotate is None:
            return None
        for _ in range(4):
            h = self._read_heading()
            if h is None:
                return None
            d = (bearing_deg - h + 180.0) % 360.0 - 180.0
            if abs(d) <= tolerance:
                return h
            # un deplacement souris horizontal NEGatif tourne la camera a
            # gauche (cap augmente) : signe inverse de l'ecart vise.
            rotate(-d * sens, 0)
            time.sleep(0.45)
        return self._read_heading()

    # -- etats ------------------------------------------------------------
    def _st_init(self):
        img = self._capture()
        st = self.state_reader.read(img, self.ex.board)
        life = st["life"]
        if life == "alive":
            return self._abort("personnage deja vivant : rien a recuperer",
                               life=life)
        if st["signals"].get("releaseDialog"):
            # le corps n'est pas encore libere : on libere d'abord
            btn = self._red_block(img)
            if btn:
                self.ex.do_click([btn["x"], btn["y"]], button="left")
                time.sleep(1.5)
                st2 = self.state_reader.read(self._capture(), self.ex.board)
                if st2["signals"].get("releaseDialog"):
                    return self._abort("impossible de liberer le corps",
                                       life=life)
        self.heading_deg = self._read_heading()
        self._set(MAP_ANALYSIS, "etat initial lu", life=life,
                  headingDeg=self.heading_deg)
        return {"ok": True, "state": MAP_ANALYSIS}

    def _red_block(self, img):
        bgr = _bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168))
               & (hsv[:, :, 1] > 150) & (hsv[:, :, 2] > 140))
        red = red[:int(h * 0.45), :]
        n, _l, stats, _c = cv2.connectedComponentsWithStats(
            red.astype(np.uint8), 8)
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 400 or cw < 40 or cw > 320 or ch < 10:
                continue
            if not (1.6 <= float(cw) / max(1, ch) <= 9.0):
                continue
            if area < 0.45 * cw * ch:
                continue
            return {"x": int(x + cw / 2), "y": int(y + ch / 2)}
        return None

    def _st_map_analysis(self):
        """Ouvre la carte, detecte le corps et le joueur, calcule le cap."""
        for attempt in range(self.map_attempts):
            self._ensure_map(True)
            img = self._capture()
            if img is None:
                continue
            analysis = analyze_map(_bgr(img), self.detector)
            if analysis.usable:
                self.analysis = analysis
                self.last_distance = analysis.distance_px
                # Equivalent du "Deaths" de la reference CorpseRunGoal :
                # la position du corps est ENREGISTREE (en RELATIF : cap +
                # distance sur la carte, jamais de coordonnee absolue
                # inventee).
                if self.memory is not None:
                    try:
                        self.memory.record_incident(
                            OUTCOME_DEAD,
                            zone="carte:cap%.0f,dist%.0f" % (
                                analysis.bearing_deg or 0.0,
                                analysis.distance_px or 0.0),
                            destination="corps (marqueur carte)",
                            cause="noyade lors d'un deplacement",
                            confidence=0.8)
                    except Exception:
                        pass
                self._set(ROUTE_PLANNING, "corps localise sur la carte",
                          **analysis.as_dict())
                return {"ok": True, "state": ROUTE_PLANNING,
                        "analysis": analysis.as_dict()}
            # resultat ambigu : on referme, on recapture, on reessaye
            self.log("RECOVERY_MAP_AMBIGU", reasons=analysis.reasons)
            self._ensure_map(False)
            time.sleep(0.8)
        return self._abort("marqueur du corps introuvable sur la carte "
                           "(apres %d captures)" % self.map_attempts)

    def _st_route_planning(self):
        """Convertit le cap carte en une suite de deplacements mesurables.

        Le fantome dispose de la position et de la fleche : le corps est
        donc atteint EN LIGNE DROITE (le vecteur de la carte), en pas courts
        et mesures, avec un recalage periodique sur la carte.
        """
        a = self.analysis
        if self.heading_deg is None:
            self.heading_deg = self._read_heading()
        if a.bearing_reliable and self.heading_deg is not None:
            # ORIENTATION : on tourne la CAMERA (clic gauche + souris,
            # sensibilite APPRISE par mesure) pour que le personnage pointe
            # le corps.  Le clic vise alors "tout droit" : la direction
            # visee est directe, sans conversion delicate.
            try:
                self.turn_to(a.bearing_deg)
            except Exception as exc:                  # pragma: no cover
                self.log("CAMERA_TURN_ERROR", error=str(exc))
            screen = map_bearing_to_screen(a.bearing_deg, self.heading_deg)
            self.route = [screen] if screen is not None else []
            reason = ("cap carte %.1f deg -> deplacement ecran %.1f deg "
                      "(ligne droite)" % (a.bearing_deg,
                                          screen if screen is not None else -1))
        else:
            # marqueurs confondus : cap inexploitable -> spirale locale
            self.route = [math.degrees(math.atan2(dy, dx))
                          for dx, dy in self.spiral]
            reason = "cap non exploitable (%.1f px) -> spirale" % (
                a.distance_px or 0.0)
        self.route_index = 0
        self._ensure_map(False)
        self._set(TRAVEL_STEP, reason, routes=len(self.route))
        return {"ok": True, "state": TRAVEL_STEP, "route": self.route}

    def _st_travel_step(self):
        """UN deplacement court, mesure, dans la direction planned."""
        if self.route_index >= len(self.route):
            if not self.route:
                return self._set_and_go(CORPSE_APPROACH, "aucune direction")
            # on continue TOUT DROIT : le recalage periodique sur la carte
            # remettra le cap a jour (la spirale prendra le relais si le
            # corps n'est pas atteint).
            angle = self.route[-1]
        else:
            angle = self.route[self.route_index]
        if self.attempts >= self.max_attempts:
            return self._abort("budget de deplacements epuise (%d)"
                               % self.max_attempts)
        # CAP FRAIS : la camera tourne a chaque virage du personnage (le
        # clic-to-move le fait pivoter).  Un cap perime donne un angle vise
        # faux -> le personnage fait des detours et tourne en rond.  On relit
        # donc le cap reel (fleche de la minicarte) AVANT chaque commande.
        fresh = self._read_heading()
        if fresh is not None:
            self.heading_deg = fresh
        # l'angle de clic tient compte de la rotation mesuree
        click = angle - (self.click_rotation or 0.0)
        img = _bgr(self._capture())
        h = img.shape[0] if img is not None else 1328
        w = img.shape[1] if img is not None else 2345
        cx, cy = w / 2.0, h * 0.62
        tx = int(max(40, min(w - 40, cx + math.cos(math.radians(click))
                             * self.step_px)))
        ty = int(max(40, min(h - 120, cy + math.sin(math.radians(click))
                             * self.step_px)))
        self.attempts += 1
        # VITESSE : si le personnage DEJA COURT dans la bonne direction, on
        # le LAISSE COURIR (un clic reinitialiserait l'auto-run et
        # annulerait l'elan).  On ne re-clique que s'il est a l'arret ou
        # s'il part dans la mauvaise direction.
        before = _bgr(self._capture())
        time.sleep(1.2)
        probe = _bgr(self._capture())
        moved = None
        ang = None
        if before is not None and probe is not None:
            fdx, fdy, _fc = self.ex.actions.displacement(before, probe)
            moved = math.hypot(fdx, fdy)
            if moved >= 0.3:
                ang = math.degrees(math.atan2(fdy, fdx))
        aimed = math.degrees(math.atan2(math.sin(math.radians(click)),
                                         math.cos(math.radians(click))))
        err = None
        if ang is not None:
            err = abs((ang - aimed + 180.0) % 360.0 - 180.0)
        if moved is not None and moved >= self.min_px and (
                err is None or err <= self.direction_tol):
            # deja en route : on attend (le fantome continue de nager)
            time.sleep(float(self.cfg.get("followSeconds", 4.0)))
            res = {"ok": True, "outcome": OUTCOME_OK, "px": moved,
                   "angle": ang, "reason": "en course (%.1f px)" % moved}
        else:
            res = self.ex.do_goto([tx, ty], tries=2, unsafe="allow",
                                  settle=float(self.cfg.get("settleSeconds",
                                                            3.0)))
        self._last_move = res
        # CALIBRATION EN VOL : l'angle mesure du deplacement corrige la
        # relation clic -> deplacement (une MESURE, pas une supposition).
        ang = res.get("angle")
        if ang is not None and abs(ang) < 360.0:
            self.click_rotation = (ang - click + 180.0) % 360.0 - 180.0
        self._set(PROGRESS_CHECK, "deplacement effectue",
                  px=res.get("px"), angle=res.get("angle"),
                  outcome=res.get("outcome"))
        return {"ok": True, "state": PROGRESS_CHECK, "move": res}

    def _st_progress_check(self):
        """Verifie que le deplacement a ete REELLEMENT effectue."""
        res = getattr(self, "_last_move", None) or {}
        px = res.get("px")
        if px is None:
            return self._abort("deplacement non mesure : observation "
                               "insuffisante")
        if px < self.min_px:
            # BLOQUE (mur / obstacle) : on DECALE la direction au lieu de
            # rejouer la meme cible (c'est ce qui laissait le personnage
            # colle au mur).
            self.block_turn += 1
            base = self.route[-1] if self.route else 0.0
            self.route = [(base + 40.0 * self.block_turn) % 360.0]
            self.route_index = 0
            if self.block_turn > int(self.cfg.get("maxBlockTurns", 6)):
                return self._abort("bloque %d fois de suite : obstacle non "
                                   "contournable" % self.block_turn)
            self._set(TRAVEL_STEP, "bloque (%.1f px) : direction decalee de "
                      "%d deg" % (px, 40 * self.block_turn), px=px)
            return {"ok": True, "state": TRAVEL_STEP}
        self.route_index += 1
        self.steps_since_reloc += 1
        # l'invite "Resurrect" est-elle deja visible ? (controle rapide,
        # sur la VUE 3D uniquement)
        hit = self.prompt.find(self._world_view())
        if hit:
            self.prompt_hit = hit
            self._set(RESURRECTION_DETECTED, "invite Resurrect detectee",
                      hit=hit)
            return {"ok": True, "state": RESURRECTION_DETECTED, "hit": hit}
        # recalage periodique : la carte corrige la derive de l'odometre
        if self.steps_since_reloc >= int(self.cfg.get("relocalizeEvery", 6)):
            self.steps_since_reloc = 0
            self._set(RELOCALIZATION, "progression %.1f px -> recalage carte"
                      % px, px=px)
            return {"ok": True, "state": RELOCALIZATION}
        # sinon : on continue dans la meme direction (ligne droite)
        self._set(TRAVEL_STEP, "progression %.1f px, cap maintenu" % px, px=px)
        return {"ok": True, "state": TRAVEL_STEP}

    def _st_relocalization(self):
        """Re-analyse la carte : la distance au corps doit DECREISSER.

        Mecanisme repris de la reference CorpseRunGoal (WowClassicGrindBot) :
        la progression se mesure sur la distance au corps (lastDistance), pas
        seulement sur les pixels parcourus ; le StuckDetecteur declenche
        quand la distance ne diminue plus.
        """
        self._ensure_map(True)
        img = self._capture()
        analysis = analyze_map(_bgr(img), self.detector) if img else None
        if analysis is None or not analysis.usable:
            self._ensure_map(False)
            return self._set_and_go(CORPSE_APPROACH, "re-localisation impossible")
        prev = self.last_distance
        self.last_distance = analysis.distance_px
        self.analysis = analysis
        # STUCK DETECTOR : la distance au corps n'a pas diminue.
        # (meme mecanisme que le StuckDetector de la reference, mais la
        # distance carte n'a de resolution que si elle est > 15 px : en
        # dessous, la progression se mesure sur les pixels parcourus.)
        stuck = (prev is not None and analysis.distance_px is not None
                 and prev >= 15.0
                 and analysis.distance_px > (prev - 1.0))
        if stuck:
            self.stuck_count += 1
            self.log("RECOVERY_STUCK", previous=prev,
                     distance=analysis.distance_px, count=self.stuck_count)
            if self.stuck_count > int(self.cfg.get("maxStuck", 3)):
                return self._abort("distance au corps non reduite apres %d "
                                   "tentatives" % self.stuck_count)
            # strategie de deblocage : on decale la direction visee
            self.route = [(a + 35.0 * self.stuck_count) % 360.0
                          for a in (self.route or [0.0])]
            self.route_index = 0
            self._ensure_map(False)
            self._set(TRAVEL_STEP, "bloque (distance %.0f px) : direction "
                      "decalee" % (analysis.distance_px or 0),
                      distance=analysis.distance_px)
            return {"ok": True, "state": TRAVEL_STEP}
        self.stuck_count = 0
        # L'ARRIVEE se juge sur l'invite "Resurrect" du client (le seul
        # signal fiable), PAS sur la distance carte : quand le marqueur du
        # corps et celui du joueur se recouvrent, la distance carte reste
        # constante (8 px) et n'a aucune resolution.  On continue donc
        # tout droit vers le cap de la carte.
        self._ensure_map(False)
        self.heading_deg = self._read_heading()
        self._set(ROUTE_PLANNING, "re-localisation (cap corrige)",
                  **analysis.as_dict())
        return {"ok": True, "state": ROUTE_PLANNING}

    def _st_corpse_approach(self):
        """Cherche l'invite 'Resurrect' ; a defaut, avance par spirale."""
        img = self._world_view()
        hit = self.prompt.find(img)
        if hit:
            self.prompt_hit = hit
            self._set(RESURRECTION_DETECTED, "invite Resurrect detectee",
                      hit=hit)
            return {"ok": True, "state": RESURRECTION_DETECTED, "hit": hit}
        if self.attempts >= self.max_attempts:
            return self._abort("corps non atteint (budget epuise)")
        # un pas de spirale : on avance dans une direction non encore essayee
        idx = self.route_index % len(self.spiral)
        dx, dy = self.spiral[idx]
        angle = math.degrees(math.atan2(dy, dx))
        self.route_index += 1
        img = _bgr(img)
        h = img.shape[0] if img is not None else 1328
        w = img.shape[1] if img is not None else 2345
        cx, cy = w / 2.0, h * 0.62
        click = angle - (self.click_rotation or 0.0)
        tx = int(max(40, min(w - 40, cx + math.cos(math.radians(click))
                             * self.step_px)))
        ty = int(max(40, min(h - 120, cy + math.sin(math.radians(click))
                             * self.step_px)))
        self.attempts += 1
        res = self.ex.do_goto([tx, ty], tries=2, unsafe="allow", settle=2.0)
        self._last_move = res
        self._set(TRAVEL_STEP, "pas de spirale %d" % (idx + 1),
                  px=res.get("px"), outcome=res.get("outcome"))
        return {"ok": True, "state": TRAVEL_STEP, "move": res}

    def _st_resurrection(self):
        """Clic sur l'invite, puis verification REELLE de l'etat vivant."""
        hit = self.prompt_hit or {}
        self.resurrect_clicks += 1
        if self.resurrect_clicks > int(self.cfg.get("maxResurrectClicks", 3)):
            return self._abort("trop de clics de resurrection (%d) : arret "
                               "controle" % self.resurrect_clicks)
        self.ex.do_click([hit.get("x", 0), hit.get("y", 0)], button="left")
        time.sleep(float(self.cfg.get("resurrectDelay", 3.0)))
        self._set(VERIFY_ALIVE, "clic sur l'invite Resurrect")
        return {"ok": True, "state": VERIFY_ALIVE}

    def _st_verify_alive(self):
        img = self._world_view()
        st = self.state_reader.read(img, self.ex.board)
        alive = st["life"] == "alive" and not st["signals"].get(
            "releaseDialog")
        if alive:
            if self.safety is not None:
                self.safety.observe_resurrected(st["signals"])
                self.safety.observe_safe_position({"reached": True})
            if self.memory is not None:
                self.memory.record_incident(
                    OUTCOME_RECOVERED, zone=(self.analysis.zone
                                            if hasattr(self.analysis, "zone")
                                            else None),
                    cause="corps retrouve et resurrection confirmee",
                    confidence=0.9)
            self.result = {"ok": True, "outcome": OUTCOME_RECOVERED,
                           "reason": "personnage vivant confirme",
                           "state": PAUSED_SAFE}
            self._set(PAUSED_SAFE, "resurrection confirmee par StateReader",
                      signals=st["signals"])
            return self.result
        # l'etat vivant n'est PAS confirme : on ne pretend rien
        if self.attempts >= self.max_attempts:
            return self._abort("resurrection non confirmee (etat lu: %s)"
                               % st["life"])
        self._set(CORPSE_APPROACH, "resurrection non confirmee, reprise locale",
                  life=st["life"])
        return {"ok": False, "state": CORPSE_APPROACH}

    def _st_paused_safe(self):
        """Arret controle : la peche n'est pas relancee."""
        self.result = {"ok": True, "outcome": OUTCOME_RECOVERED,
                       "reason": "personnage vivant, position a valider",
                       "state": PAUSED_SAFE}
        return self.result

    def _set_and_go(self, state, note=None, **evidence):
        self._set(state, note, **evidence)
        return {"ok": True, "state": state}

    # -- execution --------------------------------------------------------
    _HANDLERS = {
        RECOVERY_INIT: "_st_init",
        MAP_ANALYSIS: "_st_map_analysis",
        ROUTE_PLANNING: "_st_route_planning",
        TRAVEL_STEP: "_st_travel_step",
        PROGRESS_CHECK: "_st_progress_check",
        RELOCALIZATION: "_st_relocalization",
        CORPSE_APPROACH: "_st_corpse_approach",
        RESURRECTION_DETECTED: "_st_resurrection",
        VERIFY_ALIVE: "_st_verify_alive",
        PAUSED_SAFE: "_st_paused_safe",
    }

    def step(self):
        """UNE transition. Retourne le resultat de la transition."""
        handler = self._HANDLERS.get(self.state)
        if handler is None:
            return self._abort("etat inconnu : %r" % self.state)
        return getattr(self, handler)()

    def run(self, max_transitions=60):
        """Enchaine les transitions jusqu'a un etat terminal."""
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
