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

# --- entrees souris PHYSIQUES (SendInput) -----------------------------
# Le client lit la souris via le chemin d'entree reel : un message
# synthetique (WM_*) avec lParam=0 est interprete comme un clic au coin
# haut-gauche de la fenetre -> ouverture de la fiche personnage.  Pour
# toute action de CAMERA, on utilise donc de vrais evenements.
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

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
    """Primitives client reelles.

    POINT CRITIQUE (mesure du 2026-10-21) : Windows n'accorde le focus
    clavier qu'a UNE fenetre et WoW lit le clavier en RAW INPUT.  Sans
    focus reel, une touche ou un clic n'atteint pas le jeu : deplacement
    mesure = 0,00 px.  Chaque entree est donc envoyee sous BAIL DE FOCUS
    (focus_broker), et le focus est REVERIFIE apres acquisition.

    L'AFK est aussi traite : le client ignore TOUTES les entrees quand il
    est AFK ("You are now AFK"), d'ou des clics et des boutons inertes.
    """

    def __init__(self, hwnd=None, respect_user=False):
        self.hwnd = hwnd
        self.rect = None
        self.clicks = []
        self.keys = []
        self.left_down = False
        self.camera_moves = []
        self.respect_user = respect_user
        self.afk_lifted = 0

    def _win(self):
        if not self.hwnd:
            self.hwnd, _t, self.rect = find_wow_window()
        return self.hwnd

    def _focused(self):
        """Le focus clavier est-il reellement sur la fenetre du jeu ?"""
        h = self._win()
        return bool(h) and u.GetForegroundWindow() == h

    def _lease(self):
        """Bail de focus (focus_broker) : Sans ce bail, rien n'atteint le jeu."""
        from focus_broker import focus_lease
        h = self._win()
        return focus_lease(h, respect_user=self.respect_user,
                           log=lambda *a, **k: None)

    @staticmethod
    def _neutralize():
        try:
            from focus_broker import neutralize_own_input
            neutralize_own_input()
        except Exception:
            pass

    def _send(self, fn):
        """Envoie une entree sous bail de focus, avec reverification."""
        h = self._win()
        with self._lease() as got:
            if not got:
                return False
            if not self._focused():
                # le bail a ete obtenu mais l'OS n'a pas honore le focus
                u.SetForegroundWindow(h)
                time.sleep(0.15)
            fn()
            time.sleep(0.12)      # le jeu consomme l'input (async)
            return True

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

        def _do():
            pt = ctypes.wintypes.POINT(int(sx), int(sy))
            u.ScreenToClient(h, ctypes.byref(pt))
            lp = (pt.y << 16) | (pt.x & 0xFFFF)
            down, up = ((WM_LBUTTONDOWN, WM_LBUTTONUP) if button == "left"
                        else (WM_RBUTTONDOWN, WM_RBUTTONUP))
            u.SendMessageW(h, down, 0, lp)
            time.sleep(0.05)
            u.SendMessageW(h, up, 0, lp)
        ok = self._send(_do)
        if ok:
            self.clicks.append((button, int(x), int(y)))
        return ok

    def wake_up(self, hold=0.05):
        """Reveille un personnage AFK par un VRAI clic (souris physique).

        Un personnage AFK ignore les touches SYNTHETIQUES : son timer
        n'est remis a zero que par une entree reelle (constat du 21/10 :
        le fantome restait AFK, les touches partaient (Z+W+ESPACE
        journalisees) mais px=0.04 - il ne bougeait pas).  Un clic
        physique sur le personnage suffit a le reveiller.
        """
        h = self._win()
        _h, _t, rect = find_wow_window()
        self.rect = rect
        # MOUVEMENT REEL de la souris (le timer AFK repart au moindre
        # mouvement) - AUCUN clic : un clic gauche au centre de l'ecran
        # selectionne la cible, fait tourner la camera et n'est pas un
        # 'clic to move'.
        x = int((rect[0] + rect[2]) / 2)
        y = int(rect[1] + (rect[3] - rect[1]) * 0.62)
        u.SetCursorPos(x, y)
        time.sleep(0.05)
        for dx in (12, -12, 8, -8):
            u.mouse_event(0x0001, dx, 0, 0, 0)   # MOUSEEVENTF_MOVE
            time.sleep(0.02)
        return True

    def key(self, k):
        h = self._win()
        vk = VK.get(k.upper() if isinstance(k, str) else k)
        if vk is None:
            if isinstance(k, str) and len(k) == 1:
                vk = ord(k.upper())
            else:
                return False

        def _do():
            flags = 0x00000001 | (vk << 16) | (1 << 30)
            self._neutralize()
            u.SendMessageW(h, WM_KEYDOWN, vk, flags)
            time.sleep(0.05)
            u.SendMessageW(h, WM_KEYUP, vk, flags | (1 << 31))
        ok = self._send(_do)
        if ok:
            self.keys.append(k)
        return ok

    def hold_key(self, key, seconds=1.5):
        """Maintient une touche pendant N secondes (deplacement fluide).

        ATTENTION : dans le client _anniversary_, le clavier de direction
        n'est PAS l'avance (`bind W ACTIONBUTTON1`, `bind UP NONE`) et le
        client lit le clavier en RAW INPUT : cette primitive sert pour les
        raccourcis reellement lies (Z = auto-run), pas pour W/UP.
        """
        h = self._win()
        vk = VK.get(key.upper() if isinstance(key, str) else key)
        if vk is None and isinstance(key, str) and len(key) == 1:
            vk = ord(key.upper())
        if vk is None:
            return False
        down = 0x00000001 | (vk << 16) | (1 << 30)
        up = down | (1 << 31)

        def _do():
            u.SendMessageW(h, WM_KEYDOWN, vk, down)
            time.sleep(float(seconds))
            u.SendMessageW(h, WM_KEYUP, vk, up)
        ok = self._send(_do)
        if ok:
            self.keys.append("%s(%.1fs)" % (key, seconds))
        return ok

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

        BUG CORRIGE (2026-10-21) : l'envoi de WM_LBUTTONDOWN avec lParam=0
        est interprete par le client comme un clic AU COIN HAUT-GAUCHE, ou
        se trouve le PORTRAIT DU PERSONNAGE : la fiche perso s'ouvrait a
        chaque rotation.  On passe donc par SendInput (evenements souris
        REELS) apres avoir positionne le curseur au CENTRE de l'ecran, et
        l'evenement porte de vraies coordonnees.

        La SENSIBILITE (px de souris -> degres) est APPRISE par mesure
        (releve du cap sur la fleche de la minicarte).
        """
        h = self._win()
        _h, _t, rect = find_wow_window()
        self.rect = rect
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        # position de depart : centre de la fenetre du jeu (eviter tout clic
        # sur une icone d'UI)
        start_x = rect[0] + int((rect[2] - rect[0]) * 0.5)
        start_y = rect[1] + int((rect[3] - rect[1]) * 0.5)
        target_x = start_x + int(dx)
        target_y = start_y + int(dy)

        def _do():
            win32api.SetCursorPos((int(start_x), int(start_y)))
            time.sleep(0.05)
            down = INPUT(type=INPUT_MOUSE,
                         mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None))
            u.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            time.sleep(0.05)
            steps = 4
            for i in range(1, steps + 1):
                mx = int(start_x + (target_x - start_x) * i / steps)
                my = int(start_y + (target_y - start_y) * i / steps)
                inp = INPUT(type=INPUT_MOUSE,
                            mi=MOUSEINPUT(int(mx * 65535 / sw),
                                          int(my * 65535 / sh), 0,
                                          MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                                          0, None))
                u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
                time.sleep(0.02)
            time.sleep(0.05)
            up = INPUT(type=INPUT_MOUSE,
                       mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None))
            u.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        ok = self._send(_do)
        if ok:
            self.camera_moves.append((int(dx), int(dy)))
        return ok

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
