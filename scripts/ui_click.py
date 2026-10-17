#!/usr/bin/env python3
"""Clic cible dans une fenetre Windows (position relative), sans OCR.

 usages :
   python scripts/ui_click.py --title "Battle.net" --rel 0.49,0.88
   python scripts/ui_click.py --title "World of Warcraft" --rel 0.5,0.5 --double
   python scripts/ui_click.py --title "Battle.net" --rel 0.49,0.88 --shots

--rel prend des FRACTIONS de la fenetre (0..1) : le point reste correct meme
si la fenetre est redimensionnee ou sur un autre ecran. Un clic sur un element
nomme n'est pas fait ici : pas d'OCR, pas de lecture de credentials.

Avertissement : n'utilise JAMAIS cet outil pour saisir un mot de passe. Le
flux "/Battle.net" / passkey / code email est fait par l'utilisateur, ce
script se contente de cliquer sur des boutons.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_RESTORE = 9


def find_window(needle):
    needle = needle.lower()
    found = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if needle in buf.value.lower():
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def window_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def click(x, y, double=False):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    for _ in range(2 if double else 1):
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.08)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="sous-chaine du titre")
    ap.add_argument("--rel", help="position fractionnaire 'x,y' dans la fenetre")
    ap.add_argument("--x", type=int)
    ap.add_argument("--y", type=int)
    ap.add_argument("--double", action="store_true")
    ap.add_argument("--index", type=int, default=0, help="quelle fenetre si plusieurs")
    ap.add_argument("--shots", action="store_true", help="capture avant/apres")
    a = ap.parse_args()

    wins = find_window(a.title)
    if not wins:
        raise SystemExit("fenetre introuvable : %r" % a.title)
    if a.index >= len(wins):
        raise SystemExit("index %d hors range (fenetres: %d)" % (a.index, len(wins)))
    hwnd, title = wins[a.index]
    l, t, r, b = window_rect(hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.5)
        l, t, r, b = window_rect(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)

    if a.rel:
        fx, fy = (float(v) for v in a.rel.split(","))
        x, y = l + (r - l) * fx, t + (b - t) * fy
    elif a.x is not None and a.y is not None:
        x, y = a.x, a.y
    else:
        raise SystemExit("fournir --rel x,y ou --x/--y")

    if a.shots:
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).save("logs/ui-before.png")
    click(x, y, a.double)
    print("clic%s sur %r -> (%d,%d) dans (%d,%d,%d,%d)"
          % (" double" if a.double else "", title, x, y, l, t, r, b))
    if a.shots:
        time.sleep(1.2)
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).save("logs/ui-after.png")
        print("captures : logs/ui-before.png, logs/ui-after.png")


if __name__ == "__main__":
    main()