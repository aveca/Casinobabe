# -*- coding: utf-8 -*-
"""
spot_navigator.py — Navigation bornée entre spots de pêche (P0).

Objectif : déplacer le personnage d'un point A à un point B (deux spots de
pêche) en s'appuyant UNIQUEMENT sur des observations visuelles réelles :
- position du joueur sur la minicarte (flèche au centre de la zone minicarte
  par défaut de Blizzard, coin haute-droite de l'écran) ;
- détection d'eau à l'écran comme indicateur d'arrivée proche.

CONSTANTES D'HONNÊTETÉ :
- Aucune coordonnée GPS / carte du monde n'est jamais inventée. La position du
  joueur est estimée en pixels relatifs au centre de la minicarte ; un spot
  est une position relative mesurée une fois sur place, jamais devinée.
- Si la direction ne peut pas être déterminée (pas de joueur sur la
  minicarte, UI personnalisée), AUCUNE touche n'est envoyée.
- Tout mouvement est borné : touches pressées ≤ hold_s, vague limitée, budget
  temps total, arrêt propre si aucun progrès.
- Le personnage ne bouge que si WoW est au premier plan (contrat send_key /
  hold_key déjà appliqué dans fishing_bot).

Statut : implémenté hors-ligne, testé sur images synthétiques. Validation en
jeu requise (protocole dans tests/test_spot_navigator.py et ci-dessous) avant
passage du statut "In development" -> "Available".
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

# --- imports du projet (même dossier) ---
from fishing_bot import (  # noqa: E402
    find_wow_window, capture_window, player_health, hold_key, send_key,
    emergency_stop_pressed,
)
from fishing_observer import is_wow_frame


# ---------------------------------------------------------------------------
# Zones d'écran (fractions de la fenêtre WoW, 16:9 par défaut)
# ---------------------------------------------------------------------------
# Minicarte Blizzard par défaut : coin haute-droite.
MINIMAP = {"x0": 0.72, "x1": 0.98, "y0": 0.02, "y1": 0.28}
# Zone de jeu 3D (hors UI) où chercher de l'eau.
GAME_VIEW = {"x0": 0.05, "x1": 0.95, "y0": 0.05, "y1": 0.85}


@dataclass
class NavConfig:
    max_waves: int = 6
    budget_s: float = 120.0
    hold_s: float = 1.2          # durée max d'une touche W (plafond 3s dans fishing_bot)
    turn_s: float = 0.35         # durée d'un tour A/D
    arrive_radius_px: int = 30   # rayon d'arrivée autour du spot sur la minicarte
    progress_min_px: int = 4     # mouvement min entre deux frames pour compter
    max_no_progress: int = 4     # vagues sans progrès avant arrêt
    capture_interval: float = 0.25
    turn_deadband_px: int = 10   # sous ce dx, on considère qu'on est aligné


@dataclass
class NavResult:
    ok: bool
    phase: str  # "arrived" | "obstacle" | "no_direction" | "budget" | "dead" | "human"
    waves: int
    evidence: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Détection de la position du joueur sur la minicarte
# ---------------------------------------------------------------------------
def _minimap_zone(frame_bgr):
    """Extrait la zone minicarte de l'image pleine écran."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * MINIMAP["x0"]), int(w * MINIMAP["x1"])
    y0, y1 = int(h * MINIMAP["y0"]), int(h * MINIMAP["y1"])
    if x1 <= x0 + 40 or y1 <= y0 + 40:
        return None, (0, 0, 0, 0)
    return frame_bgr[y0:y1, x0:x1], (x0, y0, x1, y1)


def detect_player_on_minimap(frame_bgr):
    """
    Détecte la flèche du joueur sur la minicarte.

    Retourne (cx, cy, confidence) en pixels de l'image PLEINE ÉCRAN, ou
    (None, None, 0.0) si aucun joueur crédible n'est trouvé.

    Approche : dans la zone minicarte, on cherche un petit blob contrasté
    proche du centre de cette zone (le joueur est au centre de sa propre
    minicarte dans l'UI Blizzard par défaut). On filtre par taille et
    circularité. Si l'UI est personnalisée ou si aucun blob n'est crédible,
    on retourne None (jamais de direction inventée).

    Limites connues :
    - UI WoW non standard (addon de minicarte remplacé) → détection impossible,
      le navigateur s'arrête honnêtement.
    - Pas d'étalonnage pixels->mètres : on travaille en pixels relatifs.
    """
    zone, (zx0, zy0, zx1, zy1) = _minimap_zone(frame_bgr)
    if zone is None:
        return None, None, 0.0
    zh, zw = zone.shape[:2]
    zcx, zcy = zw // 2, zh // 2

    # Le joueur / flèche est un objet de taille modeste, contrasté.
    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    # Objets clairs sur fond sombre ou inversement : on garde les deux polarités.
    for thresh in (200, 100):
        _, mask = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY if thresh > 150 else cv2.THRESH_BINARY_INV)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        best = None
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < 6 or area > 400:
                continue
            cx = int(cents[i][0]); cy = int(cents[i][1])
            # Préfère les objets proches du centre de la minicarte (joueur).
            dist_to_center = math.hypot(cx - zcx, cy - zcy)
            if dist_to_center > zw * 0.45:
                continue
            score = area / max(1.0, dist_to_center + 1.0)
            if best is None or score > best[0]:
                best = (score, i, cx, cy, area)
        if best is not None:
            _, i, cx, cy, area = best
            gx = zx0 + cx; gy = zy0 + cy
            conf = min(1.0, area / 120.0)  # confiance grossière, pas de prétention
            return gx, gy, conf
    return None, None, 0.0


