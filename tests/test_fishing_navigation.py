#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_fishing_navigation.py — tests de la lecture de minicarte et du
controleur de navigation V2.

Deux parties :
  1. MINICARTE : sur un CORPUS REEL FIGE (captures du client, versionne) —
     le joueur y est immobile, donc la derive de l'odometre doit etre nulle,
     et les marqueurs de cibles doivent etre detectes de facon stable.
     Un test synthetique verifie le SIGNE de l'odometre (decalage impose).
  2. NAVIGATION : sur un monde SIMULE (lecteur et actions injectes) — le
     controleur ne doit declarer une arrivee que sur une OBSERVATION, ne doit
     rien envoyer en dry-run, et doit respecter ses limites.

Execution : python tests/test_fishing_navigation.py
"""
import json
import math
import os
import sys
import tempfile

# ISOLATION : les tests ne doivent pas lire la vraie carte du poste (elle
# contient de vrais bancs et fausserait la selection de cible).
os.environ["CASINOBABE_MAP_PATH"] = tempfile.mkdtemp(prefix="fb-navtest-")
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import cv2                                     # noqa: E402
import numpy as np                            # noqa: E402

import fishing_minimap as fmap                # noqa: E402
import fishing_navigation as fnav             # noqa: E402

FIX = os.path.join(HERE, "fixtures", "minimap_frames")

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


def load_corpus():
    meta = json.load(open(os.path.join(FIX, "minimap_expectations.json"),
                          encoding="utf-8"))
    frames = []
    for f in meta["frames"]:
        img = cv2.imread(os.path.join(FIX, f["file"]), cv2.IMREAD_COLOR)
        frames.append(img)
    return meta, frames


# ------------------------------------------------------- 1. la minicarte
def test_minimap_corpus():
    print("=== 1. MINICARTE (corpus reel fige) ===")
    meta, frames = load_corpus()
    check("le corpus contient des images", len(frames) >= 3,
          "(%d)" % len(frames))

    # 1a. l'odometre au repos : derive nulle
    geo = fmap.MinimapGeometry()
    geo.cx, geo.cy, geo.radius = meta["frames"][0]["center"][0], \
        meta["frames"][0]["center"][1], meta["frames"][0]["radius"]
    odo = fmap.MinimapOdometer(geo)
    for img in frames:
        odo.update_from_crop(img)
    drift = math.hypot(odo.x, odo.y)
    check("l'odometre ne derive pas quand le joueur est immobile",
          drift < 0.5, "(derive=%.3f px)" % drift)
    check("la confiance de la correlation est elevee",
          odo.confidence > 0.9, "(%.3f)" % odo.confidence)
    check("aucun deplacement n'est signale a l'arret", odo.moved is False)

    # 1b. le SIGNE de l'odometre (test synthetique deterministe)
    base = frames[0]
    # on decale l'image de +6 px vers la droite : le contenu a bouge vers la
    # droite, donc le joueur s'est deplace vers la GAUCHE (xd = -6).
    shifted = np.zeros_like(base)
    shifted[:, 6:] = base[:, :-6]
    geo2 = fmap.MinimapGeometry()
    geo2.cx, geo2.cy, geo2.radius = 99, 99, 99
    odo2 = fmap.MinimapOdometer(geo2)
    odo2.update_from_crop(base)
    res = odo2.update_from_crop(shifted)
    check("le signe de l'odometre est inverse du decalage du contenu",
          res["dx"] < -4.0, "(dx=%.2f)" % res["dx"])
    check("l'odometre mesure le decalage avec precision",
          abs(res["dx"] + 6.0) < 1.0, "(dx=%.2f, attendu -6)" % res["dx"])

    # 1c. les marqueurs de cibles
    counts = []
    for img in frames:
        reader = fmap.MinimapReader()
        m = reader.find_markers(img)
        counts.append(len(m))
    check("les marqueurs sont detectes de facon stable",
          min(counts) >= 3 and max(counts) - min(counts) <= 2,
          "(%s)" % counts)
    check("le corpus contient bien des cibles suivies", max(counts) >= 4,
          "(%s)" % counts)
    # les positions des marqueurs doivent etre dans le disque
    reader = fmap.MinimapReader()
    m = reader.find_markers(frames[0])
    bad = [x for x in m if x["dist"] > min(frames[0].shape[:2]) * 0.48]
    check("aucun marqueur hors du disque de minicarte", not bad, "(%s)" % bad)


# ------------------------------------------------- 2. le controleur (simule)
class FakeReader(object):
    """Lecteur simule : renvoie une suite d'etats de minicarte."""

    def __init__(self, states, drift=0.0):
        self.states = list(states)
        self.calls = 0
        self.odometer = _FakeOdometer(drift)

    def read(self, img):
        if not self.states:
            return None
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        # l'odometre simule evolves comme le vrai (derive eventuelle)
        self.odometer.read()
        return state


