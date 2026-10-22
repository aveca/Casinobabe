#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_process.py — MOTEUR D'ACTION UNIQUE POUR LE CLIENT WoW.

Pourquoi ce module existe (retour d experience de la session du 2026-10-21) :

* chaque besoin en jeu recreait un script jetable avec sa propre capture
  d'ecran -> lent et incoherent.  Ici : UN cache d'ecran partage (`Screen`)
  et UN tableau de bord partage (`Blackboard`) ;
* les commandes etaient envoyees a l'aveugle (bascule Z "au hasard"), d'ou
  des directions incoherentes.  Ici : le moteur CONNAIT l'etat de
  l'auto-run, ne bascule qu'apres MESURE, et verifie chaque deplacement ;
* aucune preuve n'etait conservee.  Ici : chaque tentative est journalisee
  (`ActionLog`) et les recettes qui reussissent sont APPRISES
  (`ProcedureMemory`) ;
* le lecteur d'etat disait "alive" sur l'ecran de mort.  Ici : plusieurs
  signaux independants sont combines (`StateReader`) et l'etat porte une
  confiance ;
* la navigation a continue apres une noyade.  Ici : `Executor` applique les
  garde-fous de `fishing_safety` (eau, rive, marge, duree, distance,
  progression) AVANT toute action.

Le module est concu pour etre testable hors jeu : toute la couche client
est injectee (`actions`), ce qui permet a `tests/test_fishing_process.py` de
simuler un client WoW complet.
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

try:
    from fishing_safety import (FISHING, NAVIGATION, DANGER_DETECTED, DEAD,
                                GHOST, CORPSE_RECOVERY, RESURRECTION,
                                SAFE_REPOSITION, RESUME_OR_STOP,
                                STATES_BLOCKING_NAVIGATION, OUTCOME_OK,
                                OUTCOME_BLOCKED, OUTCOME_WATER, OUTCOME_DEAD,
                                OUTCOME_RECOVERED, IncidentMemory,
                                RecoveryStateMachine, safe_click_target,
                                check_progress, direction_error, near_water,
                                WATER_SAFETY_MARGIN_PX, MAX_ACTION_SECONDS,
                                MAX_ACTION_DISTANCE_PX, MAX_NO_PROGRESS)
except ImportError:                                    # pragma: no cover
    raise

# --------------------------------------------------------------------------
# 1. ECRAN : une seule capture par tick, partagee par tous les lecteurs
# --------------------------------------------------------------------------
def as_bgr(img):
    """Normalise une capture en BGR ndarray.

    Convention du projet : les tableaux numpy sont DEJA en BGR (comme les
    images OpenCV) et ne sont donc pas permutes ; les objets PIL sont en RGB
    et sont convertis.
    """
    if img is None:
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


class Screen(object):
    """Cache de capture partage (1 capture -> N lecteurs)."""

    def __init__(self, capture_fn, max_age=0.10):
        self._capture = capture_fn
        self._max_age = max_age
        self._img = None
        self._ts = 0.0

    def grab(self, force=False):
        now = time.time()
        if (not force and self._img is not None
                and (now - self._ts) <= self._max_age):
            return self._img
        img = self._capture()
        if img is None:
            return None
        self._img = img
        self._ts = now
        return img

    def invalidate(self):
        self._img = None
        self._ts = 0.0


# --------------------------------------------------------------------------
# 2. TABLEAU DE BORD : l'etat partage entre les etapes
# --------------------------------------------------------------------------
class Blackboard(object):
    """Etat partage : ce que le moteur sait (et ne devine pas)."""

    def __init__(self):
        self.auto_run = None       # None = inconnu, True/False = connu
        self.last_dx = None
        self.last_dy = None
        self.last_px = None
        self.last_angle = None
        self.zone = None
        self.target = None
        self.life = "unknown"
        self.flags = {}
        self.notes = []

    def note(self, text):
        self.notes.append({"at": time.time(), "note": text})
        del self.notes[:-100]

    def snapshot(self):
        return {"autoRun": self.auto_run, "life": self.life, "zone": self.zone,
                "target": self.target, "lastPx": self.last_px,
                "lastAngle": self.last_angle}