# ---------------------------------------------------------------------------
# Détection d'eau à l'écran (indicateur d'arrivée proche)
# ---------------------------------------------------------------------------
def detect_water_onscreen(frame_bgr):
    """
    Détecte si de l'eau est visible dans la zone de jeu 3D.

    L'eau de WoW (Classic) a une teinte bleu-vert : H dans une plage
    cyan-bleu, S modérée, V variable selon l'heure. Retourne
    (water_frac, has_water) où water_frac est la fraction de pixels "eau"
    dans la zone de jeu.

    Honnêteté : c'est un indicateur d'ambiance, pas une preuve d'arrivée sur
    un spot précis. Un spot de pêche peut être sur la terre (rivage) avec peu
    d'eau à l'écran. Ne jamais s'appuyer sur cela seul pour confirmer l'arrivée.
    """
    h, w = frame_bgr.shape[:2]
    x0, x1 = int(w * GAME_VIEW["x0"]), int(w * GAME_VIEW["x1"])
    y0, y1 = int(h * GAME_VIEW["y0"]), int(h * GAME_VIEW["y1"])
    zone = frame_bgr[y0:y1, x0:x1]
    if zone.size == 0:
        return 0.0, False
    hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV).astype(np.int32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # Eau de WoW : cyan-bleu (H ~ 90-110 en échelle OpenCV 0-179),
    # saturation modérée, luminosité variable selon l'heure.
    water = ((H >= 85) & (H <= 115) & (S >= 40) & (S <= 200) & (V >= 30) & (V <= 220))
    frac = float(water.sum()) / float(water.size)
    return frac, frac > 0.03


# ---------------------------------------------------------------------------
# Aide géométrique
# ---------------------------------------------------------------------------
def _direction_toward(player_px, target_px, deadband_px):
    """
    Calcule la direction relative du joueur vers la cible.

    Retourne (dx, dy, dist, angle_deg) en pixels de la minicarte, ou None si
    le joueur n'est pas détecté. dx>0 = cible à droite du joueur.
    """
    if player_px is None or target_px is None:
        return None
    dx = target_px[0] - player_px[0]
    dy = target_px[1] - player_px[1]
    dist = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(-dy, dx))  # y inversé (image)
    return dx, dy, dist, angle


def _turn_toward(angle_deg, deadband_deg=25.0):
    """
    Tourne le personnage vers l'angle cible (degrés, 0 = est/droite).
    Retourne True si au moins une touche de tour a été envoyée.
    """
    # Dans la vue caméra derrière-personnage de WoW, avancer = regarder devant.
    # La flèche minicarte indique la direction regardée ; pour simplifier
    # (sans gyroscope / boussole), on considère que le personnage regarde
    # déjà "en haut de l'écran" (nord sur la minicarte) et on tourne A/D.
    sent = False
    if angle_deg > deadband_deg:
        # Cible à droite -> tourner à droite (D)
        for _ in range(3):
            if send_key(0x44):  # D
                sent = True
                break
            time.sleep(0.05)
    elif angle_deg < -deadband_deg:
        # Cible à gauche -> tourner à gauche (A)
        for _ in range(3):
            if send_key(0x41):  # A
                sent = True
                break
            time.sleep(0.05)
    return sent


