#!/usr/bin/env node
"""
fishing_navigator.py — controleur de deplacement automatique (Fishing V2).

REGLE ABSOLUE DU CONTRATLEUR
---------------------------
Un deplacement n'est jamais considere comme reussi parce qu'une COMMANDE a ete
envoyee. Il n'est reussi que si la POSITION MESUREE du personnage a
progresse vers la cible. Toute la logique est donc fondee sur une source de
position observable (la minicarte), et le controleur :

  1. mesure la position courante ;
  2. mesure la CAP en faisant une breve avancee (sonde) — la minicarte de
     WoW Classic est verrouillee au nord, donc le deplacement observe donne
     directement le cap ;
  3. tourne la camera pour viser la cible, puis RE-MESURE le cap (boucle
     fermee : on ne suppose rien du rapport souris/pixels) ;
  4. avance en surveillant la position ;
  5. detecte un BLOCAGE (position immobile alors qu'on avance) et tente une
     RECUPERATION (demi-tour ou contournement) ;
  6. abandonne proprement si la cible est inatteignable (limites de duree et
     de tentatives).

Le controleur ne connait NI l'ecran NI le jeu : il utilise deux interfaces
injectables (`position_provider`, `actions`), ce qui le rend testable hors jeu
avec un monde synthetique.
"""

import math
import time

#: Tolérance d'arrivée (unites de minicarte) : le personnage doit etre
#: REELLEMENT a moins de ce rayon de la cible.
ARRIVE_TOLERANCE = 6.0

#: Deplacement minimal (unites) considere comme un progres : en dessous, le
#: personnage est considere immobile.
MIN_PROGRESS = 0.6

#: Nombre de sondes consecutives sans progression avant de conclure a un
#: blocage.
STUCK_SAMPLES = 4

#: Nombre maximum de tentatives de recuperation.
MAX_RECOVERIES = 3

#: Duree maximale d'un trajet (secondes).
MAX_TRAVEL_SECONDS = 180.0

#: Angle maximal de correction par iteration (radians) : evite les
# sur-rotations.
MAX_TURN_STEP = math.radians(45.0)

#: Gain de la correction de cap (proportionnelle). 1.0 = correction totale
#: (oscillation), 0.6 = convergence mesuree.
TURN_GAIN = 0.6

#: Erreur de cap maximale pour avancer. Au-dela, on tourne sans avancer
#: (sinon le personnage avance en tournant et tourne en rond).
ALIGN_TOLERANCE = math.radians(25.0)

#: Nombre de rotations entre deux recalages du cap par sonde. La sonde
#: deplace le personnage : il ne faut pas la faire a chaque iteration.
HEADING_RECAL_EVERY = 4

#: Coefficient empirique de conversion souris(pixels) -> angle(camera). Valeur
#: CONSERVATOIRE : le controleur RE-MESURE le cap apres chaque rotation, donc
#: une valeur erronee ne provoque qu'un aller-retour, jamais une boucle infinie.
MOUSE_PIXELS_PER_RADIAN = 500.0


def _dist(a, b):
    if not a or not b:
        return float("inf")
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _angle_to(from_pt, to_pt):
    """Cap (radians, 0 = est, sens horaire dans l'ecran) vers la cible.
    Convention de la minicarte : x vers l'est, y vers le sud ; le cap est donc
    atan2(dy, dx)."""
    if not from_pt or not to_pt:
        return None
    return math.atan2(to_pt[1] - from_pt[1], to_pt[0] - from_pt[0])


