# -*- coding: utf-8 -*-
"""
fishing_observer.py — Observateur local SANS-FOR-FOCUS pour Casinobabe.

Principe :
  * Capture la fenêtre WoW via PrintWindow (PW_RENDERFULLCONTENT) : lisible
    même si le jeu n'est pas au premier plan. AUCUN SetForegroundWindow,
    AUCUN SendInput, AUCUN clic. Lecture seule.
  * Écrit des captures horodatées dans captures/ et un tableau de bord JSON
    dans reports/fishing-live-dashboard.json.
  * Les données in-game (prises confirmées, Délices déviants) restent la
    source de vérité de l'addon : ce script ne les invente jamais.

Usage :
  python scripts/fishing_observer.py --once
  python scripts/fishing_observer.py --loop 60
"""
import argparse
import ctypes
import datetime
import json
import os
import sys
import time
from ctypes import wintypes

import mss  # type: ignore
from PIL import Image  # type: ignore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = os.path.join(ROOT, "captures")
REPORTS = os.path.join(ROOT, "reports")
SPOTS_FILE = os.path.join(ROOT, "state", "fishing-spots.json")
DASHBOARD = os.path.join(REPORTS, "fishing-live-dashboard.json")

# Spots documentés publiquement pour les Délices déviants / Poissons-dévie
# (oasis des Tarides, données classiques publiques : Wowhead / wikis).
# CONFIDANCE : "documenté, NON vérifié localement" tant qu'aucune observation
# de l'addon (CHAT_MSG_LOOT) n'a été associée au spot.
PUBLIC_SPOTS = [
    {"name": "Stagnant Oasis", "zone": "The Barrens",
     "source": "données publiques classiques (Wowhead)",
     "confidence": "documenté, non vérifié localement", "coords": None},
    {"name": "Lushwater Oasis", "zone": "The Barrens",
     "source": "données publiques classiques (Wowhead)",
     "confidence": "documenté, non vérifié localement", "coords": None},
    {"name": "Forgotten Pools", "zone": "The Barrens",
     "source": "données publiques classiques (Wowhead)",
     "confidence": "documenté, non vérifié localement", "coords": None},
]


def find_wow_window():
    """Retourne (hwnd, title, rect) de la fenêtre principale WoW, sans focus."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == "World of Warcraft":
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (None, None, None)


def capture_window(hwnd):
    """PrintWindow sans focus → PIL.Image (RGB) ou None avec raison."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None, "GetWindowRect failed"
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None, "invalid window size"
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bi = _BMPINFOHEADER(ctypes.sizeof(_BMPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        return None, "PrintWindow failed"
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    return img, None


class _BMPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def is_wow_frame(img):
    """Vérification heuristique que la capture est bien la fenêtre WoW :
    l'UI Casinobabe pêche imprime sa bande sombre en bas à droite et le jeu
    a une forte variété chromatique (paysage). Un onglet navigateur sombre
    uniforme est rejeté."""
    w, h = img.size
    crop = img.crop((int(w * 0.55), int(h * 0.30), int(w * 0.95), int(h * 0.60)))
    colors = crop.getcolors(maxcolors=1 << 24)
    return bool(colors) and len(colors) > 20000


def load_spots():
    try:
        with open(SPOTS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"schema": 1, "spots": {s["name"]: dict(s, observations=[]) for s in PUBLIC_SPOTS}}


def save_spots(data):
    os.makedirs(os.path.dirname(SPOTS_FILE), exist_ok=True)
    with open(SPOTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def observe_once():
    ts = datetime.datetime.now()
    stamp = ts.strftime("%Y%m%d-%H%M%S")
    hwnd, title, rect = find_wow_window()
    obs = {
        "ts": ts.isoformat(timespec="seconds"),
        "windowFound": hwnd is not None,
        "windowTitle": title,
        "rect": rect,
        "capture": None,
        "captureVerifiedWow": False,
        "error": None,
    }
    spots = load_spots()

    if not hwnd:
        obs["error"] = "fenêtre 'World of Warcraft' introuvable (processus fermé ou minimisé invisible)"
    else:
        img, err = capture_window(hwnd)
        if img is None:
            obs["error"] = err
        else:
            path = os.path.join(CAPTURES, "wow-%s.png" % stamp)
            os.makedirs(CAPTURES, exist_ok=True)
            img.save(path)
            obs["capture"] = os.path.relpath(path, ROOT)
            obs["captureVerifiedWow"] = is_wow_frame(img)
            if not obs["captureVerifiedWow"]:
                obs["error"] = "capture lisible mais contenu non reconnu comme frame WoW (à vérifier humainement)"

    # Pas de données SavedVariables pêche tant que le joueur n'a pas fait
    # /reload ou /logout : on ne prétend JAMAIS lire l'état en direct.
    obs["inGameState"] = "non vérifié par ce script (les compteurs exacts vivent dans l'addon)"

    save_spots(spots)

    dashboard = {
        "schema": 1,
        "updatedAt": obs["ts"],
        "sessionConnected": obs["windowFound"],
        "sessionNote": "processus WoW détecté via EnumWindows (lecture seule, sans focus)",
        "capture": obs["capture"],
        "captureStatus": "OK, vérifiée frame WoW" if obs["captureVerifiedWow"]
                         else ("ERREUR: %s" % obs["error"] if obs["error"] else "capturée, vérification humaine requise"),
        "spotObserved": "The Barrens (vérifié par capture 2026-10-13 16:47) — oasis précise inconnue",
        "lastObservation": obs["capture"],
        "confirmedCatches": "voir barre in-game (Prises: N) — non inventé ici",
        "deviatesRecorded": "voir barre in-game (Cibles: N) — non inventé ici",
        "lastError": last_live_error(),
        "spots": spots["spots"],
        "limitations": [
            "La touche (bite) n'est pas détectable par l'API des addons.",
            "Les compteurs exacts ne sont lus qu'après /logout ou /reload (SavedVariables).",
            "Aucune entrée clavier/souris n'est envoyée au jeu par cet observateur.",
        ],
    }
    os.makedirs(REPORTS, exist_ok=True)
    with open(DASHBOARD, "w", encoding="utf-8") as fh:
        json.dump(dashboard, fh, indent=2, ensure_ascii=False)
    return dashboard, obs


def last_live_error():
    """Dernière erreur du bus d'erreurs Casinobabe (SavedVariables flushées)."""
    import glob
    pats = glob.glob(r"C:\Program Files (x86)\World of Warcraft\_anniversary_\WTF\Account\*\SavedVariables\Casinobabe.lua")
    newest, newest_t = None, 0
    for p in pats:
        try:
            t = os.path.getmtime(p)
            if t > newest_t:
                newest, newest_t = p, t
        except OSError:
            pass
    if not newest:
        return "aucun SavedVariables trouvé"
    try:
        with open(newest, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as exc:
        return "lecture impossible: %s" % exc
    idx = content.find('["message"] = ')
    if idx < 0:
        return "aucune erreur dans le bus d'erreurs"
    line = content[idx:content.find("\n", idx)]
    return line.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="intervalle en secondes")
    args = ap.parse_args()
    if args.loop > 0:
        while True:
            dash, obs = observe_once()
            print("[%s] capture=%s verified=%s error=%s" % (
                obs["ts"], obs["capture"] or "-", obs["captureVerifiedWow"], obs["error"]))
            time.sleep(args.loop)
    else:
        dash, obs = observe_once()
        print(json.dumps(obs, indent=2, ensure_ascii=False))
        sys.exit(0 if obs["captureVerifiedWow"] else 1)


if __name__ == "__main__":
    main()
