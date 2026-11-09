#!/usr/bin/env node
# -*- coding: utf-8 -*-
"""
test_fishing_navigator.py — tests du controleur de deplacement (hors jeu).

Le controleur est teste contre un MONDE SYNTHETIQUE SIMULE (un decor avec un
obstacle, un cap qui evolue avec la souris, des touches qui deplace le
personnage). On ne mocke pas la logique du controleur : on simule le jeu.

Contrat verifie : un deplacement n'est declare reussi QUE si la position
MESUREE a progresse ; un blocage est detecte et donne lieu a une
recuperation ; les limites (duree, tentatives) sont respectees.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fishing_navigator as fn  # noqa: E402

FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s %s" % (name, detail))


class World(object):
    """Monde 2D simule : personnage, cap, touches, obstacle."""

    def __init__(self, start=(10.0, 50.0), heading=0.0, wall=None,
                 speed=30.0, size=100.0):
        self.pos = [float(start[0]), float(start[1])]
        self.heading = float(heading)
        self.keys = set()
        self.wall = wall            # (x_min, x_max, y_min, y_max) : bloque
        self.speed = speed
        self.size = size
        self.t = 0.0
        self.moves = 0

    # -- horloge manuelle ------------------------------------------------
    def clock(self):
        return self.t

    def tick(self, dt=0.05):
        self.t += dt
        # deplacement selon les touches tenues
        vx = vy = 0.0
        if "w" in self.keys:
            vx += math.cos(self.heading) * self.speed
            vy += math.sin(self.heading) * self.speed
        if "s" in self.keys:
            vx -= math.cos(self.heading) * self.speed
            vy -= math.sin(self.heading) * self.speed
        if "d" in self.keys:
            vx -= math.sin(self.heading) * self.speed
            vy += math.cos(self.heading) * self.speed
        if "a" in self.keys:
            vx += math.sin(self.heading) * self.speed
            vy -= math.cos(self.heading) * self.speed
        if vx or vy:
            nx = self.pos[0] + vx * dt
            ny = self.pos[1] + vy * dt
            nx = max(0.0, min(self.size, nx))
            ny = max(0.0, min(self.size, ny))
            if self.wall:
                x0, x1, y0, y1 = self.wall
                if (x0 <= nx <= x1) and (y0 <= ny <= y1):
                    pass                       # bloque : la position ne bouge pas
                else:
                    self.pos = [nx, ny]
            else:
                self.pos = [nx, ny]

    # -- interfaces du controleur ----------------------------------------
    def position(self):
        return tuple(self.pos)

    def key_down(self, k):
        self.keys.add(k)
        self.moves += 1

    def key_up(self, k):
        self.keys.discard(k)

    def mouse_move(self, dx, dy):
        # convention du controleur : un dx negatif tourne a gauche
        self.heading = fn._norm_angle(self.heading - dx / fn.MOUSE_PIXELS_PER_RADIAN)


def make_nav(world, **kw):
    return fn.Navigator(world.position, world.key_down, world.key_up,
                        world.mouse_move, clock=world.clock,
                        sleep=world.tick, **kw)


# ------------------------------------------------------------------- tests
def test_arrival():
    print("=== 1. ARRIVEE VERIFIEE PAR LA POSITION MESUREE ===")
    w = World(start=(10.0, 50.0), heading=0.0)   # cap = est
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    res = nav.goto((80.0, 50.0))
    check("le trajet se termine en 'arrived'", res["status"] == "arrived",
          "(%s / %s)" % (res["status"], res.get("reason")))
    check("la position finale est proche de la cible",
          fn._dist(w.position(), (80.0, 50.0)) <= fn.ARRIVE_TOLERANCE + 1.0,
          "(%s)" % (w.position(),))
    check("le deplacement mesure est positif", res["travelled"] > 0)
    check("la trace contient l'evenement 'arrived'",
          any(r["event"] == "arrived" for r in res["trace"]))


def test_turn():
    print()
    print("=== 2. ORIENTATION (le cap est RE-MESURE, jamais suppose) ===")
    w = World(start=(50.0, 50.0), heading=0.0)   # cap est, cible au nord
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    res = nav.goto((50.0, 90.0), max_seconds=40.0)
    check("une cible perpendiculaire est atteinte", res["status"] == "arrived",
          "(%s / %s)" % (res["status"], res.get("reason")))
    check("le cap a change (le controleur a tourne)",
          abs(w.heading - 0.0) > 0.2, "(heading=%.2f)" % w.heading)
    check("la trace contient une rotation",
          any(r["event"] == "turn" for r in res["trace"]))


def test_blocked():
    print()
    print("=== 3. BLOCAGE : DETECTION, RECUPERATION, ABANDON ===")
    # la cible est DANS un obstacle solide : aucun contournement possible,
    # le controleur doit le detecter et abandonner (et non boucler).
    w = World(start=(50.0, 60.0), heading=0.0, wall=(40.0, 60.0, 70.0, 90.0))
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    res = nav.goto((50.0, 80.0), max_seconds=25.0, max_recoveries=2)
    check("un blocage conduit a l'abandon (et non a une boucle infinie)",
          res["status"] in ("blocked", "timeout"), "(%s)" % res["status"])
    check("le nombre de recuperations est borne",
          res["recoveries"] <= 3, "(%d)" % res["recoveries"])
    check("la trace signale le blocage",
          any(r["event"] == "stuck" for r in res["trace"]))
    check("le deplacement n'est pas declare reussi",
          res["status"] != "arrived")


def test_avoid_obstacle():
    print()
    print("=== 3b. CONTOURNEMENT D'UN OBSTACLE ===")
    # un mur partiel : le bon controleur doit le contourner et atteindre la cible
    w = World(start=(50.0, 10.0), heading=0.0, wall=(40.0, 60.0, 20.0, 60.0))
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    res = nav.goto((50.0, 80.0), max_seconds=40.0, max_recoveries=3)
    check("un obstacle partiel est contourne (arrivee verifiee)",
          res["status"] == "arrived", "(%s / %s)" % (res["status"], res.get("reason")))
    check("la position finale est bien proche de la cible",
          fn._dist(w.position(), (50.0, 80.0)) <= fn.ARRIVE_TOLERANCE + 2.0,
          "(%s)" % (w.position(),))


def test_no_position():
    print()
    print("=== 4. POSITION INMESURABLE ===")
    class NoPos(World):
        def position(self):
            return None
    w = NoPos()
    nav = make_nav(w)
    res = nav.goto((50.0, 50.0))
    check("sans position, le controleur s'arrete proprement",
          res["status"] == "no_position", "(%s)" % res["status"])
    check("aucune touche n'est envoyee", w.moves == 0, "(moves=%d)" % w.moves)


def test_limits():
    print()
    print("=== 5. LIMITES (duree, tolerances) ===")
    w = World(start=(10.0, 10.0), heading=0.0)
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    t0 = w.clock()
    res = nav.goto((90.0, 90.0), max_seconds=3.0)
    check("la duree maximale est respectee", w.clock() - t0 <= 6.0,
          "(%.1f s)" % (w.clock() - t0))
    check("un trajet impossible n'est pas declare reussi",
          res["status"] in ("timeout", "blocked"))
    # cible absente
    res2 = nav.goto(None)
    check("une cible absente est refusee", res2["status"] == "aborted")


def test_dedup_positions():
    print()
    print("=== 6. LA POSITION EST LA SEULE PREUVE ===")
    # un monde ou la commande est envoyee mais le personnage ne bouge pas
    class Frozen(World):
        def tick(self, dt=0.05):
            self.t += dt        # le temps passe, la position non
    w = Frozen(start=(10.0, 50.0), heading=0.0)
    nav = make_nav(w, step_seconds=0.2, probe_seconds=0.25)
    res = nav.goto((80.0, 50.0), max_seconds=8.0, max_recoveries=1)
    check("des commandes envoyees sans deplacement ne donnent pas 'arrived'",
          res["status"] != "arrived", "(%s)" % res["status"])
    check("le blocage est detecte malgre les commandes envoyees",
          any(r["event"] == "stuck" for r in res["trace"]))


def main():
    print("=== TESTS DU CONTROLEUR DE DEPLACEMENT (monde synthetique) ===")
    test_arrival()
    test_turn()
    test_blocked()
    test_avoid_obstacle()
    test_no_position()
    test_limits()
    test_dedup_positions()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
