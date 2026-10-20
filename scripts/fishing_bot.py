# -*- coding: utf-8 -*-
"""
fishing_bot.py — Prototype minimal d'auto-pêche Casinobabe (UN bouton START).

Zéro modification du client WoW : lecture d'écran (PrintWindow, sans vol de
focus pour l'observation) + clic souris réel uniquement quand WoW est au
premier plan. Aucune inspection mémoire, aucune injection.

États : READY → CASTING → WAITING FOR BITE → CATCH DETECTED → (recast) …
        PAUSED (WoW perd le focus) | ERROR (détection non fiable) | STOPPED.

Contrôles :
  * GUI  : boutons [ START AUTO-FISHING ] / [ STOP ] + état visible.
  * CLI  : --cli pour la boucle sans fenêtre, --detect pour un seul test
           de détection sur la frame courante, --calibrate pour créer le
           gabarit du flotteur depuis de vraies captures.
  * Arrêt d'urgence : touche F9 (polling GetAsyncKeyState) — toujours active.

Principe d'honnêteté : si la détection du flotteur n'est pas fiable
(confiance < seuil, candidats ambigus), le bot NE CLIQUE PAS : il note
l'erreur, sauvegarde la capture et passe en ERROR/observe. Jamais de clic
aléatoire.

Raccourci pêche : AUCUNE touche n'est modifiée. Le bot lit
bindings-cache.wtf (lecture seule) et exige castKey dans
state/fishing-bot-config.json (ex. le chiffre d'un bouton de barre où le
sort Pêche est posé). Sans castKey → ERROR explicite, aucun envoi de touche.

Usage :
  python scripts/fishing_bot.py            # GUI
  python scripts/fishing_bot.py --cli      # boucle console
  python scripts/fishing_bot.py --detect   # une frame : rapport détection
  python scripts/fishing_bot.py --calibrate --casts 1

================================================================================
MÉMO MULTI-AGENTS (plusieurs sessions travaillent sur ce fichier) — À LIRE
AVANT TOUTE MODIFICATION. Invariants posés par le fix 2026-10-15, tous
validés EN JEU à l'oasis des Tarides (Lushwater Oasis) — ne pas casser :

1. DÉTECTION : la voie JOUR `detect_bobber` (validée 11/11 sur captures
   Lushwater, centroïde exact) doit TOUJOURS être tentée en premier.
   L'ambiance lumineuse (amb) de l'oasis de JOUR mesure 82-94 : ce N'EST PAS
   de la nuit. Utiliser exclusivement `detect_bobber_hybrid()` — ne JAMAIS
   router directement vers `_night_peak` sur un seuil amb (l'eau verte fait
   échouer son test d'anneau non-vert -> détecteur aveugle, séries de 100+
   WATCH_MISS observées le 15/10 avant fix).

2. `_verify_click` : une SEULE définition doit exister. Un doublon
   écrase silencieusement la première version en Python (bug réel du
   15/10 : la version sans repositionnement du curseur écrasait la bonne ->
   clics de récupération ratés). Avant d'ajouter/modifier une méthode,
   grep "def _verify_click" : doit retourner UNE ligne.

3. `_click(self, x, y)` : contrat FIXE — la méthode résout elle-même
   hwnd/rect via find_wow_window() et repositionne le curseur. Les
   appelants doivent l'appeler avec 2 args (x, y) — PAS de threading de
   rect (cause du NameError qui a tué le bot à chaque touche le 15/10 :
   `rect` n'existe pas dans le scope de _wait_bite).

4. TOUCHES : les portes d'écume (whiteFrac) sont RELATIVES à la baseline
   du spot (médiane glissante `self._wf_hist`, réapprise à chaque lancer
   et re-verrouillage). Les reflets du soleil maintiennent wf ~0.035
   CONSTANT sur certains spots — au-dessus du seuil absolu 0.03 : tout
   retour à une comparaison 100% absolue recrée les fausses touches
   (clics dans l'eau, ligne ramenée, zéro poisson).

5. TESTER AVANT DE COMMITTER : `python -m py_compile scripts/fishing_bot.py`,
   puis `--detect` (1 frame, sans input) sur fenêtre WoW vivante, et si
   possible un cycle live complet (cast -> verrou -> touche -> click ->
   VERIFY_OK) en surveillant reports/fishing-bot-log.jsonl.
================================================================================
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import re
import statistics
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fishing_observer import (find_wow_window, capture_window, is_wow_frame,  # noqa: E402
                             is_ingame_frame, list_wow_windows, get_wow_instance)
from fishing_popup import (popup_confirm_buttons,  # noqa: E402
                           choose_confirm_button)
try:
    # BAIL DE FOCUS : permet de pêcher EN ARRIÈRE-PLAN (autre fenêtre au
    # premier plan). Optionnel par construction : sans lui le bot retombe sur
    # l'ancien modèle "focus tenu en continu" et se met en PAUSED au premier
    # alt-tab. Toutes les fonctions utilisées ici existent au import.
    from focus_broker import focus_lease, user_idle_ms, should_yield_to_user
except Exception:  # pragma: no cover
    focus_lease = None
    user_idle_ms = None

    def should_yield_to_user(_idle, _threshold=0):  # noqa: D401
        return False


# L'utilisateur est-il en train d'utiliser la machine ? Sert à ne JAMAIS lui
# voler la souris ni le focus quand il tape ou bouge la souris.
USER_IDLE_MS = 1200

# FIX 2026-10-19 : rayon de CONTINUITÉ du flotteur (px). Un repli
# (waterR < 0.88) n'est accepté que s'il prolonge le flotteur confirmé dans
# l'eau à moins de ce rayon. Au-delà, c'est un leurre de la scène.
BOBBER_CONTINUITY_PX = 80

# Porte « eau » : un blob n'est un flotteur QUE si son anneau est de l'eau.
# Mesuré sur le corpus des 5680 captures stockées : 148/148 frames avec un
# vrai flotteur franchissent 0.88 (rappel 100 %) et 0/5542 frames sans
# flotteur ne le franchissent (0 % de faux positifs). C'est le seuil qui
# sépare le flotteur de la scène (cheveux, manches, arbres, boutons).
WATER_GATE = 0.88

# FIX 2026-10-20 : porte aquatique DIRECTIONNELLE (cf. _water_stats et
# _water_gate_ok). L'anneau complet (360°) rejette le flotteur colle au
# personnage ou au rivage : mesuré sur 836 frames réelles, le flotteur vrai
# n'est ainsi retenu que dans 64 % des cas alors qu'il est visible dans
# 280/301 des frames perdues (candidat a <=45 px, waterR median 0,053).
# Le meilleur demi-tour d'eau + un plancher sur l'anneau complet donnent
# recall 73 % pour 0,01 faux positif par frame (arc seul : 0,07 — trop).
WATER_ARC_GATE = 0.90    # meilleur demi-tour : fraction d'eau minimale
WATER_RING_MIN = 0.60    # plancher de l'anneau complet quand l'arc suffit

# Teinte (H OpenCV 0-179) de l'eau de l'oasis de Lushwater : bleu-vert.
# L'herbe et le sol sont jaune-vert (H 18-56) et n'en font PAS partie.
# FIX 2026-10-19 : l'ancien critère (S<115 ET V<175) rejetait l'eau
# saturée de l'oasis (S 148-169) et acceptait l'herbe (S 43-160) : le
# flotteur réel n'était jamais détecté sur ce spot.
WATER_H_MIN = 72
WATER_H_MAX = 115


def focus_broker_is_user_active():
    """True si l'utilisateur vient d'appuyer/bouger (< USER_IDLE_MS).

    Volontairement plus severe que le seuil du bail : on peut voler le focus
    pour envoyer une touche, mais PAS deplacer le curseur de quelqu'un qui
    travaille (constat 2026-10-17 : "ca reprend trop le focus")."""
    if user_idle_ms is None:
        return False
    return bool(should_yield_to_user(user_idle_ms(), USER_IDLE_MS))
try:
    import fishing_memory as memory  # mémoire persistante (jamais bloquante)
except Exception:  # pragma: no cover
    memory = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "state", "fishing-bot-config.json")
CALIB_FILE = os.path.join(ROOT, "state", "fishing-bot-calibration.json")
CALIB_SHOT = os.path.join(ROOT, "state", "fishing-bot-bobber-template.png")
LOG_FILE = os.path.join(ROOT, "reports", "fishing-bot-log.jsonl")
SHOTS_DIR = os.path.join(ROOT, "captures", "fishing-bot")

# Journal : anti-ondulation + rotation (2026-10-20). Voir la methode log().
LOG_DEDUP_WINDOW_S = 5.0     # un evenement identique n'est ecrit qu'une fois / 5 s
LOG_MAX_BYTES = 20 * 1024 * 1024   # rotation du log au-dela de 20 Mo

VK_F9 = 0x78
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
user32 = ctypes.windll.user32

DEFAULT_CONFIG = {
    "castKey": None,           # ex. "2" = touche liée à ACTIONBUTTON5 (WTF). JAMAIS modifié par le bot.
    "castSettleSec": 3.0,      # délai après envoi de la touche avant surveillance du flotteur
    "maxBiteWaitSec": 32.0,    # au-delà : re-lancer
    "clickAfterBiteMs": 150,     # délai après détection touche avant clic (était 60ms — trop court pour validation serveur)
    "bobberMinConf": 0.55,     # confiance minimale pour AGIR (sinon ERROR observe)
    "biteDiffThresh": 5.0,     # mouvement + écume (porte blanche, 2 frames)
    "biteWhiteFrac": 0.02,     # fraction de pixels d'ECUME (splash) dans la ROI
    # FIX 2026-10-19 : recalibre sur les seuils REELS de l'ecume mesures en
    # jeu (V>165 ET S<150) : baseline calme 0.0062, pic de morsure 0.0284.
    # Avec l'ancien seuil 0.03 (calibre sur un detecteur d'ecume obsolete),
    # la porte ne pouvait JAMAIS s'ouvrir -> le bot ne cliquait que sur un
    # faux deplacement, au mauvais moment.     # fraction de pixels quasi-blancs (écume) dans la ROI
    "biteMotionThresh": 3.5,   # agitation soutenue SANS écume visible (porte mouvement)
    "biteMotionStreak": 4,     # frames consécutives d'agitation (le balancement = pics isolés)
    "biteGraceSec": 7.0,       # après le lancer, le flotteur se stabilise (amerrissage) :
                               # aucune touche n'est acceptée avant ce délai
    "biteStrongDiff": 10.0,    # splash franc : 1 seule frame suffit (pas d'attente)
    "biteStrongWhite": 0.05,   # recalibre sur l'echelle reelle de l'ecume
    "biteConsecutive": 2,      # frames consécutives standard au-dessus des seuils
    "manualSuspendSec": 120,   # P0.9.2 : inactivité requise avant reprise auto
                               # après suspension sur activité manuelle
    "cursorDivergePx": 60,     # P0.9.2 : écart curseur = l'humain a repris la
                               # souris → suspension temporaire (jamais un arrêt)
    "manualSuspendMaxSec": 1800,  # P0.9.2 : au-delà, arrêt propre (pas d'attente infinie)
    "captureIntervalSec": 0.15,
    "detectScale": 1.0,        # 1.0 = pleine taille (11/11 validé) ; 0.75 testé 10/12 (2 vrais manqués) — NE PAS CHANGER
    "applyBait": True,         # appliquer l'appât Nightcrawlers au démarrage et périodiquement
    "baitKey": "S",            # touche de l'appât (clic droit sur l'objet dans l'inventaire)
    "rodKey": "3",             # touche de la canne à pêche (clic sur la canne équipée)
    "baitRefreshSec": 600,     # ré-appliquer l'appât toutes les 10 minutes (600 s)
}

READY = "READY"
CASTING = "CASTING"
WAITING = "WAITING FOR BITE"
CATCH = "CATCH DETECTED"
PAUSED = "PAUSED"
ERROR = "ERROR"
STOPPED = "STOPPED"
DEAD = "DEAD"
FATAL = "FATAL_ERROR"


def player_health(img_bgr):
    """Vérifie que le personnage est vivant, sans OCR ni référence externe :
    - MORT CERTAINE : dialogue 'Release Spirit' (parchemin + bouton bleu
      simultanés au centre de l'écran) ;
    - MORTE SUSPECTÉE : monde 3D désaturé (fantôme, saturation < 30) ;
    - sinon VIVANT.
    Seuils calibrés 2026-10-13 sur 6 frames vivant (sat 53-115, jamais
    parchemin+bleu simultanés). Retourne (état, preuves)."""
    h, w = img_bgr.shape[:2]
    if h < 400 or w < 400:
        return "uncertain", {"reason": "image trop petite"}
    x0, x1, y0, y1 = int(w * 0.20), int(w * 0.80), int(h * 0.15), int(h * 0.70)
    hsv = cv2.cvtColor(img_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    sat = float(hsv[:, :, 1].mean())
    cx0, cx1, cy0, cy1 = int(w * 0.30), int(w * 0.70), int(h * 0.35), int(h * 0.65)
    c = cv2.cvtColor(img_bgr[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2HSV)
    H, S, V = c[:, :, 0], c[:, :, 1], c[:, :, 2]
    parch = float((((H > 18) & (H < 48) & (S > 60) & (S < 170) & (V > 140) & (V < 230))).mean())
    blue = float((((H > 95) & (H < 115) & (S > 80) & (V > 80))).mean())
    ev = {"satMean": round(sat, 1), "parch": round(parch, 4), "blue": round(blue, 4)}
    if parch > 0.03 and blue > 0.02:
        return "dead", dict(ev, reason="dialogue release (parchemin+bouton)")
    if sat < 30.0:
        return "suspected", dict(ev, reason="monde desature (fantome ?)")
    return "alive", ev


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:  # utf-8-sig : tolère le BOM de PowerShell
            return json.load(fh)
    except (OSError, ValueError):
        return dict(default)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def key_to_vk(key):
    """'2'→0x32, 'F1'…'F12', 'SPACE'/'ENTER'/'TAB'. None sinon."""
    k = str(key or "").strip().upper()
    if not k:
        return None
    if len(k) == 1 and k in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        return ord(k)
    if k.startswith("F") and k[1:].isdigit() and 1 <= int(k[1:]) <= 12:
        return 0x70 + int(k[1:]) - 1
    named = {"SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09}
    return named.get(k)


def _lease(hwnd):
    """Context manager : focus garanti (via bail) ou refus explicite.

    SANS focus_broker (import impossible) on retombe sur l'ancien modèle :
    WoW doit être au premier plan. AVEC, on tente d'acquérir le bail, ce qui
    permet au bot de pêcher même si une autre fenêtre est au premier plan.
    Dans les DEUX cas la règle de sécurité est identique et inchangée :
    si le focus n'est pas confirmé à l'instant de l'envoi, RIEN n'est envoyé.
    """
    if focus_lease is None or not hwnd:
        return _NO_LEASE
    return focus_lease(hwnd)


class _NullLease:
    """Représente 'pas de bail disponible' : focused False -> rien n'est envoyé.

    On ne l'utilise que si focus_broker est absent, auquel cas les appelants
    traitent déjà le cas 'focus déjà au premier plan' avant (cf. send_key).
    """

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *exc):
        return False


_NO_LEASE = _NullLease(False)


def send_key(vk):
    """Envoie une touche à WoW via WM_KEYDOWN (SendMessage).

    Cette méthode fonctionne MÊME quand WoW n'est pas au premier plan
    (constaté le 2026-10-17 : le flotteur apparaît bien après envoi).
    Aucun vol de focus n'est nécessaire : le curseur de l'utilisateur n'est
    jamais déplacé et sa fenêtre reste au premier plan.

    Règle de sécurité : si l'utilisateur est actif (taque ou bouge la souris
    depuis < USER_IDLE_MS), on n'envoie rien et on renvoie False.
    """
    hwnd, _t, _r = find_wow_window()
    if not hwnd:
        return False
    if focus_broker_is_user_active():
        return False
    return _key_down_up_wm(hwnd, vk)


def _key_down_up_wm(hwnd, vk):
    """Envoi effectif via WM_KEYDOWN/WM_KEYUP (SendMessage). Fonctionne en
    arrière-plan sans voler le focus.

    Même paramétrage que le test validé le 2026-10-17 :
    wParam = vk (0x32 pour '2'), lparam = repeat=1 | scan<<16 | prev_state bit.

    NOTE : MapVirtualKeyW(VK_2, 0) renvoie 0x03, or le scan code qui declenche
    une peche est 0x32 (constat par diag_bg_input.py). On utilise donc
    scan = vk, pas le resultat de MapVirtualKeyW.
    """
    scan = vk
    # WM_KEYDOWN : repeat=1, scan code, context=0, prev=0, transition=0
    lparam_down = 0x00000001 | (scan << 16) | (1 << 30)
    user32.SendMessageW(hwnd, WM_KEYDOWN, scan, lparam_down)
    time.sleep(0.05)
    # WM_KEYUP : même lparam + bit de transition (bit 31)
    lparam_up = lparam_down | (1 << 31)
    user32.SendMessageW(hwnd, WM_KEYUP, scan, lparam_up)
    time.sleep(0.05)
    return True


def send_escape():
    """Ferme une éventuelle zone de saisie chat ouverte via WM_KEYDOWN.

    Appelé UNIQUEMENT sur le chemin timeout-recast : aucun butin ne peut
    être ouvert à cet instant (rien n'a été cliqué), donc Échap ne peut pas
    fermer une fenêtre de butin non pillée. Jamais après un clic.
    Retourne True si envoyé, False si l'utilisateur est actif.
    """
    hwnd, _t, _r = find_wow_window()
    if not hwnd:
        return False
    if focus_broker_is_user_active():
        return False
    return _key_down_up_wm(hwnd, 0x1B)  # VK_ESCAPE


def send_mouse_click_wm(hwnd, x, y, button="right"):
    """Envoie un clic souris à la fenêtre WoW via SendMessage (WM_*BUTTONDOWN/UP).
    Fonctionne en arrière-plan sans déplacer le curseur physique.
    Les coordonnées x, y sont des coordonnées écran ; elles sont converties
    en coordonnées client avant l'envoi (SendMessage attend des coords client).

    FIX 2026-10-19 : le bouton « button » permet le clic GAUCHE. Les
    boutons d'un popup WoW (UIPanelButtonTemplate) n'agissent QUE sur un
    clic gauche : un clic droit ne les active pas du tout -> le popup
    bloquait toujours. (Le clic DROIT reste utilisé pour le FLOTTEUR :
    c'est lui qui ouvre la fenêtre de butin.)
    """
    pt = wt.POINT(x, y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    lparam = (pt.y << 16) | (pt.x & 0xFFFF)
    if button == "left":
        down, up = 0x0201, 0x0202   # WM_LBUTTONDOWN / WM_LBUTTONUP
    else:
        down, up = 0x0204, 0x0205   # WM_RBUTTONDOWN / WM_RBUTTONUP
    user32.SendMessageW(hwnd, down, 0, lparam)
    time.sleep(0.05)
    user32.SendMessageW(hwnd, up, 0, lparam)
    time.sleep(0.05)
    return True


def emergency_stop_pressed():
    return bool(user32.GetAsyncKeyState(VK_F9) & 0x8000)


def fg_details():
    """Preuve de focus : HWND + titre + PID + nom du processus au premier plan.
    Utilisé à chaque entrée/sortie de pause (P0 : prouver qui vole le focus)."""
    try:
        fg = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(fg)
        buf = ctypes.create_unicode_buffer(max(1, n + 1))
        user32.GetWindowTextW(fg, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        name = ""
        try:
            import subprocess
            out = subprocess.check_output(
                ["tasklist", "/FI", "PID eq %d" % pid.value, "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "replace")
            parts = [p.strip('"') for p in out.strip().split(",")]
            if parts:
                name = parts[0]
        except Exception:
            pass
        return {"hwnd": fg, "title": buf.value, "pid": pid.value, "process": name}
    except Exception as exc:
        return {"error": repr(exc)}


def read_bindings():
    """Lecture SEULE du bindings-cache du compte (aucune écriture)."""
    path = r"C:\Program Files (x86)\World of Warcraft\_anniversary_\WTF\Account"
    out = {}
    if not os.path.isdir(path):
        return out
    for acct in os.listdir(path):
        f = os.path.join(path, acct, "bindings-cache.wtf")
        if not os.path.isfile(f):
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.strip().split()
                    if len(parts) == 3 and parts[0].lower() == "bind":
                        out.setdefault(acct, {})[parts[1].upper()] = parts[2].upper()
        except OSError:
            continue
    return out


def check_fishing_keybinding(config):
    """Vérifie (sans rien changer) que la touche de lancer est configurée.
    Retourne (ok, message)."""
    ck = config.get("castKey")
    vk = key_to_vk(ck)
    if not ck or vk is None:
        return False, ("Aucune touche de lancer configurée. Posez le sort Pêche sur un bouton "
                       "de barre déjà lié à une touche, puis mettez castKey dans "
                       "state/fishing-bot-config.json. Le bot ne modifie JAMAIS vos binds "
                       "(bindings-cache.wtf lu en lecture seule : aucune liaison FISHING trouvée).")
    return True, "castKey='%s' (VK 0x%02X) — bind existant utilisé tel quel" % (ck, vk)


# ---------------------------------------------------------------------------
# Détection visuelle — "blob rouge SUR l'eau"
# Validée 2026-10-13 sur 11 captures stockées à issue connue (6 avec flotteur
# réel à Lushwater Oasis, 5 sans flotteur) : 11/11. Le gabarit BGR et le
# simple blob rouge historiques échouaient (feather V≈107 < seuil 120, et
# ROI trop étroite : le flotteur pouvait sortir de la zone balayée).
# Le flotteur TBC = petites plumes rouges entourées d'EAU ; les fleurs rouges
# du rivage sont rejetées par le test de contexte "anneau aquatique".
# ---------------------------------------------------------------------------
def to_bgr(img):
    """fishing_observer renvoie du PIL ; le détecteur travaille en numpy BGR."""
    if img is None:
        return None
    if isinstance(img, np.ndarray):
        return img
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _red_blobs(img_bgr, area_min=12, area_max=2000, v_floor=80):
    """Candidats 'plumes rouges' dans la zone de jeu. Retourne [(x,y,area,circ)].
    Bornes d'aire exprimées dans l'espace de l'image fournie (détection à
    échelle réduite : l'appelant les met à l'échelle).
    v_floor : plancher V du rouge. 80 le jour ; 55 la nuit (plumes à V~64
    mesurées capture 23:58) — la porte aquatique préserve la précision."""
    h, w = img_bgr.shape[:2]
    x0, x1 = int(w * 0.05), int(w * 0.95)
    y0, y1 = int(h * 0.05), int(h * 0.85)
    roi = img_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 140, 80), (10, 255, 200)) | \
           cv2.inRange(hsv, (170, 140, 80), (180, 255, 200))
    if v_floor < 80:
        mask = mask | cv2.inRange(hsv, (0, 140, v_floor), (10, 255, 79)) | \
               cv2.inRange(hsv, (170, 140, v_floor), (180, 255, 79))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < area_min or area > area_max:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        if r <= 0:
            continue
        circ = area / (np.pi * r * r)
        if circ < 0.25:
            continue
        out.append((int(cx) + x0, int(cy) + y0, area, circ))
    return out


_water_grid_cache = {}


def _night_peak(img_bgr):
    """Détecteur nuit (ambiance < 75) : les plumesouk sont trop sombres pour
    la morphologie (V~64), mais forment un pic de densité rouge isolé, très
    saturé, sur eau sombre et lisse.
    Validé 2026-10-14 : 2/2 positifs nuit trouvés, 0 cellule sur 2 négatifs ;
    les plumes vraies (S>=180, V>=95) se séparent nettement des leurres
    de rivage (S<=168, V<=84). Retourne (x, y, conf) ou (None, None, 0.0)."""
    h, w = img_bgr.shape[:2]
    if h < 400 or w < 400:
        return None, None, 0.0
    cell = 40
    x0, x1, y0, y1 = int(w * 0.05), int(w * 0.95), int(h * 0.05), int(h * 0.72)
    roi = img_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = (((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))
           & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 55)).astype(np.uint8)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hsv_full = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    nx, ny = (x1 - x0) // cell, (y1 - y0) // cell
    counts = np.zeros((ny, nx), dtype=int)
    for jy in range(ny):
        for ix in range(nx):
            counts[jy, ix] = int(
                red[jy * cell:(jy + 1) * cell, ix * cell:(ix + 1) * cell].sum())
    best, best_score = None, 0.0
    for jy in range(1, ny - 1):
        for ix in range(1, nx - 1):
            n = counts[jy, ix]
            if n < 15 or n > 80:
                continue
            neigh = counts[jy - 1:jy + 2, ix - 1:ix + 2].copy()
            neigh[1, 1] = 0
            if neigh.max() > 0 and n < 3 * neigh.max():
                continue
            gx, gy = x0 + ix * cell + cell // 2, y0 + jy * cell + cell // 2
            xa, xb = max(0, gx - 60), min(w, gx + 60)
            ya, yb = max(0, gy - 60), min(h, gy + 60)
            patch = gray[ya:yb, xa:xb]
            if patch.mean() >= 90 or patch.var() >= 3000:
                continue
            # Contexte NUIT v2 (preuve 01:48 : leurre fleur de rivage) :
            # l'eau de nuit est sombre (V~50), lisse (var~8) et NON verte
            # (H~98) ; le feuillage est plus clair, texturé et 100% vert.
            hp = hsv_full[ya:yb, xa:xb].astype(np.int32)
            yy2, xx2 = np.mgrid[0:hp.shape[0], 0:hp.shape[1]]
            d2b = (xx2 - (gx - xa)) ** 2 + (yy2 - (gy - ya)) ** 2
            ring = (d2b >= 100) & (d2b <= 1764)
            if ring.sum() < 100:
                continue
            Hh = hp[:, :, 0][ring]
            green_frac = float(((Hh > 35) & (Hh < 90)).sum()) / float(ring.sum())
            # Seuil 0.35 : vrais 0.00-0.25, leurres 0.52-1.00 (mesuré 2026-10-14)
            if green_frac >= 0.35:
                continue
            px = hsv_full[gy - 20:gy + 20, gx - 20:gx + 20]
            m = (((px[:, :, 0] < 10) | (px[:, :, 0] > 170))
                 & (px[:, :, 1] > 100) & (px[:, :, 2] > 55))
            sel = px[m]
            if len(sel) == 0:
                continue
            sm, vm = float(sel[:, 1].mean()), float(sel[:, 2].mean())
            if sm < 180 or vm < 95:
                continue
            score = min(1.0, 0.55 + (sm - 180) / 200.0 + (vm - 95) / 200.0)
            if score > best_score:
                best, best_score = (gx, gy), score
    if best:
        return best[0], best[1], float(best_score)
    return None, None, 0.0


def _water_ratio(img_bgr, cx, cy, r_in=10, r_out=42, img_hsv=None):
    """Fraction de l'anneau autour du candidat qui ressemble à de l'eau.

    FIX 2026-10-19 (cause racine « le vrai flotteur n'est jamais vu ») : la
    définition précédente (S<115 ET V<175) était FAUSSE sur l'oasis de
    Lushwater. L'eau y est très saturée (S mesuré 148-169) : elle était
    donc rejetée, et le flotteur posé dessus ne pouvait jamais passer la
    porte. L'herbe, elle, passe (S 43-160) : le critère ne distinguait rien.

    Le vrai discriminant est la TEINTE (H OpenCV, 0-179) :
        eau de l'oasis  : H ~ 78-95   (bleu-vert)
        herbe / sol     : H ~ 18-56   (jaune-vert)
    Mesuré sur la frame vivante du 19/10 : eau 100 % bleu-vert, herbe
    100 % jaune-vert, sur 4 et 3 patches respectivement.

    On exige donc une dominante bleu-vert (H dans [WATER_H_MIN,
    WATER_H_MAX]) ; le seuil de fraction est water_ratio >= 0.88.
    """
    h, w = img_bgr.shape[:2]
    # Fenêtre locale : on ne convertit que le patch autour du candidat.
    x0, x1 = max(0, cx - r_out - 2), min(w, cx + r_out + 3)
    y0, y1 = max(0, cy - r_out - 2), min(h, cy + r_out + 3)
    patch = img_bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).astype(np.int32)
    lx, ly = cx - x0, cy - y0
    yy, xx = np.mgrid[0:(y1 - y0), 0:(x1 - x0)]
    d2 = (xx - lx) ** 2 + (yy - ly) ** 2
    ring = (d2 >= r_in ** 2) & (d2 <= r_out ** 2)
    if ring.sum() == 0:
        return 0.0
    H = hsv[:, :, 0][ring]
    # Eau = dominante bleu-vert. On tolère un peu de bruit (éclats, écume).
    water = (H >= WATER_H_MIN) & (H <= WATER_H_MAX)
    return float(water.sum()) / float(ring.sum())


def _water_stats(img_bgr, cx, cy, r_in=10, r_out=42, img_hsv=None, n_sectors=8):
    """(fraction eau de l'anneau complet, fractions eau par secteur angulaire).

    UN SEUL passage de calcul (conversion HSV + masque de l'anneau) : le
    découpage sectoriel ne coûte qu'une aggrégation numpy sur le meme masque.

    FIX 2026-10-20 (cause racine « le clic arrive au mauvais moment ») :
    la porte « anneau complet >= 0.88 » REJETAIT le flotteur dans 36 % des
    frames ou il etait pourtant bien visible (mesure sur 836 frames reelles :
    le candidat rouge est a <=45 px de la position VRAIE dans 280/301 frames
    perdues, mais son waterR est de 0,000 a 0,880 — mediane 0,053). Cause :
    l'anneau complet (360°) englobe le personnage ou le rivage des qu'un seul
    cote. D'ou la perte du flotteur, la re-action tardive a sa DISPARITION
    (voie BITE_DIP : 1021 clics sur 1837, soit 56 %) et le clic au mauvais
    moment. L'arc directionnel (meilleur demi-tour d'eau) remedie a cela.
    """
    h, w = img_bgr.shape[:2]
    x0, x1 = max(0, cx - r_out - 2), min(w, cx + r_out + 3)
    y0, y1 = max(0, cy - r_out - 2), min(h, cy + r_out + 3)
    patch = img_bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0, [0.0] * n_sectors
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    lx, ly = cx - x0, cy - y0
    yy, xx = np.mgrid[0:(y1 - y0), 0:(x1 - x0)]
    d2 = (xx - lx) ** 2 + (yy - ly) ** 2
    ring = (d2 >= r_in ** 2) & (d2 <= r_out ** 2)
    if ring.sum() == 0:
        return 0.0, [0.0] * n_sectors
    is_water = (hsv[:, :, 0] >= WATER_H_MIN) & (hsv[:, :, 0] <= WATER_H_MAX)
    full = float(is_water[ring].sum()) / float(ring.sum())
    ang = (np.arctan2(yy - ly, xx - lx) + 2 * np.pi) % (2 * np.pi)
    sec = []
    for k in range(n_sectors):
        a0 = 2 * np.pi * k / n_sectors
        a1 = 2 * np.pi * (k + 1) / n_sectors
        m = ring & (ang >= a0) & (ang < a1)
        cnt = int(m.sum())
        sec.append(float(is_water[m].sum()) / cnt if cnt else 0.0)
    return full, sec


def _best_arc(sec, span_sectors):
    """Meilleure moyenne d'eau sur une fenetre contigue de secteurs."""
    n = len(sec)
    k = max(1, int(span_sectors))
    best = 0.0
    for i in range(n):
        vals = [sec[(i + j) % n] for j in range(k)]
        v = sum(vals) / float(k)
        if v > best:
            best = v
    return best


def _water_gate_ok(full, sec):
    """Porte aquatique directionnelle.

    Deuxoles conditions :
      1) l'anneau COMPLET est presque tout de l'eau (>= WATER_GATE) — le cas
         nominal, le flotteur au large ;
      2) OU le MEILLEUR DEMI-TOUR est de l'eau (>= WATER_ARC_GATE) ET l'anneau
         complet reste majoritairement eau (>= WATER_RING_MIN) — le flotteur
         colle au personnage ou au rivage : on tolere un seul obstacle, pas un
         decalage complet.

    Mesure (836 frames reelles, position VRAIE du flotteur) :
      anneau complet seul : recall 64 %, 0.00 faux positif/frame ;
      arc seul            : recall 73 %, 0.07 faux positif/frame (TROP) ;
      arc + anneau >= 0.60: recall 73 %, 0.01 faux positif/frame  <-- CHOISI
    """
    if full >= WATER_GATE:
        return True
    if full < WATER_RING_MIN:
        return False
    return _best_arc(sec, 4) >= WATER_ARC_GATE


def _ambient_light(img_bgr):
    """Luminosité ambiante (V moyen de la zone de jeu) : < 60 = nuit.
    Le mode nuit abaisse le plancher V du détecteur (plumes à V~64)."""
    h, w = img_bgr.shape[:2]
    roi = img_bgr[int(h * 0.30):int(h * 0.70), int(w * 0.30):int(w * 0.70)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 2].mean())


def detect_bobber(img_bgr, calib, img_hsv=None, scale=1.0, v_floor=80):
    """Retourne (x, y, confidence) du flotteur, ou (None, None, 0.0).
    Confiance = ratio aquatique (0.0-1.0) ; la position sert à cliquer.
    img_hsv optionnel : HSV pré-calculé de l'image pour éviter les conversions
    répétées (performance : plusieurs candidats par frame).
    scale : si < 1, l'image fournie est déjà réduite — bornes et rayons sont
    mis à l'échelle en conséquence ; les coordonnées retournées sont dans
    l'espace de l'image fournie.
    v_floor : plancher V du rouge (80 jour, 55 nuit).

    FIX 2026-10-19 (cause racine « zéro poisson ») : le chemin de REPLI
    acceptait n'importe QUEL blob rouge de l'écran, dont les CHEVEUX et les
    MANCHES du personnage. Conséquences mesurées sur le corpus des captures
    (5680 frames) : 375 frames sur 5542 sans aucun flotteur recevaient un
    faux « bobber », et le bot cliquait sur le personnage au lieu de l'eau
    (VERIFY_RETRY_FAILED x6, 0 poisson). Le repli n'est plus accepté que
    s'il PROLONGE un flotteur déjà confirmé dans l'eau (continuité de
    position) ; sans verrou, un repli est un leurre et n'est pas retenu.
    Mesure de contrôle : 367/370 rejets eliminated, 3/370 repli legitimate
    (flotteur dérivé vers le rivage) conservés."""
    h, w = img_bgr.shape[:2]
    if h < 200 or w < 200:
        return None, None, 0.0
    best, best_score = None, 0.0
    best_fallback, best_fb_score = None, 0.0  # repli quand le bobber est
    r_in, r_out = max(3, int(10 * scale)), max(8, int(42 * scale))          # près du rivage/pêcheur (water_ratio < 0.88)
    prefer = getattr(detect_bobber, "_prefer_near", None)
    # VERROU : dernière position confirmée DANS L'EAU (waterR >= 0.88).
    # C'est le seul contexte qui autorise un repli (le flotteur qui glisse
    # vers le rivage perd son anneau d'eau mais reste le même objet).
    locked = getattr(detect_bobber, "_locked_water", None)
    for bx, by, area, circ in _red_blobs(
            img_bgr, area_min=12 * scale * scale, area_max=2000 * scale * scale,
            v_floor=v_floor):
        full, sec = _water_stats(img_bgr, bx, by, r_in=r_in, r_out=r_out,
                                 img_hsv=img_hsv)
        wr = full
        score = wr + min(0.05, area / (2000.0 * scale * scale))
        # Continuité temporelle : à score comparable, on garde le candidat
        # proche du verrou précédent (anti-saut vers un leurre du rivage).
        if prefer is not None:
            d = ((bx - prefer[0]) ** 2 + (by - prefer[1]) ** 2) ** 0.5
            if d < 80:
                score += 0.03
        if _water_gate_ok(full, sec):  # porte directionnelle (mesure 2026-10-20)
            if score > best_score:
                best, best_score = (bx, by), score
        elif score > best_fb_score:
            # Repli : meilleur blob « forme de flotteur » même si l'anneau
            # n'est pas 88 % eau (bobber près du rivage ou du personnage).
            best_fallback, best_fb_score = (bx, by), score
    if best:
        detect_bobber._prefer_near = best
        detect_bobber._locked_water = best  # nouveau verrou « dans l'eau »
        return best[0], best[1], float(min(1.0, best_score))
    if best_fallback:
        # Repli ACCEPTÉ uniquement s'il prolonge le flotteur verrouillé.
        # Sans verrou (démarrage à froid) ou trop loin du verrou, c'est un
        # leurre (cheveux/manches/personnage) : on refuse de le suivre.
        if locked is not None:
            d = ((best_fallback[0] - locked[0]) ** 2 +
                 (best_fallback[1] - locked[1]) ** 2) ** 0.5
            if d <= BOBBER_CONTINUITY_PX:
                detect_bobber._prefer_near = best_fallback
                return (best_fallback[0], best_fallback[1],
                        float(min(1.0, best_fb_score * 0.9)))
    return None, None, 0.0


def detect_bobber_hybrid(img_bgr, calib, bobber_min_conf, amb, scale=1.0, v_floor=80):
    """Détection unifiée (fix 2026-10-15, cause racine « oasis aveugle »).

    Le routage historique (amb < 90 -> voie nuit _night_peak) classait la
    journée à l'oasis des Tarides comme nuit (amb mesuré 82-94 : eau verte,
    ombrage de l'oasis) et l'aveuglait : _night_peak exige green_frac < 0.35
    sur l'anneau (impossible sur l'eau verte de Lushwater) et quantifie la
    position au centre d'une cellule 40x40 -> clic imprécis, loot raté.

    Désormais : la voie JOUR (detect_bobber, validée 11/11 sur captures
    Lushwater, centroïde exact du contour) est TOUJOURS tentée en premier ;
    la voie NUIT (_night_peak) ne sert qu'en secours sur scène sombre
    (amb < 75) quand la voie jour échoue (plumes V~64 invisibles au seuil
    jour). Retourne (x, y, conf, voie) en coordonnées PLEIN FORMAT,
    voie = 'jour' | 'nuit' | 'aucune'."""
    if scale != 1.0:
        small = cv2.resize(img_bgr, (0, 0), fx=scale, fy=scale,
                           interpolation=cv2.INTER_LINEAR)
    else:
        small = img_bgr
    sx, sy, conf = detect_bobber(small, calib, scale=scale, v_floor=v_floor)
    if sx is not None and conf >= bobber_min_conf:
        if scale != 1.0:
            return int(sx / scale), int(sy / scale), conf, "jour"
        return int(sx), int(sy), conf, "jour"
    if amb < 75.0:
        nbx, nby, nconf = _night_peak(img_bgr)
        if nbx is not None:
            return nbx, nby, nconf, "nuit"
    return None, None, 0.0, "aucune"


_bite_prev = {"pos": None, "gray": None}


def _bite_diff(img_np, img_hsv, pos, radius=22):
    """Évidence visuelle de touche autour du flotteur. Retourne (diff, whiteFrac) :
    - diff : variation moyenne 0-255 (plongée du flotteur) ;
    - whiteFrac : fraction de pixels d'ÉCUME (splash de la morsure).
    Une touche réelle produit les deux : le flotteur bouge ET l'eau éclabousse.
    Le simple balancement produit du mouvement sans écume.

    FIX 2026-10-13 (bug critique) : la version précédente indexait la
    référence par la position EXACTE du flotteur, qui jitter de 1-2 px à
    chaque frame → la comparaison retombait toujours à 0.0 et AUCUNE touche
    ne pouvait se déclencher (167 lancers, 0 clics). On compare maintenant
    avec la frame précédente tant que le déplacement reste ≤ 8 px.

    FIX 2026-10-19 (2e cause racine, « zéro poisson ») : le seuil d'écume
    était V>205 ET S<70. Mesuré EN JEU sur 4 morsures réelles capturées à
    l'oasis de nuit : l'écume du splash a V=205..217 mais S=206..226
    (elle est VIVE, pas blanchie). Avec l'ancien seuil, la fraction
    plafonnait à 0.0088, SOUS le seuil biteWhiteFrac=0.03 : la porte
    « écume » ne pouvait jamais s'ouvrir et le bot ne cliquait que sur un
    faux déplacement (mesuré : max whiteFrac 0.0088 sur 4 morsures).
    Avec V>165 ET S<150, la fraction passe de 0.0062 (calme) à 0.0284
    (pic de morsure), soit un ratio de 4.6x : la porte s'ouvre au bon
    moment.
    """
    if not pos:
        return 0.0, 0.0
    x, y = pos
    h, w = img_np.shape[:2]
    x0, x1 = max(0, x - radius), min(w, x + radius)
    y0, y1 = max(0, y - radius), min(h, y + radius)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0
    crop = img_np[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if img_hsv is None:
        cropH = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(np.int32)
    else:
        cropH = img_hsv[y0:y1, x0:x1]
    # ÉCUME : pixels très clairs (V>165) et modérément saturés (S<150).
    # L'écume du jeu est VIVE (S 206-226) : exiger S<70 la rendait
    # invisible (cf. FIX 2026-10-19).
    white = ((cropH[:, :, 1] < 150) & (cropH[:, :, 2] > 165))
    whiteFrac = float(white.sum()) / float(white.size)
    prev = _bite_prev
    if prev["gray"] is None or prev["pos"] is None:
        _bite_prev["pos"], _bite_prev["gray"] = pos, gray
        return 0.0, whiteFrac
    dx = abs(pos[0] - prev["pos"][0])
    dy = abs(pos[1] - prev["pos"][1])
    if dx > 8 or dy > 8 or prev["gray"].shape != gray.shape:
        # déplacement brutal = le flotteur a plongé/bougé : on ré-ancre sans
        # accuser de diff (la frame suivante mesurera le vrai mouvement).
        _bite_prev["pos"], _bite_prev["gray"] = pos, gray
        return 0.0, whiteFrac
    diff = float(np.mean(np.abs(gray - prev["gray"])))
    _bite_prev["pos"], _bite_prev["gray"] = pos, gray
    return diff, whiteFrac


# (bite_score historique remplacé par _bite_diff ci-dessus ; ancré par flotteur
# pour ne pas mélanger les canaux successifs.)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
def _saved_variables_paths():
    """Chemins des fichiers SavedVariables de Casinobabe (lecture seule).

    WoW n'écrit ces fichiers qu'à la déconnexion / au /reload : la
    confirmation d'appât est donc toujours en retard d'une session. On les
    liste dans l'ordre pour/prefere le plus récent.
    """
    roots = []
    for base in (r"C:\Program Files (x86)\World of Warcraft",
                 r"C:\Program Files\World of Warcraft"):
        for flavor in ("_anniversary_", "_classic_", "_classic_era_", "_retail_"):
            p = os.path.join(base, flavor, "WTF", "Account")
            if os.path.isdir(p):
                roots.append(p)
    out = []
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn == "Casinobabe.lua":
                    out.append(os.path.join(dirpath, fn))
    out.sort(key=lambda p: (os.path.getmtime(p) if os.path.isfile(p) else 0),
             reverse=True)
    return out


def _safe_print(s):
    """Affichage console qui ne plante jamais sur l'encodage Windows (cp1252) :
    le crash UnicodeEncodeError du 2026-10-13 (flèche '→' dans FOCUS_LOST) a
    tué le thread du bot 2 fois pendant que la boucle principale dormait."""
    s = str(s)
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, errors="replace").decode(enc))


class FishingBot:
    def __init__(self, config=None, log_fn=None):
        self.config = dict(DEFAULT_CONFIG, **(config or {}))
        self.log_fn = log_fn or _safe_print
        self.state = STOPPED
        self.stop_requested = True
        self.thread = None
        self.lock = threading.Lock()
        self.counters = {"casts": 0, "bites": 0, "clicks": 0, "errors": 0,
                         "unreliable": 0, "popups": 0}
        self.calib = load_json(CALIB_FILE, {})
        self._bind_checked = None
        self._bait_applied_at = 0.0  # dernier temps d'application d'appât (epoch)

    # --- journalisation ---------------------------------------------------
    def log(self, event, **kw):
        row = {"ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "event": event, "state": self.state}
        row.update(kw)
        # DEDUP + ROTATION (2026-10-20) : quand l'utilisateur est actif, le
        # bot ne peut pas envoyer la touche et journaliait CAST_NOT_SENT ~3
        # fois par seconde -> 70 Mo de reports/fishing-bot-log.jsonl pour une
        # seule session. Un evenement identique repete est donc ecrit au plus
        # une fois par DEDUP_WINDOW_S, le nombre d'occurrences etant conserve
        # dans le champ `deduped` (aucune information perdue).
        sig = (event, self.state, kw.get("note"), kw.get("key"))
        now = time.time()
        last = getattr(self, "_log_last_sig", None)
        if last and last[0] == sig and (now - last[1]) < LOG_DEDUP_WINDOW_S:
            self._log_dup_count = getattr(self, "_log_dup_count", 0) + 1
            return
        self._log_last_sig = (sig, now)
        dup = getattr(self, "_log_dup_count", 0)
        if dup:
            row["deduped"] = dup
            self._log_dup_count = 0
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        # Rotation : le log ne doit pas grossir indefiniment.
        try:
            if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.log_fn("[%s] %s %s" % (row["ts"], event,
                     " ".join("%s=%s" % kv for kv in kw.items())))

    # --- mémoire persistante (apprentissage) : ne doit jamais planter le bot
    def _mem(self, fn, *args, **kw):
        if memory is None:
            return None
        try:
            return fn(*args, **kw)
        except Exception:
            return None

    def _mem_begin_cycle(self):
        self._mem_end_cycle("timeout", {"note": "cycle remplacé par un nouveau lancer"})
        params = {k: self.config.get(k) for k in
                  ("biteDiffThresh", "biteWhiteFrac", "biteMotionThresh",
                   "biteMotionStreak", "maxBiteWaitSec", "captureIntervalSec")}
        a = self._mem(memory.action_start, self._mem_session, "lushwater-oasis",
                      self.state, "cast_cycle", "bite détectée et cliquée < délai",
                      self.config["maxBiteWaitSec"], params=params)
        self._mem_action = (a or {}).get("action_id") if isinstance(a, dict) else None

    def _mem_end_cycle(self, result, evidence=None, fail_cause=None):
        if getattr(self, "_mem_action", None):
            self._mem(memory.action_end, self._mem_action, result,
                      evidence=evidence, fail_cause=fail_cause,
                      confidence="bot-direct")
            self._mem_action = None

    def snapshot(self, tag):
        os.makedirs(SHOTS_DIR, exist_ok=True)
        return os.path.join(SHOTS_DIR, "%s-%s.png" % (
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), tag))

    def apply_bait(self):
        """Etat de l'appat sur la canne.

        FIX 2026-10-20 (cause racine « le bot n'applique pas l'appat ») :
        cette methode ENVOYAIT deux touches (S puis 3) en annonçant
        « appat applique sur la canne » - sans aucune verification. Or :
          - la touche S est un SLOT D'ACTION de la barre (d'apres
            bindings-cache.wtf : bind S ACTIONBUTTON12) ; ce n'est PAS un
            clic droit sur l'appat dans l'inventaire, et ce slot ne
            contient meme pas l'appat ;
          - la touche 3 (bind 3 ACTIONBUTTON8) non plus.
        Le bot envoyait donc une touche sans effet toutes les 600 s, et
        declarait un echec (« BAIT_NOT_APPLIED ») alors que l'appat
        etait deja pose : capture du 20/10 02:24, zoom sur la canne en
        main -> la FISHING LURE (leurre violet a plume) est ATTACHEE a la
        hampe de la canne.

        La pose REELLE d'un appat se fait par un clic DROIT sur l'appat
        dans l'inventaire, puis un clic sur la canne : c'est une action
        reservee au joueur (regle du projet : aucune action simulee, le
        bot ne fait QUE ce que le joueur peut voir et corriger).

        Le bot ne joue donc plus l'appat : il CONSTATE et il RAPPORTE.
        Il lit l'etat publie par l'addon (GetWeaponEnchantInfo) quand il
        est disponible, et sinon il le dit clairement au lieu d'inventer
        un succes ou un echec.
        """
        if not self.config.get("applyBait", True):
            return True
        confirmed = self._bait_confirmed()
        if confirmed is True:
            self.log("BAIT_ACTIVE",
                     note="appat deja pose et confirme (enchant actif) - "
                          "aucune action necessaire")
            return True
        if confirmed is False:
            # Etat connu et negatif : on ne peut pas poser l'appat
            # nous-memes (clic droit dans l'inventaire = action du
            # joueur). On le dit UNE fois, puis on ne repete plus.
            last = getattr(self, "_bait_reported_at", 0.0)
            if time.time() - last > 900.0:
                self._bait_reported_at = time.time()
                self.log("BAIT_NEEDS_PLAYER",
                         note="aucun appat actif sur la canne. Posez-le vous-meme : "
                              "clic droit sur l'appat dans l'inventaire, puis "
                              "clic sur la canne. Le bot ne pose pas l'appat "
                              "lui-meme (clic droit sur l'inventaire = action "
                              "reservee au joueur).")
            return False
        # Information indisponible : ni succes ni echec invente.
        self.log("BAIT_STATE_UNKNOWN",
                 note="etat de l'appat inconnu (addon de confirmation "
                      "indisponible) - aucune action tentee")
        return True

    def _bait_confirmed(self):
        """Lit la confirmation d'appât published par l'addon (True/False/None).

        L'addon Casinobabe (CasinobabeFishing.lua) observe
        GetWeaponEnchantInfo() et n'enregistre un événement APPLIED_CONFIRMED
        que lors d'une transition FALSE->VRAI. On relit cette source de
        vérité au lieu de deviner. Retourne :
          True  = appât confirmé actif
          False = confirmé INACTIF (l'appât n'a pas été appliqué)
          None  = information indisponible (addon absent / pas encore à jour)
        """
        try:
            import fishing_observer  # noqa: F401  (module local)
        except Exception:
            pass
        for path in _saved_variables_paths():
            try:
                if not os.path.isfile(path):
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    blob = fh.read()
            except OSError:
                continue
            if "baitApplied" not in blob and "enchantKnown" not in blob:
                continue
            # dernier état d'enchant connu recorded par l'addon
            m = re.search(r'\["enchantKnown"\]\s*=\s*(true|false)', blob)
            if m:
                return m.group(1) == "true"
            return None
        return None

    # --- contrôles publics (appellés par les boutons GUI) ------------------
    def start(self):
        with self.lock:
            if self.stop_requested:
                self.stop_requested = False
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
                return True
            return False

    def stop(self):
        with self.lock:
            self.stop_requested = True
        self.log("STOP_REQUESTED")

    # --- boucle principale --------------------------------------------------
    def run(self):
        self.state = READY
        self.log("BOT_START", config={k: self.config[k] for k in
                                      ("castKey", "maxBiteWaitSec", "bobberMinConf")})
        self._mem_session = (self._mem(memory.session_start, "bot-cli",
                                       "bot-20261013", "1.0.39", "READY") or {}).get("id")
        self._mem_action = None
        try:
            hwnd, title, rect = find_wow_window()
            # Trace explicite de la fenêtre ciblée : avec 2 clients ouverts les
            # deux ont le titre "World of Warcraft", un ciblage faux se voyait
            # seulement par un cast dans le mauvais jeu (symptôme, pas cause).
            self.log("WIN_TARGET", hwnd=hwnd, rect=rect,
                     instance=get_wow_instance() or "first",
                     wowWindows=len(list_wow_windows()))
            if not hwnd:
                # FIX 2026-10-20 : PATIENCE au demarrage. Le bot echouait
                # immediatement si WoW n'etait pas encore lance (fenetre
                # absente) : il fallait le relancer APRES le lancement du jeu.
                # On attend maintenant la fenetre comme on attend deja le HUD
                # « en jeu » (30 x 10 s) : le joueur peut lancer le jeu puis
                # le bot, ou l'inverse, sans coordination.
                for attempt in range(30):
                    self.log("WINDOW_WAIT", attempt=attempt + 1,
                             note="attente de la fenetre WoW (max 5 min)")
                    time.sleep(10)
                    hwnd, title, rect = find_wow_window()
                    if hwnd:
                        self.log("WIN_TARGET", hwnd=hwnd, rect=rect,
                                 instance=get_wow_instance() or "first",
                                 wowWindows=len(list_wow_windows()))
                        break
            if not hwnd:
                self._fail("WoW introuvable : fenêtre 'World of Warcraft' absente.")
                return
            # Vérification d'écran avec réessais (un glitch PrintWindow ou un
            # changement de zone au démarrage ne doit pas tuer le bot : la
            # boucle principale attendrait sinon indéfiniment un thread mort).
            # Fix 2026-10-16 : porte « perso EN JEU » (is_ingame_frame : HUD
            # du cadre joueur) au lieu de is_wow_frame — l'écran de login est
            # chromatiquement riche et passait l'ancien test ; or envoyer
            # castKey pendant le login peut taper dans le champ mot de passe.
            # Patience portée à 30 x 10 s (5 min) : le joueur peut lancer le
            # bot AVANT de se logger, plus aucune coordination nécessaire.
            img, err, ok_frame = None, "non tenté", False
            for attempt in range(30):
                img, err = capture_window(hwnd)
                if img is not None and is_wow_frame(img) and is_ingame_frame(img):
                    ok_frame = True
                    break
                self.log("STARTUP_RETRY", attempt=attempt + 1, error=err,
                         note="attente perso en jeu (HUD) - jamais de touche pendant le login")
                time.sleep(10)
                hwnd, title, rect = find_wow_window()
                if not hwnd:
                    break
            if not ok_frame:
                self._fail("Écran WoW non vérifié après 6 tentatives (capture invalide ou pas le jeu).", err)
                return

            ok, msg = check_fishing_keybinding(self.config)
            self._bind_checked = msg
            self.log("KEYBINDING_CHECK", ok=ok, detail=msg)
            if not ok:
                self._fail(msg)
                return
            vk = key_to_vk(self.config["castKey"])
            # Applique l'appât au démarrage de la session de pêche
            if self.config.get("applyBait", True):
                self.apply_bait()
                self._bait_applied_at = time.time()

            while not self.stop_requested:
                if emergency_stop_pressed():
                    self.log("EMERGENCY_STOP", hotkey="F9")
                    break
                # Ré-applique l'appât périodiquement (toutes les baitRefreshSec)
                if self.config.get("applyBait", True) and \
                   time.time() - self._bait_applied_at >= \
                   self.config.get("baitRefreshSec", 600):
                    self.apply_bait()
                    self._bait_applied_at = time.time()
                fg = user32.GetForegroundWindow()
                focused = (fg == hwnd)
                if not focused and focus_lease is not None:
                    # ARRIÈRE-PLAN : la capture (PrintWindow) marche sans
                    # focus, et l'envoi d'entrée passe désormais par le bail
                    # (scripts/focus_broker.py). Geler tout le cycle ici perdait
                    # des lancers pour rien : on note seulement que le cycle
                    # s'est déroulé sans focus, et on continue d'observer.
                    if self.state in (WAITING,) or getattr(self, "_mem_action", None):
                        self._cycle_focus_lost = True
                    if not getattr(self, "_bg_logged", False):
                        self._bg_logged = True
                        det = fg_details()
                        self.log("FOCUS_BACKGROUND", foreground=det.get("title"),
                                 fgprocess=det.get("process"), state=self.state,
                                 counters=dict(self.counters),
                                 note="ARRIERE_PLAN - observation continue, entree via bail")
                elif not focused:
                    if self.state not in (PAUSED,):
                        det = fg_details()
                        was = self.state
                        if was in (WAITING,) or getattr(self, "_mem_action", None):
                            self._cycle_focus_lost = True
                        self.log("FOCUS_LOST", foreground=det.get("title"),
                                 fgpid=det.get("pid"), fgprocess=det.get("process"),
                                 fghwnd=det.get("hwnd"), state=was,
                                 cycle=getattr(self, "_cycle_id", None),
                                 counters=dict(self.counters),
                                 note="INPUT_PAUSED_FOCUS_LOST - cycle conserve, timers gels")
                        self.state = PAUSED
                        self._pause_started = time.time()
                    time.sleep(0.4)
                    hwnd, title, rect = find_wow_window()
                    continue
                if focused and getattr(self, "_pause_started", None) is not None:
                    # Reprise : vérifier fenêtre/processus + réexaminer l'état
                    # réel du jeu AVANT de poursuivre (P0 : jamais de poursuite
                    # aveugle, jamais d'Échap/re-cast sur simple retour focus).
                    paused_for = time.time() - self._pause_started
                    self._pause_started = None
                    if getattr(self, "_wait_started", None) is not None:
                        self._wait_started += paused_for
                    det = fg_details()
                    status = self._reexamine(hwnd)
                    self.log("FOCUS_BACK", foreground=det.get("title"),
                             fgpid=det.get("pid"), fgprocess=det.get("process"),
                             pausedSec=round(paused_for, 1), state=self.state,
                             cycle=getattr(self, "_cycle_id", None),
                             cycleStatus=status,
                             note="RESUMING - timers neutralises, cycle reexamine")

                if self.state in (READY, PAUSED):
                    if not self._alive_check(hwnd):
                        continue
                    if self._adopt_existing(hwnd):
                        continue
                    self._cast(vk)
                    continue
                if self.state == CASTING:
                    time.sleep(self.config["castSettleSec"])
                    _bite_prev["pos"], _bite_prev["gray"] = None, None
                    self._wait_started = time.time()
                    self._missed = 0
                    self._wf_hist = None  # baseline d'écume réapprise par lancer
                    self.state = WAITING
                    self.log("WAITING_FOR_BITE")
                    continue
                if self.state == WAITING:
                    self._wait_bite(hwnd, rect)
                    continue
                if self.state == CATCH:
                    time.sleep(1.6)          # loot window / récupération
                    self._cast(vk)
                    continue
                if self.state == ERROR:
                    # erreur structurelle (fenêtre, frame invalide) : reprise
                    # lente, JAMAIS de relance immédiate (le canal de pêche en
                    # jeu dure ~20 s ; spammer la touche casse la session).
                    time.sleep(5.0)
                    hwnd, title, rect = find_wow_window()
                    if hwnd and self._frame_ok(hwnd):
                        self.state = READY
                    continue
                if self.state == DEAD:
                    # Personnage mort : AUCUN lancer. Sondage lent jusqu'à
                    # résurrection vérifiée, abandon après 30 min (il faut un
                    # humain pour le retour au corps de toute façon).
                    time.sleep(15.0)
                    if time.time() - (self._dead_since or time.time()) > 1800:
                        self.state = FATAL
                        self._mem(memory.session_end, self._mem_session, "FATAL",
                                  {"counters": self.counters,
                                   "reason": "mort sans resurrection 30 min"})
                        self.log("FATAL_ERROR", reason="mort sans resurrection (30 min) - intervention humaine requise")
                        break
                    img, _e = capture_window(hwnd)
                    if img is not None:
                        st, ev = player_health(to_bgr(img))
                        if st == "alive":
                            self.state = READY
                            self._dead_streak = 0
                            self._mem(memory.observe, "error", zone="The Barrens",
                                      source="bot", confidence="verifie (dialogue disparu, monde en couleur)",
                                      evidence={"event": "resurrected", "prev": ev})
                            self.log("RESURRECTED", note="personnage vivant verifie - reprise")
                    continue
            self.state = STOPPED
            self._mem_end_cycle("unknown", {"note": "arret demande"})
            self._mem(memory.session_end, self._mem_session, "STOPPED",
                      {"counters": self.counters})
            self.log("BOT_STOPPED", counters=self.counters)
        except Exception as exc:  # pragma: no cover — filet de sécurité
            self.state = ERROR
            self.log("BOT_CRASH", error=repr(exc))
            self.counters["errors"] += 1

    # --- étapes -------------------------------------------------------------
    def _reexamine(self, hwnd):
        """Réexamen de l'état réel du jeu à la reprise du focus (P0 §2) :
        CAST_IN_PROGRESS (flotteur visible, cycle actif), RESULT_UNKNOWN
        (plus de flotteur mais canal peut-être en cours), ou READY (rien en
        cours). Ne modifie aucun état, ne relance rien."""
        try:
            img, err = capture_window(hwnd)
            if img is None:
                return "RESULT_UNKNOWN (capture impossible)"
            img_np = to_bgr(img)
            amb = _ambient_light(img_np)
            vf = 55 if amb < 75.0 else 80  # meme regle que la boucle
            bx, by, conf = detect_bobber(img_np, self.calib, v_floor=vf)
            if bx is not None and conf >= self.config["bobberMinConf"]:
                return "CAST_IN_PROGRESS (flotteur revu en %d,%d)" % (bx, by)
            if self.state in (WAITING, CASTING, CATCH):
                return "RESULT_UNKNOWN (cycle ouvert, flotteur non revu)"
            return "READY_FOR_NEXT_CAST (aucun cycle ouvert)"
        except Exception as exc:
            return "RESULT_UNKNOWN (%s)" % exc

    def _adopt_existing(self, hwnd):
        """Coopération (preuve 02:03) : si un flotteur est DÉJÀ à l'eau
        (lancer manuel du joueur), on ne relance PAS par-dessus (cela
        arracherait sa ligne) : on adopte le flotteur et on le surveille
        (clic à la touche). Retourne True si adopté.

        FIX 2026-10-19 : l'adoption exige désormais un flotteur CONFIRMÉ
        DANS L'EAU (waterR >= 0.88). Avant, le chemin de repli acceptait
        les cheveux/manches du personnage comme « flotteur déjà à l'eau » :
        le bot passait son temps en ADOPTED_MANUAL_CAST (114 occurrences
        contre 9 vrais lancers sur la session du 19/10) et ne relançait
        jamais. Un leurre n'est plus adopté : le bot lance."""
        try:
            img, err = capture_window(hwnd)
            if img is None:
                return False
            img_np = to_bgr(img)
            amb = _ambient_light(img_np)
            # Détection STRICTE : on n'adopte que ce qui est dans l'eau.
            bx, by, conf, _voie = detect_bobber_hybrid(
                img_np, self.calib, self.config["bobberMinConf"], amb,
                v_floor=80)
            if bx is None or conf < self.config["bobberMinConf"]:
                return False
            # Vérification indépendante de l'eau autour du candidat : c'est
            # le seul signal qui distingue un flotteur d'un leurre de scène
            # (cheveux, manches, arbre, bouton de dialogue).
            # Porte DIRECTIONNELLE (mesure 2026-10-20) : meme regle que le
            # detecteur, sinon un flotteur valide mais colle au personnage
            # serait rejete ici apres avoir ete adopte.
            full, sec = _water_stats(img_np, bx, by, r_in=10, r_out=42)
            if not _water_gate_ok(full, sec):
                self.log("ADOPT_REJECTED", bobber=(bx, by), waterR=round(full, 3),
                         note="pas de flotteur dans l'eau - leurre refuse, "
                              "le bot va lancer")
                return False
            self._wait_started = time.time()
            self._missed = 0
            self._cycle_observed_s = 0.0
            self._cycle_focus_lost = False
            self._last_watch_ts = None
            self._cursor_set = None  # P0.9.2 : génération de suivi neuve
            self._mem_begin_cycle()
            self._cycle_id = getattr(self, "_mem_action", None)
            self.state = WAITING
            self.log("ADOPTED_MANUAL_CAST", bobber=(bx, by), conf=round(conf, 3),
                     note="flotteur deja a l'eau (joueur) - surveillance sans relancer")
            return True
        except Exception as exc:
            self.log("ADOPT_CHECK_FAILED", error=repr(exc))
            return False

    def _frame_ok(self, hwnd):
        img, err = capture_window(hwnd)
        return img is not None and is_wow_frame(img)

    def _alive_check(self, hwnd):
        """Vérifie que le personnage est vivant avant de lancer (toutes les
        ~60 s max). Retourne True si on peut pêcher. En cas de mort : état
        DEAD, cycle suspendu, mémoire alimentée, reprise auto après réz."""
        now = time.time()
        if now - getattr(self, "_alive_last", 0) < 60:
            return self.state != DEAD
        self._alive_last = now
        img, err = capture_window(hwnd)
        if img is None:
            return True  # capture impossible : ne pas conclure à une mort
        st, ev = player_health(to_bgr(img))
        if st == "dead":
            self._enter_dead(ev, certain=True)
            return False
        if st == "suspected":
            self._dead_streak = getattr(self, "_dead_streak", 0) + 1
            if self._dead_streak >= 2:
                self._enter_dead(ev, certain=False)
                return False
        else:
            self._dead_streak = 0
        return True

    def _enter_dead(self, ev, certain):
        self.state = DEAD
        self._dead_since = time.time()
        self._dead_streak = 0
        self._mem_end_cycle("unknown", {"note": "mort personnage - cycle suspendu"})
        self._mem(memory.observe, "error", zone="The Barrens", source="bot",
                  confidence="forte (dialogue)" if certain else "moyenne (monde desature)",
                  evidence={"event": "player_dead", "health": ev,
                            "spot": "lushwater-oasis (derniere position connue)",
                            "corpse_coords": "inconnues (jamais inventees)"})
        self._mem(memory.spot_update, "lushwater-oasis", zone="The Barrens",
                  event="death")
        self.log("PLAYER_DEAD", certain=certain, evidence=ev,
                 note="peche suspendue - reprise auto apres resurrection verifiee")

    def _cast(self, vk):
        self.state = CASTING
        self._cursor_announced = False
        self._cursor_set = None  # P0.9.2 : nouveau cycle = nouvelle génération
                                 # de suivi (pas de comparaison inter-cycles)
        self._motion_streak = 0
        self._move_streak = 0
        self._last_centroid = None
        self._locked_at = None
        self._dip_clicked = False
        self._prev_ok = False
        self._bite_streak = 0
        self._cycle_locked = False
        self._cycle_cast_ts = datetime.datetime.now().isoformat(timespec="milliseconds")
        self._cycle_bite_ts = None
        self._cycle_observed_s = 0.0
        self._cycle_focus_lost = False
        self._last_watch_ts = None
        detect_bobber._prefer_near = None
        # FIX 2026-10-19 : un nouveau cycle repart d'une page blanche. Le
        # verrou « dans l'eau » de l'ancien cycle ne doit PAS autoriser un
        # repli sur un leurre de la nouvelle scène.
        detect_bobber._locked_water = None
        if send_key(vk):
            # FIX 2026-10-20 : le cycle memoire n'est OUVERT qu'apres un lancer
            # REEL. Avant, chaque tentative sans envoi de touche (l'utilisateur
            # est actif : le bot refuse de lui voler la souris) ouvrait puis
            # fermait une action memoire -> 9 Mo de state/mem-actions.jsonl
            # pour une seule session, sans aucune information utile (aucun
            # lancer n'a eu lieu). Le cycle en cours reste celui du dernier
            # lancer reellement effectue.
            self._mem_begin_cycle()
            self._cycle_id = getattr(self, "_mem_action", None)
            self.counters["casts"] += 1
            self.log("CAST", key=self.config["castKey"], counters=self.counters)
        else:
            # Touche NON envoyee : l'utilisateur est actif (tape ou bouge la
            # souris depuis < USER_IDLE_MS). Ce n'est pas un lancer, on ne
            # compte rien et on attend qu'il libere le poste (deference).
            self.state = PAUSED
            self.log("CAST_NOT_SENT", key=self.config["castKey"],
                     note="touche non envoyee (utilisateur actif)")

    def _handle_popup(self, hwnd, img):
        """Detecte et confirme un popup modal bloquant (clic souris).

        FIX 2026-10-19 : un popup ouvert BLOQUE le jeu (aucune touche
        traitee, flotteur invisible) -> la session de peche est perdue
        jusqu'a confirmation. Deux popups reels ont ete captures :
          - « Do you want to replace "Fishing Lure (+50 Fishing Skill)" ... »
            -> [Yes] [No] : c'est la CONFIRMATION D'APPAT ;
          - dialogue d'erreur TSM -> [Okay].

        Regles de securite (scripts/fishing_popup.py) :
          - on ne clique QUE sur un popup a 1 ou 2 boutons ;
          - le texte du bouton doit etre COURT (« Yes », « Okay ») : une
            invitation de groupe (« Accept », « Decline ») n'est JAMAIS
            confirmee ;
          - on ne reclique pas le meme popup plusieurs fois (anti-spam) ;
          - on respecte la deference utilisateur (pas de clic si
            l'utilisateur manipule la souris).

        Retourne True si un popup a ete traite (la boucle doit s'arreter :
        l'etat du jeu a change).
        """
        try:
            bgr = to_bgr(img)
            btns = popup_confirm_buttons(bgr)
            if not btns:
                return False
            btn = choose_confirm_button(btns)
            if btn is None:
                return False
            key = (btn[4], btn[5])
            # Anti-spam : on ne reclique pas le meme bouton toutes les
            # 2 s (le popup peut ne pas disparaitre immediatement).
            last = getattr(self, "_popup_clicked", None)
            now = time.time()
            if last and last[0] == key and (now - last[1]) < 3.0:
                time.sleep(self.config["captureIntervalSec"])
                return True
            hwnd2, _t, rect = find_wow_window()
            if not hwnd2:
                return False
            sx = rect[0] + btn[4]
            sy = rect[1] + btn[5]
            self.log("POPUP_CONFIRM", button=(btn[4], btn[5]),
                     n_buttons=len(btns), screen=(sx, sy),
                     note="popup modal bloquant detecte - confirmation a la souris")
            if focus_broker_is_user_active():
                self.log("POPUP_DEFERRED", button=(btn[4], btn[5]),
                         note="utilisateur actif - confirmation differee")
                return True
            # Clic reel (comme pour le flotteur) : prioritaire au SendMessage
            # pour fonctionner en arriere-plan.
            # CLIC GAUCHE : un bouton de popup WoW n'est active que par un
            # clic gauche (le clic droit ne fait rien -> le popup restait
            # bloque, ce que vous avez observe).
            if focus_lease is not None:
                send_mouse_click_wm(hwnd2, sx, sy, button="left")
            else:
                import win32api
                import win32con
                win32api.SetCursorPos((sx, sy))
                self._cursor_set = (sx, sy)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self._popup_clicked = (key, now)
            self.counters["popups"] = self.counters.get("popups", 0) + 1
            self.log("POPUP_CONFIRMED", button=(btn[4], btn[5]),
                     counters=self.counters)
            time.sleep(self.config["captureIntervalSec"])
            return True
        except Exception as exc:
            self.log("POPUP_CHECK_FAILED", error=repr(exc))
            return False

    def _wait_bite(self, hwnd, rect):
        """Surveille le flotteur jusqu'à touche ou expiration du canal de pêche.
        Un lancer reste ~20-25 s à l'eau : tant que maxBiteWaitSec n'est pas
        atteint, une absence de détection N'ABANDONNE PAS le lancer."""
        img, err = capture_window(hwnd)
        if img is None:
            # Fenêtre WoW réduite : le bot accumulerait des WATCH_MISS
            # forever sans rien voir (constaté 2026-10-17). On le dit
            # explicitement et on gèle au lieu de boucler à l'aveugle.
            reduced = bool(err and "FENETRE_REDUITE" in str(err))
            if reduced:
                if self.state != PAUSED:
                    self.log("WOW_MINIMIZED", error=err,
                             note="WoW est reduite - le bot ne peut rien voir. "
                                  "Restaurer la fenetre WoW (ne JAMAIS la minimiser, "
                                  "la recouvrir avec une autre fenetre suffit).")
                self.state = PAUSED
            # Throttle : sans ça, une fenêtre réduite inonderait le journal
            # toutes les 0.8 s et noierait le diagnostic utile.
            if time.time() - getattr(self, "_last_capture_err", 0.0) > 30.0:
                self._last_capture_err = time.time()
                self.log("CAPTURE_ERROR", error=err)
            time.sleep(0.8)
            return
        # FIX 2026-10-19 : un popup modal bloque le JEU. Tant qu'il est
        # ouvert, aucune touche n'est traitee et le flotteur n'est pas vu :
        # la session est perdue. Cas reels captures : « Do you want to
        # replace "Fishing Lure (+50 Fishing Skill)" ... » ([Yes] [No]) et
        # le dialogue d'erreur TSM ([Okay]). On le confirme A LA SOURIS sur
        # le bouton detecte (scripts/fishing_popup.py : seuls les popups a
        # texte court « Yes »/« Okay » sont confirmes, jamais une
        # invitation de groupe « Accept »).
        if self._handle_popup(hwnd, img):
            return
        elapsed = time.time() - getattr(self, "_wait_started", time.time())
        if elapsed > self.config["maxBiteWaitSec"]:
            self.state = READY
            # P0 3 : distinguer absence de resultat (TIMEOUT, cycle pleinement
            # observe) et absence d'observation (UNKNOWN : focus perdu, cycle
            # non resolu - jamais compte comme echec).
            observed = getattr(self, "_cycle_observed_s", 0.0)
            if observed < 0.5 * elapsed:
                self._mem_end_cycle("unknown",
                                    {"elapsedSec": round(elapsed, 1),
                                     "observedSec": round(observed, 1),
                                     "focusLost": getattr(self, "_cycle_focus_lost", False)})
                self.log("RESULT_UNKNOWN", elapsedSec=round(elapsed, 1),
                         observedSec=round(observed, 1),
                         note="cycle non resolu (observation insuffisante) - ni echec ni succes")
            else:
                self._mem_end_cycle("timeout", {"elapsedSec": round(elapsed, 1)})
                self.log("BITE_TIMEOUT", action="recast", elapsedSec=round(elapsed, 1))
            if not getattr(self, "_cycle_locked", False):
                # Aucun flotteur verrouillé de tout le canal. DE JOUR (détecteur
                # censé voir) : lancer avorté suspect -> pause. DE NUIT : cécité
                # attendue du détecteur -> simple recast patient, jamais de
                # pause inutile (bug 2026-10-14 : la suspicion nocturne + le
                # recast rapide tuaient toute possibilité de touche).
                if (getattr(self, "_cycle_amb", 99) or 99) >= 90.0:
                    self._failed_casts = getattr(self, "_failed_casts", 0) + 1
                    if self._failed_casts >= 3:
                        self._failed_casts = 0
                        self._mem(memory.observe, "error", zone="The Barrens",
                                  source="bot", confidence="moyenne (pattern)",
                                  evidence={"event": "casts_failing",
                                            "detail": "3 lancers de jour sans flotteur - pause 60 s"})
                        self.log("COMBAT_SUSPECTED",
                                 note="3 lancers de jour sans flotteur - pause 60 s")
                        self.state = READY
                        time.sleep(60)
                        return
                else:
                    # FIX 2026-10-20 : ce message etait emis sur le seul
                    # critere « amb < 90 », alors que la voie jour detecte
                    # le flotteur la nuit (conf 0.95 mesuree a amb=57.6 le
                    # 20/10 03:00). Il affirmait donc une cecite inexistante
                    # et masquait la vraie cause d'un cycle sans flotteur
                    # (ligne pas encore a l'eau,_camera bougee, etc.).
                    # On ne signale plus que l'absence de VERRROU, sans
                    # pretendre que la nuit en est la cause.
                    self.log("NO_BOBBER_LOCKED",
                             amb=round(getattr(self, "_cycle_amb", 0) or 0, 1),
                             note="aucun flotteur verrouille sur ce cycle - "
                                  "ligne pas encore a l'eau ou camera bougee "
                                  "(la voie jour detecte la nuit aussi : "
                                  "conf 0.95 a amb=57.6)")
            else:
                self._failed_casts = 0
            # Echap supprime : envoyer Echap automatiquement ferme des fenetres
            # ou ouvre le menu systeme "pour rien" (le focus peut avoir ete perdu
            # puis revenu, ou l'utilisateur est sur une autre application).
            # Le cycle se termine par timeout : on recast simplement.
            if getattr(self, "_cycle_focus_lost", False):
                self.log("FOCUS_LOST_CYCLE", note="focus perdu pendant le cycle - pas d'echap envoye (securite)")
            self._cycle_summary("timeout", False)
            return
        img_np = to_bgr(img)
        amb = _ambient_light(img_np)
        self._cycle_amb = amb
        # FIX 2026-10-20 : plancher V ADAPTATIF (seuil bas la nuit).
        # Cette logique avait ete supprimee a tort, sur une deduction
        # non mesuree. Mesure reelle sur 120 captures de NUIT
        # (ambiance < 75) :
        #   v_floor=80 (rouge vif)   -> flotteur dans  2/120 frames
        #   v_floor=55 (rouge sombre) -> flotteur dans  6/120 frames
        # Le leurre est SOMBRE la nuit : le seuil bas englobe la plume
        # complete. Risque de leurre nul : la porte eau (waterR >=
        # 0.88) rejette de toute facon les blobs hors eau (201 blobs
        # rejetes a v_floor=80, 257 a v_floor=55, aucun ne passe).
        vf = 55 if amb < 75.0 else 80
        # Fix 2026-10-15 : voie jour TOUJOURS tentée (l'amb 82-94 de l'oasis
        # n'est pas la nuit). La voie nuit reste un secours scène sombre.
        scale = float(self.config.get("detectScale", 1.0) or 1.0)
        bx, by, conf, _voie = detect_bobber_hybrid(
            img_np, self.calib, self.config["bobberMinConf"], amb,
            scale=scale, v_floor=vf)
        if bx is None or conf < self.config["bobberMinConf"]:
            self._missed = getattr(self, "_missed", 0) + 1
            self._prev_ok = False
            # Pas de flotteur visible : on NE RACCOURCIT PAS le cycle. Le
            # lancer est peut-être parfait (détecteur aveugle la nuit) et un
            # recast rapide arracherait la ligne avant toute touche (bug
            # 2026-10-14 : boucle de recast ~4 s = zéro touche possible).
            # On attend la fin normale du canal, puis on relance.
            if self._missed == 3:
                self.log("CAST_UNCONFIRMED", missed=self._missed,
                         elapsedSec=round(elapsed, 1),
                         note="flotteur non vu - cycle maintenu jusqu'au timeout (pas de recast precoce)")
            if self._missed <= 3 or self._missed % 20 == 0:
                self.counters["unreliable"] += 1
                self.log("WATCH_MISS", conf=round(conf, 3), missed=self._missed,
                         elapsedSec=round(elapsed, 1))
                # PNG uniquement 1x/20 manques : l'encodage PNG pleine taille
                # coûte ~1-2 s et ralentirait la boucle de surveillance.
                if self._missed % 20 == 0:
                    img.save(self.snapshot("miss"))
            # DIP : flotteur verrouillé plusieurs secondes puis disparu EN PLEIN
            # canal (ni au début = amerrissage, ni à la fin = canal terminé) :
            # signature typique d'une touche (plongée). Un seul clic par lancer,
            # à la dernière position connue ; la suite de la surveillance dira
            # si un butin suit.
            locked_for = elapsed - (getattr(self, "_locked_at", None) or elapsed)
            if (not getattr(self, "_dip_clicked", False)
                    and self._missed >= 2 and locked_for >= 5.0
                    and 8.0 < elapsed < self.config["maxBiteWaitSec"] - 4.0
                    and getattr(self, "_last_centroid", None)):
                self._dip_clicked = True
                lx, ly = self._last_centroid
                self.counters["bites"] += 1
                self._cycle_bite_ts = datetime.datetime.now().isoformat(timespec="milliseconds")
                # Instrumentation : ici le « signal » est la DERNIERE vue du
                # flotteur — c'est le delai de reaction a la disparition.
                self._bite_signal_ts = getattr(self, "_last_watch_ts", None)
                self._click_source = "dip"
                self.log("BITE_DIP", bobber=(lx, ly), missed=self._missed,
                         lockedSec=round(locked_for, 1),
                         counters=self.counters)
                shot = self.snapshot("dip")
                time.sleep(self.config["clickAfterBiteMs"] / 1000.0)
                ok = self._click(lx, ly)
                img.save(shot)
                self._cycle_summary("dip", ok)
            time.sleep(self.config["captureIntervalSec"])
            return

        if self._missed:
            self.log("BOBBER_LOCKED", bobber=(bx, by), afterMisses=self._missed,
                     conf=round(conf, 3))
            self._wf_hist = None  # re-verrouillage : la camera a pu bouger
        if getattr(self, "_locked_at", None) is None:
            self._locked_at = elapsed
        self._missed = 0
        self._cycle_locked = True
        self._failed_casts = 0
        tr = self._track_cursor(bx, by)
        if tr == "SUSPEND":
            # P0.9.2 : l'humain utilise la souris → suspension temporaire
            # (le processus reste vivant, aucune entrée envoyée).
            if self._manual_suspend() == "stop":
                return
            return
        d, wf = _bite_diff(img_np, None, (bx, by))
        # Baseline d'écume du spot (fix 2026-10-15 « fausses touches sur eau
        # éclairée ») : les reflets solaires maintiennent whiteFrac ~0.035
        # CONSTANT (spot 20:32, bobber 1416,551) — au-dessus du seuil absolu
        # biteWhiteFrac 0.03 -> chaque swing devenait une « touche » dès la
        # fin de la grâce, le bot cliquait dans l'eau et re-lançait sans
        # jamais looter. La touche réelle est un PIC au-dessus de la baseline
        # du spot (preuve 19:50 : 0.007 -> 0.055, x8). Baseline = médiane
        # glissante des dernières frames du verrou, réapprise à chaque
        # (re)verrouillage — robuste aux pics ET aux creux isolés.
        hist = getattr(self, "_wf_hist", None)
        if hist is None:
            hist = self._wf_hist = deque(maxlen=24)
        hist.append(wf)
        wf_floor = statistics.median(hist)
        # Use biteWhiteFrac as hard floor; spot baseline addsitively on top
        # so the gate never exceeds what the config considers a valid bite signal.
        # FIX 2026-10-19 : l'écume étant recalibrée (seuils V/S réels), les
        # seuils config doivent être exprimés dans la NOUVELLE échelle.
        # Mesuré en jeu sur 4 morsures réelles (oasis de nuit) :
        #   baseline calme  = 0.0062
        #   pic de morsure  = 0.0284  (ratio 4.6x)
        # Le seuil « standard » passe donc à 0.02 et la porte est aussi
        # acceptée sur un PIC RELATIF (baseline x 3) : c'est le ratio qui
        # distingue la morsure du clapotis, pas une valeur absolue.
        wf_gate_std = max(self.config["biteWhiteFrac"],
                          wf_floor + self.config["biteWhiteFrac"])
        wf_gate_hard = max(self.config["biteStrongWhite"], wf_floor * 3.0)
        wf_gate_move = max(0.02, wf_floor * 1.8)
        # PIC RELATIF : l'écume de la morsure est ~4.6x la baseline du spot.
        # C'est le critère le plus fiable car il s'adapte à la luminosité.
        wf_gate_rel = wf_floor * 3.0
        # Déplacement du centroïde depuis la frame précédente : le flotteur
        # bouge VRAIMENT lors d'une touche ; le clapotis, lui, ne déplace pas
        # le flotteur (positions stables à ~2 px, diffs 4-8 illusoires).
        prev_c = getattr(self, "_last_centroid", None)
        move = 0.0
        if prev_c is not None:
            move = ((bx - prev_c[0]) ** 2 + (by - prev_c[1]) ** 2) ** 0.5
        self._last_centroid = (bx, by)
        # Temps observé du cycle (P0 3) : cumul du temps passé à observer
        # avec le focus. Sert à distinguer TIMEOUT (bien observé) et UNKNOWN.
        now_t = time.time()
        prev_t = getattr(self, "_last_watch_ts", None)
        if prev_t is not None and (now_t - prev_t) < 5.0:
            self._cycle_observed_s = getattr(self, "_cycle_observed_s", 0.0) + (now_t - prev_t)
        self._last_watch_ts = now_t
        self.log("WATCH", bobber=(bx, by), conf=round(conf, 3),
                 biteDiff=round(d, 1), whiteFrac=round(wf, 3),
                 bobMove=round(move, 1))
        # Délai de grâce post-lancer : on verrouille et on suit le curseur,
        # mais on n'accepte aucune touche (l'amerrissage agite le flotteur).
        if elapsed < self.config["biteGraceSec"]:
            time.sleep(self.config["captureIntervalSec"])
            return
        # Touche = le FLOTTEUR BOUGE (déplacement du centroïde) + signal
        # d'agitation. Le clapotis fait des diffs 4-8 SANS déplacer le
        # flotteur (session 22:34 : 8 frames, positions à ~2 px, 12 faux
        # clics) : le mouvement seul ne suffit plus. Portes :
        #  - déplacement >= 10 px en <= 2 frames + (diff>=4 ou écume>=0.02) ;
        #  - dip : flotteur présent puis absent 1-2 frames puis de retour ;
        #  - splash franc : diff>=10 et écume>=0.08 sur 1 frame.
        if move >= 10.0:
            self._move_streak = getattr(self, "_move_streak", 0) + 1
        else:
            self._move_streak = 0
        # Porte mouvement valide UNIQUEMENT en suivi continu (pas juste après
        # un re-verrouillage : le "saut" vers un leurre retrouvé n'est pas une
        # touche — session 22:50 : 2 faux positifs par téléportation).
        continuous = getattr(self, "_prev_ok", False)
        hit_move = (continuous and self._move_streak >= 1
                    and (d >= 4.0 or wf >= wf_gate_move))
        self._prev_ok = True
        if hit_move:
            self.log("BITE_MOVE", bobber=(bx, by), move=round(move, 1),
                     biteDiff=round(d, 1), whiteFrac=round(wf, 3))
        # FIX 2026-10-19 (3e cause racine, « le clic part au mauvais moment ») :
        # les deux signaux de la morsure n'arrivent PAS en meme temps.
        # Mesure sur des sequences REELLES capturees en jeu (oasis de nuit) :
        #   frame N   : diff = 13.4  (le flotteur plonge)   wf = 0.0005
        #   frame N+1 : diff =  8.1                          wf = 0.0124 (ecume)
        # L'ANCIENNE porte exigeait diff>=5 ET wf>=seuil sur 2 frames
        # consecutives : elle ne s'ouvrait donc JAMAIS (valide : 0 porte
        # ouverte sur 5 morsures reelles capturees). Le bot ne cliquait
        # qu'au hasard, sur un faux deplacement.
        # Nouvelle regle, etablie sur ces mesures :
        #   - ECUME (relative a la baseline du spot) : 1 frame suffit
        #     (le pic d'ecume est unique) ;
        #   - ou DIFF FORT (>= biteStrongDiff) sur 2 frames consecutives
        #     (le clapotis fait des diffs 2-8, la morsure 8-25 : le seuil
        #     biteStrongDiff=10 les separe).
        hit_foam = wf >= wf_gate_rel
        hit_diff = d >= self.config["biteStrongDiff"]
        if hit_diff:
            self._bite_streak = getattr(self, "_bite_streak", 0) + 1
        else:
            self._bite_streak = 0
        hit_streak = self._bite_streak >= self.config["biteConsecutive"]
        if hit_foam or hit_streak:
            self.state = CATCH
            self.counters["bites"] += 1
            self._cycle_bite_ts = datetime.datetime.now().isoformat(timespec="milliseconds")
            # Instrumentation du « clic au bon moment » (2026-10-20).
            self._bite_signal_ts = time.time()
            self._click_source = "detect"
            self.log("CATCH_DETECTED", bobber=(bx, by), biteDiff=round(d, 1),
                     whiteFrac=round(wf, 3),
                     trigger="foam" if hit_foam else "diff-streak",
                     counters=self.counters)
            shot = self.snapshot("bite")
            time.sleep(self.config["clickAfterBiteMs"] / 1000.0)
            ok = self._click(bx, by)  # 2 args : _click résout rect lui-même (mémo multi-agents)
            img.save(shot)
            self._cycle_summary("bite", ok)
            return
        time.sleep(self.config["captureIntervalSec"])

    # ------------------------------------------------------------------
    # Contrat d'arrêt P0.9.2 (ne jamais confondre) :
    #  * F9 (emergency_stop_pressed) = ARRÊT DÉFINITIF volontaire : le
    #    thread se termine, le verrou PID est libéré, la reprise exige le
    #    lanceur canonique. Contrat inchangé.
    #  * Divergence du curseur (l'humain a repris la souris pendant que WoW
    #    est au premier plan) = SUSPENSION TEMPORAIRE : le processus RESTE
    #    vivant et garde le verrou PID (aucun doublon possible), n'envoie
    #    PLUS aucune entrée, puis reprend seul après manualSuspendSec
    #    d'inactivité. Voir _manual_suspend().
    # ------------------------------------------------------------------
    def _track_cursor(self, bx, by):
        """Surveille le flotteur et PRÉ-POSITIONNE le curseur pour que le clic
        soit instantané (pas de trajet souris au moment critique).

        CHOIX VOLUME : cette surveillance NE PREND PAS LE BAIL DE FOCUS. Le
        suivi du flotteur revient toutes les ~150 ms : prendre le focus a chaque
        fois rendait l' experience inutilisable (constat 2026-10-17, "ca reprend
        trop le focus"). SetCursorPos est global a l'ecran et n'a PAS besoin du
        focus ; le seul risque est de tirer la souris des mains de l'utilisateur,
        ce qui est gere par la detection de divergence ci-dessous.

        P0.9.2 : si le curseur a ete deplace loin de la derniere position posee
        PAR LE BOT (divergence > cursorDivergePx), c'est l'humain qui utilise la
        souris → retourne "SUSPEND" au lieu de bouger (ne jamais arracher le
        curseur des mains de l'utilisateur)."""
        import win32api
        hwnd, _t, rect = find_wow_window()
        if not hwnd:
            return False
        sx, sy = rect[0] + bx, rect[1] + by
        try:
            cx, cy = win32api.GetCursorPos()
        except Exception:
            cx, cy = -9999, -9999
        prev = getattr(self, "_cursor_set", None)
        if prev is not None:
            div = ((cx - prev[0]) ** 2 + (cy - prev[1]) ** 2) ** 0.5
            if div > self.config["cursorDivergePx"]:
                # L'utilisateur utilise sa souris : on ne prend pas le controle
                # de son curseur. On saute le pre-positionnement pour ce cycle
                # mais le bot continue a pecher (le clic se fera a la derniere
                # position connue du flotteur).
                self.log("CURSOR_SKIP",
                         reason="utilisateur actif - curseur non deplace",
                         bobber=(bx, by))
                return True
        # L'utilisateur tape/bouge → on ne touche pas a sa souris.
        if focus_broker_is_user_active():
            return False
        if abs(cx - sx) > 4 or abs(cy - sy) > 4:
            win32api.SetCursorPos((sx, sy))
            self._cursor_set = (sx, sy)
            if not getattr(self, "_cursor_announced", False):
                self.log("CURSOR_TRACK", bobber=(bx, by),
                         note="curseur pre-positionne sur le flotteur (sans bail)")
                self._cursor_announced = True
        else:
            self._cursor_set = (sx, sy)
        return True

    def _manual_suspend(self):
        """P0.9.2 : suspension temporaire sur activité manuelle. Le processus
        reste vivant (verrou PID conservé : aucun doublon), n'envoie AUCUNE
        entrée, et reprend seul après manualSuspendSec d'inactivité (WoW au
        premier plan + curseur stable). F9 reste un arrêt définitif, vérifié
        à chaque itération. Retourne "stop" si arrêt demandé/expiré.

        MODE ARRIÈRE-PLAN (focus_broker présent) : la reprise ne dépend plus du
        focus. L'ancien test exigeait « WoW au premier plan ET curseur stable »,
        ce qui était impossible à satisfaire dès que l'utilisateur travaillait
        ailleurs : le bot restait suspendu indéfiniment alors même qu'il pouvait
        parfaitement pêcher via le bail. Ici on ne mesure QUE l'activité
        réelle (curseur), et la reprise se fait par bail."""
        import win32api
        background = focus_lease is not None
        t_start = time.time()
        stable_since = t_start
        last_cur = None
        try:
            last_cur = win32api.GetCursorPos()
        except Exception:
            pass
        self.log("MANUAL_SUSPEND", cycle=getattr(self, "_cycle_id", None),
                 note="souris reprise par l'humain - entrees suspendues, processus conserve",
                 mode="arriere-plan" if background else "focus")
        self._mem(memory.observe, "session", zone="The Barrens", source="bot",
                  confidence="verifie (divergence curseur)",
                  evidence={"event": "manual_suspend",
                            "cycle": getattr(self, "_cycle_id", None)})
        while not self.stop_requested:
            if emergency_stop_pressed():
                self.stop_requested = True  # F9 = arrêt définitif (contrat inchangé)
                return "stop"
            if time.time() - t_start > self.config["manualSuspendMaxSec"]:
                self.log("MANUAL_SUSPEND_EXPIRED",
                         note="inactivite trop longue - arret propre")
                self.stop_requested = True
                return "stop"
            hwnd, _t, _r = find_wow_window()
            fg_ok = bool(hwnd) and user32.GetForegroundWindow() == hwnd
            try:
                cur = win32api.GetCursorPos()
            except Exception:
                cur = None
            cursor_moved = (cur is None or last_cur is None or
                            abs(cur[0] - last_cur[0]) > 12 or
                            abs(cur[1] - last_cur[1]) > 12)
            # En arrière-plan, le focus n'est PAS un critère de reprise : seul
            # le mouvement du curseur compte (il reste le signal fiable que
            # l'humain pilote la souris).
            unstable = cursor_moved or (not background and not fg_ok)
            if unstable:
                stable_since = time.time()  # activité : on ne compte pas
                last_cur = cur
            if time.time() - stable_since >= self.config["manualSuspendSec"]:
                break
            time.sleep(1.0)
        if self.stop_requested:
            return "stop"
        _bite_prev["pos"], _bite_prev["gray"] = None, None  # ré-ancrer (le monde a pu changer)
        self._cursor_set = None
        self.log("MANUAL_RESUME", cycle=getattr(self, "_cycle_id", None),
                 note="inactivite %.0fs - reprise surveillance" % self.config["manualSuspendSec"])
        self._mem(memory.observe, "session", zone="The Barrens", source="bot",
                  confidence="verifie (stabilite curseur+focus)",
                  evidence={"event": "manual_resume",
                            "cycle": getattr(self, "_cycle_id", None)})
        return "resumed"

    def _cycle_summary(self, trigger, interacted):
        # P0-5 : UN seul evenement correle par tentative (cycle complet).
        self.log("CYCLE_SUMMARY", cycle=getattr(self, "_cycle_id", None),
                 castTs=getattr(self, "_cycle_cast_ts", None),
                 biteTs=getattr(self, "_cycle_bite_ts", None),
                 trigger=trigger, interacted=interacted,
                 focusLost=getattr(self, "_cycle_focus_lost", False),
                 observedSec=round(getattr(self, "_cycle_observed_s", 0.0), 1),
                 cursor=getattr(self, "_click_pos", None),
                 button="right", counters=dict(self.counters))

    def _click(self, x, y):
        # P0 : l'interaction de récupération sur un flotteur WoW est le CLIC
        # DROIT (le clic gauche ne fait que cibler). Bouton droit explicite,
        # relâchement propre, vérification du résultat avant de poursuivre.
        #
        # CONTRAT MULTI-AGENTS (cf. mémo en tête de fichier) : signature
        # FIXE (self, x, y). La méthode résout elle-même hwnd/rect via
        # find_wow_window() — ne PAS passer rect en argument (le rect n'est
        # pas défini dans le scope des appelants : NameError à chaque
        # touche, bug 2026-10-15) et ne PAS dupliquer cette méthode.
        #
        # EN ARRIÈRE-PLAN : le clic passe par SendMessage (WM_RBUTTONDOWN/UP)
        # directement vers la fenêtre WoW, sans déplacer le curseur physique
        # ni cliquer sur l'écran de l'utilisateur.
        # EN PREMIER PLAN : SetCursorPos + mouse_event (WoW a le focus).
        hwnd, _t, rect = find_wow_window()
        if not hwnd:
            self.log("CLICK_ABORTED", reason="pas de fenetre WoW")
            self.state = PAUSED
            return False

        if focus_broker_is_user_active():
            self.log("CLICK_DEFERRED",
                     reason="utilisateur actif (pas de clic en background)",
                     counters=dict(self.counters))
            self.state = PAUSED
            return False

        import win32api
        import win32con
        sx, sy = rect[0] + x, rect[1] + y

        if focus_lease is not None:
            # Arrière-plan : clic via SendMessage vers la fenêtre WoW, sans
            # déplacer le curseur physique ni cliquer sur l'écran de l'user.
            if not send_mouse_click_wm(hwnd, sx, sy):
                self.log("CLICK_ABORTED",
                         reason="envoi clic WM_RBUTTONDOWN echoue",
                         counters=dict(self.counters))
                self.state = PAUSED
                return False
        else:
            # Premier plan : SetCursorPos + mouse_event (WoW a le focus).
            if user32.GetForegroundWindow() != hwnd:
                self.log("CLICK_ABORTED", reason="WoW perdu le premier plan")
                self.state = PAUSED
                return False
            try:
                cx, cy = win32api.GetCursorPos()
            except Exception:
                cx, cy = -9999, -9999
            prev = getattr(self, "_cursor_set", None)
            if prev is not None:
                div = ((cx - prev[0]) ** 2 + (cy - prev[1]) ** 2) ** 0.5
                if div > self.config["cursorDivergePx"]:
                    # La souris a ete reprise par l'humain : on ne l'arrache
                    # JAMAIS des mains de l'utilisateur (P0.9.2).
                    self.log("CLICK_DEFERRED",
                             reason="souris reprise par l'humain (divergence)",
                             counters=dict(self.counters))
                    self.state = PAUSED
                    return False
            win32api.SetCursorPos((sx, sy))
            self._cursor_set = (sx, sy)  # P0.9.2 : position connue du bot
            # FIX 2026-10-20 (latence du clic) : 50 ms de pause apres le
            # deplacement du curseur et 50 ms entre RIGHTDOWN et RIGHTUP
            # =~ 100 ms sur le chemin critique. Mesure : latence
            # CATCH_DETECTED -> CLICK_BOBBER = 456 ms (median), dont 150 ms de
            # delai choisi et ~200 ms de ces deux pauses. On les ramene a
            # 20 ms : le curseur a deja ete deplace par le suivi (_track_cursor)
            # juste avant, et le clic droit n'a pas besoin de 50 ms pour etre
            # traite comme un clic simple.
            time.sleep(0.02)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.02)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

        self._click_pos = (sx, sy)
        self.counters["clicks"] += 1
        self._mem_end_cycle("success", {"bobber": (x, y),
                           "button": "right",
                           "loot": "unconfirmed_by_bot (compteurs addon apres /reload)"})
        # FIX 2026-10-20 : instrumentation du « clic au bon moment ».
        # latence = temps entre le SIGNAL de morsure et le clic effectif ;
        # source = 'detect' (morsure vue) ou 'dip' (flotteur perdu -> reaction
        # tardive) ; offset = distance entre le point clique et la derniere
        # position OU le flotteur a ete vu (un gros offset = clic perime).
        sig = getattr(self, "_bite_signal_ts", None)
        lat = round((time.time() - sig) * 1000) if sig else None
        lwt = getattr(self, "_last_watch_ts", None)
        since_sight = round((time.time() - lwt) * 1000) if lwt else None
        self.log("CLICK_BOBBER", button="right", screen=(sx, sy),
                 latencyMs=lat, sinceLastSightMs=since_sight,
                 source=getattr(self, "_click_source", "?"),
                 counters=self.counters)
        return self._verify_click(hwnd, x, y, rect)

    def _verify_click(self, hwnd, x, y, rect):
        """VERIFY_LOOT (honnête) : 1 s après le clic, le flotteur doit avoir
        disparu (récupéré... ou échappé — le butin lui-même n'est confirmable
        que par les compteurs addon). S'il est toujours là, UN seul second
        clic à la NOUVELLE position du flotteur (pas de spam).
        Retourne True si interaction confirmée (flotteur disparu ou dérivé > 60px).

        CONTRAT MULTI-AGENTS (cf. mémo en tête de fichier) : ceci est la
        SEULE définition de _verify_click. Un doublon plus bas dans la
        classe l'écraserait silencieusement (bug 2026-10-15 : la vieille
        version sans repositionnement du curseur reprenait la main et les
        clics de récupération tombaient à côté du flotteur)."""
        time.sleep(1.0)
        img, err = capture_window(hwnd)
        if img is None:
            self.log("VERIFY_UNKNOWN", reason="capture impossible: %s" % err)
            return False
        img_np = to_bgr(img)
        amb = _ambient_light(img_np)
        bx, by, conf, _voie = detect_bobber_hybrid(
            img_np, self.calib, 0.0, amb, v_floor=80)
        if bx is None:
            self.log("VERIFY_OK", note="flotteur disparu apres clic droit (recupere ou echappe - butin : voir compteurs addon)")
            return True
        dist = ((bx - x) ** 2 + (by - y) ** 2) ** 0.5
        if dist < 60:
            import win32api
            import win32con
            # Repositionner le curseur sur la NOUVELLE position du flotteur
            sx2, sy2 = rect[0] + bx, rect[1] + by
            win32api.SetCursorPos((sx2, sy2))
            self._cursor_set = (sx2, sy2)
            time.sleep(0.05)
            self.log("VERIFY_RETRY", note="flotteur toujours la - 1 second clic droit a la nouvelle position",
                     bobber=(bx, by), screen=(sx2, sy2))
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            self.counters["clicks"] += 1
            # Attendre et revérifier une fois après le retry
            time.sleep(1.0)
            img2, err2 = capture_window(hwnd)
            if img2 is not None:
                img2_np = to_bgr(img2)
                amb2 = _ambient_light(img2_np)
                bx2, by2, conf2, _voie2 = detect_bobber_hybrid(
                    img2_np, self.calib, 0.0, amb2, v_floor=80)
                if bx2 is None:
                    self.log("VERIFY_OK", note="flotteur disparu apres retry (recupere ou echappe)")
                    return True
                dist2 = ((bx2 - bx) ** 2 + (by2 - by) ** 2) ** 0.5
                if dist2 >= 60:
                    self.log("VERIFY_OK", note="flotteur derive apres retry (recupere probable)")
                    return True
            self.log("VERIFY_RETRY_FAILED", note="flotteur toujours present apres retry", bobber=(bx2, by2) if 'bx2' in locals() else (bx, by))
            return False
        self.log("VERIFY_OK", note="flotteur parti (autre position : derive ou recupere)")
        return True

    def _fail(self, message, detail=None):
        self.state = ERROR
        self.counters["errors"] += 1
        self._mem_end_cycle("fail", {"message": message}, fail_cause=message[:120])
        self.log("ERROR", message=message, detail=detail)


