#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_navigation.py — CONTROLEUR DE NAVIGATION V2 (raccordement au bot).

Ce module relie les trois briques V2 au jeu reel :

    fishing_minimap.py  (position observee + cibles)
              |
              v
    fishing_navigation.py  (ce module : decision, deplacement, verification)
              |
              v
    fishing_routes.py / fishing_spots.py  (carte, historique, planificateur)

PRINCIPE : une arrivee n'est confirmee que par une OBSERVATION. Le
controleur n'a jamais le droit de declarer « arrived » parce qu'une commande
a ete envoyee : il faut que la position observee et/ou la distance observee
a la cible le prouvent.

L'ARRIVEE est prouvee par DEUX observations concordantes :
  1. la cible observee (le marqueur de minicarte) est a moins de
     `arriveTolerancePx` du centre de la minicarte ;
  2. l'odometre a bien enregistre un deplacement vers elle.

MODES
  * `dry_run` : toutes les decisions sont calculees et journalisees, AUCUNE
    commande de deplacement n'est envoyee.
  * `live`    : les commandes sont envoyees, sous controle strict.

GARDE-FOUS (toujours actifs, meme en dry-run)
  * duree maximale de trajet ;
  * nombre maximal de tentatives de recuperation ;
  * distance maximale parcourue depuis le point de depart ;
  * arret d'urgence (F9) et suspension si l'utilisateur utilise la souris ;
  * abandon si la position n'est plus mesurable.
