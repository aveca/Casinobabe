"""Lecture de la carte du monde : repere du CORPS et du JOUEUR.

Deux methodes, la plus robuste en tete :

  * `read_map_markers_tpl` : appariement de GABARITS extraits de
    captures REELLES du client.  Les deux gabarits sont DISJOINTS
    (crane orange = corps, fleche bleu-gris = joueur) et le joueur est
    exige a PROXIMITE du crane : c'est le controle de coherence qui
    supprime les faux positifs.  (Bug du 2026-10-21 : deux gabarits qui
    se recouvraient donnaient une direction fantaisiste et une distance
    qui ne pouvait pas diminuer.)

  * `read_map_markers` : repli par COULEUR, moins fiable.

La carte du monde de WoW est verrouillee au NORD (comme la minicarte) :
    cap = atan2(-(dy_ecran), dx_ecran), 0 = est, +90 = nord.
"""

import math
import os

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tests", "fixtures")

CORPS_TPL = os.path.join(FIXTURES, "map_corpse_marker.png")
JOUEUR_TPL = os.path.join(FIXTURES, "map_player_marker.png")
#: silhouette binaire du crane (independante du fond de carte)
CORPS_MASK = os.path.join(FIXTURES, "map_corpse_mask.png")
#: pointe de la fleche du joueur (gabarit tourne)
JOUEUR_ARROW = os.path.join(FIXTURES, "map_player_arrow.png")

_TPL_CACHE = {}


def corpse_silhouette(bgr):
    """Masque binaire du crane du corps, a partir d'une capture de carte.

    Le crane = pixels CLAIRS (os) + bandeau ORANGE sature.  Ce masque ne
    depend PAS du fond de carte (qui change de zone) : il est donc
    reutilisable d'une zone a l'autre.
    """
    if bgr is None or cv2 is None:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    os_ = (Vv > 185) & (Ss < 120)
    orange = (Hh >= 5) & (Hh <= 30) & (Ss > 170) & (Vv > 120)
    return ((os_ | orange).astype(np.uint8) * 255)


def load_templates(corps_path=CORPS_TPL, joueur_path=JOUEUR_TPL):
    """Charge (et met en cache) les gabarits."""
    if cv2 is None:
        return None, None
    key = (corps_path, joueur_path)
    if key in _TPL_CACHE:
        return _TPL_CACHE[key]
    c = cv2.imread(corps_path) if os.path.isfile(corps_path) else None
    p = cv2.imread(joueur_path) if os.path.isfile(joueur_path) else None
    _TPL_CACHE[key] = (c, p)
    return c, p


def _components(mask, min_area, max_area, min_w, max_w, min_h, max_h):
    n, _l, stats, cent = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (min_area <= area <= max_area):
            continue
        if not (min_w <= w <= max_w and min_h <= h <= max_h):
            continue
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "area": int(area), "cx": int(cent[i][0]),
                    "cy": int(cent[i][1])})
    return out


def _match(roi, tpl, scales, off_x, off_y):
    best = None
    for sc in scales:
        t = cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= roi.shape[0] or t.shape[1] >= roi.shape[1]:
            continue
        try:
            res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
        if best is None or mx > best["score"]:
            best = {"score": float(mx), "x": int(mxl[0] + off_x),
                    "y": int(mxl[1] + off_y), "w": int(t.shape[1]),
                    "h": int(t.shape[0])}
    if best:
        best["cx"] = best["x"] + best["w"] // 2
        best["cy"] = best["y"] + best["h"] // 2
    return best


