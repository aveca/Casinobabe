#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests de la RECUPERATION DU CORPS et des garde-fous associes.

Sequence exigee : ALIVE -> DEAD_SPECTER -> NAVIGATING_TO_CORPSE ->
CORPSE_INTERACTION -> RESURRECTION_VERIFY -> PAUSED_SAFE.

Couverture :
  * detection du marqueur du corps sur une capture REELLE (gabarits
    disjoints crane / fleche verte) ;
  * distinction corps / joueur / fond de carte ;
  * detections ambigues ou absentes -> AUCUN deplacement commande ;
  * cap carte -> ecran et boucle a retroaction clic -> cap ;
  * distance au corps : elle doit DIMINUER (mesure de progression) ;
  * absence de progression et arrets de securite ;
  * detection de la fenetre d'interaction (vue 3D, pas la carte) ;
  * confirmation independante de l'etat vivant ;
  * aucune reprise de la peche avant validation ;
  * persistance de l'incident apres redemarrage.
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
FULL_MAP = os.path.join(FIX, "map_dustwallow_nomarkers.png")
CORPS_TPL = os.path.join(FIX, "map_corpse_marker.png")
JOUEUR_TPL = os.path.join(FIX, "map_player_marker.png")


def _read(path):
    import cv2
    img = cv2.imread(path)
    assert img is not None, "capture illisible : %s" % path
    return img


# --------------------------------------------------------------------------
# doubles de test
# --------------------------------------------------------------------------
class FakeActions(object):
    """Client simule : journalise les commandes, ne deplace rien."""

    def __init__(self):
        self.keys = []
        self.clicks = []
        self.camera = []
        self.held = []

    def key(self, k):
        self.keys.append(k)

    def click(self, x, y, button="right"):
        self.clicks.append((button, int(x), int(y)))

    def displacement(self, a, b):
        return 0.0, 0.0, 0.0

    def rotate_camera(self, dx, dy=0):
        self.camera.append((dx, dy))

    def hold_key(self, key, seconds=1.5):
        self.held.append((key, seconds))


class FakeScreen(object):
    def __init__(self, image=None):
        self.image = image
        self.grabs = 0

    def grab(self, force=False):
        self.grabs += 1
        return self.image


class FakeStateReader(object):
    def __init__(self, owner):
        self.owner = owner

    def read(self, img, board=None):
        return {"life": self.owner.life, "confidence": 0.9,
                "signals": {"releaseDialog": self.owner.release_dialog,
                             "ghostTint": False},
                "why": ["simule"]}


class FakeExecutor(object):
    def __init__(self, image=None, life="ghost", move_px=30.0,
                 move_ok=True, release_dialog=False):
        self.screen = FakeScreen(image)
        self.actions = FakeActions()
        self.board = FP.Blackboard()
        self.state = FakeStateReader(self)
        self.life = life
        self.move_px = move_px
        self.move_ok = move_ok
        self.release_dialog = release_dialog
        self.goto_calls = []
        self.click_calls = []

    def do_goto(self, at, min_px=0.3, tries=2, direction_tol=70.0,
                unsafe="stop", max_px=900.0, settle=1.2):
        self.goto_calls.append(list(at))
        if not self.move_ok:
            return {"ok": False, "outcome": FS.OUTCOME_BLOCKED,
                    "reason": "aucune progression", "px": 0.0}
        return {"ok": True, "outcome": FS.OUTCOME_OK, "px": self.move_px,
                "angle": 0.0, "reason": "progression"}

    def do_click(self, at, button="left"):
        self.click_calls.append(list(at))
        return {"ok": True, "outcome": FS.OUTCOME_OK}

    def do_key(self, key):
        self.actions.key(key)
        return {"ok": True}

    def do_wait(self, sec):
        return {"ok": True}


def _gold_prompt_image(w=1200, h=700):
    """Invite doree au pied du personnage, dans la vue 3D."""
    import cv2
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)
    cv2.rectangle(img, (int(w * 0.46), int(h * 0.60)),
                  (int(w * 0.46) + 90, int(h * 0.60) + 22),
                  (40, 190, 235), -1)      # BGR : dore
    return img


# --------------------------------------------------------------------------
# 1. detection du marqueur du corps
# --------------------------------------------------------------------------
def test_detect_corpse_marker_on_real_capture():
    det = FM.MapMarkerDetector.with_fixtures()
    found = det.detect_all(_read(ZONE_WITH_MARKERS))
    assert found["corpse"] is not None, "marqueur du corps non detecte"
    assert found["corpse"].score >= 0.45
    assert found["player"] is not None, "marqueur du joueur non detecte"


