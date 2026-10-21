# -*- coding: utf-8 -*-
"""
test_spot_navigator.py — tests hors-ligne de scripts/spot_navigator.py.

Aucun client WoW n'est requis : find_wow_window, capture_window, player_health,
hold_key, send_key, emergency_stop_pressed sont remplacés par des doubles.
Les images sont synthétiques (minicarte avec blob joueur, zone de jeu avec ou
sans eau).

Mode script : exit 0 si tout passe, 1 sinon.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import numpy as np
import cv2
from PIL import Image

import spot_navigator as nav  # noqa: E402

RESULTS = []


def ck(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# ---------------------------------------------------------------------------
# Doubles (remplacent les appels Windows / WoW)
# ---------------------------------------------------------------------------
class _FakeWindow:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.hold_calls = []
        self.key_calls = []

    def next_frame(self):
        if self.index >= len(self.frames):
            return self.frames[-1]
        f = self.frames[self.index]
        self.index += 1
        return f


def make_frame(player_px=None, water=False, wow=True):
    """
    Construit une image synthétique (1280x720) :
    - zone de jeu avec bruit chromatique (simule le monde 3D WoW) -> is_wow_frame True
    - minicarte en haut-droite avec un blob blanc = joueur (si player_px)
    - zone centrale bleue = eau (si water)
    """
    rng = np.random.default_rng(42)
    # Bruit a pleine amplitude dans la zone de jeu (simule la diversite
    # chromatique d'un vrai frame WoW : is_wow_frame exige >20000 couleurs
    # distinctes dans au moins une region echantillonnee).
    img = rng.integers(0, 256, size=(720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(img, (50, 50), (1230, 650), (40, 60, 90), -1)
    if water:
        cv2.rectangle(img, (300, 200), (980, 500), (60, 140, 160), -1)  # eau bleu-cyan
    # Minicarte Blizzard (zone haute-droite)
    x0, x1 = int(1280 * 0.72), int(1280 * 0.98)
    y0, y1 = int(720 * 0.02), int(720 * 0.28)
    cv2.rectangle(img, (x0, y0), (x1, y1), (30, 30, 40), -1)  # fond minicarte sombre
    if player_px is not None:
        cx, cy = player_px
        cv2.circle(img, (cx, cy), 7, (230, 230, 230), -1)  # blob joueur (claire)
    return Image.fromarray(img)


def install_doubles(frames, dead=False):
    fake = _FakeWindow(frames)

    def find_wow_window():
        return (1, "World of Warcraft", (0, 0, 1280, 720))

    def capture_window(hwnd):
        if fake.index >= len(fake.frames):
            return fake.frames[-1], None
        return fake.next_frame(), None

    def player_health(img_bgr):
        if dead:
            return "dead", {"reason": "simule"}
        return "alive", {}

    def hold_key(vk, seconds):
        fake.hold_calls.append((vk, seconds))
        return True

    def send_key(vk):
        fake.key_calls.append(vk)
        return True

    def emergency_stop_pressed():
        return False

    import spot_navigator as sn
    sn.find_wow_window = find_wow_window
    sn.capture_window = capture_window
    sn.player_health = player_health
    sn.hold_key = hold_key
    sn.send_key = send_key
    sn.emergency_stop_pressed = emergency_stop_pressed
    return fake


# ---------------------------------------------------------------------------
# 1. Détection du joueur sur la minicarte
# ---------------------------------------------------------------------------
def test_detect_player_center():
    frame_pil = make_frame(player_px=(1150, 100))  # dans la zone minicarte
    frame = cv2.cvtColor(np.asarray(frame_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    gx, gy, conf = nav.detect_player_on_minimap(frame)
    ck("detect joueur centre minicarte", gx is not None and conf > 0,
       "trouve=(%s,%s) conf=%.2f" % (gx, gy, conf))


def test_detect_player_none():
    frame_pil = make_frame(player_px=None)
    frame = cv2.cvtColor(np.asarray(frame_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    gx, gy, conf = nav.detect_player_on_minimap(frame)
    ck("detect joueur absent -> None", gx is None and conf == 0.0,
       "trouve=(%s,%s)" % (gx, gy))


def test_detect_player_outside_minimap():
    # joueur hors zone minicarte (coin bas-gauche) => pas détecté comme joueur
    frame_pil = make_frame(player_px=(100, 650))
    frame = cv2.cvtColor(np.asarray(frame_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    gx, gy, conf = nav.detect_player_on_minimap(frame)
    ck("detect joueur hors minicarte -> None", gx is None)


# ---------------------------------------------------------------------------
# 2. Détection d'eau
# ---------------------------------------------------------------------------
def test_detect_water_yes():
    frame_pil = make_frame(water=True)
    frame = cv2.cvtColor(np.asarray(frame_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    frac, has = nav.detect_water_onscreen(frame)
    ck("detect eau presente", has is True and frac > 0.03,
       "frac=%.3f" % frac)


def test_detect_water_no():
    # Fond herbe (vert forêt, H=60 en OpenCV) - hors plage eau (H 85-115)
    img = np.full((720, 1280, 3), (34, 139, 34), dtype=np.uint8)  # BGR forest green
    cv2.rectangle(img, (50, 50), (1230, 650), (40, 60, 90), -1)   # zone jeu sombre
    x0, x1 = int(1280 * 0.72), int(1280 * 0.98)
    y0, y1 = int(720 * 0.02), int(720 * 0.28)
    cv2.rectangle(img, (x0, y0), (x1, y1), (30, 30, 40), -1)      # minicarte sombre
    frac, has = nav.detect_water_onscreen(img)
    ck("detect eau absente (herbe)", has is False and frac < 0.03,
       "frac=%.3f" % frac)


# ---------------------------------------------------------------------------
# 3. Direction vers cible
# ---------------------------------------------------------------------------
def test_direction_toward():
    d = nav._direction_toward((100, 100), (200, 100), 10)
    ck("direction cible a droite", d is not None and d[0] == 100 and d[1] == 0,
       "dx=%s dy=%s dist=%s" % (d[0], d[1], round(d[2], 1)))

    d = nav._direction_toward((100, 100), (100, 50), 10)
    ck("direction cible en haut", d is not None and d[1] < 0,
       "dy=%s angle=%s" % (d[1], round(d[3], 1)))

    d = nav._direction_toward(None, (200, 100), 10)
    ck("direction joueur None -> None", d is None)


# ---------------------------------------------------------------------------
# 4. Boucle de navigation complete (joueur eloigne -> arrive)
# ---------------------------------------------------------------------------
def test_navigate_arrives():
    # Frames : joueur s'approche progressivement de la cible (1150,100)
    frames = []
    # positions successives du joueur : eloigne -> cible
    positions = [(900, 300), (980, 200), (1060, 140), (1130, 110), (1150, 100)]
    for p in positions:
        frames.append(make_frame(player_px=p, water=True))
    fake = install_doubles(frames)

    cfg = nav.NavConfig(max_waves=10, budget_s=60, hold_s=0.4,
                        arrive_radius_px=40, progress_min_px=2,
                        max_no_progress=6, capture_interval=0.0)
    result = nav.SpotNavigator(cfg).navigate_to((1150, 100))
    ck("navigation arrivee", result.ok is True and result.phase == "arrived",
       "phase=%s waves=%d ok=%s" % (result.phase, result.waves, result.ok))
    ck("touche W envoyee (mouvement)", fake.hold_calls and all(vk == 0x57 for vk, _ in fake.hold_calls),
       "hold_calls=%s" % fake.hold_calls[:3])


def test_navigate_no_direction():
    # Aucun joueur jamais détecté
    frames = [make_frame(player_px=None, water=True)] * 6
    install_doubles(frames)
    cfg = nav.NavConfig(max_waves=6, budget_s=30, hold_s=0.3,
                        max_no_progress=3, capture_interval=0.0)
    result = nav.SpotNavigator(cfg).navigate_to((1150, 100))
    ck("arret pas de direction", result.ok is False and result.phase == "no_direction",
       "phase=%s waves=%d" % (result.phase, result.waves))


def test_navigate_budget():
    # Joueur détecté mais ne progresse jamais (meme position) -> budget / obstacle
    frames = [make_frame(player_px=(1050, 100), water=True)] * 8
    install_doubles(frames)
    cfg = nav.NavConfig(max_waves=8, budget_s=30, hold_s=0.3,
                        progress_min_px=5, max_no_progress=3, capture_interval=0.0)
    result = nav.SpotNavigator(cfg).navigate_to((1150, 100))
    ck("arret pas de progres (obstacle)", result.ok is False and result.phase == "obstacle",
       "phase=%s waves=%d" % (result.phase, result.waves))


def test_navigate_dead():
    frames = [make_frame(player_px=(1150, 100), water=True)]
    install_doubles(frames, dead=True)
    cfg = nav.NavConfig(max_waves=5, budget_s=30, capture_interval=0.0)
    result = nav.SpotNavigator(cfg).navigate_to((1150, 100))
    ck("arret personnage mort", result.ok is False and result.phase == "dead",
       "phase=%s" % result.phase)


def test_navigate_no_wow():
    import spot_navigator as sn
    sn.find_wow_window = lambda: (None, None, None)
    cfg = nav.NavConfig(max_waves=3, budget_s=10, capture_interval=0.0)
    result = nav.SpotNavigator(cfg).navigate_to((1150, 100))
    ck("pas de fenetre WoW", result.ok is False and result.phase == "no_wow",
       "phase=%s" % result.phase)


# ---------------------------------------------------------------------------
# Bilan
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_detect_player_center()
    test_detect_player_none()
    test_detect_player_outside_minimap()
    test_detect_water_yes()
    test_detect_water_no()
    test_direction_toward()
    test_navigate_arrives()
    test_navigate_no_direction()
    test_navigate_budget()
    test_navigate_dead()
    test_navigate_no_wow()

    ok = sum(1 for _, o, _ in RESULTS if o)
    print("---- %d/%d PASS ----" % (ok, len(RESULTS)))
    sys.exit(0 if ok == len(RESULTS) else 1)
