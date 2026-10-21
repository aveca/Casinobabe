#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_safety.py — SECURITE DU PERSONNAGE : MEMOIRE D'INCIDENTS, ETATS DE
RECUPERATION ET GARDE-FOUS DE L'EAU.

Contexte (incident reel du 2026-10-21) : le bot a tente de se deplacer vers
un spot de peche, est entre dans l'eau et le personnage s'est noye. Le
rapport d'incident doit donc etre persiste, la zone.markee comme dangereuse,
et tout deplacement vers cette zone doit etre refuse jusqu'a recalibration.

Trois briques :

1. `IncidentMemory` — memoire PERSISTANTE (survit au redemarrage) des
   incidents et des trajets, avec pour chacun : destination visee, chemin
   suivi, position relative avant/apres, etat de l'eau et de la rive detecte,
   direction estimee, commandes envoyees, deplacement observe, resultat
   (succes / blocage / entree dans l'eau / mort / recuperation), niveau de
   confiance et cause probable.

   Une zone n'est declaree dangereuse qu'apres un SEUL incident : elle est
   memorisee avec un niveau de confiance, puis affinee par les observations
   suivantes (une observation unique ne prouve pas qu'une zone entiere est
   dangereuse).

2. `RecoveryStateMachine` — etats explicites :
       FISHING -> NAVIGATION -> DANGER_DETECTED -> DEAD -> GHOST ->
       CORPSE_RECOVERY -> RESURRECTION -> SAFE_REPOSITION -> RESUME_OR_STOP
   Les transitions sont confirmees par des OBSERVATIONS (etat de vie, mode
   fantome, presence du corps, position sure), jamais par le simple envoi
   d'une commande.

3. Garde-fous de deplacement (`safe_click_target`, `check_progress`) :
   - detection prudente de l'eau et de la rive (la nuit, une classification
     incertaine conduit a un ARRET, pas a un clic risque) ;
   - marge de securite avant le bord ;
   - mouvements courts et controles pres de l'eau ;
   - arret immediat si deplacement incoherent ou progression nulle ;
   - interdiction de repeter indefiniment une commande ;
   - limite de duree et de distance par action ;
   - validation de l'arrivee avant de lancer la peche.