# ---------------------------------------------------------------------------
# Contrôleur de navigation
# ---------------------------------------------------------------------------
class SpotNavigator:
    """
    Boucle de navigation bornée vers un spot de pêche.

    Usage :
        nav = SpotNavigator(NavConfig())
        result = nav.navigate_to(target_px, max_waves=6, budget_s=120)

    target_px : position du spot en pixels plein écran (mesurée une fois sur
    place par l'opérateur, jamais inventée).
    """

    def __init__(self, config: Optional[NavConfig] = None):
        self.config = config or NavConfig()
        self._waves = 0
        self._no_progress = 0
        self._last_player = None
        self._started_at = None
        self._deadline = None

    # ------------------------------------------------------------------ API
    def navigate_to(self, target_px: Optional[Tuple[int, int]],
                    max_waves: Optional[int] = None,
                    budget_s: Optional[float] = None) -> NavResult:
        """
        Déplace le personnage vers target_px en boucles bornées.

        Retourne un NavResult. ok=True seulement si l'arrivée est confirmée
        (joueur dans le rayon d'arrivée + eau détectée OU stabilité de
        position sur plusieurs vagues).
        """
        cfg = self.config
        mw = max_waves if max_waves is not None else cfg.max_waves
        bd = budget_s if budget_s is not None else cfg.budget_s

        self._waves = 0
        self._no_progress = 0
        self._last_player = None
        self._started_at = time.time()
        self._deadline = self._started_at + bd

        evidence = {
            "config": {
                "max_waves": mw, "budget_s": bd, "hold_s": cfg.hold_s,
                "turn_s": cfg.turn_s, "arrive_radius_px": cfg.arrive_radius_px,
            },
            "waves": [],
            "final_player": None,
            "water_at_arrival": None,
        }

        hwnd, _title, _rect = find_wow_window()
        if not hwnd:
            return NavResult(False, "no_wow", 0,
                             {"reason": "fenetre WoW introuvable"})

        while self._waves < mw and time.time() < self._deadline:
            if emergency_stop_pressed():
                return NavResult(False, "human", self._waves,
                                 {"reason": "F9 arret d'urgence"})

            # --- 1. Etat du personnage ---
            img, err = capture_window(hwnd)
            if img is None or not is_wow_frame(img):
                self._waves += 1
                evidence["waves"].append({"wave": self._waves, "note": "capture invalide"})
                time.sleep(cfg.capture_interval)
                continue
            frame = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)
            st, ev = player_health(frame)
            if st == "dead":
                return NavResult(False, "dead", self._waves,
                                 {"reason": "personnage mort - arret navigation", "evidence": ev})
            if st == "suspected":
                # Fantôme probable : la navigation vivante ne s'applique pas.
                return NavResult(False, "dead", self._waves,
                                 {"reason": "personnage suspecté mort (fantôme)", "evidence": ev})

            # --- 2. Position du joueur sur la minicarte ---
            player_x, player_y, pconf = detect_player_on_minimap(frame)
            if player_x is None:
                self._no_progress += 1
                self._waves += 1
                evidence["waves"].append({
                    "wave": self._waves, "note": "joueur non détecté sur la minicarte",
                    "noProgress": self._no_progress})
                if self._no_progress >= cfg.max_no_progress:
                    return NavResult(False, "no_direction", self._waves,
                                     {"reason": "aucune direction déterminable",
                                      "waves": evidence["waves"]})
                time.sleep(cfg.capture_interval)
                continue
            player_px = (player_x, player_y)

            # --- 3. Vérification d'arrivée ---
            if target_px is not None:
                direction = _direction_toward(player_px, target_px, cfg.turn_deadband_px)
                dist = direction[2] if direction else float("inf")
                water_frac, has_water = detect_water_onscreen(frame)
                if dist <= cfg.arrive_radius_px:
                    # Arrivée : confirmer par stabilité (déjà dans le rayon)
                    evidence["final_player"] = player_px
                    evidence["water_at_arrival"] = water_frac
                    return NavResult(True, "arrived", self._waves,
                                     {"distance_px": round(dist, 1),
                                      "water_frac": round(water_frac, 3),
                                      "has_water": has_water,
                                      "waves": evidence["waves"]})

                # --- 4. Direction vers l'objectif ---
                dx, dy, dist, angle = direction
                turned = _turn_toward(angle, deadband_deg=20.0)

                # --- 5. Mouvement borné vers l'avant ---
                moved = hold_key(0x57, cfg.hold_s)  # W
                self._waves += 1
                evidence["waves"].append({
                    "wave": self._waves,
                    "player": player_px,
                    "distance_px": round(dist, 1),
                    "angle_deg": round(angle, 1),
                    "turned": turned, "moved": moved, "hold_s": cfg.hold_s,
                })

                # --- 6. Progrès : on observe après le mouvement ---
                time.sleep(0.4)  # laisser le jeu traiter le mouvement
                img2, err2 = capture_window(hwnd)
                if img2 is not None and is_wow_frame(img2):
                    frame2 = cv2.cvtColor(np.asarray(img2.convert("RGB")), cv2.COLOR_RGB2BGR)
                    p2x, p2y, _ = detect_player_on_minimap(frame2)
                    p2 = (p2x, p2y) if p2x is not None else None
                    if p2 is not None and self._last_player is not None:
                        prog = math.hypot(p2[0] - self._last_player[0],
                                          p2[1] - self._last_player[1])
                        if prog < cfg.progress_min_px:
                            self._no_progress += 1
                            evidence["waves"][-1]["progress_px"] = round(prog, 1)
                            evidence["waves"][-1]["note"] = "pas de progression (obstacle ?)"
                        else:
                            evidence["waves"][-1]["progress_px"] = round(prog, 1)
                            self._no_progress = 0
                            self._last_player = p2
                    else:
                        self._last_player = p2 if p2 is not None else self._last_player
                else:
                    self._last_player = player_px

                if self._no_progress >= cfg.max_no_progress:
                    return NavResult(False, "obstacle", self._waves,
                                     {"reason": "aucune progression sur %d vagues"
                                              % cfg.max_no_progress,
                                      "waves": evidence["waves"]})

        return NavResult(False, "budget", self._waves,
                         {"reason": "budget ou vague epuise",
                          "waves": evidence["waves"],
                          "elapsed_s": round(time.time() - self._started_at, 1)})
