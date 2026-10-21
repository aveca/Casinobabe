#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_map.py — LECTURE DE LA CARTE DU MONDE (marqueurs, cap vers le corps).

Pourquoi ce module existe (incident du 2026-10-21) :

* le personnage s'est noye en marchant vers un spot ; apres la liberation
  de l'esprit, il faut RETROUVER LE CORPS pour ressusciter sur place ;
* la carte du monde AFFICHE le marqueur du corps (sarcophage + crane) et le
  marqueur du joueur (drapeau + fleche verte) ;
* la vue 3D ne donne qu'un repere RELATIF : la carte est la seule source
  absolue de direction exploitable.

Le module fait trois choses, sans dependre d'aucune hypothose non mesuree :

1. `MapMarkerDetector` : template matching multi-echelle des deux marqueurs,
   avec score de confiance et refus explicite en cas d'ambiguite ;
2. `analyze_map` : vecteur joueur -> corps, cap (repere NORD, la carte est
   verrouillee au nord) et distance en pixels de carte ;
3. `map_bearing_to_screen` : conversion cap (nord) -> angle de deplacement
   dans la VUE 3D, en tenant compte du cap REEL du personnage (fleche de
   la minicarte) - la camera suit le personnage, l'ecran "haut" est donc
   la direction du personnage.

Aucune conversion carte -> ecran n'est faite sans passer par
`map_bearing_to_screen` : le vecteur carte n'est JAMAIS utilise tel quel
comme point de clic.
"""

import math
import os

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

#: Dossier des gabarits (references de regression issues de captures reelles)
FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tests", "fixtures")

#: Region de la carte dans la capture (la mini-carte est exclue)
MAP_REGION = (0.02, 0.95, 0.05, 0.92)      # x0, x1, y0, y1 (fractions)

CORPS = "corpse"
JOUEUR = "player"


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
    if arr is None or arr.size == 0:
        return None
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


class MapMarker(object):
    """Marqueur detecte sur la carte."""

    def __init__(self, kind, x, y, w, h, score, scale=1.0):
        self.kind = kind
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)
        self.score = float(score)
        self.scale = float(scale)

    @property
    def cx(self):
        return self.x + self.w / 2.0

    @property
    def cy(self):
        return self.y + self.h / 2.0

    def as_dict(self):
        return {"kind": self.kind, "x": self.x, "y": self.y, "w": self.w,
                "h": self.h, "cx": round(self.cx, 1), "cy": round(self.cy, 1),
                "score": round(self.score, 4), "scale": round(self.scale, 3)}

    def __repr__(self):
        return "<MapMarker %s (%.0f,%.0f) score=%.3f>" % (
            self.kind, self.cx, self.cy, self.score)


class MapMarkerDetector(object):
    """Detecte les marqueurs du corps et du joueur sur la carte du monde.

    Le template matching multi-echelle tolere un zoom de carte different de
    celui des captures de reference.  Un score trop faible ou deux
    correspondances equivalentes sont traites comme AMBIGUITE : aucun
    deplacement directionnel ne doit alors etre commande.
    """

    def __init__(self, corpse_template=None, player_template=None,
                 min_score=0.45, ambiguity=0.06, scales=(0.7, 0.85, 1.0, 1.2,
                                                        1.5, 1.9)):
        self.corpse_template = corpse_template
        self.player_template = player_template
        self.min_score = min_score
        self.ambiguity = ambiguity
        self.scales = scales

    # -- gabarits --------------------------------------------------------
    @staticmethod
    def load_template(path):
        if not path or not os.path.isfile(path):
            return None
        img = cv2.imread(path)
        return img

    @classmethod
    def with_fixtures(cls, min_score=0.45):
        return cls(corpse_template=cls.load_template(
            os.path.join(FIXTURES, "map_corpse_marker.png")),
            player_template=cls.load_template(
                os.path.join(FIXTURES, "map_player_marker.png")),
            min_score=min_score)

    # -- matching --------------------------------------------------------
    def _match(self, bgr, template, region=MAP_REGION):
        """Meilleure correspondance multi-echelle d'un gabarit."""
        if bgr is None or template is None or cv2 is None:
            return None
        h, w = bgr.shape[:2]
        x0, x1, y0, y1 = region
        roi = bgr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        if roi.size == 0:
            return None
        best = None
        for sc in self.scales:
            th, tw = template.shape[:2]
            nw, nh = int(tw * sc), int(th * sc)
            if nw < 6 or nh < 6 or nw > roi.shape[1] or nh > roi.shape[0]:
                continue
            tpl = cv2.resize(template, (nw, nh), interpolation=cv2.INTER_AREA)
            try:
                res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
            except cv2.error:
                continue
            _mn, mx, _mnl, mxl = cv2.minMaxLoc(res)
            cand = {"score": float(mx), "x": int(mxl[0] + w * x0),
                    "y": int(mxl[1] + h * y0), "w": int(nw), "h": int(nh),
                    "scale": sc}
            if best is None or cand["score"] > best["score"]:
                best = cand
            # VITESSE : si l'echelle nominale donne une correspondance
            # franche, on ne teste pas les autres echelles.
            if best["score"] >= 0.80:
                break
        return best

    def detect(self, bgr, kind=CORPS, region=MAP_REGION):
        """Detecte un marqueur. Retourne (marqueur|None, raison)."""
        tpl = self.corpse_template if kind == CORPS else self.player_template
        cand = self._match(bgr, tpl, region)
        if cand is None:
            return None, "gabarit absent"
        if cand["score"] < self.min_score:
            return None, ("score %.3f < %.3f (marqueur non identifie)"
                          % (cand["score"], self.min_score))
        m = MapMarker(kind, cand["x"], cand["y"], cand["w"], cand["h"],
                      cand["score"], cand["scale"])
        return m, "score %.3f (echelle %.2f)" % (m.score, m.scale)

    def detect_all(self, bgr, region=MAP_REGION):
        """Detecte le corps ET le joueur. Retourne un dict + les raisons."""
        out = {"corpse": None, "player": None, "reasons": []}
        corpse, why = self.detect(bgr, CORPS, region)
        out["corpse"] = corpse
        out["reasons"].append("corps: %s" % why)
        player, why = self.detect(bgr, JOUEUR, region)
        out["player"] = player
        out["reasons"].append("joueur: %s" % why)
        return out


