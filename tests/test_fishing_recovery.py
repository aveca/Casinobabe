#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests de la RECUPERATION DU CORPS et des garde-fous associes.

Couverture demandee :
  * detection du marqueur du corps (sur capture REELLE) ;
  * distinction corps / joueur / fond de carte ;
  * detections ambigues ou absentes -> aucun deplacement commande ;
  * calcul d'itinéraire (cap carte -> angle ecran) et etapes de deplacement ;
  * absence de progression et arrets de securite ;
  * detection de la fenetre de resurrection ;
  * confirmation de l'etat vivant ;
  * arret de la peche apres la recuperation ;
  * mort pendant la navigation, auto-run actif a la mort, mode fantome,
    corps inaccessible, erreur de direction, 2e tentative sans progression,
    retour a l'etat vivant, persistance de l'incident apres redemarrage ;
  * la navigation de peche reste desactivee jusqu'a resurrection ET
    validation d'une position sure.

Les tests de detection s'appuient sur les captures REELLES deposees dans
tests/fixtures/ (carte du monde de Dustwallow Marsh, marqueur du corps et
marqueur du joueur visibles).
"""

import json
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fishing_map as FM                                  # noqa: E402
import fishing_process as FP                              # noqa: E402
import fishing_recovery as FR                             # noqa: E402
import fishing_safety as FS                               # noqa: E402

FIX = os.path.join(HERE, "fixtures")
ZONE_WITH_MARKERS = os.path.join(FIX, "map_dustwallow_zone.png")
FULL_MAP = os.path.join(FIX, "map_dustwallow_full.png")
CORPS_TPL = os.path.join(FIX, "map_corpse_marker.png")
JOUEUR_TPL = os.path.join(FIX, "map_player_marker.png")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _copy_if_needed(src, dst):
    if os.path.isfile(src) and not os.path.isfile(dst):
        import shutil
        shutil.copyfile(src, dst)
    return dst


@pytest.fixture(scope="module", autouse=True)
def _fixtures():
    """Les references reelles (zone carte + marqueurs) doivent etre presentes."""
    src_zone = r"C:\Users\user\AppData\Local\Temp\fishmeasure\carte_zoom3.png"
    _copy_if_needed(src_zone, ZONE_WITH_MARKERS)
    assert os.path.isfile(CORPS_TPL), "gabarit du corps manquant"
    assert os.path.isfile(JOUEUR_TPL), "gabarit du joueur manquant"


def _read(path):
    import cv2
    img = cv2.imread(path)
    assert img is not None, "capture illisible : %s" % path
    return img


# --------------------------------------------------------------------------
# doubles de test (client WoW simule)
# --------------------------------------------------------------------------
class FakeActions(object):
    """Client simule : journalise les commandes, ne deplace rien."""

    def __init__(self):
        self.keys = []
        self.clicks = []

    def key(self, k):
        self.keys.append(k)

    def click(self, x, y, button="right"):
        self.clicks.append((button, int(x), int(y)))

    def displacement(self, a, b):
        """Le client simule ne bouge pas (0 px)."""
        return 0.0, 0.0, 0.0

    def rotate_camera(self, dx, dy=0):
        """Rotation de camera simulee (aucun effet)."""
        self.camera = getattr(self, "camera", []) + [(dx, dy)]

    def hold_key(self, key, seconds=1.5):
        """Maintien de touche simule."""
        self.keys.append("%s(%.1fs)" % (key, seconds))


class FakeScreen(object):
    """Ecran simule : renvoie toujours la meme image."""

    def __init__(self, image=None):
        self.image = image
        self.grabs = 0

    def grab(self, force=False):
        self.grabs += 1
        return self.image


class FakeStateReader(object):
    """Lecteur d'etat simule : renvoie l'etat configure."""

    def __init__(self, owner):
        self.owner = owner

    def read(self, img, board=None):
        return {"life": self.owner.life, "confidence": 0.9,
                "signals": {"releaseDialog": False, "ghostTint": False},
                "why": ["simule"]}


class FakeExecutor(object):
    """Executor simule : les deplacements sont simules (mesure renvolee)."""

    def __init__(self, image=None, life="ghost", move_px=30.0,
                 move_ok=True, prompt_hit=None):
        self.screen = FakeScreen(image)
        self.actions = FakeActions()
        self.board = FP.Blackboard()
        self.state = FakeStateReader(self)
        self.life = life
        self.move_px = move_px
        self.move_ok = move_ok
        self.prompt_hit = prompt_hit
        self.goto_calls = []
        self.click_calls = []

    def _read(self, img, board=None):
        return self.state.read(img, board)

    def do_goto(self, at, min_px=0.3, tries=2, direction_tol=70.0,
                unsafe="stop", max_px=900.0, settle=1.2):
        self.goto_calls.append(list(at))
        if not self.move_ok:
            return {"ok": False, "outcome": FS.OUTCOME_BLOCKED,
                    "reason": "aucune progression", "px": 0.0}
        return {"ok": True, "outcome": FS.OUTCOME_OK, "px": self.move_px,
                "angle": 0.0, "reason": "progression de %.1f px" % self.move_px}

    def do_click(self, at, button="left"):
        self.click_calls.append(list(at))
        return {"ok": True, "outcome": FS.OUTCOME_OK}

    def do_key(self, key):
        self.actions.key(key)
        return {"ok": True}

    def do_wait(self, sec):
        return {"ok": True}


def _gold_prompt_image(w=1200, h=700):
    """Image synthetique avec une invite 'Resurrect' doree au pied du
    personnage (la zone de detection est centree sur le personnage)."""
    import cv2
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)
    cv2.rectangle(img, (int(w * 0.46), int(h * 0.60)), (int(w * 0.46) + 90,
                                                     int(h * 0.60) + 22),
                  (40, 190, 235), -1)      # BGR : dore
    return img


# --------------------------------------------------------------------------
# 1. detection du marqueur du corps
# --------------------------------------------------------------------------
def test_detect_corpse_marker_on_real_capture():
    det = FM.MapMarkerDetector.with_fixtures()
    img = _read(ZONE_WITH_MARKERS)
    found = det.detect_all(img)
    assert found["corpse"] is not None, "marqueur du corps non detecte"
    assert found["corpse"].score >= 0.45
    assert found["player"] is not None, "marqueur du joueur non detecte"


def test_detect_corpse_on_independent_game_capture():
    """Capture reelle de la carte EN JEU (hors fixtures) : le detecteur doit
    retrouver le meme cap que sur la reference."""
    src = r"C:\Users\user\AppData\Local\Temp\fishmeasure\carte_jeu.png"
    if not os.path.isfile(src):
        pytest.skip("capture en jeu indisponible")
    import cv2
    det = FM.MapMarkerDetector.with_fixtures()
    res = FM.analyze_map(cv2.imread(src), det)
    assert res.corpse is not None
    assert res.player is not None
    assert res.usable, "analyse de carte non exploitable : %s" % res.reasons
    # meme cap que la reference (les deux captures sont du meme endroit)
    assert abs(res.bearing_deg - 79.4) < 12.0


def test_no_false_positive_without_markers():
    """Une carte sans marqueur de corps ne doit rien detecter."""
    det = FM.MapMarkerDetector.with_fixtures()
    img = _read(FULL_MAP)
    res = FM.analyze_map(img, det)
    assert res.corpse is None
    assert res.player is None
    assert not res.usable
    assert res.ambiguous


def test_corpse_and_player_are_distinct_markers():
    det = FM.MapMarkerDetector.with_fixtures()
    img = _read(ZONE_WITH_MARKERS)
    res = FM.analyze_map(img, det)
    assert res.corpse is not None and res.player is not None
    assert res.corpse.kind == FM.CORPS
    assert res.player.kind == FM.JOUEUR
    # les deux marqueurs ne sont pas le meme objet
    assert (res.corpse.x, res.corpse.y) != (res.player.x, res.player.y)


def test_ambiguous_capture_blocks_movement():
    """Carte sans marqueur -> aucun deplacement ne doit etre commande."""
    ex = FakeExecutor(image=_read(FULL_MAP))
    mem = FS.IncidentMemory(None)
    sess = FR.RecoverySession(ex, state_reader=ex.state, memory=mem,
                              log=lambda *a, **k: None)
    res = sess.run(max_transitions=12)
    assert not res["ok"]
    assert sess.state == FR.ABORTED
    assert not ex.goto_calls, "un deplacement a ete commande sans marqueur"


# --------------------------------------------------------------------------
# 2. calcul d'itineraire
# --------------------------------------------------------------------------
def test_map_bearing_to_screen_is_a_rotation():
    """Le cap carte -> angle ecran est une rotation pure du cap du perso."""
    for heading in (0.0, 90.0, 180.0, -45.0):
        for bearing in (0.0, 45.0, 120.0, 200.0):
            screen = FM.map_bearing_to_screen(bearing, heading)
            assert screen is not None
            # le cap de la cible par rapport au personnage doit etre conserve
            rel = math.radians(bearing - heading)
            expected = math.degrees(math.atan2(-math.cos(rel), math.sin(rel)))
            assert abs(screen - expected) < 1e-6


def test_map_bearing_to_screen_refuses_unknown_heading():
    assert FM.map_bearing_to_screen(45.0, None) is None
    assert FM.map_bearing_to_screen(None, 45.0) is None


def test_click_angle_accounts_for_measured_rotation():
    assert abs(FM.screen_to_click_angle(90.0, 0.0) - 90.0) < 1e-9
    assert abs(FM.screen_to_click_angle(90.0, 30.0) - 60.0) < 1e-9
    assert FM.screen_to_click_angle(None, 10.0) is None


def test_route_planning_uses_spiral_when_bearing_unreliable():
    """Marqueurs confondus -> cap non fiable -> recherche par spirale."""
    img = _read(ZONE_WITH_MARKERS)
    ex = FakeExecutor(image=img)
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None)
    sess.analysis = FM.analyze_map(img, FM.MapMarkerDetector.with_fixtures())
    sess.heading_deg = 0.0
    sess._set(FR.ROUTE_PLANNING, "test")
    res = sess._st_route_planning()
    assert res["state"] == FR.TRAVEL_STEP
    if not sess.analysis.bearing_reliable:
        assert len(sess.route) >= 6
    else:
        assert len(sess.route) == 1


# --------------------------------------------------------------------------
# 3. deplacement, progression, arrets
# --------------------------------------------------------------------------
def test_no_progress_switches_direction_instead_of_looping():
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS), move_px=0.0,
                      move_ok=False)
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None)
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.heading_deg = 0.0
    sess.route = [0.0, 45.0, 90.0]
    sess.route_index = 0
    sess._set(FR.TRAVEL_STEP, "test")
    sess._st_travel_step()
    assert sess.state == FR.PROGRESS_CHECK
    res = sess._st_progress_check()
    assert res["state"] == FR.TRAVEL_STEP, "la direction doit changer"
    # la direction est DECALEE (contournement d'obstacle), pas rejouee
    assert sess.block_turn == 1
    assert sess.route and sess.route[0] != 0.0


def test_budget_of_attempts_stops_the_session():
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS), move_px=0.0,
                      move_ok=False)
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None,
                              config={"maxAttempts": 2})
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.heading_deg = 0.0
    sess.route = [0.0, 45.0, 90.0, 135.0]
    sess.route_index = 0
    sess._set(FR.TRAVEL_STEP, "test")
    sess._st_travel_step()
    sess._st_progress_check()
    sess._st_travel_step()
    sess._st_progress_check()
    # le budget est epuise : le deplacement suivant doit arreter la session
    res = sess._st_travel_step()
    assert sess.state == FR.ABORTED
    assert not res["ok"]


def test_unmeasured_movement_aborts():
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS))
    ex.do_goto = lambda *a, **k: {"ok": False, "outcome": FS.OUTCOME_BLOCKED,
                                  "reason": "non mesure", "px": None}
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None)
    sess._set(FR.PROGRESS_CHECK, "test")
    res = sess._st_progress_check()
    assert sess.state == FR.ABORTED
    assert not res["ok"]


# --------------------------------------------------------------------------
# 4. detection de la fenetre de resurrection
# --------------------------------------------------------------------------
def test_resurrect_prompt_detected_in_world():
    prompt = FR.ResurrectPrompt()
    hit = prompt.find(_gold_prompt_image())
    assert hit is not None, "invite Resurrect non detectee"
    assert 0 < hit["x"] < 1200 and 0 < hit["y"] < 700


def test_resurrect_prompt_ignored_in_action_bars():
    """Une invite doree dans les barres d'action ne doit pas compter."""
    import cv2
    img = np.zeros((700, 1200, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)
    cv2.rectangle(img, (900, 640), (990, 662), (40, 190, 235), -1)
    assert FR.ResurrectPrompt().find(img) is None


def test_corpse_approach_stops_when_prompt_appears():
    ex = FakeExecutor(image=_gold_prompt_image())
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None)
    sess._set(FR.CORPSE_APPROACH, "test")
    res = sess._st_corpse_approach()
    assert res["state"] == FR.RESURRECTION_DETECTED
    assert sess.prompt_hit is not None


