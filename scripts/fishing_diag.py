#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_diag.py — LECTURE du diagnostic NATIF du corps (addon).

Le module d'addon `CasinobabeDiag.lua` interroge les API natives du
client (C_Map, C_DeathInfo, UnitPosition, RetrieveCorpse, sort "Retrieve
Corpse") et publie le resultat par TROIS canaux :

  A. CHAT            -> ligne "[DiagCorps] ghost=... | mapID=... | ..."
  B. SavedVariables  -> CasinobabeDB.corpseDiag (flush a la deconnexion)
  C. PIXELS          -> grille de carres encodes en ASCII (lecture
                         deterministe par capture, sans OCR)

Ce module sait lire les trois, ce qui rend le diagnostic utilisable meme
si un seul canal est disponible.
"""

import os
import re
import time

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

#: Prefixe de la ligne de chat produite par l'addon
CHAT_PREFIX = "[DiagCorps]"

#: Marqueur de synchronisation de la bande de pixels (violet)
SYNC_RGB = (160, 0, 200)

#: Champs attendus dans le payload
EXPECTED = ("mapID=", "corpseMap=", "playerMap=", "worldPos=")


def _bgr(img):
    if img is None:
        return None
    if isinstance(img, np.ndarray):
        arr = img
    else:
        try:
            arr = np.asarray(img)
        except Exception:
            return None
    if arr.size == 0:
        return None
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    return arr


# --------------------------------------------------------------------------
# A. lecture du chat (capture d'ecran + OCR leger des chiffres / mots)
# --------------------------------------------------------------------------
def read_chat_payload(img, region=(0.0, 0.42, 0.60, 0.95)):
    """Cherche dans la zone de chat une ligne '[DiagCorps]'.

    Renvoie (texte|None, bbox|None).  Le texte est lu par decoupage de
    "mots" (groupes de pixels clairs sur fond sombre) : on ne fait pas
    d'OCR, on detecte la presence de la ligne et on la rend lisible a
    l'operateur.  Pour une lecture AUTOMATIQUE des valeurs, le canal
    SavedVariables ou la bande de pixels sont preferez.
    """
    bgr = _bgr(img)
    if bgr is None:
        return None, None
    h, w = bgr.shape[:2]
    x0, x1, y0, y1 = region
    roi = bgr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
    if roi.size == 0:
        return None, None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = (gray > 140).astype(np.uint8)
    n, _l, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
    words = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < 4 or cw < 2 or ch < 4:
            continue
        words.append((x, y, cw, ch))
    if not words:
        return None, None
    # on regroupe les "mots" par ligne
    rows = {}
    for x, y, cw, ch in words:
        rows.setdefault(y // 12, []).append((x, y, cw, ch))
    for key in sorted(rows):
        ws = sorted(rows[key])
        span = ws[-1][0] + ws[-1][2] - ws[0][0]
        if span < 200:          # trop court pour etre la ligne de diag
            continue
        y0w = min(t[1] for t in ws)
        y1w = max(t[1] + t[3] for t in ws)
        return (("ligne de diagnostic detectee (x=%d..%d, y=%d..%d) ; "
                 "decode par le canal WTF ou pixels") %
                (ws[0][0], ws[-1][0] + ws[-1][2], y0w, y1w),
                (int(x0 * w) + ws[0][0], int(y0 * h) + y0w,
                 int(x0 * w) + ws[-1][0] + ws[-1][2], int(y0 * h) + y1w))
    return None, None


# --------------------------------------------------------------------------
# B. lecture des SavedVariables (fichier WTF)
# --------------------------------------------------------------------------
def read_wtf_payload(wtf_path):
    """Lit CasinobabeDB.corpseDiag depuis le fichier WTF.

    Le fichier est ecrit par le client a la deconnexion / au rechargement
    de l'UI.  On lit la sous-table brute et on en extrait les champs.
    """
    if not wtf_path or not os.path.isfile(wtf_path):
        return None
    try:
        with open(wtf_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    if "corpseDiag" not in raw:
        return None
    out = {}
    for key in ("ghost", "alive", "mapID", "worldX", "worldY", "worldZ",
                "retrieveSpellID", "retrieveSpellKnown"):
        m = re.search(r'\["%s"\]\s*=\s*([^,\n}]+)' % key, raw)
        if m:
            out[key] = m.group(1).strip()
    for key in ("playerMap", "corpseMap"):
        m = re.search(r'\["%s"\]\s*=\s*\{(.*?)\}', raw, re.S)
        if m:
            block = m.group(1)
            xs = re.search(r'\["x"\]\s*=\s*([-\d.eE+]+)', block)
            ys = re.search(r'\["y"\]\s*=\s*([-\d.eE+]+)', block)
            if xs and ys:
                out[key] = (float(xs.group(1)), float(ys.group(1)))
    for key in ("mapErr", "playerMapErr", "corpseMapErr", "retrieve",
                "retrieveErr", "castErr", "castResult"):
        m = re.search(r'\["%s"\]\s*=\s*"([^"]*)"' % key, raw)
        if m:
            out[key] = m.group(1)
    return out or None


# --------------------------------------------------------------------------
# C. lecture de la bande de pixels
# --------------------------------------------------------------------------
def _find_sync(bgr):
    """Rectangle du marqueur violet de la PREMIERE ligne.

    Le marqueur est le carre violet le plus en HAUT A GAUCHE.  On prend
    sa boite (pas le bbox global, qui engloberait tous les marqueurs des
    lignes suivantes).
    """
    b, g, r = (bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int),
               bgr[:, :, 2].astype(int))
    m = (abs(r - SYNC_RGB[0]) <= 18) & (g <= 24) & (b >= 168)
    ys, xs = np.nonzero(m)
    if len(xs) < 10:
        return None
    top = int(ys.min())
    sel = xs[ys == top]
    left = int(sel.min())
    # largeur du carre violet de la 1re ligne
    cols = np.nonzero(m[top:top + 4].any(axis=0))[0]
    right = int(cols.max()) if len(cols) else left
    return left, top, right, top


def _square_size(bgr, x0, y0, x1, y1):
    """Taille d'un carre : la HAUTEUR du premier carre violet."""
    b, g, r = (bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int),
               bgr[:, :, 2].astype(int))
    m = (abs(r - SYNC_RGB[0]) <= 18) & (g <= 24) & (b >= 168)
    rows = np.nonzero(m[:, x0:x1 + 1].any(axis=1))[0]
    if len(rows) == 0:
        return None
    top = int(rows.min())
    # hauteur du premier groupe de lignes violettes consecutives
    end = top
    while (end + 1) in rows:
        end += 1
    height = end - top + 1
    cols = np.nonzero(m[top:end + 1].any(axis=0))[0]
    width = int(cols.max() - cols.min() + 1) if len(cols) else height
    return int(min(height, width))


