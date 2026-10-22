#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_navigation.py — navigation EXTERNE (sans addon) du bot WoW.

CHOIX DE CONCEPTION (issus de l'audit du 21/10) :

* Tout se passe dans le **repere pixel de la carte du monde**.  La carte
  peut etre deplacee (pan) ou zoomee : le vecteur (depart -> but) est
  de toute facon correct dans le rendu affiche, puisque le depart
  (joueur) et le but (corps) sont DEUX MARQUEURS DU MEME RENDU.  Aucune
  conversion vers des coordonnees normalisees n'est donc necessaire, et
  elle serait de surcroit fausse (le pan n'est pas connu).

* La classification du terrain est **INCERTAINE** : on ne peut pas
  garantir qu'une cellule unie est praticable ni qu'une cellule
  texturee est un obstacle (les cartouches de la carte, les routes, les
  zones baties sont aussi uniformes ou texturees).  La grille est donc
  une **carte de COUT** : une cellule unie coute 1, une cellule texturee
  coute plus cher mais n'est JAMAIS interdite.  Un chemin existe donc
  presque toujours, et l'A* evite les zones texturees quand il le peut.

* Le pilote ne doit JAMAIS inventer un itineraire : si la cible, la
  region de la carte ou la position du joueur sont inconnus ou non
  verifies, la planification **refuse** de produire un chemin.

* `PathMemory` ne retient que des **resultats observes** : un itineraire
  n'est rejouable que s'il a mene AU CORPS (et non seulement s'il a ete
  emis), et seulement si la zone, le point de mort et la position du
  corps sont compatibles.
"""

import json
import math
import os
import time

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

#: cout d'une cellule unie (plaine, route, eau)
COST_PLAIN = 1.0
#: cout d'une cellule texturee (relief, details de carte) : INCERTAIN
COST_RELIEF = 8.0
#: taille d'une cellule de la grille, en pixels de carte
DEFAULT_CELL_PX = 16
#: tolerance (pixels) pour rejouer un itineraire historique
DEFAULT_REPLAY_TOLERANCE_PX = 60.0


def _local_variance(block):
    """Variance locale d'un bloc (le relief est texture, la plaine unie)."""
    if block is None or block.size == 0:
        return 0.0
    mean = float(block.mean())
    return float(((block - mean) ** 2).mean())


class NavigabilityGrid(object):
    """Carte de cout du terrain, extraite du rendu de la carte du monde.

    `region` = (x0, y0, x1, y1) en pixels de CAPTURE (la zone de la
    carte).  La classification est incertaine : rien n'est interdit.
    """

    def __init__(self, region, cell_px=DEFAULT_CELL_PX):
        self.region = tuple(int(v) for v in region)
        self.cell_px = int(cell_px)
        self.cols = 0
        self.rows = 0
        self.cost = []
        self.relief = []

    # -- construction ------------------------------------------------------
    @classmethod
    def from_map(cls, bgr, region, cell_px=DEFAULT_CELL_PX):
        g = cls(region, cell_px)
        if bgr is None or cv2 is None:
            return g
        x0, y0, x1, y1 = g.region
        x1 = min(x1, bgr.shape[1])
        y1 = min(y1, bgr.shape[0])
        if x1 <= x0 or y1 <= y0:
            return g
        roi = bgr[y0:y1, x0:x1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g.cols = max(1, (x1 - x0) // g.cell_px)
        g.rows = max(1, (y1 - y0) // g.cell_px)
        g.cost = []
        g.relief = []
        for r in range(g.rows):
            costs = []
            rels = []
            for c in range(g.cols):
                cy0, cx0 = r * g.cell_px, c * g.cell_px
                cell = gray[cy0:cy0 + g.cell_px, cx0:cx0 + g.cell_px]
                if cell.size == 0:
                    costs.append(COST_PLAIN)
                    rels.append(0.0)
                    continue
                best = 0.0
                h, w = cell.shape
                hs, ws = max(1, h // 2), max(1, w // 2)
                for oy in (0, hs):
                    for ox in (0, ws):
                        sub = cell[oy:oy + hs, ox:ox + ws]
                        if sub.size:
                            best = max(best, _local_variance(sub))
                rels.append(best)
                costs.append(COST_PLAIN if best <= 120.0 else COST_RELIEF)
            g.cost.append(costs)
            g.relief.append(rels)
        return g

    # -- conversions --------------------------------------------------------
    def contains(self, px, py):
        x0, y0, x1, y1 = self.region
        return (x0 <= px < x1) and (y0 <= py < y1)

    def cell_of(self, px, py):
        x0, y0 = self.region[0], self.region[1]
        return int((py - y0) // self.cell_px), int((px - x0) // self.cell_px)

    def pixel_of(self, r, c):
        x0, y0 = self.region[0], self.region[1]
        return (x0 + c * self.cell_px + self.cell_px // 2,
                y0 + r * self.cell_px + self.cell_px // 2)

    def in_bounds(self, r, c):
        return 0 <= r < self.rows and 0 <= c < self.cols

    def cost_at(self, r, c):
        if not self.in_bounds(r, c):
            return None
        return float(self.cost[r][c])

    def textured_ratio(self):
        """Part des cellules texturees (indicateur de incertitude)."""
        if not self.cost:
            return 0.0
        tot = sum(len(l) for l in self.cost)
        hi = sum(1 for l in self.cost for v in l if v > COST_PLAIN)
        return float(hi) / max(1, tot)

    # -- A* (cout, jamais d'interdiction) -----------------------------------
    def astar(self, start_px, goal_px):
        """Chemin le moins couteux de start vers goal (pixels de carte).

        Retourne None si la geometrie est invalide (point hors carte,
        grille vide).  Comme rien n'est interdit, un chemin existe des
        que la grille est valide.
        """
        if not self.cost or not self.rows or not self.cols:
            return None
        if not (self.contains(*start_px) and self.contains(*goal_px)):
            return None
        sr, sc = self.cell_of(*start_px)
        gr, gc = self.cell_of(*goal_px)
        sr = max(0, min(self.rows - 1, sr))
        sc = max(0, min(self.cols - 1, sc))
        gr = max(0, min(self.rows - 1, gr))
        gc = max(0, min(self.cols - 1, gc))
        if (sr, sc) == (gr, gc):
            return [self.pixel_of(gr, gc)]
        import heapq
        heap = [(0.0, sr, sc)]
        gscore = {(sr, sc): 0.0}
        came = {}
        seen = set()
        while heap:
            _f, r, c = heapq.heappop(heap)
            if (r, c) in seen:
                continue
            seen.add((r, c))
            if (r, c) == (gr, gc):
                path = [(r, c)]
                cur = (r, c)
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return [self.pixel_of(rr, cc) for rr, cc in path]
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1, -1), (-1, 1), (1, -1), (1, 1)):
                nr, nc = r + dr, c + dc
                if not self.in_bounds(nr, nc):
                    continue
                step = 1.0 if (dr == 0 or dc == 0) else 1.414
                ng = gscore[(r, c)] + step * self.cost[nr][nc]
                if ng < gscore.get((nr, nc), 1e18):
                    gscore[(nr, nc)] = ng
                    came[(nr, nc)] = (r, c)
                    h = math.hypot(nr - gr, nc - gc)
                    heapq.heappush(heap, (ng + h, nr, nc))
        return None

    def path_cost(self, path):
        """Cout cumule d'un chemin (pour comparer deux itineraires)."""
        if not path:
            return 0.0
        total = 0.0
        for i in range(1, len(path)):
            r0, c0 = self.cell_of(*path[i - 1])
            r1, c1 = self.cell_of(*path[i])
            step = math.hypot(r1 - r0, c1 - c0)
            total += step * (self.cost_at(r1, c1) or COST_PLAIN)
        return total


class PathMemory(object):
    """Memoire des itineraires OBSERVES (point de mort + trajet + resultat).

    Regle : un itineraire n'est rejouable que s'il a mene AU CORPS
    (`reached=True`) et que le contexte (zone, point de mort, position du
    corps) est compatible.  Un trajet simplement emis n'est pas un
    itineraire valide.
    """

    VERSION = 1

    def __init__(self, path=None):
        self.path = path or os.path.join("state", "ghost-paths.json")
        self.data = {"version": self.VERSION, "routes": {}}

    # -- E/S atomique -------------------------------------------------------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("format")
            data.setdefault("routes", {})
            data.setdefault("version", self.VERSION)
            self.data = data
        except Exception:
            self.data = {"version": self.VERSION, "routes": {}}
        return self.data

    def save(self):
        """Ecriture ATOMIQUE (un arret brutal ne doit pas corrompre)."""
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(self.path):
                os.replace(self.path, tmp)
            else:
                os.replace(tmp, self.path)
            return True
        except Exception:
            try:
                if os.path.exists(self.path + ".tmp"):
                    os.remove(self.path + ".tmp")
            except Exception:
                pass
            return False

    @staticmethod
    def _key(zone, area):
        return "%s|%s" % (zone or "?", area or "?")

    # -- ecriture -----------------------------------------------------------
    def record_attempt(self, zone, area, death_point, route=None, reached=False,
                       corpse_point=None):
        """Enregistre une TENTATIVE (route emise) et son resultat observe."""
        self.load()
        key = self._key(zone, area)
        entry = self.data["routes"].setdefault(key, {})
        if death_point is not None:
            entry["deathPoint"] = [float(death_point[0]), float(death_point[1])]
        if corpse_point is not None:
            entry["corpsePoint"] = [float(corpse_point[0]), float(corpse_point[1])]
        if route:
            entry["lastRoute"] = [[float(p[0]), float(p[1])] for p in route]
        entry["reached"] = bool(reached)
        entry["updatedAt"] = time.time()
        if reached and route:
            # un trajet qui a atteint le corps devient REJOUABLE
            entry["route"] = [[float(p[0]), float(p[1])] for p in route]
            entry["validatedAt"] = time.time()
        else:
            # un trajet incomplet n'est PAS un itineraire valide
            entry.pop("route", None)
        self.save()
        return entry

    # -- lecture / validation ------------------------------------------------
    def known_route(self, zone, area, death_point=None, corpse_point=None,
                    tolerance=DEFAULT_REPLAY_TOLERANCE_PX):
        """Route REJOUABLE pour ce contexte, ou None.

        Refuse une route historique si la zone differe, si le point de
        mort s'est deplace, si la position du corps a change, ou si la
        route n'a jamais atteint le corps.
        """
        self.load()
        entry = self.data["routes"].get(self._key(zone, area)) or {}
        route = entry.get("route")
        if not route or not entry.get("reached"):
            return None
        if death_point is not None and entry.get("deathPoint") is not None:
            d = math.hypot(float(death_point[0]) - entry["deathPoint"][0],
                            float(death_point[1]) - entry["deathPoint"][1])
            if d > tolerance:
                return None
        if corpse_point is not None and entry.get("corpsePoint") is not None:
            d = math.hypot(float(corpse_point[0]) - entry["corpsePoint"][0],
                            float(corpse_point[1]) - entry["corpsePoint"][1])
            if d > tolerance:
                return None
        return [(float(p[0]), float(p[1])) for p in route]

    def entry(self, zone, area):
        self.load()
        return self.data["routes"].get(self._key(zone, area))


def blocked_ahead(bgr, box=(0.35, 0.60, 0.45, 0.95), threshold=220.0):
    """Indice d'obstacle dans la VUE 3D, devant le personnage.

    Indicateur COMPLEMENTAIRE du chemin planifie (la vegetation est
    texturee, le sol lisse).  Renvoie None si l'image est inexploitable.
    """
    if bgr is None or cv2 is None:
        return None
    h, w = bgr.shape[:2]
    x0, x1 = int(w * box[0]), int(w * box[1])
    y0, y1 = int(h * box[2]), int(h * box[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mean = cv2.blur(gray, (9, 9))
    var = float((((gray - mean) ** 2)).mean())
    return var > threshold