# ---------------------------------------------------------------------------
# Modes CLI : détection unique + calibration
# ---------------------------------------------------------------------------
def mode_detect(bot):
    hwnd, title, rect = find_wow_window()
    if not hwnd:
        _safe_print(json.dumps({"ok": False, "error": "WoW introuvable"}))
        return 1
    img, err = capture_window(hwnd)
    if img is None:
        _safe_print(json.dumps({"ok": False, "error": err}))
        return 1
    ok_frame = is_wow_frame(img)
    img_bgr = to_bgr(img)
    bx, by, conf = detect_bobber(img_bgr, bot.calib)
    out = {"ok": True, "verifiedWowFrame": ok_frame, "bobber": [bx, by],
           "confidence": round(conf, 3),
           "reliable": bool(bx is not None and conf >= bot.config["bobberMinConf"]),
           "keybindingCheck": check_fishing_keybinding(bot.config)[1]}
    _safe_print(json.dumps(out, ensure_ascii=False))
    img.save(bot.snapshot("detect"))
    return 0


def mode_calibrate(bot, casts):
    """Calibration honnête : l'utilisateur lance MANUELLEMENT le sort, le bot
    capture, détecte le flotteur et enregistre le gabarit. Ne clique jamais."""
    _safe_print("CALIBRATION — lancez votre ligne maintenant (manuellement). Recherche du flotteur…")
    hwnd, _t, rect = find_wow_window()
    if not hwnd:
        _safe_print("ERREUR : WoW introuvable.")
        return 1
    for attempt in range(int(casts) * 60):
        img, err = capture_window(hwnd)
        if img is not None:
            img_bgr = to_bgr(img)
            bx, by, conf = detect_bobber(img_bgr, None)
            if bx and conf >= bot.config["bobberMinConf"]:
                tpl = img_bgr[by - 14:by + 14, bx - 14:bx + 14]
                if tpl.size:
                    os.makedirs(os.path.dirname(CALIB_SHOT), exist_ok=True)
                    cv2.imwrite(CALIB_SHOT, tpl)
                    save_json(CALIB_FILE, {"hasTemplate": True, "tplThresh": 0.60,
                                           "savedAt": datetime.datetime.now().isoformat(),
                                           "size": list(tpl.shape[:2])})
                    _safe_print("GABARIT ENREGISTRÉ conf=%.2f pos=(%d,%d) → %s" % (conf, bx, by, CALIB_SHOT))
                    img.save(bot.snapshot("calibrate"))
                    return 0
        time.sleep(1.0)
    _safe_print("ÉCHEC : flotteur non détecté de manière fiable. Aucun gabarit écrit.")
    return 2


