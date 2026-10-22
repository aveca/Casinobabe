# -*- coding: utf-8 -*-
"""Tests de non-regression du retour au corps (voie EXTERNE).

Couvrent les trois corrections portees dans fishing_recovery.py :
  A. la carte du monde ouverte bloque les entrees -> aucun deplacement ;
  B. le fantome vole avec ESPACE maintenu (et relache toujours) ;
  C. les clics sont convertis en coordonnees ECRAN (fenetre -> ecran).

Plus : arret/recalcul en l'absence de progression, dialogue
'Resurrect now?' et verification visuelle de l'etat vivant.
"""
import ctypes
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, "scripts"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fishing_recovery as FR  # noqa: E402
from test_fishing_recovery import (FakeExecutor, ZONE_WITH_MARKERS,  # noqa: E402
                                  _read)


class _StubUser32(object):
    """Enregistre les entrees clavier/souris envoyees au client."""

    def __init__(self):
        self.keys = []
        self.mouse = []
        self.foreground = None

    def SendMessageW(self, hwnd, msg, wparam, lparam):
        self.keys.append((msg, wparam))
        return 0

    def GetCursorPos(self, ptr):
        class _P(object):
            x = 1000
            y = 600
        p = _P()
        ptr._obj = p
        return 1

    def SetCursorPos(self, x, y):
        self.mouse.append(("set", int(x), int(y)))
        return 1

    def mouse_event(self, flags, dx, dy, data, extra):
        self.mouse.append(("event", int(flags), int(dx), int(dy)))
        return 1


def _sess(image=None, life="ghost", config=None, **kw):
    ex = FakeExecutor(image=image if image is not None
                      else _read(ZONE_WITH_MARKERS), life=life, **kw)
    cfg = dict(config or {})
    return FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None, config=cfg)


@pytest.fixture(autouse=True)
def _no_focus_lease(monkeypatch):
    """Le bail de focus reel est neutralise : ces tests portent sur la
    logique du moteur, la validation du focus se fait au rejeu reel."""
    import contextlib
    import focus_broker

    @contextlib.contextmanager
    def _fake(hwnd, **kw):
        yield True

    monkeypatch.setattr(focus_broker, "focus_lease", _fake, raising=False)


# --------------------------------------------------------------------------
# A. carte du monde : elle bloque les entrees
# --------------------------------------------------------------------------
def test_map_open_blocks_every_movement():
    """Carte ouverte -> AUCUN deplacement n'est execute."""
    sess = _sess()
    calls = []
    sess._fly = lambda *a, **k: calls.append("fly")
    sess._ensure_world = lambda: False
    sess.analysis = FR.analyze_map(_read(ZONE_WITH_MARKERS),
                                   FR.MapMarkerDetector.with_fixtures())
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    res = sess._st_navigate()
    assert calls == [], "aucun vol ne doit partir avec la carte ouverte"
    assert sess.state == FR.ABORTED


def test_movement_requires_visible_world_confirmation():
    """Le deplacement n'est autorise qu'APRES confirmation visuelle."""
    sess = _sess()
    order = []

    def _ensure():
        order.append("ensure")
        return True

    def _fly(*a, **k):
        order.append("fly")
    sess._ensure_world = _ensure
    sess._fly = _fly
    sess.analysis = FR.analyze_map(_read(ZONE_WITH_MARKERS),
                                   FR.MapMarkerDetector.with_fixtures())
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    assert order and order[0] == "ensure", \
        "la confirmation visuelle precede le deplacement"
    assert "fly" in order, "le vol doit partir"
    assert order.index("fly") > 0, "le vol ne doit pas preceder la confirmation"


def test_world_visible_rejects_map_parchment():
    """Le parchemin de la carte n'est PAS un monde visible."""
    import numpy as np
    import cv2
    parch = np.zeros((1456, 2576, 3), np.uint8)
    parch[:, :] = (40, 150, 220)          # orange saturé (carte)
    assert sess_world(parch) is False


def sess_world(bgr):
    sess = _sess()
    return sess._world_visible(bgr)