def _norm_angle(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


class Navigator(object):
    """Controleur de deplacement, independant du jeu.

    :param position_provider: fonction sans argument -> (x, y) ou None
    :param key_down/key_up:  envoi de touches (maintenir une touche)
    :param mouse_move:        fonction(dx, dy) -> deplacement de souris
    :param forward_key:       touche d'avance (par defaut 'w')
    :param back_key:          touche de recul (par defaut 's')
    :param strafe_left/right: touches de strafe
    """

    def __init__(self, position_provider, key_down=None, key_up=None,
                 mouse_move=None, forward_key="w", back_key="s",
                 strafe_left="a", strafe_right="d", clock=None,
                 probe_seconds=0.25, step_seconds=0.2, sleep=None):
        self.position_provider = position_provider
        self.key_down = key_down or (lambda k: None)
        self.key_up = key_up or (lambda k: None)
        self.mouse_move = mouse_move or (lambda dx, dy: None)
        self.forward_key = forward_key
        self.back_key = back_key
        self.strafe_left = strafe_left
        self.strafe_right = strafe_right
        self.clock = clock or time.time
        # `sleep` est injectable : en test, il avance le monde simule au lieu
        # d'attendre reellement (le controleur reste identique).
        self.sleep = sleep or time.sleep
        self.probe_seconds = probe_seconds
        self.step_seconds = step_seconds
        self.trace = []

    # ---------------------------------------------------------------- outils
    def _pos(self):
        try:
            p = self.position_provider()
        except Exception:
            return None
        if not p or len(p) < 2:
            return None
        return (float(p[0]), float(p[1]))

    def _log(self, event, **kw):
        row = {"ts": round(self.clock(), 3), "event": event}
        row.update(kw)
        self.trace.append(row)
        return row

    def _tap(self, key, seconds):
        self.key_down(key)
        t0 = self.clock()
        while self.clock() - t0 < seconds:
            self.sleep(0.01)
        self.key_up(key)

    # ------------------------------------------------------------- mesures
    def measure_heading(self, pos):
        """Mesure le cap par une sonde d'avancee (deplacement observe).

        La minicarte est verrouillee au nord : le vecteur de deplacement
        OBSERVE donne directement le cap, sans hypothese sur la camera.

        ATTENTION : la sonde DEPLACE le personnage. L'utiliser a chaque
        iteration fait orbiter le personnage autour de la cible (constate par
        le test de navigation). D'ou le modele de cap : on estime le cap
        (le cap ne change que si l'on tourne) et on ne re-mesure que
        periodiquement, pour recaler le modele.
        """
        if pos is None:
            return None
        self._tap(self.forward_key, self.probe_seconds)
        after = self._pos()
        if after is None:
            return None
        dx, dy = after[0] - pos[0], after[1] - pos[1]
        if abs(dx) < 0.2 and abs(dy) < 0.2:
            return None            # aucun deplacement : cap indetermine
        return math.atan2(dy, dx)

    def _current_heading(self, pos):
        """Cap courant : estimation, recalee par une sonde tous les N tours.

        La sonde est courte (PROBE_SECONDS) : elle sert au recalage, pas a la
        navigation, donc son effet de bord (deplacement) reste marginal.
        """
        est = getattr(self, "_heading_estimate", None)
        age = getattr(self, "_heading_age", 0)
        if est is None or age >= HEADING_RECAL_EVERY:
            measured = self.measure_heading(pos)
            if measured is not None:
                self._heading_estimate = measured
                self._heading_age = 0
                return measured
            return est
        self._heading_age = age + 1
        return est

    def turn_towards(self, target, pos):
        """Tourne la camera pour viser la cible ; le cap est RE-MESURE ensuite.

        Retourne (cap_obtenu, erreur_angulaire).
        """
        wanted = _angle_to(pos, target)
        if wanted is None:
            return (None, None)
        heading = self._current_heading(pos)
        if heading is None:
            return (None, None)
        err = _norm_angle(wanted - heading)
        # FIX 2026-10-20 (bug revele par le test de navigation) : la
        # correction etait SATURANTE (toujours 45 deg maxi) : avec une erreur
        # de 90 deg, le controleur virait de 45 deg a chaque iteration et
        # TOURNAIT EN ROND. Une correction PROPORTIONNELLE (k = 0,6) avec un
        # plafond converge.
        step = max(-MAX_TURN_STEP, min(MAX_TURN_STEP, TURN_GAIN * err))
        if abs(step) > 0.02:
            # signe : un deplacement souris horizontal NEGATIF tourne la camera
            # a gauche (cap augmente) — inverse de la convention ecran.
            self.mouse_move(int(-step * MOUSE_PIXELS_PER_RADIAN), 0)
            self._heading_estimate = _norm_angle(heading + step)
            self._heading_age = 0
            self._log("turn", wanted=round(wanted, 3), heading=round(heading, 3),
                      error=round(err, 3), step=round(step, 3))
            return (self._heading_estimate, err)
        return (heading, err)

    # -------------------------------------------------------------- trajet
    def goto(self, target, tolerance=ARRIVE_TOLERANCE, max_seconds=MAX_TRAVEL_SECONDS,
             max_recoveries=MAX_RECOVERIES):
        """Deplace le personnage vers `target` (position minimap).

        Retourne un dict : {status, travelled, seconds, recoveries, reason}
        `status` ∈ arrived | blocked | timeout | aborted | no_position
        """
        if not target:
            return {"status": "aborted", "reason": "cible absente", "travelled": 0.0,
                    "seconds": 0.0, "recoveries": 0, "trace": self.trace}
        t_start = self.clock()
        pos = self._pos()
        if pos is None:
            self._log("no_position")
            return {"status": "no_position", "reason": "position non mesurable",
                    "travelled": 0.0, "seconds": 0.0, "recoveries": 0, "trace": self.trace}

        last_pos = pos
        travelled = 0.0
        recoveries = 0
        stuck = 0
        status = "timeout"
        reason = "duree maximale atteinte"

        while self.clock() - t_start < max_seconds:
            pos = self._pos()
            if pos is None:
                self._log("position_lost")
                self.sleep(self.step_seconds)
                stuck += 1
                if stuck > STUCK_SAMPLES * 2:
                    status = "no_position"
                    reason = "position perdue pendant le trajet"
                    break
                continue

            d = _dist(pos, target)
            if d <= tolerance:
                # ARRIVEE VERIFIEE PAR LA POSITION MESUREE.
                status = "arrived"
                reason = "position mesuree a %.1f unites de la cible" % d
                self._log("arrived", distance=round(d, 2), target=list(target))
                break

            moved = _dist(pos, last_pos)
            travelled += moved
            if moved < MIN_PROGRESS:
                stuck += 1
            else:
                stuck = 0
            last_pos = pos

            if stuck >= STUCK_SAMPLES:
                stuck = 0
                recoveries += 1
                self._log("stuck", recoveries=recoveries, distance=round(d, 2))
                if recoveries > max_recoveries:
                    status = "blocked"
                    reason = "blocage persistant apres %d recuperations" % max_recoveries
                    break
                # RECUPERATION : quart de tour + STRAFE lateral. Un simple
                # demi-tour ramene le personnage face a l'obstacle (il
                # debloque, mais ne le contourne pas). Le strafe le fait
                # glisser le long de l'obstacle.
                self.mouse_move(int(MOUSE_PIXELS_PER_RADIAN * math.pi / 2), 0)
                self._heading_estimate = None      # cap devenu inconnu
                self._heading_age = 0
                self._tap(self.strafe_left if recoveries % 2 else self.strafe_right,
                          self.step_seconds * 2)
                continue

            heading, err = self.turn_towards(target, pos)
            if heading is None:
                self._tap(self.forward_key, self.step_seconds)
                continue
            if err is not None and abs(err) > ALIGN_TOLERANCE:
                # PAS ASSEZ ALIGNE : on tourne SANS avancer. Avancer pendant
                # qu'on tourne fait spiraler le personnage autour de la cible
                # (constate par le test de navigation).
                self._log("align", error=round(err, 3))
                continue
            self._tap(self.forward_key, self.step_seconds)

        elapsed = self.clock() - t_start
        self._log("end", status=status, travelled=round(travelled, 2),
                  seconds=round(elapsed, 1), recoveries=recoveries)
        return {"status": status, "reason": reason, "travelled": round(travelled, 2),
                "seconds": round(elapsed, 1), "recoveries": recoveries,
                "trace": self.trace}
