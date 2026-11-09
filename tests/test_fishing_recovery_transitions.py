# -*- coding: utf-8 -*-
"""Transitions du moteur de recuperation, sur CAPTURES REELLES du client.

Les captures reelles disponibles dans le depot sont celles du client
(captures/fishing-bot/*.png, plein ecran 2576x1456).  Elles servent :
  * a valider le detecteur d'etat de vie (voile cyan) sur une image
    REELLE du client (et non une image synthetique) ;
  * a valider la detection de "carte du monde ouverte" sur une image
    reelle quand elle existe.

Ce qui n'est PAS couvert ici (et exige une occasion reelle) est signale
dans le rapport : dialogue 'Resurrect now?' et vue fantome.

Aucun test ne declenche de deplacement dans le jeu : le focus et le
bail sont neutralises.
"""
import glob
import os

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, "scripts"), HERE):
    if p not in sys.path if False else False:  # pragma: no cover
        pass
import sys                                             # noqa: E402
for _p in (os.path.join(ROOT, "scripts"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cv2                                             # noqa: E402
import fishing_recovery as FR                          # noqa: E402
from test_fishing_recovery import FakeExecutor, _read  # noqa: E402
from test_fishing_recovery_external import ZONE_WITH_MARKERS  # noqa: E402


def _real_captures():
    pats = [os.path.join(ROOT, "captures", "fishing-bot", "*.png"),
            os.path.join(ROOT, "captures", "*.png")]
    out = []
    for pat in pats:
        out.extend(sorted(glob.glob(pat)))
    return [p for p in out if os.path.getsize(p) > 500000]


REAL = _real_captures()


def _sess(image=None, life="ghost", config=None, **kw):
    ex = FakeExecutor(image=image if image is not None
                      else _read(ZONE_WITH_MARKERS), life=life, **kw)
    return FR.RecoverySession(ex, state_reader=ex.state,
                              log=lambda *a, **k: None,
                              config=dict(config or {}))


# --------------------------------------------------------------------------
# detection d'etat de vie sur une capture REELLE du client
# --------------------------------------------------------------------------
@pytest.mark.skipif(not REAL, reason="aucune capture reelle du client")
def test_real_capture_is_read_as_alive_not_ghost():
    """Une capture REELLE du client (personnage vivant) n'est pas un
    fantome : le voile cyan est absent."""
    sess = _sess()
    img = cv2.imread(REAL[0])
    assert img is not None
    tint = sess._ghost_tint(img)
    assert tint is not None
    assert tint < 0.5, "une capture reelle du personnage vivant ne doit " \
                        "pas etre lue comme fantome (tint=%.3f)" % tint


@pytest.mark.skipif(not REAL, reason="aucune capture reelle du client")
def test_real_capture_keeps_the_client_window_geometry():
    """La geometrie reelle (taille de capture) est prise en compte par
    les conversions : pas de coordonnee codee en dur."""
    sess = _sess()
    img = cv2.imread(REAL[0])
    h, w = img.shape[:2]
    assert w >= 2000 and h >= 1000, "capture client plein ecran attendue"
    sess._window_rect = lambda: (0, 0, w, h)
    sx, sy = sess._to_screen(w // 2, h // 2)
    assert (sx, sy) == (w // 2, h // 2)


# --------------------------------------------------------------------------
# etat vivant : la recuperation est IGNOREE
# --------------------------------------------------------------------------
def test_alive_state_skips_the_recovery_entirely():
    """Si le lecteur d'etat dit 'vivant', la machine ne lance aucune
    navigation (et le resultat est 'ignoré')."""
    sess = _sess(life="alive")
    sess._set(FR.DEAD_SPECTER, "test")
    res = sess._st_init()
    # le moteur doit SORTIR de la recuperation sans lancer de navigation
    assert sess.state != FR.NAVIGATING_TO_CORPSE
    assert sess.state in (FR.ALIVE, FR.PAUSED_SAFE, FR.ABORTED)
    reason = str(res.get("reason") or "").lower()
    assert "vivant" in reason or sess.state in (FR.ALIVE, FR.PAUSED_SAFE), \
        "la raison doit dire que le personnage est vivant : %r" % reason


# --------------------------------------------------------------------------
# etat fantome : la machine entre dans la navigation
# --------------------------------------------------------------------------
def test_ghost_state_enters_navigation():
    """Etat fantome -> analyse de carte puis navigation."""
    sess = _sess(life="ghost")
    sess._set(FR.DEAD_SPECTER, "test")
    sess._st_init()
    assert sess.state in (FR.MAP_ANALYSIS, FR.NAVIGATING_TO_CORPSE)


# --------------------------------------------------------------------------
# carte : ouverture, fermeture confirmee, echec sur confirmation
# --------------------------------------------------------------------------
def test_map_open_forces_close_and_confirmation(monkeypatch):
    """Carte ouverte -> _ensure_world() la ferme ET le confirme par une
    nouvelle capture ; un simple envoi de commande ne suffit pas."""
    sess = _sess()
    etat = {"open": True, "captures": 0, "confirmed": 0}

    def _cap():
        etat["captures"] += 1
        return np.zeros((1456, 2576, 3), np.uint8)

    def _key(_k):
        etat["open"] = False

    monkeypatch.setattr(FR, "map_is_open", lambda bgr: etat["open"])
    sess._capture = _cap
    sess.ex.actions.key = _key
    ok = sess._ensure_world()
    assert ok is True
    assert etat["captures"] >= 2, "la fermeture doit etre CONFIRMEE par une " \
                                 "nouvelle capture"
    assert etat["open"] is False


def test_unconfirmed_close_stops_before_any_movement(monkeypatch):
    """Si la fermeture n'est pas confirmee, AUCUN deplacement ne part."""
    sess = _sess()
    flew = []
    monkeypatch.setattr(FR, "map_is_open", lambda bgr: True)
    sess._capture = lambda: np.zeros((1456, 2576, 3), np.uint8)
    sess._fly = lambda *a, **k: flew.append(1)
    sess.analysis = FR.analyze_map(_read(ZONE_WITH_MARKERS),
                                   FR.MapMarkerDetector.with_fixtures())
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    assert flew == [], "aucun vol ne doit partir"
    assert sess.state == FR.ABORTED


def test_closed_map_allows_movement():
    """Carte fermee et monde visible -> le deplacement est autorise."""
    sess = _sess()
    order = []
    sess._ensure_world = lambda: (order.append("ensure") or True)
    sess._fly = lambda *a, **k: order.append("fly")
    sess.analysis = FR.analyze_map(_read(ZONE_WITH_MARKERS),
                                   FR.MapMarkerDetector.with_fixtures())
    sess._set(FR.NAVIGATING_TO_CORPSE, "test")
    sess._st_navigate()
    assert "fly" in order, "le vol doit partir carte fermee"


# --------------------------------------------------------------------------
# voie de production : aucun contournement
# --------------------------------------------------------------------------
def test_production_path_uses_recovery_session():
    """fishing_bot.py instancie RecoverySession : le moteur teste est
    celui qui tourne en production."""
    src = open(os.path.join(ROOT, "scripts", "fishing_bot.py"),
               encoding="utf-8").read()
    assert "from fishing_recovery import RecoverySession" in src
    assert "RecoverySession(" in src
    # la peche ne doit pas etre relancee automatiquement apres la
    # resurrection : on cherche l'arret explicite
    assert "PAUSED_SAFE" in src


def test_replay_script_never_starts_the_fishing():
    """Le rejeu instrumente ne lance QUE la recuperation, jamais la
    peche (pas d'import ni d'appel a fishing_bot)."""
    src = open(os.path.join(ROOT, "scripts", "fishing_recovery_replay.py"),
               encoding="utf-8").read()
    assert "import fishing_bot" not in src
    assert "from fishing_bot import" not in src
    assert "fishing_bot.main(" not in src
    assert "def main" in src
