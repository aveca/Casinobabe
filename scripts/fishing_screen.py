#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_screen.py — VALIDATION D'ECRAN APRES CHAQUE ACTION.

Exigence du developpeur (2026-10-21) : « a chaque clic, action, un screen
valide le process, pour pas se perdre ».

Constat qui a impose ce module : le bot cliquait sans verifier, et ses
clics tombaient sur l'INTERFACE (barre de gauche -> panneau AMIS, sac,
fiche personnage).  Le personnage partait alors dans une direction
inconnue, et le moteur perdait toute reference.

Le validateur fait, apres CHAQUE action :

  1. une CAPTURE (l'etat de reference n'est jamais suppose) ;
  2. une VERIFICATION de la vue de jeu :
       - la carte du monde est-elle ouverte ?  -> on la referme ;
       - un panneau d'interface est-il ouvert ? -> on le referme (Echap) ;
       - le jeu est-il bien en vue monde ?    -> sinon on refuse d'agir ;
  3. une MESURE du resultat (deplacement, cap) ;
  4. un VERDICT structure, journalise.

Il expose aussi les zones d'interface interdites : une cible de clic
DOIT tomber sur le monde (jamais sur un bouton), sinon le clic ouvre un
panneau au lieu de deplacer le personnage.
"""

import math
import os
import sys
import time

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Zones d'interface ou un clic ne deplace JAMAIS le personnage.
#: (x0, x1, y0, y1) en fractions de l'ecran.
#:  - bande de gauche  : portrait, debuffs, fleches sociales (-> AMIS, SAC)
#:  - bas d'ecran     : barre d'actions, sacs, boutons
#:  - haut-droite     : minicarte et ses boutons
#:  - bas-droite      : sacs de familier
UI_ZONES = (
    (0.00, 0.085, 0.00, 1.00),      # bande de gauche (portrait, social)
    (0.00, 1.00, 0.90, 1.00),      # barre d'actions
    (0.87, 1.00, 0.00, 0.26),      # minicarte
    (0.86, 1.00, 0.78, 1.00),      # sacs / boutons右下
)

#: Zone de jeu "pure" (vue 3D) : c'est la seule ou l'on peut cliquer.
WORLD_ZONE = (0.09, 0.86, 0.06, 0.88)


def in_ui_zone(x, y, w, h, zones=UI_ZONES):
    """Le point (x, y) est-il dans une zone d'interface ?"""
    if not w or not h:
        return True
    fx, fy = x / float(w), y / float(h)
    for x0, x1, y0, y1 in zones:
        if x0 <= fx < x1 and y0 <= fy < y1:
            return True
    return False


def in_world_zone(x, y, w, h, zone=WORLD_ZONE):
    """Le point est-il dans la vue 3D (clic de deplacement sur le sol) ?"""
    if not w or not h:
        return False
    fx, fy = x / float(w), y / float(h)
    return zone[0] <= fx < zone[1] and zone[2] <= fy < zone[3]


def clamp_to_world(x, y, w, h, zone=WORLD_ZONE, margin=12):
    """Reporte un point hors zone monde dans la zone monde la plus proche."""
    x0 = int(zone[0] * w) + margin
    x1 = int(zone[1] * w) - margin
    y0 = int(zone[2] * h) + margin
    y1 = int(zone[3] * h) - margin
    return int(max(x0, min(x1, x))), int(max(y0, min(y1, y)))


def looks_like_ui(bgr, x, y, radius=26):
    """Le point vise est-il sur un PANNEAU d'interface (et non le monde) ?

    Un panneau WoW est une surface PLATE (parchemin, bois, cadre) : la
    variance locale y est tres faible, alors que le terrain (eau, herbe,
    rochers) a du relief.  On s'en sert pour EMPECHER le clic qui
    ouvrirait un panneau (sac, amis, fiche personnage) au lieu de deplacer
    le personnage.
    """
    if bgr is None:
        return False
    h, w = bgr.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return True
    x0, x1 = max(0, x - radius), min(w, x + radius)
    y0, y1 = max(0, y - radius), min(h, y + radius)
    patch = bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return True
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return float(gray.std()) < 6.0


