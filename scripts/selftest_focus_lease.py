#!/usr/bin/env python3
"""Test du bail de focus REEL (necessite une fenetre WoW ouverte).

Mesure ce qui compte pour l'utilisateur : combien de temps le bot garde le
premier plan, et est-ce que la fenetre de l'utilisateur est rendue ensuite.

    python scripts/selftest_focus_lease.py
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import focus_broker as fb  # noqa: E402
import fishing_observer as fo  # noqa: E402
from screen_capture import list_windows  # noqa: E402

u = ctypes.windll.user32


def main():
    wins = fo.list_wow_windows()
    if not wins:
        print("pas de fenetre WoW : ouvre le jeu avant de lancer ce test")
        return 2
    hwnd = wins[0][0]
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)
        time.sleep(1.5)

    others = [h for h, t, _p in list_windows() if t and "Warcraft" not in t]
    if not others:
        print("pas de fenetre tierce pour tester la restitution")
        return 2
    other = others[0]

    print("1) je place une autre fenetre au premier plan")
    fb._force_foreground(other)
    time.sleep(1.0)
    print("   premier plan = autre fenetre : %s"
          % (u.GetForegroundWindow() == other))

    print("2) le bot prend le focus pour une action")
    t0 = time.time()
    with fb.focus_lease(hwnd, log=lambda e, **k: None) as got:
        got_focus = got
        focus_ok = (u.GetForegroundWindow() == hwnd)
        held_ms = (time.time() - t0) * 1000
    print("   focus obtenu : %s / WoW au 1er plan : %s" % (got_focus, focus_ok))
    print("   duree de pret (avec repos) : %.0f ms" % held_ms)

    print("3) restitution : l'utilisateur retrouve SA fenetre")
    restored = (u.GetForegroundWindow() == other)
    print("   fenetre utilisateur restauree : %s" % restored)
    print("   duree totale du pret : %.0f ms" % fb.last_dwell_ms["value"])

    print("4) deference : si l'utilisateur est actif, le bot ne prend rien")
    # On ne peut PAS simuler l'activite reelle avec SetCursorPos : un
    # deplacement de curseur genere n'est PAS de l'input pour Windows et
    # GetLastInputInfo n'bouge pas (verifie : le test echouait). On force donc
    # l'idle a 0, ce qui equivaut a "l'utilisateur tape en ce moment".
    real_idle = fb.user_idle_ms
    fb.user_idle_ms = lambda: 0
    try:
        t0 = time.time()
        with fb.focus_lease(hwnd) as got:
            pass
        took = (time.time() - t0) * 1000
    finally:
        fb.user_idle_ms = real_idle
    print("   focus pris malgre l'activite : %s (%.0f ms)" % (got, took))
    print("   attendu : False (l'utilisateur a la main)")

    ok = got_focus and focus_ok and restored and not got
    print("")
    print("RESULTAT : %s" % ("OK - pret court, fenetre rendue, deference honoree"
                              if ok else "ECHEC a un point"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())