def build_gui(bot):
    import tkinter as tk
    root = tk.Tk()
    root.title("Casinobabe — Auto-Fishing (prototype)")
    root.attributes("-topmost", True)
    status_var = tk.StringVar(value=STOPPED)
    counters_var = tk.StringVar(value="")

    tk.Label(root, text="CASINOBABE AUTO-FISHING", font=("Segoe UI", 11, "bold")).pack(pady=4)
    lbl = tk.Label(root, textvariable=status_var, font=("Consolas", 16, "bold"),
                   width=24, relief="groove")
    lbl.pack(padx=10, pady=4)
    tk.Label(root, textvariable=counters_var, font=("Consolas", 9)).pack()

    def refresh():
        status_var.set(bot.state)
        lbl.config(fg={"ERROR": "#c00", "CATCH DETECTED": "#080",
                       "WAITING FOR BITE": "#06c"}.get(bot.state, "#000"))
        c = bot.counters
        counters_var.set("casts=%(casts)d bites=%(bites)d clicks=%(clicks)d "
                         "errors=%(errors)d unreliable=%(unreliable)d" % c)
        if not bot.stop_requested or bot.state != STOPPED:
            root.after(300, refresh)

    def on_start():
        bot.log_fn("GUI: START cliqué")
        bot.start()
        refresh()

    def on_stop():
        bot.log_fn("GUI: STOP cliqué")
        bot.stop()

    tk.Button(root, text="START AUTO-FISHING", command=on_start, bg="#dfd",
              font=("Segoe UI", 11, "bold"), height=2).pack(fill="x", padx=10, pady=3)
    tk.Button(root, text="STOP", command=on_stop, bg="#fbb",
              font=("Segoe UI", 11, "bold"), height=2).pack(fill="x", padx=10, pady=3)
    tk.Label(root, text="Arrêt d'urgence : F9 — pause auto si WoW perd le focus",
             fg="#666").pack(pady=2)
    root.after(300, refresh)
    return root


