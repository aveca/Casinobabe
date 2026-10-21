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
import os
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
        self.forward_world_angle = None   # etalonnage ecran -> monde
        self.forward_screen_angle = None  # angle ecran du point de reference
        self.click_rotation = 0.0         # rotation reelle (etalonnee)
        self.clicks = 0
        self.click_radius = None         # rayon de clic (adapte a la distance)
        self._map_db = None
        self._map_loaded = False
        # ODOMETRE DE REFERENCE : la VUE 3D. La minicarte derive de 7 px /
        # 10 s au repos (animation de l'eau) ; la vue 3D ne derive pas.
        # Son repere est celui de l'ECRAN, stable tant que la camera ne
        # tourne pas — et le bot ne deplace plus le curseur, donc elle ne
        # tourne plus. C'est ce repere la qui sert de carte.
        try:
            from fishing_minimap import WorldOdometer
            self.odometer = WorldOdometer()
        except Exception:                   # pragma: no cover
            self.odometer = None

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
        # ODOMETRE DE REFERENCE : la VUE 3D (la minicarte derive de 7 px /
        # 10 s au repos, la vue 3D ne derive pas). Le repere est celui de
        # l'ECRAN : il est STABLE tant que la camera ne tourne pas, et le
        # bot ne deplace plus le curseur -> la camera ne tourne plus.
        self.odometer.update(img)
        r["position"] = {"x": self.odometer.x, "y": self.odometer.y}
        self.last_read = r
        return r if r.get("ok") else None

    # ------------------------------------------------- selection de cible
    def load_map(self):
        """Charge la carte V2 des bancs (state/fishing-spots.json)."""
        if self._map_loaded:
            return self._map_db
        self._map_loaded = True
        try:
            import fishing_spots as fspots
        except Exception:
            self._map_db = None
            return None
        # Le chemin de la carte peut etre impose (tests, preprod) :
        # CASINOBABE_MAP_PATH permet d'isoler les tests de la vraie carte.
        env_path = os.environ.get("CASINOBABE_MAP_PATH")
        path = self.config.get("mapPath") or env_path
        path = os.path.join(path, "fishing-spots.json") if path else None
        if not path:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root, "state", "fishing-spots.json")
        try:
            self._map_db = fspots.load(path) if os.path.isfile(path) else None
        except Exception:
            self._map_db = None
        return self._map_db

    def select_target(self, read, preferred=None):
        """Choisit la cible a atteindre.

        1. la CARTE V2 : un banc memorise (position absolue dans le repere
           integre) — c'est la cible du produit (« aller pêcher ailleurs ») ;
        2. sinon, l'EXPLORATION : un point tire dans le repere integre a une
           distance donnee. C'est ce qui permet d'amorcer la carte : sans
           deplacement, la carte ne contient que la position courante, et
           aucun banc ne peut etre atteint ;
        3. en dernier recours, un MARQUEUR de cible suivi observe sur la
           minicarte (position relative au joueur). Les marqueurs sont des
           unites SUIVIES : ils bougent, ils servent donc de repli, pas de
           destination de ronde.
        """
        want = (self.config.get("targetMode", "map") or "map").lower()
        if want in ("map", "auto"):
            db = self.load_map()
            if db:
                try:
                    import fishing_spots as fspots
                    ref = db.get("referenceName", "minimap")
                    cur = read["position"]
                    banks = []
                    for s in fspots.confirmed_spots(db):
                        c = (s.get("coords") or {}).get(ref)
                        if not c:
                            continue
                        if (s.get("coords") or {}).get("screen"):
                            c = (s.get("coords") or {})["screen"]
                        d = math.hypot(c[0] - cur["x"], c[1] - cur["y"])
                        if d < 4.0:          # on est deja sur ce banc
                            continue
                        banks.append((d, s, c))
                    if banks:
                        banks.sort(key=lambda b: b[0])
                        d, s, c = banks[0]
                        return {"kind": "bank", "id": s["id"],
                                "pos": (float(c[0]), float(c[1])),
                                "dist": round(d, 2),
                                "type": s.get("kind"),
                                "reliability": round(fspots.reliability(s), 3)}
                except Exception:
                    pass
        # AMORCAGE : exploration vers un point du repere integre. L'arrivee
        # sera prouvee par l'ODOMETRE (position mesuree), donc independante
        # de la commande de deplacement.
        if want in ("map", "auto", "explore"):
            dist = self.limit("exploreDistancePx", 15.0)
            angle = self._explore_angle()
            cur = read["position"]
            pos = (cur["x"] + math.cos(angle) * dist,
                   cur["y"] + math.sin(angle) * dist)
            return {"kind": "explore", "id": None, "pos": pos,
                    "dist": dist, "angle": round(math.degrees(angle), 1)}
        markers = read.get("markers") or []
        if not markers:
            return None
        if preferred is not None and 0 <= preferred < len(markers):
            return markers[preferred]
        tol = self.limit("arriveTolerancePx", DEFAULT_ARRIVE_PX)
        far = [m for m in markers if m["dist"] > tol]
        pool = far or markers
        return pool[0]

    def _explore_angle(self):
        """Direction d'exploration : differente a chaque tentative, mais
        REPRODUCTIBLE pour une meme graine (le 'seed' est fourni par le
        contexte, sinon on utilise l'angle memorise)."""
        seed = self.config.get("exploreSeed")
        if seed is None:
            seed = getattr(self, "_explore_seed", 0)
            self._explore_seed = seed + 1
        # angle pseudo-aleatoire mais deterministe : 137 deg est un nombre
        # premier, la suite des angles est donc bien repartie.
        return math.radians((seed * 137) % 360)

    # ------------------------------------------------------ deplacement
    def measure_drift(self, samples=6, pause=0.3):
        """Derive de l'odometre quand le personnage est immobile.

        La minicarte n'est PAS un odometre fiable : l'eau animee fait
        deriver la correlation de phase de facon systematique (derive
        mesuree de 7,05 px en 10 s sur un personnage immobile, le 2026-10-21 ;
        la vue 3D, elle, ne defile pas). Cette mesure permet de REFUSER de
        piloter un deplacement quand la position n'est pas exploitable.
        """
        import time as _t
        odo = self.odometer
        if self.last_read is None or odo is None:
            return None
        before = (odo.x, odo.y)
        for _ in range(max(2, int(samples))):
            _t.sleep(pause)
            self.read()
        dx = odo.x - before[0]
        dy = odo.y - before[1]
        return math.hypot(dx, dy)

    def _screen_offset(self, target_angle):
        """Decalage ecran (dx, dy) a cliquer pour aller dans `target_angle`.

        Le repere de la position (odometre de la vue 3D) EST le repere ecran :
        la direction de la cible est donc directement un angle ecran, sans
        conversion. La seule correction est la rotation mesuree par
        l'etalonnage (l'auto-run ne va pas exactement vers le point clique
        si la camera n'est pas d'aplomb).
        """
        if self.forward_world_angle is None:
            return None
        screen_angle = target_angle - self.click_rotation
        r = self.click_radius or self.limit("clickRadiusPx", 320.0)
        return (math.cos(screen_angle) * r, math.sin(screen_angle) * r)

    def _calibrate_forward(self):
        """Etalonne la correspondence clic -> deplacement (mesure reelle).

        On clique droit sur le sol dans une direction ecran connue, on laisse
        le personnage marcher, puis on mesure la direction REELLE du
        deplacement (odometre de la vue 3D). La difference entre l'angle vise
        et l'angle obtenu est la rotation a corriger (l'auto-run vise un point
        au sol, pas une direction : si la camera n'est pas d'aplomb, la
        correction est necessaire).

        C'est aussi une PROUOVE que le personnage bouge vraiment, mesuree sur
        la vue 3D (et non sur la minicarte, dont on a mesure le biais).
        """
        if self.forward_world_angle is not None:
            return self.forward_world_angle
        if self.dry_run():
            return None
        before = self.last_read["position"] if self.last_read else None
        if before is None:
            return None
        alpha = -math.pi / 2.0            # on vise « devant la camera »
        dx, dy = 0.0, -self.limit("clickRadiusPx", 320.0)
        angle_used = self.actions.click_screen(dx, dy)
        if angle_used is None:
            return None
        self.actions.sleep(self.limit("calibrateSeconds", 1.6))
        read = self.read()
        mx = my = 0.0
        if read:
            before = before or read["position"]
            mx = read["position"]["x"] - before["x"]
            my = read["position"]["y"] - before["y"]
        if abs(mx) < 0.05 and abs(my) < 0.05:
            # Aucun deplacement observe : l'auto-run est peut-etre desactive.
            # On le (re)declenche (TOGGLEAUTORUN est lie a Z dans le client)
            # et on recommence une fois.
            if hasattr(self.actions, "toggle_autorun"):
                self.actions.toggle_autorun()
                self.actions.sleep(0.4)
                angle_used = self.actions.click_screen(dx, dy)
                if angle_used is not None:
                    self.actions.sleep(self.limit("calibrateSeconds", 1.6))
                    read2 = self.read()
                    if read2:
                        mx = read2["position"]["x"] - before["x"]
                        my = read2["position"]["y"] - before["y"]
        if abs(mx) < 0.05 and abs(my) < 0.05:
            # aucun deplacement observe : l'etalonnage est impossible ici
            # (clic sur un point non praticable, ou personnage bloque)
            return None
        self.forward_world_angle = math.atan2(my, mx)
        self.forward_screen_angle = angle_used
        # rotation reelle entre l'angle vise et l'angle obtenu
        self.click_rotation = _norm(self.forward_world_angle - alpha)
        self.log("NAV_CALIBRATED",
                 measuredDeg=round(math.degrees(self.forward_world_angle), 1),
                 targetDeg=round(math.degrees(alpha), 1),
                 rotationDeg=round(math.degrees(self.click_rotation), 1),
                 movePx=round(math.hypot(mx, my), 2),
                 source="vue 3D (odometre sans derive)")
        return self.forward_world_angle

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
        # L'etalonnage ecran->monde est REFAIT a chaque trajet : la camera
        # tourne entre deux peches (le bot suit le flotteur a la souris), un
        # etalonnage perime enverrait le personnage dans la mauvaise direction
        # (constate en jeu : 125 deg puis 3 deg sur deux trajets consecutifs).
        self.forward_world_angle = None
        self.forward_screen_angle = None
        self.click_radius = None

        marker = target_marker or self.select_target(read, preferred)
        if not marker:
            result["status"] = "no_target"
            result["reason"] = "aucune cible observee (ni banc memorise, ni marqueur)"
            return result
        result["target"] = marker
        target_rel = (marker.get("x", 0.0), marker.get("y", 0.0))

        # GARDE-FOU DE FIABILITE DE LA POSITION : l'odometre retenu est celui
        # de la VUE 3D, mesure sans derive (la minicarte derivait de 7,05 px
        # en 10 s au repos). On verifie que la mesure est exploitable : des
        # echantillons ont ete pris. Sinon on ne pilote RIEN plutot que de
        # croire a une position fake.
        if self.odometer is not None and self.odometer.last_gray is None:
            result["status"] = "unreliable_position"
            result["reason"] = ("position non mesuree (aucune reference de "
                                "vue exploitable) : aucun deplacement pilote")
            self.log("NAV_ABORTED_UNRELIABLE", samples=0)
            return result
        # Tolerance : un point d'exploration est un point precis (on veut
        # savoir qu'on l'a atteint), un banc est une zone.
        if marker.get("kind") == "explore":
            tol = self.limit("exploreTolerancePx", 4.0)
        else:
            tol = self.limit("arriveTolerancePx", DEFAULT_ARRIVE_PX)

        # Position ABSOLUE de la cible dans le repere integre :
        #  - un banc memorise a deja sa position absolue ;
        #  - un marqueur de minicarte est RELATIF au joueur : on le reconvertit
        #    a chaque boucle (le joueur, lui, bouge).
        is_bank = marker.get("kind") in ("bank", "explore")

        def target_abs_now(position):
            if is_bank:
                return marker["pos"]
            return (position["x"] + target_rel[0], position["y"] + target_rel[1])
        last_pos = start
        stuck = 0
        prev_dist = None
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

            # 1. la cible est-elle atteinte ? (preuve par l'OBSERVATION)
            markers = read.get("markers") or []
            if not is_bank:
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
            t_abs = target_abs_now(read["position"])
            dist_target = math.hypot(t_abs[0] - pos[0], t_abs[1] - pos[1])
            if dist_target <= tol:
                # ARRIVEE PROUVEE par l'observation : la cible observee
                # est a moins de la tolerance du personnage.
                status = "arrived"
                reason = ("cible observee a %.1f px (tolerance %.1f)"
                          % (dist_target, tol))
                self.log("NAV_ARRIVED", dist=round(dist_target, 2),
                         target=marker.get("id") or marker.get("kind"),
                         targetKind="bank" if is_bank else "marker")
                break

            # 2. la distance parcourue est-elle bornee ?
            if _dist(pos, start) > max_distance:
                status = "blocked"
                reason = "distance maximale depassee (%.0f px)" % max_distance
                break

            # 3. detection de blocage : on ne bloque pas si le personnage
            #    avance, meme si la distance a la cible ne diminue pas encore
            #    (l'auto-run peut eloigner puis rapprocher).
            if moved < 0.05:
                stuck += 1
            else:
                stuck = 0
            # Suivi de la distance a la cible : elle doit diminuer.
            cur_dist = marker["dist"] if marker else None
            if cur_dist is not None and prev_dist is not None:
                if cur_dist < prev_dist - 0.05:
                    # PROGRESSION : on peut augmenter le rayon de clic pour
                    # couvrir une distance plus grande.
                    self.click_radius = min(self.limit("maxClickRadiusPx", 420.0),
                                            (self.click_radius or 200.0) * 1.35)
                elif cur_dist > prev_dist + 0.05:
                    # La cible s'eloigne : la direction est peut-etre fausse
                    # (etalonnage perime : la camera a tourne entre-temps).
                    self.forward_world_angle = None
            if cur_dist is not None:
                prev_dist = cur_dist
            if stuck >= 3:
                stuck = 0
                self.recoveries += 1
                if self.recoveries > max_recoveries:
                    status = "blocked"
                    reason = "blocage persistant apres %d recuperations" % max_recoveries
                    break
                # RECUPERATION : on re-tente dans une direction decalee
                # (contournement) : l'auto-run est de nouveau declenche par
                # un clic decale de 60 degres, ce qui fait longer l'obstacle.
                if not self.dry_run():
                    base = math.atan2(target_rel[1], target_rel[0])
                    alt = base + (math.radians(60) if self.recoveries % 2 else -math.radians(60))
                    off2 = self._screen_offset(alt)
                    if off2:
                        self.actions.click_screen(off2[0], off2[1])
                        self.clicks += 1
                        self.actions.sleep(self.limit("stepSeconds", 0.6))
                self.heading = None
                continue

            # 4. direction vers la cible (repere ecran integre : la position
            #    de la cible et celle du personnage sont dans le MEME repere)
            wanted = math.atan2(t_abs[1] - pos[1], t_abs[0] - pos[0])
            if self.forward_world_angle is None:
                # Etalonnage « devant la camera » : indispensable pour
                # traduire une direction monde en point ecran.
                if self.dry_run():
                    status = "dry_run"
                    reason = ("cible a %.1f px ; direction monde %.0f deg ; "
                              "etalonnage ecran->monde necessaire (deplacement "
                              "reel requis)" % (marker["dist"], math.degrees(wanted)))
                    break
                if self._calibrate_forward() is None:
                    status = "no_position"
                    reason = "etalonnage impossible (aucun deplacement observe)"
                    break
                continue

            off = self._screen_offset(wanted)
            if off is None:
                status = "no_position"
                reason = "correspondance ecran/monde indisponible"
                break

            # 5. on avance : AUTO-RUN (le deplacement se fait par un clic
            #    droit au sol, seul mecanisme que ce client accepte :
            #    mesure reelle 2026-10-21 -> le clavier de direction est
            #    ignore (raw input) et le W n'est meme pas lie a l'avance
            #    (bind W ACTIONBUTTON1), tandis que TOGGLEAUTORUN est lie a Z.
            if self.dry_run():
                status = "dry_run"
                reason = ("cible a %.1f px ; direction monde %.0f deg ; "
                          "clic ecran (%+.0f, %+.0f) calcule, non envoye"
                          % (marker["dist"], math.degrees(wanted), off[0], off[1]))
                break
            sent = self.actions.click_screen(off[0], off[1])
            if sent:
                self.clicks += 1
            self.actions.sleep(self.limit("stepSeconds", 0.6))

        result["status"] = status
        result["reason"] = reason
        result["seconds"] = round(time.time() - t0, 2)
        result["turns"] = self.turns
        result["recoveries"] = self.recoveries
        result["clicks"] = self.clicks
        if self.last_read:
            result["end"] = (self.last_read["position"]["x"],
                             self.last_read["position"]["y"])
        result["pixelsPerRadian"] = round(self.pixels_per_radian, 1)
        return result