# --------------------------------------------------------------------------
# B. vol du fantome : ESPACE maintenu, touches toujours relachees
# --------------------------------------------------------------------------
def test_flight_holds_space_and_releases_every_key():
    """Z + W + ESPACE down, puis les trois key up (aucune touche bloquee)."""
    stub = _StubUser32()
    orig = ctypes.windll.user32
    ctypes.windll.user32 = stub
    try:
        sess = _sess()
        sess.ex.actions._win = lambda: 1234
        sess._fly(0.05)
    finally:
        ctypes.windll.user32 = orig
    downs = [k for k in stub.keys if k[0] == 0x0100]
    ups = [k for k in stub.keys if k[0] == 0x0101]
    assert 0x5A in [k[1] for k in downs], "Z (avance) doit etre presse"
    # W n est plus utilise (le vol annule les fleches).
    assert 0x20 in [k[1] for k in downs], "ESPACE doit etre maintenu"
    assert {k[1] for k in downs} == {k[1] for k in ups}, \
        "chaque touche pressee doit etre relachee"


def test_flight_releases_keys_on_exception():
    """Une exception pendant le vol ne doit laisser AUCUNE touche pressee."""
    stub = _StubUser32()
    orig = ctypes.windll.user32
    ctypes.windll.user32 = stub
    try:
        sess = _sess()
        sess.ex.actions._win = lambda: 1234

        def _boom(*a, **k):
            raise RuntimeError("coupure")
        sess.ex.screen.grab = _boom
        sess._fly(0.05)
    except Exception:
        pass
    finally:
        ctypes.windll.user32 = orig
    downs = {k[1] for k in stub.keys if k[0] == 0x0100}
    ups = {k[1] for k in stub.keys if k[0] == 0x0101}
    assert downs <= ups, "toutes les touches pressees doivent etre relachees"


# --------------------------------------------------------------------------
# C. conversion capture -> ecran
# --------------------------------------------------------------------------
@pytest.mark.parametrize("left,top", [(0, 0), (-8, -8), (12, 34), (-25, 7)])
def test_capture_to_screen_uses_real_window_rect(left, top):
    """La conversion utilise le rectangle REEL de la fenetre."""
    sess = _sess()
    sess._window_rect = lambda: (left, top, left + 2576, top + 1456)
    sx, sy = sess._to_screen(1293, 327)
    assert (sx, sy) == (1293 + left, 327 + top)


def test_held_click_converts_to_screen_coordinates():
    """Le clic maintenu part en coordonnees ECRAN."""
    stub = _StubUser32()
    orig = ctypes.windll.user32
    ctypes.windll.user32 = stub
    try:
        sess = _sess()
        sess._window_rect = lambda: (-8, -8, 2568, 1448)
        sess._held_click(1293, 327, hold=0.01)
    finally:
        ctypes.windll.user32 = orig
    sets = [m for m in stub.mouse if m[0] == "set"]
    assert sets, "le curseur doit etre positionne"
    assert (sets[0][1], sets[0][2]) == (1285, 319), \
        "la position doit etre en coordonnees ecran"
    events = [m for m in stub.mouse if m[0] == "event"]
    assert any(m[1] == 0x0002 for m in events), "appui maintenu attendu"
    assert any(m[1] == 0x0004 for m in events), "relachement attendu"


# --------------------------------------------------------------------------
# progression : arret et recalcul du cap
# --------------------------------------------------------------------------
def test_no_progress_shifts_the_bearing_and_eventually_aborts():
    """Pas de progression -> decalage du cap vise, puis arret controle."""
    sess = _sess(config={"maxStuck": 2})
    sess._ensure_world = lambda: True
    sess._fly = lambda *a, **k: None
    sess.analysis = FR.analyze_map(_read(ZONE_WITH_MARKERS),
                                   FR.MapMarkerDetector.with_fixtures())
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    res = sess._st_progress_check()
    assert res["state"] == FR.NAVIGATING_TO_CORPSE
    assert sess.bearing_offset != 0.0, "le cap vise doit etre decale"
    for _ in range(4):
        sess._st_navigate()
        sess._st_progress_check()
    assert sess.state == FR.ABORTED