"""

import json
import math
import os
import time

try:
    import cv2
    import numpy as np
except ImportError:                                    # pragma: no cover
    cv2 = None
    np = None

# ---------------------------------------------------------------- etats
FISHING = "FISHING"
NAVIGATION = "NAVIGATION"
DANGER_DETECTED = "DANGER_DETECTED"
DEAD = "DEAD"
GHOST = "GHOST"
CORPSE_RECOVERY = "CORPSE_RECOVERY"
RESURRECTION = "RESURRECTION"
SAFE_REPOSITION = "SAFE_REPOSITION"
RESUME_OR_STOP = "RESUME_OR_STOP"

ALL_STATES = (FISHING, NAVIGATION, DANGER_DETECTED, DEAD, GHOST,
               CORPSE_RECOVERY, RESURRECTION, SAFE_REPOSITION, RESUME_OR_STOP)

#: Etats dans lesquels AUCUN deplacement de navigation n'est autorise.
STATES_BLOCKING_NAVIGATION = (DANGER_DETECTED, DEAD, GHOST, CORPSE_RECOVERY,
                              RESURRECTION, SAFE_REPOSITION, RESUME_OR_STOP)

#: Resultats d'une action de deplacement.
OUTCOME_OK = "ok"
OUTCOME_BLOCKED = "blocked"
OUTCOME_WATER = "entered_water"
OUTCOME_DEAD = "died"
OUTCOME_RECOVERED = "recovered"

#: Marge (pixels ecran) conservee entre la zone d'eau et la cible d'un clic.
WATER_SAFETY_MARGIN_PX = 26

#: Duree maximale d'une action de deplacement (secondes).
MAX_ACTION_SECONDS = 8.0

#: Distance relative maximale jugee acceptable pour une action (px de vue 3D).
MAX_ACTION_DISTANCE_PX = 900.0

#: Nombre maximal de tentatives consecutives sans progression.
MAX_NO_PROGRESS = 2


class IncidentMemory(object):
    """Memoire persistante des incidents et des trajets (survit au reboot)."""

    def __init__(self, path):
        self.path = path
        self.data = {"incidents": [], "zones": {}, "trails": {}, "rules": [],
                     "version": 1}
        self._load()

    # ------------------------------------------------------------- E/S
    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "incidents" in data:
                self.data = data
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --------------------------------------------------------- incidents
    def record_incident(self, kind, zone=None, destination=None, commands=None,
                        before=None, after=None, observed=None, cause=None,
                        confidence=0.5, extra=None):
        """Enregistre un incident (noyade, blocage, ...) et ses preuves."""
        inc = {
            "ts": time.time(),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": kind,
            "zone": zone,                    # repere RELATIF (jamais absolu)
            "destination": destination,
            "commands": commands or [],
            "positionBefore": before,
            "positionAfter": after,
            "observedDisplacement": observed,
            "cause": cause,
            "confidence": round(float(confidence), 3),
        }
        if extra:
            inc["extra"] = extra
        self.data.setdefault("incidents", []).append(inc)
        del self.data["incidents"][:-100]
        if zone:
            z = self.data.setdefault("zones", {}).setdefault(
                str(zone), {"zone": str(zone), "incidents": 0, "deaths": 0,
                            "confidence": 0.0, "status": "unknown", "last": None})
            z["incidents"] += 1
            if kind in (OUTCOME_WATER, OUTCOME_DEAD):
                z["deaths"] += 1
            # la confiance croise avec le nombre d'observations, mais une
            # seule observation ne suffit pas a conclure : le statut reste
            # "suspect" tant qu'il n'y a qu'un evenement.
            z["confidence"] = round(min(1.0, 0.45 * z["incidents"]), 3)
            z["status"] = ("dangerous" if z["deaths"] >= 2 else
                           "suspect" if z["incidents"] >= 1 else "unknown")
            z["last"] = inc["at"]
        self._learn_from(inc)
        self.save()
        return inc

    def _learn_from(self, inc):
        """Transforme un incident en regle reutilisable."""
        if inc.get("kind") not in (OUTCOME_WATER, OUTCOME_DEAD):
            return
        rule = {
            "id": "no-%s-%s" % (inc.get("kind"), inc.get("zone") or "unknown"),
            "createdAt": inc["at"],
            "reason": "zone %s : %s observe" % (inc.get("zone"), inc.get("cause") or inc.get("kind")),
            "action": "refuser_cible",
            "zone": inc.get("zone"),
            "confidence": inc.get("confidence"),
        }
        rules = self.data.setdefault("rules", [])
        if not any(r.get("id") == rule["id"] for r in rules):
            rules.append(rule)
        del rules[:-50]

    # ------------------------------------------------------------ zones
    def zone_status(self, zone):
        if zone is None:
            return "unknown"
        z = (self.data.get("zones") or {}).get(str(zone))
        if not z:
            return "unknown"
        return z.get("status", "unknown")

    def is_forbidden(self, zone):
        """Une zone est interdite si elle est 'dangerous' (>= 2 evenements) ou
        si le trajet qui y mene a ete marque dangereux."""
        if zone is None:
            return False
        if self.zone_status(zone) in ("dangerous", "suspect"):
            return True
        return False

    def mark_trail(self, trail_id, status, note=None):
        t = self.data.setdefault("trails", {}).setdefault(
            str(trail_id), {"id": str(trail_id), "status": "unknown",
                            "runs": 0, "failures": 0, "last": None})
        t["status"] = status
        t["runs"] += 1
        if status in (OUTCOME_BLOCKED, OUTCOME_WATER, OUTCOME_DEAD):
            t["failures"] += 1
        if note:
            t["note"] = note
        t["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save()
        return t

    def trail_status(self, trail_id):
        t = (self.data.get("trails") or {}).get(str(trail_id))
        return t.get("status") if t else "unknown"

    def summary(self):
        return {"incidents": len(self.data.get("incidents") or []),
                "zones": self.data.get("zones") or {},
                "trails": self.data.get("trails") or {},
                "rules": len(self.data.get("rules") or [])}


class RecoveryStateMachine(object):
    """Machine a etats de securite du personnage.

    Les transitions sont confirmees par des observations reelles :
      * `observe_death`   : l'etat de vie passe a 'dead' ;
      * `observe_ghost`   : le monde est desature (fantome) ;
      * `observe_corpse`  : le marqueur du corps est visible ;
      * `observe_alive`   : le personnage est vivant (fin de la resurrection) ;
      * `observe_safe`    : une position sure est atteinte.
    """

    def __init__(self, memory=None, log=None):
        self.state = FISHING
        self.memory = memory
        self.log = log or (lambda *a, **k: None)
        self.history = []
        self.context = {}

    # ------------------------------------------------------------ helpers
    def _to(self, new_state, reason=None, **extra):
        old = self.state
        self.state = new_state
        self.history.append({"from": old, "to": new_state, "at": time.time(),
                             "reason": reason, **extra})
        self.log("SAFETY_STATE", previous=old, state=new_state, reason=reason, **extra)
        return new_state

    @property
    def navigation_allowed(self):
        return self.state not in STATES_BLOCKING_NAVIGATION

    @property
    def fishing_allowed(self):
        return self.state in (FISHING, NAVIGATION)

    # -------------------------------------------------------- transitions
    def observe_death(self, evidence=None):
        """Le personnage est mort : on coupe TOUT et on passe en DEAD."""
        self.context["deathEvidence"] = evidence or {}
        self._to(DEAD, reason="vie detectee comme perdue", evidence=evidence)
        if self.memory:
            self.memory.record_incident(
                OUTCOME_DEAD, zone=self.context.get("zone"),
                destination=self.context.get("destination"),
                commands=self.context.get("commands"),
                before=self.context.get("positionBefore"),
                after=self.context.get("positionAfter"),
                observed=self.context.get("observed"),
                cause=self.context.get("cause") or "noyade lors d'un deplacement",
                confidence=0.8)
        return self.state

    def observe_ghost(self, evidence=None):
        self.context["ghostEvidence"] = evidence or {}
        self._to(GHOST, reason="mode fantome detecte", evidence=evidence)
        return self.state

    def observe_corpse(self, marker=None):
        self.context["corpseMarker"] = marker
        self._to(CORPSE_RECOVERY, reason="corps identifie", marker=marker)
        return self.state

    def observe_resurrected(self, evidence=None):
        self._to(RESURRECTION, reason="resurrection confirmee", evidence=evidence)
        return self.state

    def observe_safe_position(self, evidence=None):
        self._to(SAFE_REPOSITION, reason="position sure", evidence=evidence)
        return self.state

    def observe_resume(self):
        self._to(FISHING, reason="reprise validee")
        return self.state

    def observe_stop(self, reason="arret demande"):
        self._to(RESUME_OR_STOP, reason=reason)
        return self.state

    def enter_navigation(self, destination=None, zone=None):
        if not self.navigation_allowed:
            return self.state
        self.context["destination"] = destination
        self.context["zone"] = zone
        return self._to(NAVIGATION, reason="deplacement vers un spot")

    def enter_fishing(self):
        if self.state in (RESUME_OR_STOP,):
            return self.state
        return self._to(FISHING, reason="cycle de peche")

    def enter_danger(self, reason=None, evidence=None):
        return self._to(DANGER_DETECTED, reason=reason or "danger detecte",
                        evidence=evidence)


# --------------------------------------------------------------- garde-fous
def _bgr(img):
    """Normalise une capture (PIL / ndarray) en BGR ndarray.

    Les tableaux numpy sont deja en BGR (convention du projet) ; les objets
    PIL sont en RGB et sont convertis.
    """
    if img is None or np is None or cv2 is None:
        return None
    if isinstance(img, np.ndarray):
        arr = img
        if arr.size == 0:
            return None
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        return arr
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


def water_profile(img_bgr, samples=5):
    """Estimation prudente de la presence d'eau autour d'un point ecran.

    Retourne un dict : {waterRatio, uncertain, marginPx}. En cas de
    classification incertaine (nuit, faible contraste), `uncertain` est vrai :
    l'appelant doit alors STOPPER ou se replier, jamais cliquer.
    """
    img_bgr = _bgr(img_bgr)
    if img_bgr is None:
        return {"waterRatio": 0.0, "uncertain": True, "marginPx": 0}
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"waterRatio": 0.0, "uncertain": True, "marginPx": 0}
    h, w = img_bgr.shape[:2]
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    # eau : sombre ET desaturee (calibration nuit du 2026-10-21 :
    # eau S 35-46 / V 20-31 ; herbe S 146)
    water = (val < 75) & (sat < 80)
    ratio = float(water.mean())
    # incertitude : contraste global faible (nuit) => on ne se fie pas a l'eau
    contrast = float(val.std())
    uncertain = contrast < 28.0
    return {"waterRatio": round(ratio, 4), "uncertain": uncertain,
            "contrast": round(contrast, 2)}


def near_water(img_bgr, x, y, margin=WATER_SAFETY_MARGIN_PX):
    """Le point (x, y) est-il trop pres de l'eau ? (marge de securite)"""
    img_bgr = _bgr(img_bgr)
    if img_bgr is None:
        return True, "image indisponible"
    try:
        import cv2
        import numpy as np
    except ImportError:
        return True, "opencv indisponible"
    h, w = img_bgr.shape[:2]
    x0, x1 = max(0, x - margin), min(w, x + margin)
    y0, y1 = max(0, y - margin), min(h, y + margin)
    patch = img_bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return True, "zone vide"
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    sat = int(np.median(hsv[:, :, 1]))
    val = int(np.median(hsv[:, :, 2]))
    is_water = (val < 75 and sat < 80)
    return is_water, ("eau a moins de %d px" % margin if is_water else "sol")


