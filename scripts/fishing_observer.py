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
PID_FILE = os.path.join(ROOT, "state", "fishing-observer.pid")
PREV_FILE = os.path.join(ROOT, "state", "fishing-observer-prev.json")

try:
    import fishing_memory as memory  # mémoire persistante (jamais bloquante)
except Exception:  # pragma: no cover
    memory = None


def _safe_print(s):
    """Ne plante jamais sur l'encodage console Windows (cf. bug bot 2026-10-13)."""
    s = str(s)
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, errors="replace").decode(enc))

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


# Régions (fractions de la fenêtre) où le monde 3D du jeu est normalement
# visible. Cause racine du faux négatif du 2026-10-13 18:18 : une fenêtre UI
# sombre et uniforme (panneau TSM Crafting) recouvrait l'unique région testée
# (droite-milieu), ce jour-là la capture WoW 100 % authentique était rejetée.
# La vérification échantillonne maintenant PLUSIEURS régions : une frame WoW
# réelle est chromatiquement riche dans au moins l'une d'elles ; un onglet
# navigateur sombre uniforme (ou une capture noire) échoue partout.
WOW_REGIONS = [
    (0.02, 0.10, 0.45, 0.60),  # monde 3D centre-gauche
    (0.25, 0.55, 0.75, 0.95),  # monde 3D bas-centre
    (0.55, 0.30, 0.95, 0.60),  # région historique droite-milieu
]
WOW_MIN_COLORS = 20000


def _region_color_count(img, frac):
    w, h = img.size
    crop = img.crop((int(w * frac[0]), int(h * frac[1]),
                     int(w * frac[2]), int(h * frac[3])))
    colors = crop.getcolors(maxcolors=1 << 24)
    # getcolors renvoie None si > maxcolors couleurs distinctes : très riche.
    return len(colors) if colors else (1 << 24)


def is_wow_frame(img):
    """Vrai si au moins une région échantillonnée dépasse le seuil de
    diversité chromatique. Ne dépend pas du focus ni de l'occlusion d'une
    seule zone par une fenêtre d'addon sombre."""
    if img is None or img.size[0] < 64 or img.size[1] < 64:
        return False
    return max(_region_color_count(img, r) for r in WOW_REGIONS) > WOW_MIN_COLORS


# ---------------------------------------------------------------------------
# Détection « perso EN JEU » (HUD) — fix 2026-10-16.
# Cause racine : is_wow_frame laisse passer l'écran de login/char-select
# (artwork très riche en couleurs). Or le bot envoie castKey dès que WoW est
# au premier plan : un « 2 » tapé pendant le login peut atterrir dans le
# champ mot de passe. Signature HUD utilisée : les barres du cadre joueur en
# haut à gauche — vie (vert saturé, mesuré y 0.071-0.080, teinte PIL 80) +
# ressource en dessous (mana bleu teinte PIL 168 / énergie jaune / rage rouge,
# mesuré y 0.084-0.097) — présentes en jeu, absentes du login.
# ---------------------------------------------------------------------------
_HUD_HEALTH = (0.050, 0.069, 0.175, 0.082)   # bande verte du cadre joueur
_HUD_SECOND = (0.050, 0.084, 0.175, 0.098)   # bande ressource sous la vie
_HUD_MIN_FRAC = 0.20                         # barres partiellement vides OK


def _hud_bar_frac(img, frac, hue_lo=None, hue_hi=None):
    """Fraction de pixels saturés et lumineux dans la région fractionnaire.
    Si hue_lo/hue_hi donnés (échelle PIL 0-255), filtre en plus sur la teinte."""
    w, h = img.size
    crop = img.crop((int(w * frac[0]), int(h * frac[1]),
                     int(w * frac[2]), int(h * frac[3])))
    try:
        hsv = crop.convert("HSV")
    except Exception:
        return 0.0
    data = list(hsv.getdata())
    if not data:
        return 0.0
    n = sum(1 for (hh, ss, vv) in data
            if ss > 110 and vv > 90
            and (hue_lo is None or hue_lo <= hh <= hue_hi))
    return n / float(len(data))