# --------------------------------------------------------------------------
# 3. LECTEUR D'ETAT : plusieurs signaux, une conclusion, une confiance
# --------------------------------------------------------------------------
class StateReader(object):
    """Lit l'etat REEL du personnage a partir de signaux independants.

    Signaux :
      * dialogue de liberation (bouton rouge "Release Spirit" + parchemin) ;
      * monde desature + silhouette fantomatique (mode fantome) ;
      * pixels rouges du bouton (dialogue de mort) ;
      * mouvement (deplacement mesure entre deux captures).
    """

    def __init__(self, red_button_fn=None, ghost_fn=None, facing_fn=None,
                 displacement_fn=None):
        self._red_button = red_button_fn
        self._ghost = ghost_fn
        self._facing = facing_fn
        self._disp = displacement_fn

    # -- signaux individuels ------------------------------------------
    def release_dialog(self, img):
        """Le dialogue de liberation est-il affiche ?

        Le bouton "Release Spirit" est une BARRE ROUGE DESATUREE (mesure :
        H=0, S=0, V=42-119) posee sur le panneau SOMBRE du dialogue de mort.
        Un simple comptage de pixels rouges confondait avec le texte rouge du
        jeu ("You can't chat when you're dead!") : on exige donc

          1. une barre horizontale compacte (ratio 2 a 9) ;
          2. un teinte rouge (R > G et R > B) ;
          3. un environnement SOMBRE (le panneau du dialogue), ce qui ecarte
             la terre ou la vegetation rouge en plein jour.
        """
        if self._red_button is not None:
            return self._red_button(img)
        if img is None or cv2 is None:
            return None
        bgr = as_bgr(img)
        if bgr is None:
            return None
        # la carte du monde n'est pas une scene de jeu : elle a elle aussi un
        # bouton ROUGE ("Zoom Out"), qui faisait passer la capture pour un
        # ecran de mort (faux positif observe le 2026-10-21).
        try:
            from fishing_map import map_is_open
            if map_is_open(bgr):
                return False
        except Exception:
            pass
        h, w = bgr.shape[:2]
        b, g, r = (bgr[:, :, 0].astype(np.int16), bgr[:, :, 1].astype(np.int16),
                   bgr[:, :, 2].astype(np.int16))
        val = bgr[:, :, 2]
        redish = ((r - np.maximum(g, b)) > 10) & (val > 60)
        band = np.zeros_like(redish)
        band[:int(h * 0.45), :] = True
        redish = redish & band
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats(
            redish.astype(np.uint8), 8)
        for i in range(1, n):
            x, y, cw, ch, area = stats[i]
            if area < 120 or cw < 40 or ch < 8 or cw > 320:
                continue
            if not (2.0 <= float(cw) / max(1, ch) <= 9.0):
                continue
            if area < 0.35 * cw * ch:
                continue
            # environnement sombre (le panneau du dialogue)
            x0 = max(0, x - cw)
            x1 = min(w, x + 2 * cw)
            y0 = max(0, y - ch)
            y1 = min(h, y + 2 * ch)
            around = val[y0:y1, x0:x1]
            if around.size == 0:
                continue
            if float(np.median(around)) > 70.0:
                continue          # plein jour : ce n'est pas le dialogue
            return True
        return False

    def ghost_mode(self, img):
        """Le personnage est-il en mode FANTOME ?

        Mesures reelles (2026-10-21) sur la zone monde :

            fantome      : fracCyan = 0,95-0,96   Vmed = 209-214
            vivant (nuit): fracCyan = 0,39         Vmed = 38
            ecran de mort: fracCyan = 0,53         Vmed = 23

        La vue fantome est un rendu monochrome CYAN : la proportion de
        pixels cyan dans la zone monde, combinee a la luminance, est un
        discriminant robuste (la seule saturation mediane ne l'est pas :
        le cyan fantome est tres sature, 84).
        """
        if self._ghost is not None:
            return self._ghost(img)
        if img is None or cv2 is None:
            return None
        bgr = as_bgr(img)
        if bgr is None:
            return None
        h, w = bgr.shape[:2]
        region = bgr[int(h * 0.06):int(h * 0.55), int(w * 0.10):int(w * 0.85)]
        if region.size == 0:
            return None
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        cyan = float(((hue >= 90) & (hue <= 118) & (sat > 40)).mean())
        vmed = float(np.median(val))
        self._ghost_stats = {"cyanRatio": round(cyan, 3),
                             "valueMedian": round(vmed, 1)}
        return cyan > 0.75 and vmed > 120.0

    # -- lecture complete ---------------------------------------------
    def read(self, img, board=None):
        sig = {}
        dlg = self.release_dialog(img)
        sig["releaseDialog"] = bool(dlg) if dlg is not None else None
        gh = self.ghost_mode(img)
        sig["ghostTint"] = bool(gh) if gh is not None else None
        if getattr(self, "_ghost_stats", None):
            sig["ghostStats"] = self._ghost_stats

        life, confidence, why = "unknown", 0.0, []
        # PRECEDENCE : le mode fantome est un signal visuel certain (vue
        # monochrome cyan, 96 % de pixels cyan) : il prime sur la detection
        # du dialogue de liberation, dont le bouton rouge peut ressembler a
        # du bois sombre dans une vue fantome.
        if sig["ghostTint"]:
            life, confidence = "ghost", 0.90
            why.append("vue fantome (monochrome cyan)")
        elif sig["releaseDialog"]:
            life, confidence = "dead", 0.95
            why.append("dialogue de liberation affiche")
        else:
            # vivant par defaut : les signaux de mort (dialogue de
            # liberation, mode fantome) sont explicitement exclus ci-dessus.
            life, confidence = "alive", 0.55
            why.append("aucun signal de mort/fantome")
        # "alive" avec une confiance faible est un simple fait : ce n'est
        # PAS un motif pour bloquer (le personnage vit la nuit).
        sig["suspect"] = False

        if self._facing is not None and img is not None:
            try:
                f = self._facing(img)
            except Exception:
                f = None
            sig["facingDeg"] = None if f is None else math.degrees(f)
        else:
            sig["facingDeg"] = None

        state = {"life": life, "confidence": confidence, "signals": sig,
                 "why": why}
        if board is not None:
            board.life = life
            if sig.get("facingDeg") is not None:
                board.last_angle = sig["facingDeg"]
        return state


