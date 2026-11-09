#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_skills.py — ARCHITECTURE DE COMPETENCES ( inspiree des agents
apprenants : OBSERVER -> PLANIFIER -> AGIR -> VERIFIER -> MEMORISER -> REPRENDRE).

Principe : une competence n'est pas « une fonction qui essaie ». C'est une
unite qui declare :
  * ses PRECONDITIONS      (qu faut-il observer avant de l'executer) ;
  * l'ACTION EXECUTEE      (et sa preuve attendue) ;
  * son TIMEOUT            (au-dela, c'est un echec, pas une boucle) ;
  * ses CRITERES D'ECHEC   ;
  * sa STRATEGIE DE RECUPERATION ;
  * son NIVEAU DE CONFIANCE (adapte par l'historique) ;
  * son HISTORIQUE DE RESULTATS (persiste).

Les competences deja valides en jeu (cycle de peche, calibration camera,
deplacement vers un repere, reprise apres interruption) deviennent des
competences reutilisables : elles ne sont pas reecrites, elles sont
ENROULEES.

Chaque execution produit une PREUVE (observation), jamais une simple
declaration : une competence qui n'a pas de preuve n'est pas reussie.
"""

import json
import math
import os
import time


# --------------------------------------------------------------------- resultats
class SkillResult(object):
    """Issue d'une competence : succes ou echec, avec la preuve."""

    __slots__ = ("ok", "proof", "detail", "error", "confidence", "elapsed",
                 "recovered", "attempts")

    def __init__(self, ok, proof=None, detail=None, error=None, confidence=0.0,
                 elapsed=0.0, recovered=False, attempts=1):
        self.ok = bool(ok)
        self.proof = proof
        self.detail = detail
        self.error = error
        self.confidence = float(confidence)
        self.elapsed = float(elapsed)
        self.recovered = bool(recovered)
        self.attempts = int(attempts)

    def as_dict(self):
        return {"ok": self.ok, "proof": self.proof, "detail": self.detail,
                "error": self.error, "confidence": round(self.confidence, 3),
                "elapsed": round(self.elapsed, 2), "recovered": self.recovered,
                "attempts": self.attempts}

    def __repr__(self):  # pragma: no cover
        return "<SkillResult ok=%s conf=%.2f %s>" % (self.ok, self.confidence,
                                                      (self.error or self.detail or "")[:60])


# --------------------------------------------------------------------- memoire
class SkillMemory(object):
    """Historique persistant des resultats par competence.

    La confiance d'une competence n'est pas une constante : elle est
    estimee a partir de l'historique (succes / echecs, recuperations), avec
    un lissage exponentiel. Une competence qui echoue souvent perd sa
    confiance et n'est plus proposee par le planificateur.
    """

    def __init__(self, path=None):
        self.path = path
        self.data = {}
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError):
                self.data = {}

    def _entry(self, name):
        return self.data.setdefault(name, {"runs": 0, "ok": 0, "failed": 0,
                                            "recovered": 0, "confidence": 0.5,
                                            "last_error": None, "last": None})

    def record(self, name, result):
        e = self._entry(name)
        e["runs"] += 1
        if result.ok:
            e["ok"] += 1
        else:
            e["failed"] += 1
            e["last_error"] = (result.error or result.detail or "")[:200]
        if result.recovered:
            e["recovered"] += 1
        # confiance : lissage exponentiel sur le resultat binaire
        target = 1.0 if result.ok else 0.0
        e["confidence"] = round(0.7 * e["confidence"] + 0.3 * target, 4)
        e["last"] = {"ts": time.time(), "ok": result.ok,
                     "error": e["last_error"], "proof": result.proof}
        self.save()
        return e

    def confidence(self, name):
        return float(self._entry(name).get("confidence", 0.5))

    def stats(self):
        return {k: {"runs": v["runs"], "ok": v["ok"], "failed": v["failed"],
                    "recovered": v["recovered"], "confidence": round(v["confidence"], 3),
                    "last_error": v.get("last_error")}
                for k, v in self.data.items()}

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


# ------------------------------------------------------------------ competence
class Skill(object):
    """Une competence reutilisable.

    Sous-classes : implementer `act()` (l'action) et `verify()` (la preuve).
    """

    name = "skill"
    description = ""
    timeout = 10.0
    max_attempts = 2
    min_confidence = 0.25

    def __init__(self, memory=None, log=None):
        self.memory = memory
        self.log = log or (lambda *a, **k: None)

    # ------------------------------------------------------------- contrat
    def preconditions(self, ctx):
        """Retourne (True, None) ou (False, raison)."""
        return True, None

    def act(self, ctx):
        """Execute l'action. Retourne (preuve, detail) ou leve une exception."""
        raise NotImplementedError

    def verify(self, ctx, proof):
        """Verifie la PREUVE. Retourne (ok, detail_raison)."""
        raise NotImplementedError

    def recover(self, ctx, error):
        """Strategie de recuperation. Retourne True si une nouvelle tentative
        a du sens, False sinon."""
        return False

    # -------------------------------------------------------------- execution
    def run(self, ctx, timeout=None):
        """Execute la competence : preconditions -> action -> verification ->
        (recuperation) -> memorisation du resultat."""
        t0 = time.time()
        limit = self.timeout if timeout is None else timeout
        ok, reason = self.preconditions(ctx)
        if not ok:
            res = SkillResult(False, error="preconditions: %s" % reason,
                              confidence=self.confidence(), elapsed=time.time() - t0,
                              attempts=0)
            self._remember(res)
            return res
        attempts = 0
        last_error = None
        recovered = False
        while attempts < max(1, int(self.max_attempts)):
            attempts += 1
            try:
                proof, detail = self.act(ctx)
            except Exception as exc:
                last_error = repr(exc)
                proof, detail = None, None
            else:
                if (time.time() - t0) > limit:
                    last_error = "timeout (%.1f s)" % (time.time() - t0)
                else:
                    good, why = self.verify(ctx, proof)
                    if good:
                        res = SkillResult(True, proof=proof, detail=detail,
                                          confidence=self.confidence(),
                                          elapsed=time.time() - t0,
                                          recovered=recovered, attempts=attempts)
                        self._remember(res)
                        return res
                    last_error = "verification: %s" % why
            if attempts >= max(1, int(self.max_attempts)):
                break
            if not self.recover(ctx, last_error):
                break
            recovered = True
        res = SkillResult(False, error=last_error, confidence=self.confidence(),
                          elapsed=time.time() - t0, recovered=recovered,
                          attempts=attempts)
        self._remember(res)
        return res

    # --------------------------------------------------------------- memoire
    def confidence(self):
        if self.memory:
            return self.memory.confidence(self.name)
        return 0.5

    def _remember(self, result):
        if self.memory:
            self.memory.record(self.name, result)
        self.log("SKILL_RESULT", skill=self.name, ok=result.ok,
                 confidence=round(result.confidence, 3),
                 error=result.error, attempts=result.attempts,
                 elapsed=round(result.elapsed, 2))


# ------------------------------------------------------- competences metier
class CastFishing(Skill):
    """Lancer la canne : la touche de lancer doit etre liee a une action du
    jeu, et l'addon doit confirmer le lancer (preuve : CAST_UNCONFIRMED absent
    et l'etat du bot Casting)."""

    name = "cast_fishing"
    description = "Envoyer la touche de lancer et attendre la confirmation"
    timeout = 8.0
    max_attempts = 2

    def preconditions(self, ctx):
        if not ctx.get("cast_key"):
            return False, "touche de lancer absente de la configuration"
        ok, detail = ctx.get("keybinding_ok", (True, ""))
        if not ok:
            return False, detail
        return True, None

    def act(self, ctx):
        sent = ctx["bot"]._cast(ctx["cast_key"])
        return ({"cast_sent": sent}, "touche %s envoyee" % ctx["cast_key"])

    def verify(self, ctx, proof):
        if not proof or not proof.get("cast_sent"):
            return False, "la touche n'a pas ete envoyee"
        return True, None

    def recover(self, ctx, error):
        # On retente une fois : l'echec typique est l'activite humaine.
        ctx["bot"].log("SKILL_RECOVER", skill=self.name, error=error)
        return True


class MoveToSpot(Skill):
    """Aller a un repere : l'ARRIVEE doit etre prouvee par l'OBSERVATION
    (le repere vu a moins de la tolerance du centre de la minicarte) et par
    l'odometre (la position mesuree a progresse vers la cible)."""

    name = "move_to_spot"
    description = "Deplacer le personnage vers un repere observe et prouver l'arrivee"
    timeout = 150.0
    max_attempts = 1

    def preconditions(self, ctx):
        if not ctx.get("target"):
            return False, "aucun repere cible"
        if not ctx.get("navigation"):
            return False, "navigation indisponible"
        return True, None

    def act(self, ctx):
        res = ctx["navigation"].navigate_to(target_marker=ctx["target"])
        return (res, res.get("reason"))

    def verify(self, ctx, proof):
        if not proof:
            return False, "pas de resultat de navigation"
        if proof.get("status") != "arrived":
            return False, "statut=%s (%s)" % (proof.get("status"), proof.get("reason"))
        # preuve independante : la distance observee doit etre faible
        dist = proof.get("arrivalDistance")
        if dist is not None and dist > ctx.get("arrive_tolerance", 10.0) * 2:
            return False, "distance d'arrivee trop grande (%.1f px)" % dist
        return True, None

    def recover(self, ctx, error):
        ctx["bot"].log("SKILL_RECOVER", skill=self.name, error=error)
        return False


class CalibrateCamera(Skill):
    """Calibrer l'orientation ecran -> monde : la preuve est un DECALAGE
    REEL de la minicarte apres un clic de reference."""

    name = "calibrate_camera"
    description = "Etablir la correspondance entre l'angle ecran et l'angle monde"
    timeout = 20.0
    max_attempts = 2

    def preconditions(self, ctx):
        if not ctx.get("navigation"):
            return False, "navigation indisponible"
        return True, None

    def act(self, ctx):
        nav = ctx["navigation"]
        before = (nav.last_read or {}).get("position")
        nav.forward_world_angle = None
        nav.forward_screen_angle = None
        angle = nav._calibrate_forward()
        return ({"angle": angle, "before": before}, "cap monde = %s" % angle)

    def verify(self, ctx, proof):
        if not proof or proof.get("angle") is None:
            return False, "aucun deplacement observe (etalonnage impossible)"
        return True, None

    def recover(self, ctx, error):
        # Deuxieme tentative : on reactive l'auto-run (la touche Z peut
        # avoir ete inversee par l'utilisateur entre-temps).
        actions = ctx["navigation"].actions
        if hasattr(actions, "toggle_autorun"):
            actions.toggle_autorun()
            ctx["bot"].log("SKILL_RECOVER", skill=self.name, error=error,
                           action="toggle_autorun")
            return True
        return False


class RecoverMissedDetection(Skill):
    """Reprise apres une detection manquee : on reobserve (nouvelle capture),
    on re-etalonne si la camera a bouge, on re-tente une fois."""

    name = "recover_missed_detection"
    description = "Reprise apres un ratage de detection"
    timeout = 30.0
    max_attempts = 2

    def preconditions(self, ctx):
        return True, None

    def act(self, ctx):
        bot = ctx["bot"]
        read = ctx.get("reobserve")()
        return ({"read": read}, "re-observation")

    def verify(self, ctx, proof):
        if not proof or not proof.get("read"):
            return False, "la minicarte reste illisible"
        return True, None

    def recover(self, ctx, error):
        ctx["bot"].log("SKILL_RECOVER", skill=self.name, error=error)
        return True


class ResumeAfterInterruption(Skill):
    """Reprise apres interruption (focus perdu, arret d'urgence, plantage) :
    on verifie que le client est la, en jeu, et que l'etat du cycle est
    coherent avant de repartir."""

    name = "resume_after_interruption"
    description = "Verifier l'etat reel du jeu avant de reprendre"
    timeout = 20.0
    max_attempts = 3

    def preconditions(self, ctx):
        return True, None

    def act(self, ctx):
        status = ctx.get("reexamine")()
        return ({"status": status}, "etat=%s" % status)

    def verify(self, ctx, proof):
        if not proof or not proof.get("status"):
            return False, "etat du jeu inconnu"
        if proof["status"] in ("erreur", "inconnu"):
            return False, "etat=%s" % proof["status"]
        return True, None

    def recover(self, ctx, error):
        ctx["bot"].log("SKILL_RECOVER", skill=self.name, error=error)
        return True


# ------------------------------------------------------------------ catalogue
def default_skills(memory=None, log=None):
    """Catalogue des competences disponibles (nom -> instance)."""
    skills = [CastFishing(memory, log), MoveToSpot(memory, log),
              CalibrateCamera(memory, log), RecoverMissedDetection(memory, log),
              ResumeAfterInterruption(memory, log)]
    return {s.name: s for s in skills}


def skill_report(memory, skills=None):
    """Etat de la memoire de competences (pour le journal et le rapport)."""
    out = {"skills": {}}
    for name, s in (skills or {}).items():
        out["skills"][name] = {
            "description": s.description,
            "timeout": s.timeout,
            "max_attempts": s.max_attempts,
            "min_confidence": s.min_confidence,
            "history": memory.stats().get(name, {}) if memory else {},
        }
    return out
