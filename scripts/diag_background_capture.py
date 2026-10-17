#!/usr/bin/env python3
"""Diagnostic : la capture de la fenetre WoW est-elle fiable EN ARRIERE-PLAN ?

Interroge concrete : est-ce que PrintWindow renvoie une image fraiche quand WoW
n'est pas au premier plan ? C'est la condition pour que le bot peche en
arriere-plan (l'entree, elle, passe par le bail de focus).

    python scripts/diag_background_capture.py --samples 20
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fishing_observer import (capture_window, find_wow_window, is_wow_frame,  # noqa: E402
                              is_ingame_frame)

try:
    from fishing_bot import to_bgr, _ambient_light, detect_bobber_hybrid
except Exception as exc:  # pragma: no cover
    print("import fishing_bot impossible : %s" % exc)
    to_bgr = _ambient_light = detect_bobber_hybrid = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0.7)
    a = ap.parse_args()

    ok = wow = ingame = 0
    stds, confs = [], []
    for i in range(a.samples):
        hwnd, title, rect = find_wow_window()
        if not hwnd:
            print("pas de fenetre WoW")
            return 2
        fg = __import__("ctypes").windll.user32.GetForegroundWindow()
        img, err = capture_window(hwnd)
        focused = (fg == hwnd)
        if img is None:
            print("  %02d focus=%-5s CAPTURE KO: %s" % (i, focused, err))
        else:
            arr = to_bgr(img)
            std = float(arr.std())
            stds.append(std)
            w = is_wow_frame(img)
            g = is_ingame_frame(img)
            ok += 1
            wow += 1 if w else 0
            ingame += 1 if g else 0
            conf = None
            if detect_bobber_hybrid and g:
                try:
                    amb = _ambient_light(arr)
                    bx, by, conf, _ = detect_bobber_hybrid(arr, {}, 0.0, amb, v_floor=80)
                    if conf is not None:
                        confs.append(conf)
                except Exception as exc:
                    conf = "err:%s" % exc
            print("  %02d focus=%-5s std=%6.2f wow=%-5s ingame=%-5s conf=%s"
                  % (i, focused, std, w, g, conf))
        time.sleep(a.interval)

    print("")
    print("captures reussies : %d/%d" % (ok, a.samples))
    print("is_wow_frame      : %d/%d" % (wow, a.samples))
    print("is_ingame_frame   : %d/%d" % (ingame, a.samples))
    if stds:
        print("std image         : min=%.1f median=%.1f max=%.1f"
              % (min(stds), statistics.median(stds), max(stds)))
    if confs:
        print("conf flotteur      : min=%.3f median=%.3f max=%.3f"
              % (min(confs), statistics.median(confs), max(confs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())