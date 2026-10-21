#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_minimap.py — LECTURE DE LA MINICARTE (source de position du bot V2).

CE QUE LA MINICARTE PERMET (verifie sur captures reelles, 2026-10-21)
---------------------------------------------------------------------
Capture reelle du coin superieur droit (Lushwater Oasis) :
  - la minicarte est VERROUILLEE AU NORD (le « N » en haut) ;
  - elle est CENTREE SUR LE JOUEUR (la fleche du joueur est au centre).

Consequences, decisives pour la navigation :
  1. la position ABSOLUE du joueur n'est PAS lisible dans la minicarte
     (elle y est toujours au centre) ;
  2. en revanche, le CONTENU de la minicarte se decale quand le joueur
     avance : la minicarte est un odometre ;
  3. les CIBLES SUIVIES (anneaux colores) sont visibles et leur position
     RELATIVE au joueur est directement lisible.

Le module fournit donc :
  * `MinimapGeometry`  : localise le disque de minicarte dans la fenetre
    (position et rayon caches : l'interface ne bouge pas) ;
  * `MinimapOdometer`  : mesure le decalage entre deux images de minicarte
    (phase correlation) et l'integre en position relative, referencee NORD ;
  * `MinimapReader`    : extrait les marqueurs de cibles (anneaux colores)
    et leurs positions relatives au joueur, en pixels de minicarte.

Unite : le PIXEL DE MINICARTE. La distance monde (metres) n'est pas connue
(le zoom de la minicarte n'est pas expose) : les distances sont donc
exprimees en pixels de minicarte, comme dans le modele de carte V2.

CONVENTION DE SIGNE (verifiee en session reelle, voir tests) :
le contenu de la minicarte se deplace OPPOSITE au deplacement du joueur.
Quand le joueur va vers l'est, le contenu glisse vers l'ouest : le decalage
mesure est donc negatif. `MinimapOdometer.update()` renvoie la position du
JOUEUR (donc deja corrigee du signe).
"""

import math
import time

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None

#: Rayon du disque de minicarte, en pixels de la fenetre capturee.
#: 2336x1323 -> rayon ~103 ; le rayon est recalcule si la detection change.
DEFAULT_RADIUS = 103

#: Les anneaux de cibles sont des cercles SATURES de rayon ~4-6 px.
MARKER_MIN_RADIUS = 3
MARKER_MAX_RADIUS = 9

#: Teintes HSV (OpenCV, 0..179) des anneaux de cibles observes :
#: jaune (~25), vert (~55), rose/magenta (~150).
MARKER_HUE_RANGES = ((15, 40), (40, 85), (135, 170))

#: Le degrade de la fleche du joueur n'est PAS lu ici : le cap est mesure
#: par le DEPLACEMENT observe (la sonde du navigateur), ce qui est plus
#: robuste qu'une analyse de forme.


def _bgr(frame):
    """Normalise une image en BGR numpy (accepte PIL, BGR, RGB, BGRA)."""
    if frame is None:
        return None
    if np is None:
        return None
    if hasattr(frame, "convert"):          # PIL
        arr = np.array(frame)
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        if arr.ndim == 3 and arr.shape[2] == 3:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        return arr
    arr = np.asarray(frame)
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    return arr


class MinimapGeometry(object):
    """Localisation du disque de minicarte dans la fenetre du client.

    L'interface du client ne bouge pas : la detection est faite UNE fois et
    mise en cache. `refresh(force=True)` permet de la refaire.
    """

    def __init__(self):
        self.cx = None          # centre (x, y) en pixels de fenetre
        self.cy = None
        self.radius = DEFAULT_RADIUS
        self.detected_at = 0.0
        self.method = None

    # -- detection ---------------------------------------------------------
    def detect(self, bgr):
        """Cherche le disque de minicarte. Retourne True si trouve."""
        if bgr is None or cv2 is None:
            return False
        h, w = bgr.shape[:2]
        y0, y1 = 0, int(h * 0.30)
        x0, x1 = int(w * 0.78), w
        region = bgr[y0:y1, x0:x1]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.4, minDist=60,
                                   param1=90, param2=45,
                                   minRadius=int(self.radius * 0.7),
                                   maxRadius=int(self.radius * 1.3))
        if circles is None:
            return False
        c = circles[0][0]
        nx = x0 + int(c[0])
        ny = y0 + int(c[1])
        nr = int(c[2])
        # STABILITE : l'interface ne bouge pas, donc une re-detection qui
        # decale le centre de quelques pixels est du bruit de detecteur. On
        # garde l'ancien centre (et l'ancien rayon) dans ce cas : sinon
        # l'odometre enregistrerait un faux deplacement (mesure : 4,98 px de
        # derive sur un joueur immobile).
        if self.cx is not None:
            if (abs(nx - self.cx) <= 6 and abs(ny - self.cy) <= 6
                    and abs(nr - self.radius) <= 6):
                self.detected_at = time.time()
                return True
        self.cx = nx
        self.cy = ny
        self.radius = nr
        self.detected_at = time.time()
        self.method = "hough"
        return True

    def ensure(self, bgr, max_age=30.0):
        """Detecte la minicarte si besoin (cache TTL)."""
        if self.cx is None or (time.time() - self.detected_at) > max_age:
            self.detect(bgr)
        return self.cx is not None

    # -- decoupe -----------------------------------------------------------
    def crop(self, bgr, scale=1.0):
        """Recadre un carre AUTOUR du disque, de taille FIXE.

        La taille fixe est indispensable : la correlation de phase exige
        deux images de meme taille.
        """
        if bgr is None or self.cx is None:
            return None
        r = int(round(self.radius * scale))
        cx, cy = self.cx, self.cy
        h, w = bgr.shape[:2]
        x0 = max(0, cx - r)
        y0 = max(0, cy - r)
        x1 = min(w, cx + r)
        y1 = min(h, cy + r)
        if x1 - x0 != 2 * r or y1 - y0 != 2 * r:
            return None            # fenetre trop petite : pas de crop honnete
        return bgr[y0:y1, x0:x1].copy()

    def mask(self, size):
        """Masque circulaire (exclut le contour, les boutons et l'horloge)."""
        m = np.zeros((size, size), dtype=np.float32)
        cv2.circle(m, (size // 2, size // 2), int(size * 0.46), 1.0, -1)
        return m


class MinimapOdometer(object):
    """Odometre : position relative du joueur, referencee NORD.

    Le decalage du contenu de la minicarte est mesure par correlation de
    phase entre deux images consecutivees, puis INTEGRE : c'est ce qui donne
    une position (et non un simple vecteur instantane).
    """

    def __init__(self, geometry=None):
        self.geometry = geometry or MinimapGeometry()
        self.x = 0.0            # pixels de minicarte, est positif
        self.y = 0.0            # pixels de minicarte, sud positif
        self.last_crop = None
        self.last_shift = (0.0, 0.0)
        self.confidence = 0.0
        self.samples = 0
        self.moved = False       # au moins un decalage significatif observe

    def reset(self):
        self.x = 0.0
        self.y = 0.0
        self.last_crop = None
        self.last_shift = (0.0, 0.0)
        self.confidence = 0.0
        self.samples = 0
        self.moved = False

    def update(self, bgr, min_shift=0.15):
        """Met a jour la position a partir d'une nouvelle capture.

        Retourne un dict : {ok, x, y, dx, dy, confidence, moved}
        """
        out = {"ok": False, "x": self.x, "y": self.y, "dx": 0.0, "dy": 0.0,
               "confidence": 0.0, "moved": False}
        if not self.geometry.ensure(bgr):
            return out
        crop = self.geometry.crop(bgr)
        if crop is None:
            return out
        return self.update_from_crop(crop, min_shift=min_shift)

    def update_from_crop(self, crop, min_shift=0.15):
        """Variante : le recadrage est deja fait (corpus de test fige)."""
        out = {"ok": False, "x": self.x, "y": self.y, "dx": 0.0, "dy": 0.0,
               "confidence": 0.0, "moved": False}
        if crop is None:
            return out
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self.last_crop is None:
            self.last_crop = gray
            out["ok"] = True
            return out
        if self.last_crop.shape != gray.shape:
            self.last_crop = gray
            out["ok"] = True
            return out
        win = cv2.createHanningWindow((gray.shape[1], gray.shape[0]), cv2.CV_32F)
        try:
            (sx, sy), resp = cv2.phaseCorrelate(self.last_crop, gray, win)
        except cv2.error:
            return out
        self.last_crop = gray
        self.samples += 1
        # Le contenu se deplace OPPOSITE au joueur : on inverse.
        dx = -float(sx)
        dy = -float(sy)
        self.last_shift = (dx, dy)
        self.confidence = float(resp)
        if abs(dx) >= min_shift or abs(dy) >= min_shift:
            self.x += dx
            self.y += dy
            self.moved = True
        out.update({"ok": True, "x": self.x, "y": self.y, "dx": dx, "dy": dy,
                    "confidence": self.confidence, "moved": self.moved})
        return out


class MinimapReader(object):
    """Lecteur de minicarte : geometrie + odometre + marqueurs de cibles."""

    def __init__(self):
        self.geometry = MinimapGeometry()
        self.odometer = MinimapOdometer(self.geometry)

    def read(self, bgr, detect_markers=True):
        """Analyse une capture.

        Retourne :
          {ok, center, radius, position:{x,y}, markers:[{x,y,kind,dist}],
           heading: None (le cap vient du deplacement observe)}
        Les positions des marqueurs sont RELATIVES au joueur (origine = le
        centre de la minicarte), en pixels de minicarte, reference nord.
        """
        res = {"ok": False, "center": None, "radius": None,
               "position": {"x": self.odometer.x, "y": self.odometer.y},
               "markers": [], "samples": self.odometer.samples}
        if not self.geometry.ensure(bgr):
            return res
        r = self.geometry.radius
        res["center"] = (self.geometry.cx, self.geometry.cy)
        res["radius"] = r
        pos = self.odometer.update(bgr)
        res["position"] = {"x": pos["x"], "y": pos["y"]}
        res["odometer"] = pos
        crop = self.geometry.crop(bgr)
        if crop is None:
            return res
        if detect_markers:
            res["markers"] = self.find_markers(crop)
        res["ok"] = True
        return res

    # -- marqueurs ---------------------------------------------------------
    def find_markers(self, crop):
        """Anneaux de cibles suivies (positions relatives au joueur)."""
        if crop is None or cv2 is None:
            return []
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        cx = cy = w // 2
        mask = np.zeros((h, w), dtype=np.uint8)
        for lo, hi in MARKER_HUE_RANGES:
            mask |= cv2.inRange(hsv, np.array([lo, 90, 90]), np.array([hi, 255, 255]))
        # SEULE l'interieur du disque de minicarte est exploitable : les
        # boutons (loupe, soleil, +/-, horloge) sont a l'EXTERIEUR du rond et
        # produisaient des faux positifs (mesures reelles : 9 marqueurs
        # detectes pour 5 reels, dont le soleil et l'horloge).
        inside = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(inside, (cx, cy), int(min(h, w) * 0.46), 1, -1)
        mask = cv2.bitwise_and(mask, inside)
        # on exclut la fleche du joueur (au centre)
        cv2.circle(mask, (cx, cy), 12, 0, -1)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        max_dist = min(h, w) * 0.48
        out = []
        for i in range(1, count):
            area = stats[i, cv2.CC_STAT_AREA]
            bw = int(stats[i, cv2.CC_STAT_WIDTH])
            bh = int(stats[i, cv2.CC_STAT_HEIGHT])
            # Un anneau de cible est un PETIT contour : 5..18 px de large.
            # Ce filtre elimine les grandes zones saturees (herbe, eau) et les
            # icones pleines (soleil, loupe) qui passaient pour des cibles.
            if area < 12 or bw < 5 or bh < 5 or bw > 18 or bh > 18:
                continue
            ratio = area / float(max(1, bw * bh))
            if ratio < 0.30 or ratio > 0.95:
                continue
            mx, my = centroids[i]
            dx = mx - cx
            dy = my - cy
            dist = math.hypot(dx, dy)
            if dist < 14 or dist > max_dist:
                continue
            hue = int(np.median(hsv[:, :, 0][labels == i]))
            kind = "jaune" if hue < 40 else ("vert" if hue < 85 else "rose")
            out.append({"x": round(float(dx), 2), "y": round(float(dy), 2),
                        "dist": round(dist, 2), "kind": kind,
                        "area": int(area)})
        out.sort(key=lambda m: m["dist"])
        return out


class WorldOdometer(object):
    """Odometre du PERSONNAGE base sur la VUE 3D (et non sur la minicarte).

    POURQUOI : la minicarte n'est pas un odometre fiable. Mesure du
    2026-10-21, personnage IMMOBILE : derive de 7,05 px en 10 s (l'eau
    animee biaise la correlation de phase), alors que la vue 3D ne defile
    pas (0,00 px/s). La vue 3D est donc la mesure de reference.

    REPERE : le deplacement est mesure dans le repere ECRAN (la vue 3D). Ce
    repere est fixe tant que la CAMERA ne tourne pas. Or le bot ne deplace
    plus le curseur (la camera ne bouge donc plus de la valeur du tout) : le
    repere ecran est donc un repere STABLE, et les positions accumulees
    (bancs memorises) restent comparables entre elles.

    C'est cette stabilite qui permet de naviguer de banc a banc sans connaitre
    l'orientation absolue de la camera.
    """

    def __init__(self, min_shift=0.05):
        self.x = 0.0
        self.y = 0.0
        self.last_gray = None
        self.samples = 0
        self.min_shift = min_shift
        self.confidence = 0.0
        self.drift = 0.0          # derive mesuree quand le personnage est immobile
        self.moved = False

    def reset(self):
        self.x = 0.0
        self.y = 0.0
        self.last_gray = None
        self.samples = 0
        self.drift = 0.0
        self.moved = False

    def update(self, bgr, region=(0.12, 0.72, 0.05, 0.62)):
        """Met a jour la position a partir d'une nouvelle capture."""
        out = {"ok": False, "x": self.x, "y": self.y, "dx": 0.0, "dy": 0.0,
               "confidence": 0.0, "moved": False}
        if bgr is None or cv2 is None:
            return out
        h, w = bgr.shape[:2]
        y0, y1, x0, x1 = region
        zone = bgr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        if zone.size == 0:
            return out
        gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self.last_gray is None or self.last_gray.shape != gray.shape:
            self.last_gray = gray
            out["ok"] = True
            return out
        win = cv2.createHanningWindow((gray.shape[1], gray.shape[0]), cv2.CV_32F)
        try:
            (sx, sy), resp = cv2.phaseCorrelate(self.last_gray, gray, win)
        except cv2.error:
            return out
        self.last_gray = gray
        self.samples += 1
        dx = -float(sx)
        dy = -float(sy)
        self.confidence = float(resp)
        if abs(dx) >= self.min_shift or abs(dy) >= self.min_shift:
            self.x += dx
            self.y += dy
            self.moved = True
        out.update({"ok": True, "x": self.x, "y": self.y, "dx": dx, "dy": dy,
                    "confidence": self.confidence, "moved": self.moved})
        return out

    def measure_drift(self, bgr_prev, bgr_now):
        """Derive de l'odometre entre deux captures (personnage immobile)."""
        if bgr_prev is None or bgr_now is None:
            return None
        h, w = bgr_prev.shape[:2]
        if (h, w) != bgr_now.shape[:2]:
            return None
        y0, y1, x0, x1 = (0.12, 0.72, 0.05, 0.62)
        a = cv2.cvtColor(bgr_prev[int(h * y0):int(h * y1),
                               int(w * x0):int(w * x1)], cv2.COLOR_BGR2GRAY).astype(np.float32)
        b = cv2.cvtColor(bgr_now[int(h * y0):int(h * y1),
                               int(w * x0):int(w * x1)], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if a.size == 0 or a.shape != b.shape:
            return None
        win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
        try:
            (sx, sy), _r = cv2.phaseCorrelate(a, b, win)
        except cv2.error:
            return None
        return math.hypot(float(sx), float(sy))


def world_displacement(frame_a, frame_b, region=(0.12, 0.72, 0.05, 0.62)):
    """Deplacement de la VUE 3D entre deux captures (pixels ecran).

    C'est la mesure de reference du mouvement du personnage : le monde
    defile quand le personnage avance, et il est IMMOBILE quand le
    personnage est immobile (verifie : 0,00 px/s au repos, contre une derive
    de 0,7 px/s sur la minicarte). Independant de la minicarte, donc
    immune a son biais.

    :param region: (y0, y1, x0, x1) en fractions de l'image, zone monde
                    (la minicarte et l'interface sont exclues).
    :return: (dx, dy, confiance) en pixels ecran (dy < 0 = le monde monte,
             donc le personnage recule).
    """
    if frame_a is None or frame_b is None:
        return (0.0, 0.0, 0.0)
    ha, wa = frame_a.shape[:2]
    hb, wb = frame_b.shape[:2]
    if (ha, wa) != (hb, wb):
        return (0.0, 0.0, 0.0)
    y0, y1, x0, x1 = region
    za = frame_a[int(ha * y0):int(ha * y1), int(wa * x0):int(wa * x1)]
    zb = frame_b[int(hb * y0):int(hb * y1), int(wb * x0):int(wb * x1)]
    if za.size == 0 or za.shape != zb.shape:
        return (0.0, 0.0, 0.0)
    g1 = cv2.cvtColor(za, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(zb, cv2.COLOR_BGR2GRAY).astype(np.float32)
    win = cv2.createHanningWindow((g1.shape[1], g1.shape[0]), cv2.CV_32F)
    try:
        (sx, sy), resp = cv2.phaseCorrelate(g1, g2, win)
    except cv2.error:
        return (0.0, 0.0, 0.0)
    return (float(sx), float(sy), float(resp))
    # -- aide --------------------------------------------------------------
    def target_position(self, marker, current=None):
        """Position absolue (frame integre) d'un marqueur donne."""
        p = current or {"x": self.odometer.x, "y": self.odometer.y}
        return (p["x"] + marker["x"], p["y"] + marker["y"])