# --------------------------------------------------------------------------
# 5. verification de l'etat vivant + arret de la peche
# --------------------------------------------------------------------------
def test_alive_requires_state_reader_confirmation():
    """Un clic ne suffit pas : sans StateReader 'alive', pas de validation."""
    ex = FakeExecutor(image=_gold_prompt_image(), life="ghost")
    sess = FR.RecoverySession(ex, state_reader=ex.state, log=lambda *a, **k: None)
    sess.prompt_hit = {"x": 400, "y": 300}
    sess._set(FR.RESURRECTION_DETECTED, "test")
    sess._st_resurrection()
    assert sess.state == FR.VERIFY_ALIVE
    res = sess._st_verify_alive()
    assert sess.state == FR.CORPSE_APPROACH, "pas de validation sans 'alive'"
    assert not res.get("ok")


def test_recovery_ends_paused_safe_and_does_not_resume_fishing():
    safety = FS.RecoveryStateMachine(log=lambda *a, **k: None)
    ex = FakeExecutor(image=_gold_prompt_image(), life="alive")
    mem = FS.IncidentMemory(None)
    sess = FR.RecoverySession(ex, state_reader=ex.state, safety=safety,
                              memory=mem, log=lambda *a, **k: None)
    sess.prompt_hit = {"x": 400, "y": 300}
    sess._set(FR.RESURRECTION_DETECTED, "test")
    sess._st_resurrection()
    res = sess._st_verify_alive()
    assert res["ok"]
    assert sess.state == FR.PAUSED_SAFE
    assert safety.state == FS.SAFE_REPOSITION
    # la navigation de peche reste bloquee tant que la securite n'est pas
    # explicitement validee
    assert not safety.navigation_allowed
    assert not safety.fishing_allowed


