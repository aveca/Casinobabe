#!/usr/bin/env python3
"""Sonde : peut-on envoyer une entree a WoW SANS lui voler le focus ?

SendInput/keybd_event ne touchent que la fenetre au premier plan. Il existe
pourtant une autre voie : poster directement dans la FILE DE MESSAGES de la
fenetre cible via WM_INPUT (l'API que DirectInput utilise pour le clavier et la
souris). Un message poste/sendu n'exige pas le focus.

Ce script teste, WoW etant reellement en ARRIERE-PLAN :
  1. WM_INPUT clavier (touche de lancer)
  2. WM_INPUT souris (clic droit)
et verifie l'EFFET observable (apparition du flotteur) plutot que de croire
un code de retour.

    python scripts/win32_input_probe.py --key-scan 0x03
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import focus_broker as fb  # noqa: E402
import fishing_observer as fo  # noqa: E402
from screen_capture import list_windows  # noqa: E402

u = ctypes.windll.user32

WM_INPUT = 0x00D2
RIM_INPUT = 0x00001000
RI_KEY_MAKE = 0x00
RI_KEY_BREAK = 0x01
RI_MOUSE_MOVE_RELATIVE = 0x0001

RID_INPUT = 0x10000003      # dwType
USAGE_PAGE_KEYBOARD = 0x01
USAGE_PAGE_MOUSE = 0x02
USAGE_MOUSE = 0x02
USAGE_BTN_RIGHT = 0x02


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hwndTarget", wt.HWND), ("wParam", wt.WPARAM),
                ("time", wt.DWORD)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wt.USHORT), ("Flags", wt.USHORT),
                ("Reserved", wt.USHORT), ("TimeStamp", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("lLastX", wt.LONG), ("lLastY", wt.LONG),
                ("dwButtons", wt.DWORD), ("usFlags", wt.USHORT),
                ("ulRawButtons", wt.DWORD),
                ("lLastButton", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class RAWINPUTDATA(ctypes.Union):
    _fields_ = [("keyboard", RAWKEYBOARD), ("mouse", RAWMOUSE)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUTDATA)]


def send_key_raw(hwnd, scancode):
    """Envoie key make + break via WM_INPUT dans la file de WoW."""
    sent = []
    for flags in (RI_KEY_MAKE, RI_KEY_BREAK):
        ri = RAWINPUT()
        ri.header.dwType = RID_INPUT
        ri.header.dwSize = ctypes.sizeof(RAWINPUT)
        ri.header.hwndTarget = hwnd
        ri.header.wParam = RIM_INPUT
        ri.data.keyboard.MakeCode = scancode
        ri.data.keyboard.Flags = flags
        res = u.SendMessageW(hwnd, WM_INPUT, ctypes.c_void_p(RIM_INPUT),
                         ctypes.cast(ctypes.byref(ri), ctypes.c_void_p))
        sent.append(res)
        time.sleep(0.06)
    return sent


def move_cursor_abs(hwnd, x, y):
    """Deplace le curseur (global) - necessaire car le jeu lit la position
    reelle du curseur pour viser, la WM_INPUT souris ne portant que le
    deplacement relatif."""
    u.SetCursorPos(int(x), int(y))


def send_click_raw(hwnd, button="right"):
    """Clic droit via WM_INPUT (down puis up)."""
    down_flag = 0x0004 if button == "right" else 0x0001
    sent = []
    for pressed in (True, False):
        ri = RAWINPUT()
        ri.header.dwType = RID_INPUT
        ri.header.dwSize = ctypes.sizeof(RAWINPUT)
        ri.header.hwndTarget = hwnd
        ri.header.wParam = RIM_INPUT
        ri.data.mouse.lLastX = 0
        ri.data.mouse.lLastY = 0
        ri.data.mouse.dwButtons = down_flag if pressed else 0
        ri.data.mouse.usFlags = 0
        sent.append(u.SendMessageW(hwnd, WM_INPUT, ctypes.c_void_p(RIM_INPUT),
                                   ctypes.cast(ctypes.byref(ri), ctypes.c_void_p)))
        time.sleep(0.06)
    return sent


def bobber_visible(hwnd, tries=14, interval=0.6):
    """Cherche le flotteur a l'ecran : preuve que l'entree a ete prise en
    compte PAR LE JEU (pas seulement acceptee par l'API)."""
    from fishing_bot import detect_bobber_hybrid, to_bgr, _ambient_light
    for _ in range(tries):
        img, err = fo.capture_window(hwnd)
        if img is None:
            time.sleep(interval)
            continue
        arr = to_bgr(img)
        amb = _ambient_light(arr)
        bx, by, conf, _ = detect_bobber_hybrid(arr, {}, 0.0, amb, v_floor=80)
        if bx is not None and conf and conf >= 0.5:
            return bx, by, conf
        time.sleep(interval)
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-scan", default="0x03", help="scancode (0x03 = touche '2')")
    a = ap.parse_args()
    scan = int(a.key_scan, 0)

    wins = fo.list_wow_windows()
    if not wins:
        print("pas de fenetre WoW")
        return 2
    hwnd = wins[0][0]
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)
        time.sleep(1.2)

    others = [h for h, t, _p in list_windows() if t and "Warcraft" not in t]
    other = others[0] if others else None
    if other:
        fb._force_foreground(other)
        time.sleep(1.0)
    bg = (u.GetForegroundWindow() != hwnd)
    print("WoW en arriere-plan : %s" % bg)
    print("rect : %s" % (fo.list_wow_windows()[0][2],))

    print("envoi WM_INPUT clavier (scancode 0x%02X)..." % scan)
    res = send_key_raw(hwnd, scan)
    print("  SendMessage retourne : %s" % res)

    bx, by, conf = bobber_visible(hwnd)
    if bx is not None:
        print("RESULTAT : FLOTTEUR DETECTE en (%d,%d) conf=%.2f" % (bx, by, conf))
        print("=> WoW a bien recu la touche SANS avoir le focus : 100%% background possible.")
        return 0
    print("RESULTAT : aucun flotteur apres %.1f s" % (14 * 0.6))
    print("=> WM_INPUT n'a pas ete pris en compte (le jeu ignore l'entree")
    print("   injectee quand la fenetre n'a pas le focus).")
    return 1


if __name__ == "__main__":
    sys.exit(main())