def is_ingame_frame(img):
    """Vrai si le HUD du cadre joueur est visible (perso EN JEU, pas login).
    Convertit via PIL (pas de numpy ici) : coût ~1 ms sur les 2 petits crops.
    Vie = vert spécifique ; ressource = n'importe quelle teinte saturée
    (mana bleu / énergie jaune / rage rouge — compatible toutes classes).
    Seuil bas (_HUD_MIN_FRAC) : accepte une vie/ressource partielle."""
    if img is None or img.size[0] < 200 or img.size[1] < 200:
        return False
    # Teinte PIL (H = degré * 255 / 360) : vert ~ 75-100 (mesuré 80).
    health = _hud_bar_frac(img, _HUD_HEALTH, 70, 105)
    resource = _hud_bar_frac(img, _HUD_SECOND)
    return health >= _HUD_MIN_FRAC and resource >= _HUD_MIN_FRAC


def load_spots():
    """Charge le registre. En cas d'erreur retourne None : JAMAIS de registre
    par défaut vide — ce défaut (fallback silencieux) a effacé toutes les
    observations horodatées le 2026-10-13 (~20:04) lors d'une écriture
    concurrente pendant une édition manuelle."""
    try:
        with open(SPOTS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        _safe_print("REGISTRE ILLISIBLE (%s) — fichier conservé tel quel, rien n'est écrasé" % exc)
        return None


def save_spots(data):
    """Écriture atomique (tmp + os.replace) : une écriture interrompue ne
    laisse jamais un fichier tronqué."""
    if data is None:
        return
    os.makedirs(os.path.dirname(SPOTS_FILE), exist_ok=True)
    tmp = SPOTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SPOTS_FILE)


def latest_spot_observation(spots):
    """Dernière observation horodatée du registre (spot identifié, sinon zone).
    Ne prétend jamais qu'un spot précis est connu si seule la zone l'est."""
    best = None
    for name, spot in (spots.get("spots") or {}).items():
        for o in (spot or {}).get("observations") or []:
            ts = o.get("ts")
            if ts and (best is None or ts > best[0]):
                best = (ts, "%s (%s) — %s [%s]"
                        % (name, o.get("zone", "?"), o.get("confidence", ""),
                           o.get("capture", "sans capture")))
    for o in (spots.get("zoneObservations") or []):
        ts = o.get("ts")
        if ts and (best is None or ts > best[0]):
            best = (ts, "%s — spot non identifié (%s) [%s]"
                    % (o.get("zone", "?"), o.get("confidence", ""),
                       o.get("capture", "sans capture")))
    return best[1] if best else "aucune observation vérifiée"


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
        elif img.size[0] < 64 or img.size[1] < 64:
            # Micro-capture inexploitable (fenêtre réduite, DIBits vide) :
            # on ne la persiste PAS (un fichier de 1 Ko polluerait les preuves).
            obs["error"] = "capture trop petite (%dx%d), non persistée" % img.size
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
    obs["observationStatus"] = ("live" if obs["captureVerifiedWow"]
                                else "uncertain" if obs["capture"] else "no-capture")

    # Transitions de connectivité → mémoire (déconnexion/reconnexion).
    # Ne plante jamais l'observation.
    try:
        prev = None
        if os.path.isfile(PREV_FILE):
            with open(PREV_FILE, "r", encoding="utf-8") as fh:
                prev = json.load(fh).get("windowFound")
        if prev is not None and prev != obs["windowFound"] and memory is not None:
            memory.observe("connectivity", source="observer",
                           confidence="verifie (EnumWindows)",
                           evidence={"from": prev, "to": obs["windowFound"],
                                     "ts": obs["ts"]})
            obs["connectivityTransition"] = {"from": prev, "to": obs["windowFound"]}
        with open(PREV_FILE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"windowFound": obs["windowFound"], "ts": obs["ts"]}, fh)
    except Exception:
        pass

    if spots is not None:
        save_spots(spots)
    else:
        obs["error"] = (obs["error"] or "") + " | registre spots illisible — non modifié"
        obs["observationStatus"] = "uncertain"

    dashboard = {
        "schema": 1,
        "updatedAt": obs["ts"],
        "sessionConnected": obs["windowFound"],
        "sessionNote": "processus WoW détecté via EnumWindows (lecture seule, sans focus)",
        "capture": obs["capture"],
        "captureStatus": "OK, vérifiée frame WoW" if obs["captureVerifiedWow"]
                         else ("ERREUR: %s" % obs["error"] if obs["error"] else "capturée, vérification humaine requise"),
        "observationStatus": obs["observationStatus"],
        "spotObserved": latest_spot_observation(spots) if spots else "registre indisponible (conservé sur disque, aucune écriture destructive)",
        "lastObservation": obs["capture"],
        "confirmedCatches": "voir barre in-game (Prises: N) — non inventé ici",
        "deviatesRecorded": "voir barre in-game (Cibles: N) — non inventé ici",
        "lastError": last_live_error(),
        "spots": spots["spots"] if spots else {},
        "limitations": [
            "La touche (bite) n'est pas détectable par l'API des addons.",
            "Les compteurs exacts ne sont lus qu'après /logout ou /reload (SavedVariables).",
            "Aucune entrée clavier/souris n'est envoyée au jeu par cet observateur.",
        ],
    }
    try:
        if memory is not None:
            dashboard["learning"] = memory.report()
            dashboard["learning"]["observer_health"] = {
                "pid": os.getpid(), "ts": obs["ts"],
                "capture_ok": obs["captureVerifiedWow"],
            }
    except Exception:
        pass
    os.makedirs(REPORTS, exist_ok=True)
    with open(DASHBOARD, "w", encoding="utf-8", newline="\n") as fh:
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