# --------------------------------------------------------------------------
# 4. JOURNAL + MEMOIRE DES RECETTES (apprentissage)
# --------------------------------------------------------------------------
class ActionLog(object):
    """Journal persistant : chaque tentative, avec sa MESURE de resultat."""

    def __init__(self, path):
        self.path = path
        self.entries = []

    def record(self, kind, params=None, outcome=None, measured=None,
              note=None):
        e = {"ts": time.time(), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "kind": kind, "params": params or {}, "outcome": outcome,
             "measured": measured, "note": note}
        self.entries.append(e)
        del self.entries[:-500]
        if self.path:
            try:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return e


class ProcedureMemory(object):
    """Recettes apprises : nom -> etapes + statistiques de reussite.

    Le moteur reutilise la recette qui a le meilleur score : la 2e execution
    d'une action est donc immediate au lieu d'etre rediscovery.
    """

    def __init__(self, path):
        self.path = path
        self.data = {"procedures": {}}
        self._load()

    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "procedures" in data:
                self.data = data
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.path:
            return
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def remember(self, name, steps, outcome):
        p = self.data["procedures"].setdefault(
            name, {"name": name, "steps": steps, "ok": 0, "ko": 0,
                   "last": None, "variants": []})
        if steps and steps not in p["variants"]:
            p["variants"].append(steps)
            del p["variants"][:-10]
        if outcome == OUTCOME_OK:
            p["ok"] += 1
        else:
            p["ko"] += 1
        p["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        p["score"] = round(p["ok"] / float(max(1, p["ok"] + p["ko"])), 3)
        self.save()
        return p

    def get(self, name):
        return self.data["procedures"].get(name)

    def best(self, name):
        """Meilleure recette connue pour ce nom (la plus confirmee)."""
        p = self.get(name)
        if not p:
            return None
        return p.get("steps") or (p.get("variants") or [None])[0]

    def stats(self, name=None):
        if name:
            p = self.get(name)
            return dict(p) if p else None
        return {k: {"ok": v.get("ok", 0), "ko": v.get("ko", 0),
                    "score": v.get("score", 0.0)}
                for k, v in self.data["procedures"].items()}


# --------------------------------------------------------------------------
# 5. EXECUTEUR : agir -> mesurer -> verifier -> decider (essais bornes)
# --------------------------------------------------------------------------
class Analyzer(object):
    """Analyse apres CHAQUE action : verdict + post-mortem exploitable.

    Le principe impose : on ne clique pas en boucle sans analyser ce qui a
    ete fait ou rate.  Apres chaque commande, l'analyseur produit un
    VERDICT structure :

        ok | no_progress | wrong_direction | blocked | state_changed |
        uncertain

    et ecrit un "post-mortem" (capture + resume de la decision) dans un
    dossier : c'est ce materiau qu'un agent (ou un LLM local, si configure)
    peut relire pour corriger la tache programmee.  Aucun appel reseau
    n'est fait par defaut (contrainte : solution locale et gratuite).
    """

    VERDICTS = ("ok", "no_progress", "wrong_direction", "blocked",
                "state_changed", "uncertain")

    def __init__(self, log=None, out_dir=None, memory=None):
        self.log = log or ActionLog(None)
        self.out_dir = out_dir
        self.memory = memory
        self.last_verdict = None
        self.history = []

    def analyze(self, img=None, action=None, measured=None, state=None,
                expected=None, tolerance=45.0):
        """Verdict sur la derniere action. Retourne le dict de verdict."""
        px = (measured or {}).get("px")
        angle = (measured or {}).get("angle")
        verdict = "uncertain"
        reasons = []
        if state and state.get("life") and state.get("life") != expected:
            verdict = "state_changed"
            reasons.append("etat de vie : %s" % state.get("life"))
        elif px is None:
            verdict = "uncertain"
            reasons.append("deplacement non mesure")
        elif px < 0.3:
            verdict = "no_progress"
            reasons.append("progression nulle (%.2f px)" % px)
        elif angle is not None and expected is not None and \
                isinstance(expected, (int, float)) and \
                abs((angle - expected + 180.0) % 360.0 - 180.0) > tolerance:
            verdict = "wrong_direction"
            reasons.append("ecart de direction %.0f deg" %
                           abs((angle - expected + 180.0) % 360.0 - 180.0))
        else:
            verdict = "ok"
            reasons.append("progression %.1f px" % px)
        entry = {"verdict": verdict, "reasons": reasons, "action": action,
                 "measured": measured, "state": (state or {}).get("life"),
                 "at": time.time()}
        self.last_verdict = entry
        self.history.append(entry)
        del self.history[:-200]
        self.log.record("ANALYSIS", action, verdict, measured, "; ".join(reasons))
        self._postmortem(img, entry)
        return entry

    def _postmortem(self, img, entry):
        """Ecrit la capture + le verdict pour relecture (IA comprise)."""
        if not self.out_dir:
            return None
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            name = "pm-%s-%s" % (stamp, entry.get("verdict"))
            if img is not None:
                try:
                    import cv2
                    bgr = as_bgr(img)
                    if bgr is not None:
                        cv2.imwrite(os.path.join(self.out_dir, name + ".png"), bgr)
                except Exception:
                    pass
            with open(os.path.join(self.out_dir, name + ".json"), "w",
                      encoding="utf-8") as fh:
                json.dump(entry, fh, indent=2, ensure_ascii=False, default=str)
            return name
        except OSError:
            return None


class Executor(object):
    """Execute un plan etat par etat.

    APRES CHAQUE ACTION, l'etat du client est VALIDE par une capture
    (exigence du 2026-10-21) : un panneau ouvert (sac, amis, fiche
    personnage), la carte du monde, ou une capture invalide sont detectes
    et REPARES ; la suite n'est autorisee que si la vue de jeu est
    propre.  Chaque verdict est journalise : le moteur peut ainsi
    s'auto-diagnostiquer a partir des logs, sans intervention humaine.
    """

    def __init__(self, screen, state, actions, log=None, memory=None,
                 board=None, safety=None, validator=None):
        self.screen = screen
        self.state = state
        self.actions = actions            # adaptateur client (injecte)
        self.log = log or ActionLog(None)
        self.memory = memory
        self.board = board or Blackboard()
        self.safety = safety or RecoveryStateMachine(log=None)
        self.validator = validator
        self.diagnostics = []

    # -- primitives ----------------------------------------------------
    def _measure(self, before):
        """Deplacement mesure (px) entre deux images de la vue 3D."""
        after = self.screen.grab(force=True)
        if after is None:                     # capture ratee : on reessaye
            time.sleep(0.2)
            after = self.screen.grab(force=True)
        if before is None or after is None:
            return None, None
        fn = getattr(self.actions, "displacement", None)
        if fn is None:
            return None, None
        dx, dy, conf = fn(before, after)
        return (dx, dy), math.hypot(dx, dy)

    def _angle(self, before, after):
        fn = getattr(self.actions, "angle", None)
        if fn is None:
            return None
        return fn(before, after)

    # -- etat "goto" : le deplacement fiable, verifie --------------------
    def do_goto(self, at, min_px=0.3, tries=2, direction_tol=70.0,
                unsafe="stop", max_px=MAX_ACTION_DISTANCE_PX, settle=1.2):
        """Deplace le personnage vers une cible ecran, avec garde-fous.

        Protocole (corrige apres mesures reelles du 2026-10-21) :
          1. une SEULE capture avant ;
          2. controle de securite (eau/rive) AVANT tout clic ;
          3. la cible est REPLACEE dans la vue monde : un clic sur l'UI
             (sac, amis, fiche personnage) ouvre un PANNEAU au lieu de
             deplacer le personnage (constat : "il a ouvert les sacs") ;
          4. l'auto-run est remis dans un etat CONNU (rien n'est suppose) ;
          5. clic, puis MESURE du deplacement ;
          6. si rien n'a bouge : UN essai de plus (re-armement + re-clic) ;
          7. VALIDATION de l'ecran apres l'action (panneau ouvert ? carte
             ouverte ?) : le moteur se repare seul, sans intervention.
        """
        from fishing_screen import clamp_to_world, in_world_zone, looks_like_ui
        img0 = as_bgr(self.screen.grab(force=True))
        h0 = img0.shape[0] if img0 is not None else 1328
        w0 = img0.shape[1] if img0 is not None else 2345
        x, y = int(at[0]), int(at[1])
        if not in_world_zone(x, y, w0, h0):
            x, y = clamp_to_world(x, y, w0, h0)
            self.log.record("goto", {"at": [x, y], "clamped": True},
                            OUTCOME_OK, None,
                            "cible ramenee dans la vue monde")
        # Un clic sur un PANNEAU (sac, amis, fiche personnage) ouvre le
        # panneau au lieu de deplacer le personnage : on refuse donc les
        # surfaces plates (interface) et on decale la cible.
        if img0 is not None and looks_like_ui(img0, x, y):
            for radius in (140, 220, 320):
                for sign in (1, -1):
                    nx = int(x + sign * radius)
                    ny = int(y - radius // 3)
                    if in_world_zone(nx, ny, w0, h0) and \
                            not looks_like_ui(img0, nx, ny):
                        x, y = nx, ny
                        break
                else:
                    continue
                break
            self.log.record("goto", {"at": [x, y], "ui_avoided": True},
                            OUTCOME_OK, None,
                            "cible deplacee hors d'un panneau d'interface")
        before = img0
        last = None
        for attempt in range(1, max(1, int(tries)) + 1):
            if before is not None and unsafe != "allow":
                ok, why = safe_click_target(before, x, y)
                if not ok:
                    self.log.record("goto", {"at": [x, y], "attempt": attempt},
                                    OUTCOME_WATER, None, why)
                    return {"ok": False, "outcome": OUTCOME_WATER, "reason": why}
            # AUTO-RUN : protocole EMPIRIQUE (etat initial inconnu).
            # Un clic NE RE-CIBLE PAS un auto-run deja actif -> le
            # personnage continuait vers son ANCIENNE cible (donc vers le
            # mur).  Protocole : clic -> MESURE -> si rien ne bouge,
            # re-armer l'auto-run et re-cliquer (2 tentatives max).
            # (bail de focus obligatoire : sans focus reel, 0,00 px.)
            self.actions.click(x, y, button="right")
            time.sleep(settle)
            disp, px = self._measure(before)
            if (px is not None and px < 0.3) and attempt < tries:
                self.actions.key("Z")           # re-arme l'auto-run
                self.board.auto_run = None      # etat redevenu inconnu
                time.sleep(0.45)
                self.actions.click(x, y, button="right")
                time.sleep(settle)
                disp, px = self._measure(before)
            if px is not None:
                self.board.last_dx, self.board.last_dy = disp
                self.board.last_px = px
            ang = self._angle(before, self.screen.grab(force=True))
            if ang is not None:
                self.board.last_angle = ang
            ok, reason, outcome = check_progress(px, None, before, None)
            if ok and px is not None and px > float(max_px):
                ok, reason, outcome = (False,
                                       "deplacement aberrant (%.0f px)" % px,
                                       OUTCOME_BLOCKED)
            verdict = self._validate_after_action()
            if ok:
                self.log.record("goto", {"at": [x, y], "attempt": attempt},
                                OUTCOME_OK, {"px": round(px, 2), "angle": ang,
                                            "screen": verdict})
                return {"ok": True, "outcome": OUTCOME_OK, "px": px,
                        "angle": ang, "reason": reason, "screen": verdict}
            last = {"ok": False, "outcome": outcome, "reason": reason,
                    "px": px, "screen": verdict}
            self.log.record("goto", {"at": [x, y], "attempt": attempt},
                            outcome,
                            {"px": None if px is None else round(px, 2),
                             "screen": verdict}, reason)
            if attempt < tries:
                # on/off pour re-cibler (un clic ne re-cible pas l'auto-run)
                self.actions.key("Z")
                self.board.auto_run = None
                time.sleep(0.3)
        return last or {"ok": False, "outcome": OUTCOME_BLOCKED,
                        "reason": "aucune tentative"}

    def _validate_after_action(self):
        """Verifie (et repare) l'etat du client apres une action."""
        if self.validator is None:
            return "no_validator"
        res = self.validator.validate(expected="world")
        verdict = res.get("verdict", "?")
        if verdict != "ok":
            self.diagnostics.append({"verdict": verdict, "at": time.time()})
            del self.diagnostics[:-50]
        return verdict

    # -- etat "click" / "key" / "wait" ---------------------------------
    def do_click(self, at, button="left"):
        x, y = int(at[0]), int(at[1])
        self.actions.click(x, y, button=button)
        self.log.record("click", {"at": [x, y], "button": button}, OUTCOME_OK)
        return {"ok": True, "outcome": OUTCOME_OK}

    def do_key(self, key):
        self.actions.key(key)
        if key == "Z":
            self.board.auto_run = not bool(self.board.auto_run)
        self.log.record("key", {"key": key}, OUTCOME_OK)
        return {"ok": True, "outcome": OUTCOME_OK}

    def do_wait(self, sec):
        time.sleep(float(sec))
        self.log.record("wait", {"sec": sec}, OUTCOME_OK)
        return {"ok": True, "outcome": OUTCOME_OK}

    def do_expect(self, spec):
        img = self.screen.grab(force=True)
        st = self.state.read(img, self.board)
        ok = True
        reason = ""
        if "life" in spec and spec["life"] != "unknown":
            ok = st["life"] == spec["life"]
            reason = "vie=%s (attendu %s, confiance %.2f)" % (
                st["life"], spec["life"], st["confidence"])
        if ok and spec.get("no_release_dialog"):
            dlg = st["signals"].get("releaseDialog")
            ok = not dlg
            reason = "dialogue de liberation absent=%s" % (not dlg)
        outcome = OUTCOME_OK if ok else OUTCOME_BLOCKED
        self.log.record("expect", spec, outcome, st["signals"], reason)
        return {"ok": ok, "outcome": outcome, "reason": reason, "state": st}

    # -- execution d'un plan -------------------------------------------
    def run(self, steps, stop_on_failure=True):
        results = []
        for step in steps or []:
            t = step.get("type")
            if t == "goto":
                r = self.do_goto(step["at"], step.get("min_px", 0.3),
                                 step.get("tries", 2),
                                 step.get("direction_tol", 70.0))
            elif t == "click":
                r = self.do_click(step["at"], step.get("button", "left"))
            elif t == "key":
                r = self.do_key(step["key"])
            elif t == "wait":
                r = self.do_wait(step.get("sec", 1.0))
            elif t == "expect":
                r = self.do_expect(step)
            else:
                r = {"ok": False, "outcome": OUTCOME_BLOCKED,
                     "reason": "type d etat inconnu: %r" % t}
            r["step"] = step
            results.append(r)
            if not r.get("ok") and stop_on_failure:
                break
        ok = all(r.get("ok") for r in results)
        return {"ok": ok, "results": results,
                "outcome": OUTCOME_OK if ok else
                next((r.get("outcome") for r in results
                      if not r.get("ok")), OUTCOME_BLOCKED)}

    # -- procedures apprises -------------------------------------------
    def run_procedure(self, name, steps=None):
        """Execute la recette apprise (ou la variante fournie)."""
        plan = steps or self.memory.best(name) if self.memory else steps
        if not plan:
            return {"ok": False, "outcome": OUTCOME_BLOCKED,
                    "reason": "procedure inconnue: %s" % name}
        res = self.run(plan)
        if self.memory:
            self.memory.remember(name, plan,
                                 OUTCOME_OK if res["ok"] else res["outcome"])
        return res
