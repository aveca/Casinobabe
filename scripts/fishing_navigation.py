#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_navigation.py — navigation EXTERNE (sans addon) du bot de peche.

Trois briques :

1. `NavigabilityGrid` : un modele de navigabilite extrait du RENDU DE LA
   CARTE DU MONDE.  La carte dessine le relief : les montagnes ont un
   relief texture (hachures) et les plaines sont lisses.  On exploite la
   meme statistique que pour l'eau (dispersion robuste IQR de la
   teinte) : une cellule unie = navigable, une cellule texturee =
   obstacle.  L'eau reste navigable (le fantome marche dessus avec
   ESPACE maintenu).

2. `astar()` : plus court chemin sur cette grille -> une suite de
   waypoints.  C'est la reponse a « ne pas traverser la montagne » :
   on ne vise plus le corps en ligne droite, on suit un chemin.

3. `PathMemory` : memoire persistante des itineraires (point de mort +
   chemin valide).  Le bot n'a pas besoin de redécouvrir un chemin qu'il
   a deja parcouru (le personnage est deja mort une premiere fois).

Tout est externe : capture d'ecran + entrees simulees.
"""

import json
import math
import os

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

#: variance locale max d'une cellule de carte jugee lisse (navigable)
RELIEF_VAR_MAX = 120.0
#: taille d'une cellule de la grille, en pixels de carte
DEFAULT_CELL_PX = 16


def _iqr(values):
    """Dispersion robuste (ecart interquartile) d'un tableau de teintes."""
    if values is None or len(values) == 0:
        return 255.0
    arr = np.asarray(values, dtype=np.float32)
    q1 = float(np.percentile(arr, 25))
    q3 = float(np.percentile(arr, 75))
    return q3 - q1


