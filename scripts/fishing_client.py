"""Adapter client WoW -> moteur d'action (fishing_process).

Une seule implementation des primitives :
  * click(x, y, button)  -> clic synthesise (WM_*BUTTONDOWN/UP)
  * key(k)               -> touche (Z = auto-run)
  * displacement(a, b)   -> deplacement mesure dans la vue 3D
  * angle(a, b)          -> direction mesuree (degres, repere ecran)

Ce module remplace les scripts jetables : toute action en jeu passe par là.
"""
import ctypes
import math
import os
import sys
import time

import cv2
import numpy as np
import win32api

V2 = r"C:\factory\worktrees\casinobabe-v2"
if V2 not in sys.path:
    sys.path.insert(0, os.path.join(V2, "scripts"))

from fishing_observer import capture_window, find_wow_window   # noqa: E402

u = ctypes.windll.user32
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101

VK = {"Z": 0x5A, "M": 0x4D, "E": 0x45, "X": 0x58, "ESC": 0x1B,
      "ENTER": 0x0D, "SPACE": 0x20, "F9": 0x78}


def to_bgr(img):
    if img is None:
        return None
    arr = np.array(img)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def world_displacement(a, b):
    """Deplacement mesure entre deux captures (vue 3D)."""
    from fishing_minimap import world_displacement as _wd
    a = to_bgr(a)
    b = to_bgr(b)
    if a is None or b is None:
        return 0.0, 0.0, 0.0
    try:
        dx, dy, conf = _wd(a, b)
    except Exception:
        return 0.0, 0.0, 0.0
    return float(dx), float(dy), float(conf)


class WowActions(object):
    """Primitives client reelles."""

    def __init__(self, hwnd=None):
        self.hwnd = hwnd
        self.rect = None
        self.clicks = []
        self.keys = []
        self.left_down = False
        self.camera_moves = []

    def _win(self):
        if not self.hwnd:
            self.hwnd, _t, self.rect = find_wow_window()
        return self.hwnd

    def capture(self):
        h = self._win()
        if not h:
            return None
        img = capture_window(h)
        if isinstance(img, tuple):
            img = img[0]
        return img

    def click(self, x, y, button="right"):
        h = self._win()
        _h, _t, rect = find_wow_window()
        self.rect = rect
        sx, sy = rect[0] + int(x), rect[1] + int(y)
        pt = ctypes.wintypes.POINT(int(sx), int(sy))
        u.ScreenToClient(h, ctypes.byref(pt))
        lp = (pt.y << 16) | (pt.x & 0xFFFF)
        down, up = ((WM_LBUTTONDOWN, WM_LBUTTONUP) if button == "left"
                    else (WM_RBUTTONDOWN, WM_RBUTTONUP))
        u.SendMessageW(h, down, 0, lp)
        time.sleep(0.05)
        u.SendMessageW(h, up, 0, lp)
        self.clicks.append((button, int(x), int(y)))

    def key(self, k):
        h = self._win()
        # la fenetre doit avoir le focus : sinon la touche part ailleurs
        # (et la carte reste ouverte / le clic n'atteint pas l'UI).
        try:
            if u.GetForegroundWindow() != h:
                u.SetForegroundWindow(h)
                time.sleep(0.15)
        except Exception:
            pass
        vk = VK.get(k.upper() if isinstance(k, str) else k)
        if vk is None:
            if isinstance(k, str) and len(k) == 1:
                vk = ord(k.upper())
            else:
                return
        flags = 0x00000001 | (vk << 16) | (1 << 30)
        u.SendMessageW(h, WM_KEYDOWN, vk, flags)
        time.sleep(0.05)
        u.SendMessageW(h, WM_KEYUP, vk, flags | (1 << 31))
        self.keys.append(k)

    def press_left(self, down=True):
        """Maintient ou relache le bouton GAUCHE (rotation de camera)."""
        h = self._win()
        flags = 0x00000001 | (0x01 << 16) | (1 << 30)
        if not down:
            flags |= 1 << 31
        u.SendMessageW(h, 0x0100 if down else 0x0101, 0x01, flags)
        self.left_down = bool(down)

    def rotate_camera(self, dx, dy=0):
        """Tourne la camera : clic gauche enfonce + deplacement de souris.

        C'est le mecanisme standard du client pour s'orienter ; le bot
        l'utilise pour faire pointer le personnage vers le corps, et la
        SENSIBILITE (px de souris -> degres) est APPRISE par mesure.
        """
        self.press_left(True)
        time.sleep(0.06)
        self.move_mouse(int(dx), int(dy))
        time.sleep(0.10)
        self.press_left(False)
        self.camera_moves.append((int(dx), int(dy)))

    def move_mouse(self, dx, dy):
        cx, cy = win32api.GetCursorPos()
        win32api.SetCursorPos((int(cx + dx), int(cy + dy)))

    # -- mesure ---------------------------------------------------------
    def displacement(self, a, b):
        return world_displacement(a, b)

    def angle(self, a, b):
        dx, dy, _c = world_displacement(a, b)
        if math.hypot(dx, dy) < 0.2:
            return None
        return math.degrees(math.atan2(dy, dx))