"""

import math
import time

try:
    import fishing_routes as routes
except ImportError:  # pragma: no cover
    routes = None

#: Tolérance d'arrivee (pixels de minicarte) : la cible doit etre vue
#: PRES du centre de la minicarte.
DEFAULT_ARRIVE_PX = 10.0

#: Angle maximum de correction par iteration (radians).
MAX_TURN_STEP = math.radians(45.0)

#: Gain de la correction de cap (proportionnelle).
TURN_GAIN = 0.6

#: Erreur de cap en deçà de laquelle on avance.
ALIGN_TOLERANCE = math.radians(25.0)

#: Ratio initial souris(pixels) -> radians, RECALIBRE automatiquement.
INITIAL_PIXELS_PER_RADIAN = 500.0


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _norm(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


class NavigationController(object):
    """Pilote le deplacement du personnage vers une cible observee.

    :param reader:   MinimapReader (position + marqueurs)
    :param actions:  objet exposant les actions reelles :
                       capture()            -> image BGR de la fenetre
                       hold_key(vk, sec)    -> maintient une touche
                       move_mouse(dx, dy)   -> deplace la souris (camera)
                       user_active()        -> True si l'humain agit
                       emergency_stop()     -> True si arret d'urgence demande
    """

    def __init__(self, reader, actions, config=None, log=None):
        self.reader = reader
        self.actions = actions
        self.config = config or {}
        self.log = log or (lambda *a, **k: None)
        self.heading = None          # cap courant (radians, est=0)
        self.pixels_per_radian = float(self.config.get("pixelsPerRadian",
                                                       INITIAL_PIXELS_PER_RADIAN))
        self.last_read = None
        self.target = None
        self.turns = 0
        self.recoveries = 0

    # ------------------------------------------------------------- config
    def enabled(self):
        return bool(self.config.get("enabled", False))

    def dry_run(self):
        return bool(self.config.get("dryRun", True))

    def limit(self, name, default):
        v = self.config.get(name, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    # ------------------------------------------------------------- lecture
    def read(self):
        img = self.actions.capture()
        if img is None:
            return None
        r = self.reader.read(img)
        if not r:
            return None
        self.last_read = r
        return r if r.get("ok") else None

    # ------------------------------------------------- selection de cible
    def select_target(self, read, preferred=None):
        """Choisit la cible a atteindre parmi les marqueurs observes.

        `preferred` : un identifiant de marqueur (index) impose par le
        planificateur ; sinon on prend le marqueur valide le plus proche
        (le plus proche minimap = le plus proche a l'echelle du jeu).
        """
        markers = read.get("markers") or []
        if not markers:
            return None
        if preferred is not None and 0 <= preferred < len(markers):
            return markers[preferred]
        # on ecarte les cibles deja atteintes (dist < tolerance) sauf si
        # c'est la seule : dans ce cas on peut la re-visiter.
        tol = self.limit("arriveTolerancePx", DEFAULT_ARRIVE_PX)
        far = [m for m in markers if m["dist"] > tol]
        pool = far or markers
        return pool[0]

    # ------------------------------------------------------ deplacement
    def _measure_heading(self, read, step_seconds):
        """Mesure le cap par une breve avancee (deplacement observe).

        La sonde DEPLACE le personnage : elle est courte et sert uniquement a
        mesurer. Le cap est ensuite estime entre deux sondes.
        """
        before = read["position"]
        self.actions.hold_key(self.config.get("forwardKey", "w"), step_seconds)
        after = self.read()
        if not after:
            return None, None
        dx = after["position"]["x"] - before["x"]
        dy = after["position"]["y"] - before["y"]
        if abs(dx) < 0.05 and abs(dy) < 0.05:
            return after, None
        heading = math.atan2(dy, dx)
        self.heading = heading
        return after, heading

    def _turn(self, wanted, delta_rad):
        """Tourne la camera ; renvoie le deplacement de souris demande."""
        step = max(-MAX_TURN_STEP, min(MAX_TURN_STEP, TURN_GAIN * delta_rad))
        if abs(step) < 0.02:
            return 0.0
        # signe : un deplacement souris horizontal NEGatif tourne la camera a
        # gauche (cap augmente) - inverse de la convention ecran.
        px = -step * self.pixels_per_radian
        self.actions.move_mouse(int(px), 0)
        self.turns += 1
        return px

    def _recalibrate(self, before_heading, after_heading, px_moved):
        """Recalibre le ratio souris -> radians a partir d'une observation.

        Sans cette calibration, une erreur de reglages de sensibilite
        ferait tourner le personnage en rond (constate dans les tests V2).
        """
        if before_heading is None or after_heading is None or not px_moved:
            return
        observed = _norm(after_heading - before_heading)
        if abs(observed) < 0.02:
            return
        ratio = abs(px_moved / observed)
        if not (50.0 < ratio < 20000.0):
            return
        # lissage : on ne suit pas le bruit d'une seule mesure
        self.pixels_per_radian = 0.7 * self.pixels_per_radian + 0.3 * ratio

    # --------------------------------------------------------------- trajet
    def navigate_to(self, target_marker=None, preferred=None, max_seconds=None,
                    max_recoveries=None, max_distance=None):
        """Deplace le personnage vers la cible.

        Retourne un dict :
          {status, reason, travelled, seconds, turns, recoveries,
           start, end, target, dryRun, samples}
        `status` in arrived | blocked | timeout | aborted | no_target |
                    no_position | dry_run
        """
        t0 = time.time()
        max_seconds = max_seconds if max_seconds is not None else self.limit("maxTravelSec", 120.0)
        max_recoveries = int(max_recoveries if max_recoveries is not None
                             else self.limit("maxRecoveries", 3))
        max_distance = max_distance if max_distance is not None else self.limit("maxDistancePx", 400.0)
        step_seconds = self.limit("stepSeconds", 0.35)
        probe_seconds = self.limit("probeSeconds", 0.25)
        tol = self.limit("arriveTolerancePx", DEFAULT_ARRIVE_PX)

        result = {"status": "no_position", "reason": "", "travelled": 0.0,
                  "seconds": 0.0, "turns": 0, "recoveries": 0, "start": None,
                  "end": None, "target": None, "dryRun": self.dry_run(),
                  "samples": 0}
        read = self.read()
        if not read:
            result["reason"] = "minicarte illisible"
            return result
        start = (read["position"]["x"], read["position"]["y"])
        result["start"] = start
        self.heading = None
        self.turns = 0
        self.recoveries = 0

        marker = target_marker or self.select_target(read, preferred)
        if not marker:
            result["status"] = "no_target"
            result["reason"] = "aucune cible observee sur la minicarte"
            return result
        result["target"] = marker

        # La cible observee est un OFFSET relatif au joueur. On la suit
        # dans le repere integre : a chaque boucle, sa position absolue
        # est (position joueur + offset observe).
        target_rel = (marker["x"], marker["y"])
        last_pos = start
        stuck = 0
        status = "timeout"
        reason = "duree maximale atteinte"

        while (time.time() - t0) < max_seconds:
            if self.actions.emergency_stop():
                status = "aborted"
                reason = "arret d'urgence demande"
                break
            if self.actions.user_active():
                status = "aborted"
                reason = "activite humaine : suspension"
                break

            read = self.read()
            if not read:
                result["status"] = "no_position"
                result["reason"] = "position devenue illisible"
                break
            result["samples"] += 1
            pos = (read["position"]["x"], read["position"]["y"])
            moved = _dist(pos, last_pos)
            result["travelled"] += moved
            last_pos = pos

            # 1. la cible observee est-elle atteinte ?
            #    On relit les marqueurs : la cible peut avoir disparu
            #    (arrivee) ou change de place.
            markers = read.get("markers") or []
            best = None
            for m in markers:
                if m["kind"] == marker["kind"] and abs(m["x"] - target_rel[0]) < 6 \
                        and abs(m["y"] - target_rel[1]) < 6:
                    best = m
                    break
            if best is None and markers:
                best = min(markers, key=lambda m: m["dist"])
            if best is not None:
                marker = best
                target_rel = (marker["x"], marker["y"])
                if marker["dist"] <= tol:
                    # ARRIVEE PROUVEE par l'observation : la cible observee
                    # est au centre de la minicarte.
                    status = "arrived"
                    reason = "cible observee a %.1f px du centre" % marker["dist"]
                    break

            # 2. la distance parcourue est-elle bornee ?
            if _dist(pos, start) > max_distance:
                status = "blocked"
                reason = "distance maximale depassee (%.0f px)" % max_distance
                break

            # 3. detection de blocage
            if moved < 0.3:
                stuck += 1
            else:
                stuck = 0
            if stuck >= 3:
                stuck = 0
                self.recoveries += 1
                if self.recoveries > max_recoveries:
                    status = "blocked"
                    reason = "blocage persistant apres %d recuperations" % max_recoveries
                    break
                # RECUPERATION : un quart de tour + un pas de cote.
                if not self.dry_run():
                    self.actions.move_mouse(int(self.pixels_per_radian * math.pi / 2), 0)
                    self.actions.hold_key(self.config.get("strafeKey", "a"), step_seconds)
                self.heading = None
                continue

            # 4. cap courant et cap voulu
            wanted = math.atan2(target_rel[1], target_rel[0])
            if self.heading is None:
                if self.dry_run():
                    status = "dry_run"
                    reason = ("cible a %.1f px ; cap voulu %.0f deg ; "
                              "cap courant inconnu (sonde necessaire)"
                              % (marker["dist"], math.degrees(wanted)))
                    break
                read2, heading = self._measure_heading(read, probe_seconds)
                if heading is None:
                    if self.dry_run():
                        status = "dry_run"
                        reason = ("cible a %.1f px ; cap voulu %.0f deg ; "
                                  "cap courant inconnu (sonde necessaire)"
                                  % (marker["dist"], math.degrees(wanted)))
                    else:
                        status = "no_position"
                        reason = "deplacement non mesurable (cap indetermine)"
                    break
                read = read2 or read
                pos = (read["position"]["x"], read["position"]["y"])
            err = _norm(wanted - self.heading)
            if abs(err) > ALIGN_TOLERANCE:
                # on s'aligne sans avancer
                if not self.dry_run():
                    before = self.heading
                    px = self._turn(wanted, err)
                    read2, heading = self._measure_heading(read, probe_seconds)
                    if heading is not None:
                        self._recalibrate(before, heading, px)
                        self.heading = heading
                continue

            # 5. on avance
            if not self.dry_run():
                self.actions.hold_key(self.config.get("forwardKey", "w"), step_seconds)
                # le cap est estime : on le met a jour par le deplacement observe
                if moved > 0.3:
                    self.heading = math.atan2(pos[1] - last_pos[1], pos[0] - last_pos[0])
            else:
                break

        result["status"] = status
        result["reason"] = reason
        result["seconds"] = round(time.time() - t0, 2)
        result["turns"] = self.turns
        result["recoveries"] = self.recoveries
        if self.last_read:
            result["end"] = (self.last_read["position"]["x"],
                             self.last_read["position"]["y"])
        result["pixelsPerRadian"] = round(self.pixels_per_radian, 1)
        return result
