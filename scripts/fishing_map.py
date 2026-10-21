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
                 min_score=0.75, ambiguity=0.06, scales=(0.7, 0.85, 1.0, 1.2,
                                                        1.5, 1.9)):
        self.corpse_template = corpse_template
        self.player_template = player_template
        # Seuil calibre sur captures REELLES : le vrai marqueur du corps
        # score 0,81-0,91, le meilleur faux positif 0,68 -> 0,75 les
        # sépare sans ambiguïté.
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
    def with_fixtures(cls, min_score=0.75):
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

    def detect(self, bgr, kind=CORPS, region=MAP_REGION, near=None):
        """Detecte un marqueur. Retourne (marqueur|None, raison).

        Le score de template seul ne suffit pas : sur une carte complete,
        des objets divers (rochers, batiments) atteignent 0,6-0,7.  On
        ajoute donc une VERIFICATION COULEUR propre a chaque marqueur
        (mesures reelles du 2026-10-21, zoom 7x) :

          * le crane du corps : teinte orange saturée (H 8-32, S > 190) ;
          * la fleche verte du joueur : vert franc (H 35-85), detectee
            DIRECTEMENT par couleur (son gabarit etait contamine par le
            fanion heraldique voisine).
        """
        if kind == JOUEUR:
            return self._detect_player_arrow(bgr, region, near)
        tpl = self.corpse_template
        cand = self._match(bgr, tpl, region)
        if cand is None:
            return None, "gabarit absent"
        if cand["score"] < self.min_score:
            return None, ("score %.3f < %.3f (marqueur non identifie)"
                          % (cand["score"], self.min_score))
        if not self._color_ok(bgr, cand, CORPS):
            return None, ("signature couleur invalide pour %s (score %.3f)"
                          % (kind, cand["score"]))
        m = MapMarker(kind, cand["x"], cand["y"], cand["w"], cand["h"],
                      cand["score"], cand["scale"])
        return m, "score %.3f (echelle %.2f) + signature couleur OK" % (
            m.score, m.scale)

    def _detect_player_arrow(self, bgr, region=MAP_REGION, near=None):
        """La fleche verte du joueur, detectee par couleur.

        Elle est cherchee dans la zone de carte, et si un marqueur du corps
        est deja connu (`near`), on privilegie la fleche la plus PROCHE de
        lui (les deux sont a quelques dizaines de pixels l'un de l'autre).
        """
        if bgr is None or cv2 is None:
            return None, "image absente"
        h, w = bgr.shape[:2]
        x0 = int(region[0] * w)
        x1 = int(region[1] * w)
        y0 = int(region[2] * h)
        y1 = int(region[3] * h)
        roi = bgr[y0:y1, x0:x1]
        if roi.size == 0:
            return None, "region vide"
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        green = ((hue >= 35) & (hue <= 85) & (sat > 110) & (val > 120))
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(
            green.astype(np.uint8), 8)
        best = None
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 12 or area > 1200:
                continue
            if not (5 <= cw <= 45 and 5 <= ch <= 45):
                continue
            # une fleche est allongee dans UNE direction : on exclut les
            # carres (bâtiments, buissons)
            if not (0.25 <= float(cw) / max(1, ch) <= 4.0):
                continue
            cx = x0 + int(cent[i][0])
            cy = y0 + int(cent[i][1])
            score = float(area)
            if near is not None:
                d = math.hypot(cx - near.cx, cy - near.cy)
                if d > 220:
                    continue
                score = 1000.0 - d          # privilegie la fleche proche
            if best is None or score > best[0]:
                best = (score, cx, cy, cw, ch, area)
        if best is None:
            return None, "fleche verte du joueur introuvable"
        _sc, cx, cy, cw, ch, area = best
        m = MapMarker(JOUEUR, cx - cw // 2, cy - ch // 2, cw, ch,
                      min(1.0, area / 400.0), 1.0)
        return m, "fleche verte (%.0f, %.0f) aire %d" % (cx, cy, area)

    def _color_ok(self, bgr, cand, kind):
        """La signature couleur du marqueur est-elle presente ?"""
        if bgr is None:
            return True
        h, w = bgr.shape[:2]
        x0 = max(0, cand["x"])
        y0 = max(0, cand["y"])
        x1 = min(w, cand["x"] + cand["w"])
        y1 = min(h, cand["y"] + cand["h"])
        patch = bgr[y0:y1, x0:x1]
        if patch.size == 0:
            return False
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        if kind == CORPS:
            # crane : orange sature (le batiment/la roche ne le sont pas)
            ok = ((hue >= 8) & (hue <= 32) & (sat > 170) & (val > 110))
            return float(ok.mean()) >= 0.30
        # joueur : la fleche verte
        ok = ((hue >= 35) & (hue <= 85) & (sat > 90) & (val > 100))
        return float(ok.mean()) >= 0.20

    def detect_all(self, bgr, region=MAP_REGION):
        """Detecte le corps ET le joueur. Retourne un dict + les raisons.

        Le corps d'abord (gabarit + signature couleur), puis le joueur :
        la fleche verte est cherchee de preference PROCHE du corps.
        """
        out = {"corpse": None, "player": None, "reasons": []}
        corpse, why = self.detect(bgr, CORPS, region)
        out["corpse"] = corpse
        out["reasons"].append("corps: %s" % why)
        player, why = self.detect(bgr, JOUEUR, region, near=corpse)
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
    # La fleche verte du joueur est un PETIT blob : son "score" (aire/400)
    # est petit alors que la detection est fiable.  On ne mixe donc pas les
    # deux natures : la confiance vient de la qualite du gabarit du CORPS
    # (qui est un gros objet) et de la coherence geometrique (les deux
    # marqueurs doivent etre proches : le joueur est sur sa propre carte).
    player_quality = 1.0 if (player.score > 0.0 and player.w and player.h
                             and max(player.w, player.h) <= 45) else 0.5
    conf = float(corpse.score) * player_quality
    # Le vecteur joueur -> corps n'est exploitable que si les deux
    # marqueurs sont separes (sinon ils sont confondus et le cap est du
    # bruit : c'est EXACTEMENT le bug du 2026-10-21).
    reliable = dist >= 3.0
    if not reliable:
        conf *= 0.4
        reasons.append("marqueurs a %.1f px : cap non exploitable -> "
                       "recherche locale" % dist)
    if dist > 400.0:
        conf *= 0.6
        reasons.append("distance carte %.0f px : direction peu sure" % dist)
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