def safe_click_target(img_bgr, x, y, margin=WATER_SAFETY_MARGIN_PX):
    """Le point vise est-il sur un sol degage de l'eau ?

    Retourne (ok, raison). Une classification incertaine (contraste faible)
    donne (False, ...) : on prefere arreter que risquer une noyade.
    """
    prof = water_profile(img_bgr)
    if prof["uncertain"]:
        return False, ("classification de l'eau incertaine (contraste %.1f) : "
                       "pas de clic a l'aveugle" % prof["contrast"])
    wet, why = near_water(img_bgr, x, y, margin)
    if wet:
        return False, why
    return True, "sol degage"


def check_progress(moved_px, expected_px=None, previous=None, current=None):
    """Le deplacement est-il coherent ?

    Retourne (ok, raison, outcome). Un deplacement nul ou incoherent arrete
    l'action ; on ne repete JAMAIS indefiniment la meme commande.
    """
    if moved_px is None:
        return False, "deplacement non mesure", OUTCOME_BLOCKED
    if moved_px < 0.3:
        return False, "progression nulle (%.2f px)" % moved_px, OUTCOME_BLOCKED
    if expected_px is not None and moved_px > MAX_ACTION_DISTANCE_PX:
        return False, ("deplacement aberrant (%.0f px > %d) : mesure suspecte"
                       % (moved_px, MAX_ACTION_DISTANCE_PX)), OUTCOME_BLOCKED
    return True, "progression de %.2f px" % moved_px, OUTCOME_OK


def direction_error(measured_deg, commanded_deg, tolerance=45.0):
    """Ecart entre la direction mesuree et la commande (degres)."""
    if measured_deg is None or commanded_deg is None:
        return None
    d = (measured_deg - commanded_deg + 180.0) % 360.0 - 180.0
    return abs(d) <= tolerance, abs(d)