class _FakeOdometer(object):
    """Odometre simule : derive configurable (pour tester le garde-fou)."""

    def __init__(self, drift=0.0):
        self.x = 0.0
        self.y = 0.0
        self.drift = drift
        self.reads = 0

    def read(self):
        self.reads += 1
        self.x += self.drift
        self.y += self.drift
        return {"ok": True, "x": self.x, "y": self.y, "dx": self.drift,
                "dy": self.drift, "confidence": 1.0, "moved": bool(self.drift)}


class FakeActions(object):
    """Actions simulees : enregistre tout, n'execute rien."""

    def __init__(self):
        self.keys = []
        self.moves = []
        self.user = False
        self.stop = False

    def capture(self):
        # L'odometre de la vue 3D a besoin d'une vraie image, et l'image
        # doit DEFILE pour que le deplacement soit mesure.
        try:
            import numpy as np
            self.shot = getattr(self, "shot", 0) + 1
            img = np.zeros((120, 160, 3), dtype=np.uint8)
            img[:, 3 * self.shot:3 * self.shot + 40] = 200
            img[:, :40] = 60
            return img
        except Exception:
            return None

    def hold_key(self, key, seconds):
        self.keys.append((key, round(seconds, 2)))
        return True

    def move_mouse(self, dx, dy):
        self.moves.append((dx, dy))
        return True

    def click_screen(self, dx, dy):
        self.moves.append((round(dx, 1), round(dy, 1)))
        return True

    def toggle_autorun(self):
        self.keys.append(("autorun", 0))
        return True

    def sleep(self, seconds):
        pass

    def user_active(self):
        return self.user

    def emergency_stop(self):
        return self.stop


def state(pos=(0.0, 0.0), markers=None):
    return {"ok": True, "position": {"x": pos[0], "y": pos[1]},
            "markers": markers or [], "odometer": {"dx": 0, "dy": 0},
            "center": (100, 100), "radius": 99}


def test_navigation_dry_run():
    print()
    print("=== 2. NAVIGATION : dry-run (aucune commande envoyee) ===")
    reader = FakeReader([state(markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                          "kind": "jaune", "area": 60}])])
    actions = FakeActions()
    ctrl = fnav.NavigationController(reader, actions,
                                      {"enabled": True, "dryRun": True, "targetMode": "marker"},
                                      log=lambda *a, **k: None)
    res = ctrl.navigate_to()
    check("le dry-run se termine en 'dry_run'", res["status"] == "dry_run",
          "(%s / %s)" % (res["status"], res["reason"]))
    check("aucune touche n'est envoyee en dry-run", not actions.keys)
    check("aucun deplacement de souris en dry-run", not actions.moves)
    check("la cible observee est rapportee",
          res.get("target") is not None and res["target"]["dist"] == 40.0)


def test_navigation_arrival_requires_observation():
    print()
    print("=== 3. NAVIGATION : l'arrivee exige une OBSERVATION ===")
    # la cible reste a 40 px : le controleur ne doit JAMAIS declarer l'arrivee
    reader = FakeReader([state(markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                          "kind": "jaune", "area": 60}])])
    actions = FakeActions()
    ctrl = fnav.NavigationController(reader, actions,
                                      {"enabled": True, "dryRun": False, "targetMode": "marker",
                                       "maxTravelSec": 1.0},
                                      log=lambda *a, **k: None)
    res = ctrl.navigate_to()
    check("pas d'arrivee sans observation qui la prouve",
          res["status"] != "arrived", "(%s)" % res["status"])

    # la cible passe a 3 px du centre : arrivee prouvee
    reader2 = FakeReader([
        state(markers=[{"x": 40.0, "y": 0.0, "dist": 40.0, "kind": "jaune", "area": 60}]),
        state(markers=[{"x": 3.0, "y": 0.0, "dist": 3.0, "kind": "jaune", "area": 60}]),
    ])
    actions2 = FakeActions()
    ctrl2 = fnav.NavigationController(reader2, actions2,
                                       {"enabled": True, "dryRun": False, "targetMode": "marker",
                                        "maxTravelSec": 5.0},
                                       log=lambda *a, **k: None)
    res2 = ctrl2.navigate_to()
    check("l'arrivee est declaree sur une observation concordante",
          res2["status"] == "arrived", "(%s / %s)" % (res2["status"], res2["reason"]))
    check("le motif est cite dans la raison",
          "cible observee" in (res2["reason"] or "").lower(), "(%s)" % res2["reason"])


