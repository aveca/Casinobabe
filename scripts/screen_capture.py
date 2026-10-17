#!/usr/bin/env python3
"""Capture l'ecran (ou une fenetre) en PNG - outil de diagnostic visuel.

 usages :
   python scripts/screen_capture.py out.png                # ecran entier
   python scripts/screen_capture.py out.png --title "X"    # fenetre X
   python scripts/screen_capture.py --list                 # lister les fenetres visibles
Aucune entree de credential n'est manipulee ici : uniquement des pixels.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def list_windows():
    out = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((hwnd, buf.value, pid.value))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def find_by_title(needle):
    needle = needle.lower()
    for hwnd, title, pid in list_windows():
        if needle in title.lower():
            return hwnd, title, pid
    return None, None, None


def shot(path, title=None):
    from PIL import ImageGrab
    if title:
        hwnd, real, _pid = find_by_title(title)
        if not hwnd:
            raise SystemExit("fenetre introuvable : %r" % title)
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        import time
        time.sleep(0.4)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        box = (max(r.left, 0), max(r.top, 0), r.right, r.bottom)
        img = ImageGrab.grab(bbox=box, all_screens=True)
    else:
        img = ImageGrab.grab(all_screens=True)
    img.save(path)
    print("%s (%dx%d) <- %s" % (path, img.width, img.height, title or "ecran entier"))


def _print(s):
    """La console Windows est en cp1252 : un titre de fenetre peut contenir
    des caracteres hors ASCII (zero-width space...) et faire tomber print()."""
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, errors="replace").decode(enc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for hwnd, title, pid in list_windows():
            _print("%-10s pid=%-7s %s" % (hex(hwnd), pid, title))
        return
    shot(a.out or "screen.png", a.title)


if __name__ == "__main__":
    main()