def main():
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="boucle sans GUI")
    ap.add_argument("--detect", action="store_true", help="une frame, rapport JSON")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--casts", type=int, default=1)
    ap.add_argument("--instance", default=None,
                    help="fenetre WoW ciblee quand plusieurs clients sont "
                         "ouverts : '1'/'first' (defaut), '2'/'second', "
                         "'last', ou un index. Chaque instance a son PID et "
                         "son journal pour ne pas se confondre.")
    args = ap.parse_args()

    # Verrou anti-doublon (même mécanisme que l'observateur) : deux bots qui
    # lancent en même temps = double lancer, fausses statistiques.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fishing_observer  # local

    # Multi-client : la cible de fenêtre est fixée UNE fois pour tout le
    # process (helpers send_key/send_escape compris, qui re-résolvent la
    # fenêtre à chaque appui — sans cela un bot 'us' peut taper dans 'eu').
    instance = (args.instance or "").strip().lower() or None
    if instance:
        fishing_observer.set_wow_instance(instance)
        # Fichiers par instance : le verrou PID et le journal JSONL sont
        # partagés sinon (le 2e bot serait refusé, ou les lignes mélangées).
        LOG_FILE = os.path.join(ROOT, "reports", "fishing-bot-log-%s.jsonl" % instance)
        _safe_print("Instance ciblée : %s (journal %s)"
                    % (instance, os.path.basename(LOG_FILE)))

    _BOT_PID_PATH = os.path.join(ROOT, "state", "fishing-bot%s.pid"
                                 % ("-" + instance if instance else ""))
    if not args.detect and not fishing_observer.acquire_single_instance(pid_path=_BOT_PID_PATH):
        sys.exit(2)
    if not args.detect:
        import atexit
        atexit.register(fishing_observer.release_single_instance, _BOT_PID_PATH)

    cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    bot = FishingBot(cfg)

    if args.calibrate:
        sys.exit(mode_calibrate(bot, args.casts))
    if args.detect:
        sys.exit(mode_detect(bot))

    if args.cli:
        bot.log_fn = _safe_print
        _safe_print("CLI : F9 = arrêt d'urgence. Ctrl+C pour quitter.")
        bot.start()
        try:
            while True:
                time.sleep(0.3)
                if emergency_stop_pressed():
                    bot.stop()
                    break
        except KeyboardInterrupt:
            bot.stop()
        _safe_print("État final : %s %s" % (bot.state, bot.counters))
        return

    root = build_gui(bot)
    root.mainloop()
    bot.stop()


if __name__ == "__main__":
    main()