def _pid_running(pid):
    """Vrai si un processus python avec ce PID existe (lecture seule, sans wmic :
    WMIC n'est pas installé sur cette machine — cause racine d'un faux
    négatif du verrou anti-doublon)."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = wintypes.DWORD(512)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return "python" in os.path.basename(buf.value).lower()
        return True  # processus existe mais nom illisible : prudent = "çà tourne"
    finally:
        k32.CloseHandle(h)


def acquire_single_instance(pid_path=None):
    """Garde anti-doublon pour le mode --loop : refuse de démarrer si un
    observateur est déjà vivant (fichier PID + vérification du processus).
    pid_path optionnel : permet à un autre programme (ex. fishing_bot) de
    réutiliser le même mécanisme avec son propre fichier PID."""
    pid_file = pid_path or PID_FILE
    if os.path.isfile(pid_file):
        try:
            old_pid = int(open(pid_file, "r", encoding="utf-8").read().strip())
        except (OSError, ValueError):
            old_pid = None
        if old_pid and old_pid != os.getpid() and _pid_running(old_pid):
            _safe_print("ERREUR: une instance est déjà en cours (PID %d). "
                        "Supprimez %s si c'est un résidu." % (old_pid, pid_file))
            return False
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def release_single_instance(pid_path=None):
    try:
        pid_file = pid_path or PID_FILE
        if os.path.isfile(pid_file):
            os.remove(pid_file)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="intervalle en secondes")
    args = ap.parse_args()
    if args.loop > 0:
        if not acquire_single_instance():
            sys.exit(2)
        try:
            while True:
                dash, obs = observe_once()
                _safe_print("[%s] capture=%s verified=%s error=%s" % (
                    obs["ts"], obs["capture"] or "-", obs["captureVerifiedWow"], obs["error"]))
                time.sleep(args.loop)
        finally:
            release_single_instance()
    else:
        dash, obs = observe_once()
        _safe_print(json.dumps(obs, indent=2, ensure_ascii=False))
        sys.exit(0 if obs["captureVerifiedWow"] else 1)


if __name__ == "__main__":
    main()