class ScreenValidator(object):
    """Verifie l'etat du client apres chaque action et le repare si besoin.

    `probe`   : fonction qui rend une capture BGR (None si capture impossible)
    `actions` : adaptateur client (pour envoyer Echap / une touche)
    """

    def __init__(self, probe, actions=None, log=None):
        self.probe = probe
        self.actions = actions
        self.log = log or (lambda *a, **k: None)
        self.last_valid = None
        self.history = []

    # -- detections ------------------------------------------------------
    @staticmethod
    def map_open(bgr):
        try:
            from fishing_map import map_is_open
            return bool(map_is_open(bgr))
        except Exception:
            return False

    @staticmethod
    def panel_open(bgr, reference=None):
        """Un panneau d'interface s'est-il ouvert ?

        Detection par COMPARAISON avec la vue de reference : un panneau
       ouvre une zone ecran clairement differente (cadre sombre / texte) et
        fait chuter la luminance mediane de la zone centrale.
        """
        if bgr is None:
            return False
        h, w = bgr.shape[:2]
        zone = bgr[int(h * 0.10):int(h * 0.80), int(w * 0.20):int(w * 0.80)]
        if zone.size == 0:
            return False
        if reference is None:
            return False
        rh, rw = reference.shape[:2]
        if (rh, rw) != (h, w):
            return False
        rzone = reference[int(h * 0.10):int(h * 0.80),
                          int(w * 0.20):int(w * 0.80)]
        if rzone.size == 0:
            return False
        # 1. estimation du defilement du monde (il defile quand on avance :
        #    sans cette compensation, le terrain ressemble a un panneau)
        shift = (0.0, 0.0)
        try:
            from fishing_minimap import world_displacement
            dx, dy, _c = world_displacement(reference, bgr)
            shift = (dx, dy)
        except Exception:
            shift = (0.0, 0.0)
        # 2. comparaison de la reference DECALEE du defilement : ce qui
        #    reste different est STATIQUE -> c'est bien un panneau.
        M = np.float32([[1, 0, -shift[0]], [0, 1, -shift[1]]])
        warped = cv2.warpAffine(rzone, M, (rzone.shape[1], rzone.shape[0]),
                                flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
        g1 = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g2 = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY).astype(np.float32)
        diff = float(np.mean(np.abs(g1 - g2)))
        return diff > 14.0

    @staticmethod
    def looks_like_ui(bgr, x, y, radius=26):
        """Voir la fonction module `looks_like_ui`."""
        return looks_like_ui(bgr, x, y, radius)

    # -- verification ----------------------------------------------------
    def validate(self, expected="world", allow_close=True):
        """Valide l'etat courant. Retourne un dict de verdict.

        expected : "world" (vue de jeu) ou "any".

        IMPORTANT : on n'envoie JAMAIS la touche Echap ici.  Dans WoW,
        Echap OUVRE le menu de jeu : l'utiliser pour "fermer un panneau"
        CREAIT le panneau (constat du 2026-10-21 : le bot ouvrait des
        panneaux sans arret).  Un panneau detecte est donc REPORTE, et
        l'action correspondante est consideree comme non validee.
        """
        img = self.probe()
        if img is None:
            self.log("SCREEN_INVALID", reason="capture impossible")
            return {"ok": False, "verdict": "no_capture", "img": None}
        res = {"ok": True, "verdict": "ok", "img": img}
        # 1. la carte du monde doit etre fermee pour juger de l'UI
        if self.map_open(img):
            # M est un interrupteur : il REFERME la carte, sans ouvrir de
            # panneau modal.  C'est la seule reparation automatique-made.
            res["verdict"] = "map_open"
            if allow_close and self.actions is not None:
                self.actions.key("M")
                time.sleep(1.2)
                img2 = self.probe()
                if img2 is not None:
                    res["img"] = img2
                    res["ok"] = not self.map_open(img2)
                    res["verdict"] = "ok" if res["ok"] else "map_stuck"
            self.log("SCREEN_MAP_OPEN")
            self._record(res)
            return res
        # 2. un panneau s'est-il ouvert ? -> on le REPORTE (pas d'Echap)
        if expected == "world" and self.panel_open(img, self.last_valid):
            res["verdict"] = "panel_open"
            res["ok"] = False
            self.log("SCREEN_PANEL_OPEN",
                     note="panneau detecte : action invalidee, aucun Echap "
                          "envoye (il ouvrirait le menu de jeu)")
            self._record(res)
            return res
        # 3. la vue monde est-elle exploitable ?
        if expected == "world":
            h, w = img.shape[:2]
            zone = img[int(h * 0.10):int(h * 0.80), int(w * 0.20):int(w * 0.80)]
            if zone.size == 0:
                res["ok"] = False
                res["verdict"] = "no_world"
        self._record(res)
        return res

    def _record(self, res):
        self.history.append({"verdict": res.get("verdict"), "at": time.time()})
        del self.history[:-100]
        if res.get("ok") and res.get("img") is not None:
            self.last_valid = res["img"]

    def baseline(self):
        """Fixe la vue de reference (avant une action)."""
        img = self.probe()
        if img is not None:
            self.last_valid = img
        return img

    def measure(self, before, after):
        """Deplacement mesure entre deux captures (px de vue 3D)."""
        if before is None or after is None:
            return None
        try:
            from fishing_minimap import world_displacement
            dx, dy, conf = world_displacement(before, after)
            return math.hypot(dx, dy), dx, dy, conf
        except Exception:
            return None