def test_navigation_guards():
    print()
    print("=== 4. NAVIGATION : garde-fous ===")
    # arret d'urgence
    actions = FakeActions()
    actions.stop = True
    reader = FakeReader([state(markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                          "kind": "jaune", "area": 60}])])
    ctrl = fnav.NavigationController(reader, actions,
                                      {"enabled": True, "dryRun": False, "targetMode": "marker"},
                                      log=lambda *a, **k: None)
    res = ctrl.navigate_to()
    check("l'arret d'urgence interrompt le trajet", res["status"] == "aborted",
          "(%s)" % res["status"])
    check("aucune commande n'est envoyee apres l'arret d'urgence",
          not actions.keys and not actions.moves)

    # activite humaine
    actions2 = FakeActions()
    actions2.user = True
    reader2 = FakeReader([state(markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                           "kind": "jaune", "area": 60}])])
    ctrl2 = fnav.NavigationController(reader2, actions2,
                                       {"enabled": True, "dryRun": False, "targetMode": "marker"},
                                       log=lambda *a, **k: None)
    res2 = ctrl2.navigate_to()
    check("l'activite humaine suspend le trajet", res2["status"] == "aborted",
          "(%s)" % res2["status"])

    # aucune cible
    reader3 = FakeReader([state(markers=[])])
    ctrl3 = fnav.NavigationController(reader3, FakeActions(),
                                       {"enabled": True, "dryRun": False, "targetMode": "marker"},
                                       log=lambda *a, **k: None)
    res3 = ctrl3.navigate_to()
    check("sans cible observee, le controleur s'arrete proprement",
          res3["status"] == "no_target", "(%s)" % res3["status"])

    # position illisible
    reader4 = FakeReader([None])
    ctrl4 = fnav.NavigationController(reader4, FakeActions(),
                                       {"enabled": True, "dryRun": False, "targetMode": "marker"},
                                       log=lambda *a, **k: None)
    res4 = ctrl4.navigate_to()
    check("sans minicarte lisible, le controleur s'arrete proprement",
          res4["status"] == "no_position", "(%s)" % res4["status"])

    # limite de distance : la position s'eloigne du depart
    reader5 = FakeReader([state(pos=(0.0, 0.0),
                                 markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                           "kind": "jaune", "area": 60}]),
                          state(pos=(900.0, 0.0),
                                markers=[{"x": 40.0, "y": 0.0, "dist": 40.0,
                                          "kind": "jaune", "area": 60}])])
    ctrl5 = fnav.NavigationController(reader5, FakeActions(),
                                       {"enabled": True, "dryRun": False,
                                        "maxDistancePx": 100.0,
                                        "maxTravelSec": 5.0},
                                       log=lambda *a, **k: None)
    res5 = ctrl5.navigate_to()
    check("la distance maximale est respectee",
          res5["status"] == "blocked", "(%s / %s)" % (res5["status"], res5["reason"]))


def test_navigation_limits_config():
    print()
    print("=== 5. NAVIGATION : configuration et mode ===")
    actions = FakeActions()
    ctrl = fnav.NavigationController(FakeReader([state()]), actions,
                                      {"enabled": False, "dryRun": True},
                                      log=lambda *a, **k: None)
    check("la navigation peut etre desactivee", ctrl.enabled() is False)
    ctrl2 = fnav.NavigationController(FakeReader([state()]), actions,
                                       {"enabled": True, "dryRun": True, "targetMode": "marker"},
                                       log=lambda *a, **k: None)
    check("le mode sec est disponible", ctrl2.dry_run() is True)
    ctrl3 = fnav.NavigationController(FakeReader([state()]), actions,
                                       {"navigationV2": {}},
                                       log=lambda *a, **k: None)
    check("les valeurs par defaut sont utilisees si absentes",
          ctrl3.limit("maxTravelSec", 120.0) == 120.0)
    check("le dry-run par defaut est actif (securite)",
          fnav.NavigationController(FakeReader([state()]), actions, {},
                                     log=lambda *a, **k: None).dry_run() is True)