class NavigabilityGrid(object):
    """Grille de navigabilite deduite du rendu de la carte du monde.

    `cell_px` : taille d'une cellule en pixels de carte.
    `region`  : (x0, y0, x1, y1) en pixels de capture (la zone utile
                 de la carte).
    """

    def __init__(self, region, cell_px=DEFAULT_CELL_PX):
        self.region = tuple(int(v) for v in region)
        self.cell_px = int(cell_px)
        self.cols = 0
        self.rows = 0
        self.free = []          # lignes de booleens (True = franchissable)
        self.relief = []         # IQR par cellule (diagnostic)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_map(cls, bgr, region, cell_px=DEFAULT_CELL_PX):
        g = cls(region, cell_px)
        if bgr is None or cv2 is None:
            return g
        x0, y0, x1, y1 = g.region
        x1 = min(x1, bgr.shape[1])
        y1 = min(y1, bgr.shape[0])
        roi = bgr[y0:y1, x0:x1]
        if roi.size == 0:
            return g
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g.cols = max(1, (x1 - x0) // g.cell_px)
        g.rows = max(1, (y1 - y0) // g.cell_px)
        g.free = []
        g.relief = []
        for r in range(g.rows):
            line = []
            line_iqr = []
            for c in range(g.cols):
                cy0 = r * g.cell_px
                cx0 = c * g.cell_px
                cell = gray[cy0:cy0 + g.cell_px, cx0:cx0 + g.cell_px]
                if cell.size == 0:
                    line.append(True)
                    line_iqr.append(0.0)
                    continue
                # VARIANCE LOCALE : le relief (montagnes, aretes) est
                # texture, la plaine est unie.
                best = 0.0
                h = cell.shape[0]
                w = cell.shape[1]
                hs = max(1, h // 2)
                ws = max(1, w // 2)
                for oy in (0, hs):
                    for ox in (0, ws):
                        sub = cell[oy:oy + hs, ox:ox + ws]
                        if sub.size:
                            mean = float(sub.mean())
                            var = float(((sub - mean) ** 2).mean())
                            best = max(best, var)
                line_iqr.append(best)
                line.append(best <= RELIEF_VAR_MAX)
            g.free.append(line)
            g.relief.append(line_iqr)
        return g

    # -- requetes ---------------------------------------------------------
    def cell_of(self, px, py):
        x0, y0 = self.region[0], self.region[1]
        c = int((px - x0) // self.cell_px)
        r = int((py - y0) // self.cell_px)
        return r, c

    def pixel_of(self, r, c):
        x0, y0 = self.region[0], self.region[1]
        return (x0 + c * self.cell_px + self.cell_px // 2,
                y0 + r * self.cell_px + self.cell_px // 2)

    def is_free(self, r, c):
        if r < 0 or c < 0 or r >= self.rows or c >= self.cols:
            return False
        return bool(self.free[r][c])

    def free_ratio(self):
        if not self.free:
            return 1.0
        tot = sum(len(l) for l in self.free)
        ok = sum(1 for l in self.free for v in l if v)
        return float(ok) / max(1, tot)

    # -- A* ----------------------------------------------------------------
    def astar(self, start_px, goal_px, allow_blocked_goal=True):
        """Plus court chemin (liste de pixels) de start vers goal.

        Renvoie None si aucun chemin n'existe.  Le but est autorise a
        etre dans une cellule non franchissable (le corps peut etre au
        bord d'une falaise) ; on cherche alors la cellule libre la plus
        proche du but.
        """
        if not self.free:
            return None
        sr, sc = self.cell_of(*start_px)
        gr, gc = self.cell_of(*goal_px)
        if not self.is_free(sr, sc):
            free = [(r, c) for r in range(self.rows) for c in range(self.cols)
                    if self.free[r][c]]
            if not free:
                return None
            sr, sc = min(free, key=lambda rc: (rc[0] - sr) ** 2 + (rc[1] - sc) ** 2)
        goal_cell = (gr, gc)
        if not self.is_free(gr, gc):
            cands = [(r, c) for r in range(self.rows) for c in range(self.cols)
                      if self.free[r][c]]
            if not cands:
                return None
            goal_cell = min(cands, key=lambda rc: (rc[0] - gr) ** 2 + (rc[1] - gc) ** 2)
        gr, gc = goal_cell
        if (sr, sc) == (gr, gc):
            return [self.pixel_of(gr, gc)]
        import heapq
        open_heap = [(0.0, sr, sc)]
        came = {}
        cost = {(sr, sc): 0.0}
        gscore = {(sr, sc): 0.0}
        seen = set()
        while open_heap:
            _f, r, c = heapq.heappop(open_heap)
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
                if not self.is_free(nr, nc):
                    continue
                step = 1.0 if (dr == 0 or dc == 0) else 1.414
                ng = gscore[(r, c)] + step
                if ng < gscore.get((nr, nc), 1e9):
                    gscore[(nr, nc)] = ng
                    came[(nr, nc)] = (r, c)
                    h = math.hypot(nr - gr, nc - gc)
                    heapq.heappush(open_heap, (ng + h, nr, nc))
        return None


class PathMemory(object):
    """Memoire des itineraires (point de mort + chemin deja parcouru).

    Le bot n'a pas a redécouvrir un chemin deja pris : a chaque mort il
    enregistre le point de mort et l'itinéraire suivi ; a la mort
    suivante, dans la meme zone, il rejoue le chemin connu.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join("state", "ghost-paths.json")
        self.data = {"routes": {}}

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            if "routes" not in self.data:
                self.data["routes"] = {}
        except Exception:
            self.data = {"routes": {}}
        return self.data

    def save(self):
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @staticmethod
    def _key(zone, area):
        return "%s:%s" % (zone or "?", area or "?")

    def record_death(self, zone, area, death_point, route=None):
        self.load()
        key = self._key(zone, area)
        entry = self.data["routes"].setdefault(key, {})
        entry["deathPoint"] = [float(death_point[0]), float(death_point[1])]
        if route:
            entry["route"] = [[float(p[0]), float(p[1])] for p in route]
        entry["updatedAt"] = __import__("time").time()
        self.save()
        return entry

    def record_route(self, zone, area, route):
        self.load()
        key = self._key(zone, area)
        entry = self.data["routes"].setdefault(key, {})
        entry["route"] = [[float(p[0]), float(p[1])] for p in route]
        entry["updatedAt"] = __import__("time").time()
        self.save()
        return entry

    def known_route(self, zone, area):
        self.load()
        entry = self.data["routes"].get(self._key(zone, area)) or {}
        route = entry.get("route")
        if route:
            return [(float(p[0]), float(p[1])) for p in route]
        return None

    def known_death(self, zone, area):
        self.load()
        entry = self.data["routes"].get(self._key(zone, area)) or {}
        return entry.get("deathPoint")


def blocked_ahead(bgr, box=(0.35, 0.60, 0.45, 0.95)):
    """Indice d'obstacle dans la VUE 3D, devant le personnage.

    `box` = (x0, x1, y0, y1) en fractions de l'image : la bande
    juste devant du personnage.  On renvoie True si la zone est
    texturee (vegetation : arbres, buissons) ou si elle presente un
    front clair vertical (falaise).  Indicateur COMPLEMENTAIRE du
    chemin calcule : il sert a decelerer ou a pivoter quand le
    personnage est colle a un obstacle.
    """
    if bgr is None or cv2 is None:
        return None
    h, w = bgr.shape[:2]
    x0, x1 = int(w * box[0]), int(w * box[1])
    y0, y1 = int(h * box[2]), int(h * box[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # variance locale : la vegetation est texturee, le sol lisse
    mean = cv2.blur(gray, (9, 9))
    var = float((((gray.astype(np.float32) - mean.astype(np.float32)) ** 2)).mean())
    return var > 220.0