def test_templates_are_disjoint():
    """Les gabarits NE DOIVENT PAS se recouvrir : sinon les deux marqueurs
    tombent au meme endroit, le cap devient du bruit et la distance au
    corps ne diminue jamais (bug reel du 2026-10-21)."""
    import cv2
    a = cv2.imread(CORPS_TPL)
    b = cv2.imread(JOUEUR_TPL)
    assert a is not None and b is not None
    # le gabarit du corps ne doit pas contenir de pixels verts (la fleche
    # du joueur est verte et VOISINE du crane sur la carte)
    hsv = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)
    green = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85)
             & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 120)).mean()
    assert green < 0.25, "le gabarit du corps contient la fleche du joueur"


def test_corpse_and_player_are_separated_on_the_map():
    det = FM.MapMarkerDetector.with_fixtures()
    res = FM.analyze_map(_read(ZONE_WITH_MARKERS), det)
    assert res.corpse is not None and res.player is not None
    assert res.distance_px > 15.0, "les marqueurs ne doivent pas confondus"
    assert res.bearing_reliable


def test_bearing_is_stable_between_two_independent_captures():
    """Le cap doit etre identique sur deux captures REELLES differentes :
    c'est la preuve que le signal est reel et pas du bruit."""
    import cv2
    src = r"C:\Users\user\AppData\Local\Temp\fishmeasure\carte_jeu.png"
    if not os.path.isfile(src):
        pytest.skip("capture en jeu indisponible")
    det = FM.MapMarkerDetector.with_fixtures()
    ref = FM.analyze_map(_read(ZONE_WITH_MARKERS), det)
    live = FM.analyze_map(cv2.imread(src), det)
    assert live.corpse is not None and live.player is not None
    assert abs(live.bearing_deg - ref.bearing_deg) < 12.0
    assert abs(live.distance_px - ref.distance_px) < 12.0


def test_no_false_positive_without_markers():
    det = FM.MapMarkerDetector.with_fixtures()
    res = FM.analyze_map(_read(FULL_MAP), det)
    assert res.corpse is None
    assert res.player is None
    assert not res.usable
    assert res.ambiguous


def test_ambiguous_capture_blocks_movement():
    """Carte sans marqueur -> aucun deplacement ne doit etre commande."""
    ex = FakeExecutor(image=_read(FULL_MAP))
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              memory=FS.IncidentMemory(None),
                              log=lambda *a, **k: None)
    res = sess.run(max_transitions=12)
    assert not res["ok"]
    assert sess.state == FR.ABORTED
    assert not ex.goto_calls, "un deplacement a ete commande sans marqueur"
    assert not ex.actions.clicks, "un clic a ete envoye sans marqueur"


# --------------------------------------------------------------------------
# 2. cap carte -> ecran et retroaction clic -> cap
# --------------------------------------------------------------------------
def test_map_bearing_to_screen_is_a_rotation():
    for heading in (0.0, 90.0, 180.0, -45.0):
        for bearing in (0.0, 45.0, 120.0, 200.0):
            screen = FM.map_bearing_to_screen(bearing, heading)
            assert screen is not None
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


def test_navigation_uses_feedback_loop_on_heading():
    """Le moteur pilote par BOUCLE A RETROACTION : l'angle de clic est
    corrige de (cap vise - cap obtenu)."""
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS), move_px=25.0)
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.heading = 0.0
    sess.theta = 0.0
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    assert sess.state == FR.PROGRESS_CHECK
    # le cap obtenu et l'angle applique doivent etre journalises
    last = sess._last
    assert last["target"] is not None
    assert last["theta"] is not None
    assert last["click"][0] >= 0 and last["click"][1] >= 0


# --------------------------------------------------------------------------
# 3. progression : la distance au corps doit diminuer
# --------------------------------------------------------------------------
def test_distance_to_corpse_is_the_progress_metric():
    """Apres un relocalisation, la distance au corps est mesuree et
    comparee a la precedente."""
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS))
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.last_distance = 100.0
    sess._set(FR.RELOCALIZATION, "test")
    res = sess._st_relocalization()
    assert res["state"] == FR.NAVIGATING_TO_CORPSE
    assert sess.last_distance is not None
    assert sess.last_distance < 100.0


def test_no_progress_triggers_controlled_correction_then_abort():
    """Aucune progression -> CORRECTION CONTROLEE (angle decale) ; abandon
    seulement apres plusieurs echecs (le moteur doit pouvoir se
    degager d'un obstacle, pas s'arreter au premier echec)."""
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS), move_px=0.0,
                      move_ok=False)
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None,
                              config={"maxStuck": 2})
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.heading = 0.0
    sess.theta = 0.0
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    res = sess._st_progress_check()
    assert res["state"] == FR.NAVIGATING_TO_CORPSE, "il doit reessayer"
    assert sess.stuck_count == 1
    assert sess.theta != 0.0, "l'angle doit etre decale"
    # apres plusieurs echecs : arret controle avec preuve
    sess._st_navigate()
    sess._st_progress_check()
    sess._st_navigate()
    res = sess._st_progress_check()
    assert sess.state == FR.ABORTED
    assert not res["ok"]
    assert "progression" in res["reason"]