def isolated(cfg):
    """Configuration de test ISOLEE de la vraie carte du poste.

    Sans cela, le fichier state/fishing-spots.json du developpement
    (qui contient de vrais bancs) s'impose aux tests : ils ne testeraient
    plus ce qu'ils pretendent tester.
    """
    out = dict(cfg)
    out["mapPath"] = tempfile.mkdtemp(prefix="fb-navtest-")
    return out


def test_explore_target():
    print()
    print("=== 7. AMORCAGE : EXPLORATION VERS UN POINT (arrivee prouvee par l'odometre) ===")
    read = state(markers=[{"x": 30.0, "y": 0.0, "dist": 30.0, "kind": "jaune", "area": 60}])
    ctrl = fnav.NavigationController(FakeReader([read]), FakeActions(),
                                     isolated({"enabled": True, "dryRun": True,
                                               "targetMode": "map",
                                               "exploreDistancePx": 12.0}),
                                     log=lambda *a, **k: None)
    target = ctrl.select_target(read)
    check("sans banc accessible, la cible est un point d'exploration",
          target is not None and target.get("kind") == "explore",
          "(%s)" % (target and target.get("kind")))
    if target and target.get("pos"):
        d = math.hypot(target["pos"][0] - read["position"]["x"],
                       target["pos"][1] - read["position"]["y"])
        check("le point d'exploration est a la distance demandee",
              abs(d - 12.0) < 0.01, "(%.2f)" % d)
    # l'exploration est DETERMINISTE a graine fixee
    ctrl2 = fnav.NavigationController(FakeReader([read]), FakeActions(),
                                      {"enabled": True, "dryRun": True,
                                       "targetMode": "map",
                                       "exploreSeed": 3},
                                      log=lambda *a, **k: None)
    t1 = ctrl2.select_target(read)
    ctrl3 = fnav.NavigationController(FakeReader([read]), FakeActions(),
                                      {"enabled": True, "dryRun": True,
                                       "targetMode": "map",
                                       "exploreSeed": 3},
                                      log=lambda *a, **k: None)
    t2 = ctrl3.select_target(read)
    check("l'exploration est reproductible a graine fixee",
          t1 and t2 and t1["pos"] == t2["pos"])


def test_unreliable_position_guard():
    print()
    print("=== 8. GARDE-FOU : POSITION NON MESURABLE ===")

    class BlindActions(FakeActions):
        def capture(self):
            return None      # aucune image : la position est inexploitable

    reader = FakeReader([state(markers=[{"x": 30.0, "y": 0.0, "dist": 30.0,
                                        "kind": "jaune", "area": 60}])])
    actions = BlindActions()
    ctrl = fnav.NavigationController(reader, actions,
                                      {"enabled": True, "dryRun": False,
                                       "targetMode": "marker",
                                       "maxTravelSec": 3.0},
                                      log=lambda *a, **k: None)
    res = ctrl.navigate_to()
    check("sans mesure de position, aucun deplacement n'est pilote",
          res["status"] in ("unreliable_position", "no_position"),
          "(%s)" % res["status"])
    check("aucune commande n'est envoyee sans position",
          not actions.keys and not actions.moves,
          "(keys=%s moves=%s)" % (actions.keys, actions.moves))

    # avec une position mesuree, le trajet est tente
    reader2 = FakeReader([state(markers=[{"x": 30.0, "y": 0.0, "dist": 30.0,
                                          "kind": "jaune", "area": 60}])])
    actions2 = FakeActions()
    ctrl2 = fnav.NavigationController(reader2, actions2,
                                       {"enabled": True, "dryRun": False,
                                        "targetMode": "marker",
                                        "maxTravelSec": 3.0},
                                       log=lambda *a, **k: None)
    res2 = ctrl2.navigate_to()
    check("avec une position mesuree, le trajet est tente",
          res2["status"] != "unreliable_position", "(%s)" % res2["status"])


def main():
    print("=== TESTS MINICARTE + NAVIGATION V2 ===")
    test_minimap_corpus()
    test_navigation_dry_run()
    test_navigation_arrival_requires_observation()
    test_navigation_guards()
    test_navigation_limits_config()
    test_explore_target()
    test_unreliable_position_guard()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
