#!/usr/bin/env python3
"""Mesure : PrintWindow renvoie-t-il une frame FRAICHE quand WoW n'a pas le focus ?

Constat du 2026-10-17 : en arriere-plan, le bot ne detecte pas le flotteur
(unreliable qui grimpe, observedSec=0) alors que le jeu tourne. Hypothese :
PrintWindow rend la DERNIERE frame peinte, et DirectX/WoW ne redessine pas
quand la fenetre n'a pas le focus -> le bot regarde un ecran fige.

Ce script mesure la variation entre captures successives, AVEC et SANS focus.
Un jeu anime (eau qui bouge) doit produire une variation non nulle quand la
capture est fraiche.

    python scripts/diag_stale_frames.py --samples 6
"""
import argparse
import ctypes
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import focus_broker as fb  # noqa: E402
import fishing_observer as fo  # noqa: E402
from fishing_bot import to_bgr  # noqa: E402
from screen_capture import list_windows  # noqa: E402


def grab(hwnd):
    img, err = fo.capture_window(hwnd)
    if img is None:
        return None, err
    return to_bgr(img).astype(np.int16), None


def series(hwnd, n, interval, label):
    """Variation entre captures successives. 0% = frame figee (perimee)."""
    prev, err = grab(hwnd)
    if prev is None:
        print("%s : capture KO (%s)" % (label, err))
        return []
    diffs = []
    for i in range(n - 1):
        time.sleep(interval)
        cur, err = grab(hwnd)
        if cur is None:
            print("%s : capture KO (%s)" % (label, err))
            continue
        pct = 100.0 * (np.abs(cur - prev).max(axis=2) > 8).mean()
        diffs.append(pct)
        print("   %s sample %d : %.2f%% de pixels changes" % (label, i, pct))
        prev = cur
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--interval", type=float, default=0.8)
    a = ap.parse_args()

    wins = fo.list_wow_windows()
    if not wins:
        print("pas de fenetre WoW")
        return 2
    hwnd = wins[0][0]

    others = [h for h, t, _p in list_windows() if t and "Warcraft" not in t]
    other = others[0] if others else None

    if other is not None:
        fb._force_foreground(other)
        time.sleep(1.0)
        focused = (ctypes.windll.user32.GetForegroundWindow() == hwnd)
        print("focus WoW pendant la mesure : %s" % focused)
        bg = series(hwnd, a.samples, a.interval, "SANS-FOCUS")

    fb._force_foreground(hwnd)
    time.sleep(1.5)
    print("focus WoW pendant la mesure : %s"
          % (ctypes.windll.user32.GetForegroundWindow() == hwnd))
    fg = series(hwnd, a.samples, a.interval, "AVEC-FOCUS")

    def stat(xs):
        return "n=%d min=%.2f%% median=%.2f%% max=%.2f%%" % (
            len(xs), min(xs), sorted(xs)[len(xs) // 2], max(xs)) if xs else "aucun"

    print("")
    print("SANS focus : %s" % stat(bg))
    print("AVEC focus : %s" % stat(fg))
    if bg and fg and (sorted(bg)[len(bg) // 2] < 0.5 * sorted(fg)[len(fg) // 2]):
        print("VERDICT : frames PERIMEES en arriere-plan (variation ~0 alors que")
        print("          le jeu anime). Le bot lit un ecran fige -> pas de peche")
        print("          fiable en arriere-plan avec PrintWindow seul.")
    return 0


if __name__ == "__main__":
    sys.exit(main())