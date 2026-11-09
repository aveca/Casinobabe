# -*- coding: utf-8 -*-
"""P0.35 - tests du moteur de navigation externe (A* + memoire).

Ce que ces tests prouvent :
  * le FONCTIONNEMENT de l'A* sur une grille synthetique (cout, cout
    incertain, absence de chemin, conversions) ;
  * que `RecoverySession` CONSOMME reellement les waypoints ;
  * que la memoire des chemins n'enregistre que des resultats OBSERVES
    et refuse de rejouer un itineraire incompatible.

Ce qu'ils ne prouvent PAS : la precision de la carte du jeu (le terrain
reel n'est pas verifiable hors ligne) -> la grille reste une carte de
COUT incertaine (rien n'est interdit sur la seule texture).
"""
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (os.path.join(ROOT, "scripts"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cv2                                             # noqa: E402
import fishing_navigation as FN                         # noqa: E402
import fishing_recovery as FR                           # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _plain_map(h=260, w=260, cell=16):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = (30, 160, 200)
    return img


def _map_with_relief(h=260, w=260, cell=16, r0=6, r1=12):
    img = _plain_map(h, w, cell)
    for r in range(r0, r1):
        for c in range(r0, r1):
            x0, y0 = c * cell, r * cell
            blk = img[y0:y0 + cell, x0:x0 + cell]
            blk[:, :] = (60, 60, 60)
            for i in range(0, cell, 2):
                blk[i:i + 1, :] = (230, 230, 230)
    return img


def _grid(img=None, region=(0, 0, 260, 260), cell=16):
    return FN.NavigabilityGrid.from_map(img if img is not None else _plain_map(),
                                        region, cell_px=cell)


# --------------------------------------------------------------------------
# 1. chemin direct sans obstacle
# --------------------------------------------------------------------------
def test_direct_path_without_obstacle():
    g = _grid()
    path = g.astar((16, 16), (240, 240))
    assert path and len(path) >= 2
    # en ligne directe : le cout est minimal
    straight = g.path_cost(path)
    assert straight <= 1.45 * math_hypot(240 - 16, 240 - 16)


def math_hypot(a, b):
    import math
    return math.hypot(a, b)


# --------------------------------------------------------------------------
# 2. contournement d'un obstacle reellement defini
# --------------------------------------------------------------------------
def test_path_avoids_the_relief_block():
    img = _map_with_relief()
    g = _grid(img)
    path = g.astar((16, 16), (240, 240))
    assert path
    for (px, py) in path:
        r, c = g.cell_of(px, py)
        if 6 <= r < 12 and 6 <= c < 12:
            pytest.fail("le chemin traverse la zone texturee en %s" % ((r, c),))
    # le cout du chemin detourne est superieur au cout direct
    direct = _grid().path_cost(g.astar((16, 16), (240, 240)))
    assert g.path_cost(path) >= direct - 1e-6


# --------------------------------------------------------------------------
# 3. absence de chemin (geometrie invalide)
# --------------------------------------------------------------------------
def test_no_path_when_geometry_is_invalid():
    g = _grid()
    # point hors de la region -> refus
    assert g.astar((-50, -50), (240, 240)) is None
    assert g.astar((16, 16), (9999, 9999)) is None
    # grille vide
    vide = FN.NavigabilityGrid((0, 0, 0, 0))
    assert vide.astar((1, 1), (2, 2)) is None


# --------------------------------------------------------------------------
# 4. coordonnees hors carte
# --------------------------------------------------------------------------
def test_points_outside_the_region_are_refused():
    g = _grid(region=(0, 0, 100, 100))
    assert not g.contains(200, 200)
    assert g.astar((10, 10), (200, 200)) is None


# --------------------------------------------------------------------------
# 5. conversions pixels <-> grille <-> waypoints
# --------------------------------------------------------------------------
def test_pixel_cell_waypoint_roundtrip():
    g = _grid()
    r, c = g.cell_of(100, 150)
    px, py = g.pixel_of(r, c)
    assert abs(px - 100) <= g.cell_px
    assert abs(py - 150) <= g.cell_px
    r2, c2 = g.cell_of(px, py)
    assert (r2, c2) == (r, c)
    path = g.astar((16, 16), (240, 240))
    for (wx, wy) in path:
        assert g.contains(wx, wy)


# --------------------------------------------------------------------------
# 6. cible inconnue / invalide
# --------------------------------------------------------------------------
def test_unknown_target_is_refused():
    sess = _session()
    # pas d'analyse -> pas de chemin
    assert sess._plan_path(None, None) is None
    assert sess._path is None
    # analyse sans corpse -> refus explicite
    sess._plan_path(_plain_map(), object())
    assert sess._path is None
    assert sess._path_note


# --------------------------------------------------------------------------
# 7. carte incertaine : arret sur plutot qu'itineraire invente
# --------------------------------------------------------------------------
def test_uncertain_map_does_not_forbid_cells():
    """Rien n'est interdit : une cellule texturee coute plus cher, elle
    n'est pas un mur.  Un chemin existe donc meme dans une zone
    'incertaine'."""
    img = _map_with_relief()
    g = _grid(img)
    assert g.textured_ratio() > 0.0
    # un chemin existe et peut traverser la zone texturee si necessaire
    path = g.astar((100, 100), (104, 104))
    assert path is not None


# --------------------------------------------------------------------------
# 8. memoire : persistance et rechargement
# --------------------------------------------------------------------------
def test_path_memory_persists_and_reloads(tmp_path):
    p = str(tmp_path / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_attempt("The Barrens", "Lushwater", (10, 20),
                       route=[(1, 2), (3, 4)], reached=True,
                       corpse_point=(50, 60))
    again = FN.PathMemory(p)
    route = again.known_route("The Barrens", "Lushwater",
                               death_point=(10, 20), corpse_point=(50, 60))
    assert route == [(1.0, 2.0), (3.0, 4.0)]


# --------------------------------------------------------------------------
# 9. rejet d'un itineraire historique incompatible
# --------------------------------------------------------------------------
def test_incompatible_historical_route_is_rejected(tmp_path):
    p = str(tmp_path / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_attempt("The Barrens", "Lushwater", (10, 20),
                       route=[(1, 2)], reached=True, corpse_point=(50, 60))
    mem2 = FN.PathMemory(p)
    # zone differente
    assert mem2.known_route("Autre", "Lushwater") is None
    # point de depart different
    assert mem2.known_route("The Barrens", "Lushwater",
                             death_point=(999, 999)) is None
    # position du corps differente
    assert mem2.known_route("The Barrens", "Lushwater",
                             death_point=(10, 20),
                             corpse_point=(9999, 9999)) is None


def test_unreached_route_is_not_replayable(tmp_path):
    p = str(tmp_path / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_attempt("Z", "A", (0, 0), route=[(1, 1)], reached=False)
    assert FN.PathMemory(p).known_route("Z", "A") is None


def test_memory_write_is_atomic(tmp_path):
    p = str(tmp_path / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_attempt("Z", "A", (0, 0), route=[(1, 1)], reached=True)
    assert os.path.exists(p)
    assert not os.path.exists(p + ".tmp")
    with open(p, "r", encoding="utf-8") as f:
        assert "routes" in json.load(f)


# --------------------------------------------------------------------------
# 10. recalcul apres absence de progression
# --------------------------------------------------------------------------
def test_path_is_dropped_when_no_progress():
    sess = _session()
    sess._path = [(1, 1), (2, 2)]
    sess._st_progress_check.__doc__      # la methode existe
    # le decalage de cap est applique et le chemin invalide
    sess.theta = 0.0
    sess.attempts = 1
    sess._last = {"px": 0.0, "errBefore": 10.0, "errAfter": 10.0}
    sess.stuck_count = 0
    sess._set(FR.PROGRESS_CHECK, "test")
    sess._st_progress_check()
    assert sess.bearing_offset != 0.0
    assert sess._path is None


# --------------------------------------------------------------------------
# 11. RecoverySession consomme reellement les waypoints
# --------------------------------------------------------------------------
def test_recovery_session_consumes_the_waypoints():
    sess = _session()
    sess.analysis = _analysis(player=(100, 100), corpse=(200, 100))
    sess._path = [(100, 100), (150, 100), (200, 100)]
    wp = sess._waypoint_target(sess.analysis)
    assert wp is not None
    # le waypoint (150,100) est a l'EST du joueur -> bearing ~0
    assert abs(wp) < 5.0
    # sans chemin, pas de waypoint -> on vise le corps
    sess._path = None
    assert sess._waypoint_target(sess.analysis) is None


def test_recovery_plans_a_path_from_the_map():
    sess = _session()
    # les points doivent etre DANS la region de la carte du client
    region = sess._map_region_px(2547, 1456)
    x0, y0, x1, y1 = region
    player = (x0 + 40, y0 + 40)
    corpse = (x1 - 40, y1 - 40)
    img = np.zeros((1456, 2547, 3), np.uint8)
    img[:, :] = (30, 160, 200)
    sess._plan_path(img, _analysis(player=player, corpse=corpse))
    assert sess._path, "un chemin doit etre planifie"
    assert len(sess._path) >= 2


# --------------------------------------------------------------------------
# 12. non-regression
# --------------------------------------------------------------------------
def _analysis(player, corpse):
    class _A(object):
        pass
    a = _A()
    a.player = {"cx": player[0], "cy": player[1], "score": 1.0}
    a.corpse = {"cx": corpse[0], "cy": corpse[1], "score": 1.0}
    a.bearing_deg = 0.0
    a.distance_px = 100.0
    return a


def _session():
    from test_fishing_recovery import FakeExecutor
    ex = FakeExecutor(image=_read_fixture())
    return FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)


def _read_fixture():
    from test_fishing_recovery import _read, ZONE_WITH_MARKERS
    return _read(ZONE_WITH_MARKERS)
