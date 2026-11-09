"""Mesure des touches FLECHES sur le client (source de verite).

Pour chaque fleche : maintien 2,5 s, puis on mesure
  * le deplacement de la vue 3D (le personnage a bouge ?) ;
  * la variation du cap lu sur la minicarte (la camera a tourne ?).

Un fantome qui ne bouge pas sur les fleches ne sert a rien : c'est
pour cela qu'on mesure avant de coder.
"""
import ctypes
import math
import os
import sys
import time

sys.path.insert(0, r"C:\factory\worktrees\casinobabe-v2\scripts")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import fishing_observer  # noqa: E402
from fishing_client import WowActions  # noqa: E402
from fishing_minimap import MinimapGeometry, player_facing  # noqa: E402
from focus_broker import focus_lease  # noqa: E402

u = ctypes.windll.user32
VK = {"HAUT": 0x26, "BAS": 0x28, "GAUCHE": 0x25, "DROITE": 0x27,
      "Z": 0x5A, "ESPACE": 0x20}


def main():
    h, _t, _r = fishing_observer.find_wow_window()
    if h and u.IsIconic(h):
        u.ShowWindow(h, 9)
        time.sleep(2.0)
        h, _t, _r = fishing_observer.find_wow_window()
    acts = WowActions()

    def cap():
        im = fishing_observer.capture_window(h)
        if isinstance(im, tuple):
            im = im[0]
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)

    def head():
        a = cap()
        geo = MinimapGeometry()
        geo.detect(a)
        if geo.cx is None:
            return None
        rad = player_facing(geo.crop(a))
        return None if rad is None else math.degrees(rad)

    def shift(a, b):
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())

    print("  touche |  deplacement vue3D |  variation du cap", flush=True)
    for name, vk in VK.items():
        a = cap()
        h0 = head()
        with focus_lease(h, respect_user=False, log=lambda *a, **k: None):
            u.SendMessageW(h, 0x0100, vk, 0x00000001 | (vk << 16) | (1 << 30))
            time.sleep(2.5)
            u.SendMessageW(h, 0x0101, vk, 0x00000001 | (vk << 16) | (1 << 31))
        time.sleep(0.8)
        b = cap()
        h1 = head()
        d = None
        if h0 is not None and h1 is not None:
            d = (h1 - h0 + 180) % 360 - 180
        print("   %-7s | %10.2f        | %s" % (name, shift(a, b),
                                              "?" if d is None else "%.1f deg" % d),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