def find_corpse_mask(bgr, region=(0.18, 0.98, 0.04, 0.96)):
    """Cherche le crane du corps par APPARIEMENT DE SILHOUETTE.

    Le gabarit est binaire (os clairs + bandeau orange) : il ne depend pas
    du fond de la carte, qui change de zone.  On exige un score eleve et
    une forme compacte.
    """
    if bgr is None or cv2 is None:
        return None
    tpl = cv2.imread(CORPS_MASK, 0) if os.path.isfile(CORPS_MASK) else None
    if tpl is None:
        return None
    h, w = bgr.shape[:2]
    x0, x1 = int(w * region[0]), int(w * region[1])
    y0, y1 = int(h * region[2]), int(h * region[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    mask = corpse_silhouette(roi)
    if mask is None or mask.max() == 0:
        return None
    best = None
    for sc in (0.85, 1.0, 1.15, 1.3, 0.7, 1.5):
        t = cv2.resize(tpl, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
        if t.shape[0] >= mask.shape[0] or t.shape[1] >= mask.shape[1]:
            continue
        try:
            res = cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
        if best is None or mx > best["score"]:
            best = {"score": float(mx), "x": int(mxl[0] + x0),
                    "y": int(mxl[1] + y0), "w": int(t.shape[1]),
                    "h": int(t.shape[0])}
    if best and best["score"] >= 0.55:
        best["cx"] = best["x"] + best["w"] // 2
        best["cy"] = best["y"] + best["h"] // 2
        return best
    return None


def find_player_arrow_candidates(bgr, centre=None, radius=150, steps=36,
                                 min_score=0.45):
    """Liste des CANDIDATS fleche du joueur (gabarit tourne).

    La carte contient d'autres fleches (quetes, PNJ) : on renvoie tous
    les candidats et c'est l'appelant qui tranche (continuite de la
    position d'une lecture a l'autre).
    """
    if bgr is None or cv2 is None:
        return []
    tpl = cv2.imread(JOUEUR_ARROW, cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        return []
    h, w = bgr.shape[:2]
    if centre is None:
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        x0 = max(0, centre[0] - radius)
        y0 = max(0, centre[1] - radius)
        x1 = min(w, centre[0] + radius)
        y1 = min(h, centre[1] + radius)
    roi = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if roi.size == 0:
        return []
    th, tw = tpl.shape[:2]
    out = []
    seen = set()
    for i in range(steps):
        ang = 360.0 * i / steps
        M = cv2.getRotationMatrix2D((tw / 2.0, th / 2.0), ang, 1.0)
        r = cv2.warpAffine(tpl, M, (tw, th), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if r.shape[0] >= roi.shape[0] or r.shape[1] >= roi.shape[1]:
            continue
        try:
            res = cv2.matchTemplate(roi, r, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
        if mx < min_score:
            continue
        cx = mxl[0] + x0 + tw // 2
        cy = mxl[1] + y0 + th // 2
        key = (cx // 6, cy // 6)
        if key in seen:
            continue
        seen.add(key)
        rad = math.radians(ang)
        out.append({"cx": int(cx), "cy": int(cy), "score": float(mx),
                    "rot": ang, "facing": (-math.sin(rad), -math.cos(rad))})
    out.sort(key=lambda c: -c["score"])
    return out


def find_player_dot(bgr, region=(0.30, 0.99, 0.05, 0.60), all_candidates=False):
    """LE JOUEUR sur la carte : le petit CERCLE BLEU qui surmonte la
    fleche du joueur.

    Ce bleu profond (H 110-130) n'existe nulle part ailleurs sur la carte
    de zone.  Pour ne pas confondre avec un pixel bleu isole, on exige
    la GEOMETRIE COMPLETE : une pointe de fleche CLAIRE juste au-dessus
    du cercle (le cercle est la base, la pointe est 20 px plus haut).
    """
    if bgr is None or cv2 is None:
        return [] if all_candidates else None
    h, w = bgr.shape[:2]
    x0, x1 = int(w * region[0]), int(w * region[1])
    y0, y1 = int(h * region[2]), int(h * region[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return [] if all_candidates else None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    blue = ((Hh >= 105) & (Hh <= 135) & (Ss > 60) & (Vv > 8) & (Vv < 140))
    cands = _components(blue.astype(np.uint8), 3, 300, 2, 18, 2, 18)
    if not cands:
        return [] if all_candidates else None
    # la pointe claire doit etre juste au-dessus du cercle
    bright = ((Vv > 165) & (Ss < 70)).astype(np.uint8)
    out = []
    for c in cands:
        cx = c["cx"]
        cy = c["cy"]
        bx0 = max(0, cx - 13)
        bx1 = min(roi.shape[1], cx + 13)
        by0 = max(0, cy - 26)
        by1 = max(0, cy - 1)
        if bx1 <= bx0 or by1 <= by0:
            continue
        sub = bright[by0:by1, bx0:bx1]
        n, _l, st, _ce = cv2.connectedComponentsWithStats(sub, 8)
        for i in range(1, n):
            _x, _y, ww, hh, area = st[i]
            if area < 25 or area > 400:
                continue
            if not (5 <= ww <= 26 and 5 <= hh <= 26):
                continue
            out.append({"cx": int(cx + x0), "cy": int(cy + y0),
                        "w": c["w"], "h": c["h"], "area": int(c["area"]),
                        "score": 1.0, "rot": 0.0, "facing": (0.0, -1.0),
                        "src": "dot"})
    out.sort(key=lambda c: -c["area"])
    return out if all_candidates else (out[0] if out else None)


def find_player_arrow(bgr, centre=None, radius=150, steps=36, min_score=0.55,
                      max_gap=150, last_player=None):
    """Trouve la FLECHE DU JOUEUR sur la carte (gabarit tourne).

    La fleche pointe dans la direction du personnage : elle donne donc a
    la fois la POSITION du joueur et son ORIENTATION, dans le repere de
    la carte.  La carte du monde est une fenetre de position FIXE : ce
    repere est stable d'une lecture a l'autre.

    Si `last_player` est fourni (position connue au pas precedent), on
    privilegie le candidat le plus PROCHE de cette position : c'est la
    continuity, qui elimine les fausses fleches (quetes, PNJ) qui
   给了他 un bon score.
    """
    cands = find_player_arrow_candidates(bgr, centre, radius, steps,
                                         min_score=min(min_score, 0.45))
    if not cands:
        return None
    if centre is not None:
        near = [c for c in cands
                if math.hypot(c["cx"] - centre[0], c["cy"] - centre[1]) <= max_gap]
        cands = near or cands
    if last_player is not None:
        prev = [(last_player["cx"], last_player["cy"]),
                (last_player.get("x"), last_player.get("y"))]
        best = None
        for c in cands:
            for px, py in prev:
                if px is None or py is None:
                    continue
                d = math.hypot(c["cx"] - px, c["cy"] - py)
                if best is None or d < best[0]:
                    best = (d, c)
        if best is not None:
            return best[1]
    best = max(cands, key=lambda c: c["score"])
    if best["score"] < min_score:
        return None
    return best


def map_state(bgr, corpse=None, last_player=None):
    """Etat complet de la carte : corps, joueur, cap et distance.

    Tout est exprime dans le repere de la carte (fenetre fixe) :
      * `bearingDeg`  : direction du corps vue du joueur (0 = est,
        +90 = nord, comme la minicarte) ;
      * `distancePx`  : distance en pixels de carte (progression) ;
      * `facing`      : direction ecran vers laquelle le personnage
        regarde (repere carte, x droite / y bas) ;
      * `rotErrorDeg` : ecart a corriger, en DEGREES (signe : positif =
        il faut tourner vers la DROITE sur la carte).
    """
    if corpse is None:
        corpse = find_corpse_mask(bgr)
    if corpse is None:
        return None
    # Candidats joueur : cercle bleu (sur) ET gabarit tourne (repli).
    cands = list(find_player_dot(bgr, all_candidates=True))
    if not cands:
        tpl = find_player_arrow(bgr, centre=(corpse["cx"], corpse["cy"]),
                                last_player=last_player)
        if tpl is None:
            return None
        cands = [tpl]
    # tri par CONTINUITE : le vrai joueur BOUGE entre deux lectures, un
    # faux pixel bleu reste fixe.  On exige donc un deplacement reel.
    if last_player is not None:
        moved = [c for c in cands
                 if math.hypot(c["cx"] - last_player["cx"],
                               c["cy"] - last_player["cy"]) >= 3.0]
        if moved:
            cands = moved
        cands.sort(key=lambda c: math.hypot(c["cx"] - last_player["cx"],
                                            c["cy"] - last_player["cy"]))
    else:
        cands.sort(key=lambda c: math.hypot(c["cx"] - corpse["cx"],
                                            c["cy"] - corpse["cy"]))
    arrow = cands[0]
    dx = corpse["cx"] - arrow["cx"]
    dy = corpse["cy"] - arrow["cy"]
    dist = math.hypot(dx, dy)
    bearing = math.degrees(math.atan2(-dy, dx))
    fx, fy = arrow["facing"]
    # ecart angulaire (repere ecran) entre le regard et la direction du
    # corps : positif = rotation horaire a l'ecran (droite)
    cross = fx * dy - fy * dx
    dot = fx * dx + fy * dy
    rot_err = math.degrees(math.atan2(cross, dot))
    return {"corpse": corpse, "player": arrow, "bearingDeg": bearing,
            "distancePx": dist, "facing": (fx, fy),
            "rotErrorDeg": rot_err, "score": arrow["score"]}


def read_map_markers_tpl(bgr, corps_tpl=None, joueur_tpl=None,
                         region=(0.18, 0.98, 0.04, 0.96),
                         scales=(0.85, 1.0, 1.2, 1.45, 1.75, 0.7, 2.1),
                         max_gap=260):
    """Apparie les gabarits du crane (corps) et de la fleche (joueur)."""
    if bgr is None or cv2 is None:
        return None, None, {}
    c_tpl, p_tpl = load_templates()
    if corps_tpl is not None:
        c_tpl = corps_tpl
    if joueur_tpl is not None:
        p_tpl = joueur_tpl
    if c_tpl is None or p_tpl is None:
        return None, None, {"erreur": "gabarits absents"}
    h, w = bgr.shape[:2]
    x0, x1 = int(w * region[0]), int(w * region[1])
    y0, y1 = int(h * region[2]), int(h * region[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None, None, {}
    c = _match(roi, c_tpl, scales, x0, y0)
    dbg = {"corps": None if not c else round(c["score"], 3)}
    if not c or c["score"] < 0.75:
        return None, None, dbg
    # La fleche du joueur est cherchee AU VOISINAGE du crane : la carte
    # contient plusieurs fleches (autres joueurs, PNJ) et le match global
    # designait parfois la mauvaise.  Le fantome etant sur SA carte, sa
    # fleche est a proximite immediate du crane.
    r = int(max(40, 1.6 * max(c["w"], c["h"])))
    wx0 = max(0, c["cx"] - r)
    wx1 = min(roi.shape[1], c["cx"] + r)
    wy0 = max(0, c["cy"] - r)
    wy1 = min(roi.shape[0], c["cy"] + r)
    sub = roi[wy0:wy1, wx0:wx1]
    if sub.size == 0:
        return None, None, dbg
    p = _match(sub, p_tpl, scales, wx0 + x0, wy0 + y0)
    dbg["joueur"] = None if not p else round(p["score"], 3)
    if not p or p["score"] < 0.35:
        return None, None, dbg
    gap = math.hypot(p["cx"] - c["cx"], p["cy"] - c["cy"])
    dbg["gap"] = round(gap, 1)
    if gap > max_gap:
        return None, None, dbg
    return c, p, dbg


def read_map_markers(bgr, region=(0.18, 0.98, 0.04, 0.96)):
    """Repli par COULEUR (crane orange, fleche bleu-gris)."""
    if bgr is None or cv2 is None:
        return None, None, {}
    h, w = bgr.shape[:2]
    x0, x1 = int(w * region[0]), int(w * region[1])
    y0, y1 = int(h * region[2]), int(h * region[3])
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None, None, {}
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    Hh, Ss, Vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    orange = ((Hh >= 5) & (Hh <= 30) & (Ss > 150) & (Vv > 110))
    cands_c = _components(orange, 100, 2000, 6, 45, 6, 45)
    blue = ((Hh >= 90) & (Hh <= 135) & (Ss > 30) & (Vv > 80))
    cands_p = _components(blue, 40, 2500, 10, 50, 8, 50)

    def to_abs(c):
        return {"x": c["x"] + x0, "y": c["y"] + y0, "w": c["w"], "h": c["h"],
                "area": c["area"], "cx": c["cx"] + x0, "cy": c["cy"] + y0}

    corps = [to_abs(c) for c in cands_c]
    joueur = [to_abs(c) for c in cands_p]
    dbg = {"orange": len(corps), "bleu": len(joueur)}
    if not corps or not joueur:
        return None, None, dbg
    best = None
    for p in joueur:
        d = math.hypot(p["cx"] - corps[0]["cx"], p["cy"] - corps[0]["cy"])
        if best is None or d < best[0]:
            best = (d, p)
    if best is None or best[0] > 200:
        return None, None, dbg
    return corps[0], best[1], dbg


def direction_to_corpse(corps, joueur):
    """Cap vers le corps (repere carte, nord en haut) et distance en pixels."""
    if not corps or not joueur:
        return None, None
    dx = corps["cx"] - joueur["cx"]
    dy = corps["cy"] - joueur["cy"]
    dist = math.hypot(dx, dy)
    bearing = math.degrees(math.atan2(-dy, dx))
    return bearing, dist