def _read_grid(bgr, x0, y0, sq, max_rows=8, largeur=60):
    """Lit la grille, ligne par ligne.

    Convention : le marqueur violet de la ligne 0 est en (x0, y0) ; les
    `largeur` caracteres suivants occupent les colonnes 1..largeur ; la
    case vide qui suit marque la fin de ligne.
    """
    h, w = bgr.shape[:2]

    def violet(px):
        b, g, r = int(px[0]), int(px[1]), int(px[2])
        return abs(r - SYNC_RGB[0]) <= 26 and g <= 34 and b >= 155

    out = []
    for row in range(max_rows):
        base_y = y0 + row * sq
        line = []
        for col in range(1, largeur + 1):
            cx = x0 + col * sq + sq // 2
            cy = base_y + sq // 2
            if cx >= w or cy >= h:
                break
            px = bgr[cy, cx]
            if violet(px):
                break
            v = int(round(float(px[0]) + float(px[1]) + float(px[2])) / 3.0)
            if v < 6:
                break            # case vide -> fin de cette ligne
            line.append(chr(v) if 32 <= v < 127 else "?")
        if not line:
            break
        out.append("".join(line))
    return " ".join(out)


def read_strip_payload(img):
    """Decode la bande de pixels. Retourne (texte|None, echelle|None).

    La convention exacte peut varier (nombre de caracteres par ligne) :
    on essaie les variantes plausibles et on ne retient que celle dont le
    texte contient TOUTES les cles attendues.
    """
    bgr = _bgr(img)
    if bgr is None:
        return None, None
    sync = _find_sync(bgr)
    if sync is None:
        return None, None
    x0, y0, x1, y1 = sync
    sq = _square_size(bgr, x0, y0, x1, y1)
    echelles = ([sq] if sq else []) + [12, 14, 16, 18, 20, 22, 24, 26, 10, 8]
    for s in echelles:
        if not s:
            continue
        for largeur in (60, 59, 58, 61, 62):
            text = _read_grid(bgr, x0, y0, s, largeur=largeur)
            if not text:
                continue
            flat = text.replace(" ", "")
            if all(k.replace(" ", "") in flat for k in EXPECTED):
                return text, s
    return None, None


# --------------------------------------------------------------------------
# facade
# --------------------------------------------------------------------------
def collect(img=None, wtf_path=None):
    """Reunit ce qui est lisible. Retourne un dict par canal."""
    res = {"strip": None, "stripScale": None, "wtf": None, "chat": None,
            "at": time.time()}
    if img is not None:
        text, sq = read_strip_payload(img)
        res["strip"] = text
        res["stripScale"] = sq
        chat, _bbox = read_chat_payload(img)
        res["chat"] = chat
    if wtf_path:
        res["wtf"] = read_wtf_payload(wtf_path)
    return res


def is_diagnostic_available(res):
    """Une donnee exploitable a-t-elle ete obtenue ?"""
    return bool(res.get("strip") or res.get("wtf"))