def test_unmeasured_movement_aborts():
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS))
    ex.actions.displacement = lambda a, b: (0.0, 0.0, 0.0)
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess._last = {"px": None}
    sess._set(FR.PROGRESS_CHECK, "test")
    res = sess._st_progress_check()
    assert sess.state == FR.ABORTED
    assert not res["ok"]


def test_budget_of_attempts_stops_the_session():
    ex = FakeExecutor(image=_read(ZONE_WITH_MARKERS), move_px=0.0,
                      move_ok=False)
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None,
                              config={"maxAttempts": 2, "maxStuck": 1})
    sess.analysis = FM.analyze_map(_read(ZONE_WITH_MARKERS),
                                    FM.MapMarkerDetector.with_fixtures())
    sess.heading = 0.0
    sess.theta = 0.0
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    sess._st_progress_check()
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    res = sess._st_progress_check()
    assert sess.state == FR.ABORTED
    assert not res["ok"]


# --------------------------------------------------------------------------
# 4. interaction avec le corps
# --------------------------------------------------------------------------
def test_resurrect_prompt_detected_in_world_view():
    hit = FR.ResurrectPrompt().find(_gold_prompt_image())
    assert hit is not None, "invite d'interaction non detectee"
    assert 0 < hit["x"] < 1200 and 0 < hit["y"] < 700


def test_resurrect_prompt_ignored_outside_the_world_region():
    """Une invite doree dans les barres d'action ne doit pas compter."""
    import cv2
    img = np.zeros((700, 1200, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)
    cv2.rectangle(img, (900, 640), (990, 662), (40, 190, 235), -1)
    assert FR.ResurrectPrompt().find(img) is None


def test_corpse_interaction_clicks_and_moves_to_verify():
    ex = FakeExecutor(image=_gold_prompt_image())
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess._set(FR.CORPSE_INTERACTION, "test")
    res = sess._st_corpse_interaction()
    assert res["state"] == FR.RESURRECTION_VERIFY
    assert sess.prompt_hit is not None
    assert ex.click_calls, "aucun clic sur l'invite"


def test_interaction_attempts_are_bounded():
    ex = FakeExecutor(image=_gold_prompt_image())
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None,
                              config={"maxResurrectClicks": 2})
    sess.attempts = 0
    for _ in range(4):
        sess._set(FR.CORPSE_INTERACTION, "test")
        sess._st_corpse_interaction()
    assert sess.state == FR.ABORTED


# --------------------------------------------------------------------------
# 5. confirmation de l'etat vivant + arret de la peche
# --------------------------------------------------------------------------
def test_alive_requires_state_reader_confirmation():
    """Un clic ne suffit pas : sans 'alive' confirme, pas de validation."""
    ex = FakeExecutor(image=_gold_prompt_image(), life="ghost")
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess.prompt_hit = {"x": 400, "y": 300}
    sess._set(FR.RESURRECTION_VERIFY, "test")
    res = sess._st_verify_alive()
    assert sess.state == FR.CORPSE_INTERACTION, "pas de validation sans 'alive'"
    assert not res.get("ok")


def test_recovery_ends_paused_safe_and_does_not_resume_fishing():
    safety = FS.RecoveryStateMachine(log=lambda *a, **k: None)
    ex = FakeExecutor(image=_gold_prompt_image(), life="alive")
    mem = FS.IncidentMemory(None)
    sess = FR.RecoverySession(ex, state_reader=ex.state, safety=safety,
                              memory=mem, log=lambda *a, **k: None)
    sess.prompt_hit = {"x": 400, "y": 300}
    sess._set(FR.RESURRECTION_VERIFY, "test")
    res = sess._st_verify_alive()
    assert res["ok"]
    assert sess.state == FR.PAUSED_SAFE
    assert safety.state == FS.SAFE_REPOSITION
    assert not safety.navigation_allowed
    assert not safety.fishing_allowed


def test_paused_safe_is_only_reachable_after_confirmation():
    """PAUSED_SAFE ne doit pas etre atteignable sans 'alive' confirme."""
    assert "PAUSED_SAFE" in FR.REQUIRED_SEQUENCE
    assert FR.RESURRECTION_VERIFY in FR.REQUIRED_SEQUENCE
    ex = FakeExecutor(image=_gold_prompt_image(), life="ghost")
    sess = FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None)
    sess._set(FR.RESURRECTION_VERIFY, "test")
    sess._st_verify_alive()
    assert sess.state != FR.PAUSED_SAFE


def test_required_state_sequence_is_declared():
    assert FR.REQUIRED_SEQUENCE == (
        "ALIVE", "DEAD_SPECTER", "NAVIGATING_TO_CORPSE",
        "CORPSE_INTERACTION", "RESURRECTION_VERIFY", "PAUSED_SAFE")


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
    img = np.full((400, 600, 3), 128, dtype=np.uint8)      # contraste nul
    ok, why = FS.safe_click_target(img, 100, 100)
    assert not ok
    assert "incertaine" in why or "contraste" in why


def test_safe_click_target_refuses_water_edge():
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