def test_incident_memory_records_the_recovery(tmp_path):
    path = str(tmp_path / "incidents.json")
    mem = FS.IncidentMemory(path)
    mem.record_incident(FS.OUTCOME_DEAD, zone="Dustwallow Marsh",
                        cause="noyade", confidence=0.9)
    mem2 = FS.IncidentMemory(path)
    assert len(mem2.data["incidents"]) == 1
    assert mem2.data["incidents"][0]["cause"] == "noyade"
    assert mem2.zone_status("Dustwallow Marsh") == "suspect"


# --------------------------------------------------------------------------
# 6. garde-fous de securite (incident de noyade)
# --------------------------------------------------------------------------
def test_death_during_navigation_blocks_fishing_navigation():
    safety = FS.RecoveryStateMachine(log=lambda *a, **k: None)
    safety.enter_navigation(destination="spot-1", zone="Dustwallow Marsh")
    assert safety.navigation_allowed
    safety.observe_death({"dialogue": True})
    assert safety.state == FS.DEAD
    assert not safety.navigation_allowed
    assert not safety.fishing_allowed


def test_ghost_mode_transition():
    safety = FS.RecoveryStateMachine(log=lambda *a, **k: None)
    safety.observe_death()
    safety.observe_ghost({"tint": 0.05})
    assert safety.state == FS.GHOST