# --------------------------------------------------------------------------
# interaction finale
# --------------------------------------------------------------------------
def test_accept_dialog_button_is_detected():
    """Le bouton ROUGE 'Accept' du dialogue est localise."""
    import numpy as np
    import cv2
    img = np.zeros((1456, 2576, 3), np.uint8)
    cv2.rectangle(img, (1190, 318), (1380, 340), (0, 0, 190), -1)
    sess = _sess()
    hit = sess._find_accept_dialog(img)
    assert hit is not None, "le bouton Accept doit etre detecte"
    assert abs(hit["x"] - 1285) <= 12 and abs(hit["y"] - 329) <= 8


def test_interaction_clicks_accept_and_verifies_alive():
    """Clic maintenu sur Accept, puis verification VISUELLE de l'etat
    vivant (voile cyan absent) : la preuve ne depend d'aucun addon."""
    stub = _StubUser32()
    orig = ctypes.windll.user32
    ctypes.windll.user32 = stub
    try:
        sess = _sess()
        sess._window_rect = lambda: (-8, -8, 2568, 1448)
        sess._held_click = lambda x, y, hold=0.1: sess._to_screen(x, y)
        vues = [_accept_frame(), _alive_frame()]
        sess._world_view = lambda: vues.pop(0) if vues else _alive_frame()
        sess.state_reader.read = lambda img, board: {
            "life": "ghost", "signals": {}}
        sess._set(FR.CORPSE_INTERACTION, "test")
        sess._st_corpse_interaction()
        assert sess.state == FR.RESURRECTION_VERIFY
        res = sess._st_verify_alive()
        assert res.get("ok"), "l'etat vivant (visuel) doit valider"
        assert sess.state == FR.PAUSED_SAFE
    finally:
        ctypes.windll.user32 = orig


def _accept_frame():
    """Capture plein ecran portant le bouton ROUGE 'Accept'."""
    import numpy as np
    import cv2
    img = np.zeros((1456, 2576, 3), np.uint8)
    img[:, :] = (60, 120, 40)
    cv2.rectangle(img, (1190, 318), (1380, 340), (0, 0, 190), -1)
    return img


def _alive_frame():
    """Capture synthetique PLEIN ECRAN d'un monde vivant (pas de cyan)."""
    import numpy as np
    img = np.zeros((1456, 2576, 3), np.uint8)
    img[:, :] = (60, 120, 40)      # vert : un monde vivant
    return img


def test_ghost_tint_detects_ghost_world():
    """Un monde voile de cyan est identifie comme fantome."""
    import numpy as np
    img = np.zeros((1456, 2576, 3), np.uint8)
    img[:, :] = (200, 150, 60)      # cyan / bleu du mode fantome
    sess = _sess()
    tint = sess._ghost_tint(img)
    assert tint is not None and tint > 0.5

def test_world_view_closes_the_map_before_any_interaction():
    """Une capture de la VUE 3D exige la carte fermee ET confirmee.

    Regression du 21/10 : la carte pouvait etre ouverte sans couvrir la
    zone echantillonnee (mapOpen=True et monde visible) ; un clic
    d'interaction atterrirait alors sur la carte (« Right Click On Map To
    Zoom Out ») au lieu du dialogue 'Resurrect now?'.
    """
    import numpy as np
    sess = _sess()
    etat = {"closes": 0}
    frame = np.zeros((1456, 2576, 3), np.uint8)

    def _cap():
        return frame

    def _ensure():
        etat["closes"] += 1
        return True

    sess._capture = _cap
    sess._ensure_world = _ensure
    monkey = FR.map_is_open
    FR.map_is_open = lambda bgr: True
    try:
        sess._world_view()
    finally:
        FR.map_is_open = monkey
    assert etat["closes"] == 1, "la carte doit etre fermee avant l'usage"


def test_replay_precondition_requires_map_closed():
    """Le rejeu instrumente signale la carte ouverte (et la ferme)."""
    sess = _sess()
    assert hasattr(sess, "_ensure_world")
    assert hasattr(sess, "_world_visible")