class MapAnalysis(object):
    """Resultat de l'analyse d'une capture de carte."""

    def __init__(self, corpse=None, player=None, bearing_deg=None,
                 distance_px=None, confidence=0.0, reasons=None,
                 ambiguous=False, bearing_reliable=True):
        self.corpse = corpse
        self.player = player
        self.bearing_deg = bearing_deg
        self.distance_px = distance_px
        self.confidence = confidence
        self.reasons = list(reasons or [])
        self.ambiguous = ambiguous
        #: False quand les deux marqueurs sont quasi confondus : le cap est
        #: alors inutile (et potentiellement faux) -> recherche par spirale.
        self.bearing_reliable = bearing_reliable

    @property
    def usable(self):
        """L'analyse est-elle exploitable pour commander un deplacement ?"""
        return (self.corpse is not None and self.player is not None
                and self.bearing_deg is not None and not self.ambiguous
                and self.confidence >= 0.5)

    def as_dict(self):
        return {"corpse": self.corpse.as_dict() if self.corpse else None,
                "player": self.player.as_dict() if self.player else None,
                "bearingDeg": (None if self.bearing_deg is None
                               else round(self.bearing_deg, 1)),
                "distancePx": (None if self.distance_px is None
                               else round(self.distance_px, 1)),
                "confidence": round(self.confidence, 3),
                "ambiguous": self.ambiguous,
                "bearingReliable": self.bearing_reliable,
                "usable": self.usable,
                "reasons": self.reasons}

    def __repr__(self):
        return "<MapAnalysis %s cap=%s conf=%.2f>" % (
            "corps" if self.corpse else "-", self.bearing_deg, self.confidence)


