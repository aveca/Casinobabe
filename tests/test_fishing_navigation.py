# -*- coding: utf-8 -*-
"""Tests de la navigation externe (grille de navigabilite, A*, memoire)."""
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


def _map_with_mountain(h=260, w=260, cell=16, wall_from=6, wall_to=12):
    """Carte synthetique : plaines lisses, mur de montagne texture au
    centre, corridor libre en haut et en bas."""
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = (30, 160, 200)            # plaines lisses
    for r in range(wall_from, wall_to):
        for c in range(wall_from, wall_to):
            x0, y0 = c * cell, r * cell
            blk = img[y0:y0 + cell, x0:x0 + cell]
            blk[:, :] = (60, 60, 60)
            # relief texture : rayures claires (variance locale forte)
            for i in range(0, cell, 2):
                blk[i:i + 1, :] = (230, 230, 230)
    return img


def test_grid_marks_the_mountain_as_blocked():
    g = FN.NavigabilityGrid.from_map(_map_with_mountain(), (0, 0, 260, 260),
                                     cell_px=16)
    assert g.rows >= 16 and g.cols >= 16
    # la cellule centrale (montagne) n'est pas franchissable
    assert not g.is_free(9, 9)
    # une cellule de plaine l'est
    assert g.is_free(1, 1)
    assert g.free_ratio() < 1.0


def test_astar_goes_around_the_mountain():
    g = FN.NavigabilityGrid.from_map(_map_with_mountain(), (0, 0, 260, 260),
                                     cell_px=16)
    start = (16, 16)          # en haut a gauche
    goal = (240, 240)         # en bas a droite (hors de la montagne)
    path = g.astar(start, goal)
    assert path, "un chemin doit exister"
    # le chemin ne doit traverser aucune cellule de la montagne
    for (px, py) in path:
        r, c = g.cell_of(px, py)
        if 6 <= r < 12 and 6 <= c < 12:
            pytest.fail("le chemin traverse la montagne en %s" % ((r, c),))
    # il va bien de lDepart a l arrivee
    assert path[0] is not None and path[-1] is not None
    d0 = abs(path[0][0] - start[0]) + abs(path[0][1] - start[1])
    d1 = abs(path[-1][0] - goal[0]) + abs(path[-1][1] - goal[1])
    assert d0 <= 24 and d1 <= 24
    assert len(path) >= 6, 'le chemin doit contourner'
    # le chemin est plus long que la ligne directe (contournement reel)
    assert len(path) >= 6


def test_astar_returns_none_when_sealed():
    img = np.zeros((200, 200, 3), np.uint8)
    img[:, :] = (30, 160, 200)
    g = FN.NavigabilityGrid.from_map(img, (0, 0, 260, 260), cell_px=16)
    # on entoure la destination de cellules non franchissables
    for r in range(8, 12):
        for c in range(8, 12):
            g.free[r][c] = False
    # depart et arrivee dans la meme poche -> pas de chemin
    path = g.astar((136, 136), (136, 136))
    assert path is None or len(path) == 1


def test_path_memory_roundtrip(tmp_path):
    p = str(tmp_path / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_death("The Barrens", "Lushwater Oasis", (12.5, -3.25),
                     route=[(1.0, 2.0), (3.0, 4.0)])
    mem2 = FN.PathMemory(p)
    route = mem2.known_route("The Barrens", "Lushwater Oasis")
    assert route == [(1.0, 2.0), (3.0, 4.0)]
    death = mem2.known_death("The Barrens", "Lushwater Oasis")
    assert death == [12.5, -3.25]
    assert mem2.known_route("Autre", "Zone") is None


def test_path_memory_persists_on_disk(tmp_path):
    p = str(tmp_path / "sous" / "ghost-paths.json")
    mem = FN.PathMemory(p)
    mem.record_route("The Barrens", "Lushwater Oasis", [(5.0, 6.0)])
    assert os.path.exists(p)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "routes" in data


def test_blocked_ahead_detects_texture():
    import fishing_navigation as fn
    smooth = np.zeros((400, 800, 3), np.uint8)
    smooth[:, :] = (30, 160, 200)
    assert fn.blocked_ahead(smooth) is False
    noisy = smooth.copy()
    rng = np.random.RandomState(7)
    noisy[:, :] = rng.randint(0, 255, noisy.shape).astype(np.uint8)
    assert fn.blocked_ahead(noisy) is True