def test_navigation_stays_blocked_until_resurrection_and_safe_position():
    safety = FS.RecoveryStateMachine(log=lambda *a, **k: None)
    safety.observe_death()
    safety.observe_ghost()
    safety.observe_corpse(marker=(10, 20))
    assert safety.state == FS.CORPSE_RECOVERY
    assert not safety.navigation_allowed
    safety.observe_resurrected()
    assert safety.state == FS.RESURRECTION
    assert not safety.navigation_allowed, "resurrection seule ne suffit pas"
    safety.observe_safe_position({"dry": True})
    assert safety.state == FS.SAFE_REPOSITION
    assert not safety.navigation_allowed


def test_incident_persists_across_restart(tmp_path):
    path = str(tmp_path / "mem.json")
    mem = FS.IncidentMemory(path)
    mem.record_incident(FS.OUTCOME_WATER, zone="mare", cause="eau",
                        confidence=0.8)
    restarted = FS.IncidentMemory(path)
    assert len(restarted.data["incidents"]) == 1
    assert restarted.is_forbidden("mare")


def test_single_observation_does_not_condemn_a_zone():
    mem = FS.IncidentMemory(None)
    mem.record_incident(FS.OUTCOME_WATER, zone="mare", cause="eau")
    assert mem.zone_status("mare") == "suspect"
    mem.record_incident(FS.OUTCOME_WATER, zone="mare", cause="eau")
    assert mem.zone_status("mare") == "dangerous"


def test_safe_click_target_refuses_uncertain_classification():
    import cv2
    img = np.full((400, 600, 3), 128, dtype=np.uint8)      # contraste nul
    ok, why = FS.safe_click_target(img, 100, 100)
    assert not ok
    assert "incertaine" in why or "contraste" in why


def test_safe_click_target_refuses_water_edge():
    import cv2
    img = np.zeros((400, 600, 3), dtype=np.uint8)
    img[:, :] = (30, 30, 30)          # eau : sombre et desaturee
    ok, why = FS.safe_click_target(img, 100, 100)
    assert not ok


def test_check_progress_and_direction_error():
    ok, reason, outcome = FS.check_progress(12.0)
    assert ok and outcome == FS.OUTCOME_OK
    ok, reason, outcome = FS.check_progress(0.0)
    assert not ok and outcome == FS.OUTCOME_BLOCKED
    ok, err = FS.direction_error(10.0, 20.0)
    assert ok and abs(err - 10.0) < 1e-6
    ok, err = FS.direction_error(10.0, 120.0)
    assert not ok