def analyze_map(bgr, detector=None, region=MAP_REGION):
    """Analyse une capture de carte : ou est le corps, dans quelle direction.

    Le vecteur joueur -> corps est converti en CAP (repere nord, 0 = est,
    +90 = nord) puisque la carte du monde est verrouillee au nord.
    """
    detector = detector or MapMarkerDetector.with_fixtures()
    found = detector.detect_all(bgr, region)
    reasons = found["reasons"]
    corpse, player = found["corpse"], found["player"]
    if corpse is None or player is None:
        return MapAnalysis(corpse, player, None, None, 0.0, reasons,
                           ambiguous=(corpse is None))

    dx = corpse.cx - player.cx
    dy = corpse.cy - player.cy
    dist = math.hypot(dx, dy)
    # repere carte : x = est, y = sud -> cap (0 = est, +90 = nord)
    bearing = math.degrees(math.atan2(-dy, dx))
    # confiance : moyenne geometrique des deux scores (le produit est trop
    # Severe : un score de 0,62 sur un second marqueur est une bonne
    # detection), penalisee quand les marqueurs sont quasi confondus.
    conf = math.sqrt(max(0.0, corpse.score) * max(0.0, player.score))
    # Le vecteur joueur -> corps reste la MEILLEURE estimation de la
    # direction, meme quand les marqueurs sont proches (8 px => erreur
    # angulaire de l'ordre de 15 degres, largement suffisant : le corps est
    # a quelques metres).  Sous 3 px le vecteur n'est plus exploitable.
    reliable = dist >= 3.0
    if not reliable:
        conf *= 0.8
        reasons.append("marqueurs a %.1f px : cap non exploitable -> "
                       "recherche locale" % dist)
    return MapAnalysis(corpse, player, bearing, dist, conf, reasons,
                       bearing_reliable=reliable)


def map_is_open(bgr):
    """La carte du monde est-elle ouverte ?

    Discriminant retenu : la BARRE D'ACTION (bandeau d'icones colorees en
    bas de l'ecran) est masquee par la carte du monde.  On mesure donc la
    proportion de pixels colores satures dans ce bandeau.  (Le cerclage
    dore de la mini-carte ne convient pas : le parchemin de la carte est
    lui-meme dore.)
    """
    bgr = _bgr(bgr)
    if bgr is None:
        return None
    h, w = bgr.shape[:2]
    band = bgr[int(h * 0.76):int(h * 0.86), int(w * 0.25):int(w * 0.75)]
    if band.size == 0:
        return None
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    colored = (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 60)
    ratio = float(colored.mean())
    # mesures : carte = 0,83 | vue 3D (barre d'action) = 0,00-0,12
    return ratio > 0.35


def map_bearing_to_screen(bearing_deg, heading_deg):
    """Cap (repere nord) -> angle de deplacement dans la VUE 3D.

    La camera suit le personnage : l'ecran "haut" correspond a la direction
    du personnage (cap `heading_deg`, obtenu de la fleche de la minicarte,
    elle aussi verrouillee au nord).  Pour une cible de cap `bearing_deg` :

        dx_ecran = sin(bearing - heading)
        dy_ecran = -cos(bearing - heading)

    Returns None si un des deux caps est inconnu (pas de clic a l'aveugle).
    """
    if bearing_deg is None or heading_deg is None:
        return None
    d = math.radians(bearing_deg - heading_deg)
    return math.degrees(math.atan2(-math.cos(d), math.sin(d)))


def screen_to_click_angle(screen_deg, click_rotation_deg):
    """Angle de clic (ecran) pour obtenir un deplacement de `screen_deg`.

    `click_rotation_deg` est la rotation mesuree par la calibration du
    moteur de navigation (clic vise -> deplacement obtenu).  0 signifie
    "la camera est a l'aplomb".
    """
    if screen_deg is None:
        return None
    return screen_deg - (click_rotation_deg or 0.0)
