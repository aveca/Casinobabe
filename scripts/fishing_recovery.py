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
        self._ensure_map(False)
        target = a.bearing_deg
        # ANGLE DE CLIC : appris en ligne.  On ne suppose rien de la
        # camera : le cap du personnage est relu sur la fleche de la
        # minicarte apres chaque clic, et l'angle est corrige de l'ecart
        # (cap vise - cap obtenu).  Au premier pas, l'angle est deduit du
        # cap de depart ; ensuite il converge.
        head_now = self._read_heading()
        if head_now is not None:
            self.heading = head_now
        if self.theta is None:
            if self.heading is None:
                # aucun cap lisible : on tente la conversion directe
                theta = map_bearing_to_screen(target, self.heading or 0.0)
            else:
                theta = map_bearing_to_screen(target, self.heading)
            if theta is None:
                theta = -90.0
        else:
            theta = (self.theta + (target - (self.heading or 0.0))
                     + 180.0) % 360.0 - 180.0
        self.theta = theta
        self.attempts += 1
        before = _bgr(self._capture())
        # On passe par l'EXECUTEUR : c'est lui qui arme l'auto-run (Z),
        # qui retente une fois, qui mesure et qui VALIDE l'ecran apres
        # l'action.  (un clic nu sans auto-run arme ne deplace RIENT :
        # mesure 0,01 px.)
        ccx, ccy = self._center()
        tx = int(ccx + math.cos(math.radians(theta)) * self.click_px)
        ty = int(ccy + math.sin(math.radians(theta)) * self.click_px)
        res = self.ex.do_goto([tx, ty], tries=2, unsafe="allow",
                              settle=float(self.cfg.get("moveSeconds", 2.4)))
        px = res.get("px")
        ang = res.get("angle")
        after = _bgr(self._capture())
        head2 = self._read_heading()
        if head2 is not None:
            self.heading = head2
            self._refine_rotation(head2, theta)
        # AUTO-DIAGNOSTIC : si rien n'a bouge, on conserve la capture et
        # on consigne POURQUOI (le moteur doit pouvoir se corriger seul).
        if px is None or px < 0.5:
            path = self._evidence("noprogress-%d" % self.attempts, after)
            self.log("RECOVERY_NO_PROGRESS", attempt=self.attempts,
                     px=None if px is None else round(px, 2),
                     theta=round(theta, 1),
                     target=round(target, 1),
                     head=None if head2 is None else round(head2, 1),
                     screen=res.get("screen"),
                     evidence=path)
        self._last = {"px": px, "angle": ang, "theta": theta,
                      "target": target, "headBefore": self.heading,
                      "headAfter": head2, "click": [tx, ty]}
        self._set(PROGRESS_CHECK, "pas de navigation",
                  px=None if px is None else round(px, 2),
                  theta=round(theta, 1), target=round(target, 1),
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
            self.theta = ((self.theta or 0.0) + 45.0 * self.stuck_count) \
                % 360.0
            self.log("RECOVERY_STUCK", attempt=self.attempts,
                     px=round(px, 2), count=self.stuck_count,
                     newTheta=round(self.theta, 1))
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

    def _st_corpse_interaction(self):
        """Cherche l'invite d'interaction AVEC LE CORPS (vue 3D)."""
        img = self._world_view()
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
        """CONFIRMATION INDEPENDANTE de la resurrection."""
        img = self._world_view()
        st = self.state_reader.read(img, self.ex.board)
        alive = (st["life"] == "alive"
                 and not st["signals"].get("releaseDialog"))
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
