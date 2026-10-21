#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_fishing_skills.py — tests de l'architecture de competences.

On verifie le CONTRAT de chaque competence :
  - les preconditions bloquent quand elles doivent bloquer ;
  - une competence qui ne produit pas de PREUVE est un ECHEC (jamais un
    succes par simple absence d'erreur) ;
  - le timeout borne l'execution ;
  - la strategie de recuperation est appliquee et bornee ;
  - la confiance evolue avec l'historique et la memoire persiste ;
  - une competence en echec n'est plus proposee par le planificateur.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fishing_skills as sk      # noqa: E402

Sk = sk      # alias utilise dans le fichier

FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s %s" % (name, detail))


class FakeBot(object):
    def __init__(self):
        self.logs = []
        self.casts = 0

    def log(self, event, **kw):
        self.logs.append((event, kw))

    def _cast(self, key):
        self.casts += 1
        return True


class Ok(Sk.Skill):
    name = "ok_skill"
    timeout = 1.0
    max_attempts = 1

    def __init__(self, memory=None, log=None, proof=None, fail_verify=False,
                 delay=0.0):
        super(Ok, self).__init__(memory, log)
        self.proof = proof
        self.fail_verify = fail_verify
        self.delay = delay
        self.calls = 0

    def act(self, ctx):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return (self.proof, "detail")

    def verify(self, ctx, proof):
        if self.fail_verify:
            return False, "preuve refusee"
        return True, None


def test_preconditions():
    print("=== 1. PRECONDITIONS ===")
    s = Ok()
    s.preconditions = lambda ctx: (False, "pas de configuration")
    r = s.run({})
    check("une precondition non satisfaite bloque la competence",
          r.ok is False and "preconditions" in (r.error or ""), "(%s)" % r.error)
    check("une competence bloquee ne consume pas de tentative", r.attempts == 0)

    cast = sk.CastFishing()
    r2 = cast.run({"bot": FakeBot(), "cast_key": None})
    check("lancer sans touche configuree est refuse",
          r2.ok is False and "touche" in (r2.error or ""), "(%s)" % r2.error)
    r3 = cast.run({"bot": FakeBot(), "cast_key": "2",
                   "keybinding_ok": (False, "touche non liee")})
    check("lancer avec une touche non liee est refuse",
          r3.ok is False and "non liee" in (r3.error or ""), "(%s)" % r3.error)


def test_proof_required():
    print()
    print("=== 2. UNE PREUVE EST EXIGEE ===")
    s = Ok(proof=None, fail_verify=True)
    r = s.run({})
    check("une verification qui refuse la preuve = echec", r.ok is False)
    check("le motif de l'echec est conserve", "verification" in (r.error or ""),
          "(%s)" % r.error)
    s2 = Ok(proof={"x": 1})
    r2 = s2.run({})
    check("une competence avec preuve est un succes", r2.ok is True)
    check("la preuve est transportee dans le resultat", r2.proof == {"x": 1})


def test_timeout_and_recovery():
    print()
    print("=== 3. TIMEOUT ET RECUPERATION ===")
    s = Ok(proof=None, fail_verify=True, delay=0.25)
    s.timeout = 0.05
    s.max_attempts = 2
    r = s.run({}, timeout=0.05)
    check("un depassement de timeout est un echec", r.ok is False)
    check("le depassement est signale", "timeout" in (r.error or ""),
          "(%s)" % r.error)

    s2 = Ok(proof=None, fail_verify=True)
    s2.max_attempts = 3
    s2.recover = lambda ctx, err: True      # strategie de recuperation active
    r2 = s2.run({})
    check("les tentatives sont bornees", r2.attempts == 3, "(%s)" % r2.attempts)
    check("une recuperation est signalee", r2.recovered is True)

    s3 = Ok(proof=None, fail_verify=True)
    s3.recover = lambda ctx, err: False
    s3.max_attempts = 3
    r3 = s3.run({})
    check("sans strategie de recuperation, on n\'essaie qu\'une fois",
          r3.attempts == 1, "(%s)" % r3.attempts)


def test_memory_and_confidence():
    print()
    print("=== 4. MEMOIRE ET CONFIANCE ===")
    tmp = os.path.join(tempfile.mkdtemp(prefix="fb-skills-"), "skills.json")
    mem = sk.SkillMemory(tmp)
    s = Ok(memory=mem, proof={"ok": 1})
    for _ in range(4):
        s.run({})
    check("la confiance monte avec les succes", mem.confidence("ok_skill") > 0.5,
          "(%.3f)" % mem.confidence("ok_skill"))
    s2 = Ok(memory=mem, proof=None, fail_verify=True)
    for _ in range(6):
        s2.run({})
    check("la confiance baisse avec les echecs", mem.confidence("ok_skill") < 0.7,
          "(%.3f)" % mem.confidence("ok_skill"))
    check("l'historique est persiste sur disque", os.path.isfile(tmp))
    mem2 = sk.SkillMemory(tmp)
    check("la memoire se recharge", mem2.stats().get("ok_skill", {}).get("runs", 0) >= 10)
    check("la derniere erreur est memorisee",
          bool(mem2.stats()["ok_skill"].get("last_error")))


def test_move_to_spot_proof():
    print()
    print("=== 5. L'ARRIVEE EST PROUVEE PAR L'OBSERVATION ===")

    class Nav(object):
        def __init__(self, result):
            self.result = result
            self.last_read = None
            self.actions = None
            self.forward_world_angle = None
            self.forward_screen_angle = None

        def navigate_to(self, target_marker=None):
            return self.result

    bot = FakeBot()
    s = sk.MoveToSpot()
    # un statut "blocked" n'est PAS une arrivee
    r = s.run({"bot": bot, "navigation": Nav({"status": "blocked", "reason": "x"}),
               "target": {"x": 1, "y": 2}})
    check("un trajet bloque n'est pas une arrivee", r.ok is False,
          "(%s)" % r.error)
    # un statut "arrived" mais avec une distance observee trop grande : refuse
    r2 = s.run({"bot": bot,
                "navigation": Nav({"status": "arrived", "reason": "ok",
                                   "arrivalDistance": 90.0}),
                "target": {"x": 1, "y": 2}, "arrive_tolerance": 10.0})
    check("une arrivee avec une distance observee excessive est refusee",
          r2.ok is False, "(%s)" % r2.error)
    # une arrivee avec une distance observee faible : acceptee
    r3 = s.run({"bot": bot,
                "navigation": Nav({"status": "arrived", "reason": "ok",
                                   "arrivalDistance": 3.0}),
                "target": {"x": 1, "y": 2}, "arrive_tolerance": 10.0})
    check("une arrivee prouvee par la distance observee est acceptee",
          r3.ok is True, "(%s)" % r3.error)
    # pas de cible : refuse
    r4 = s.run({"bot": bot, "navigation": Nav({}), "target": None})
    check("sans cible, la competence est refusee", r4.ok is False)


def test_calibrate_camera():
    print()
    print("=== 6. CALIBRATION CAMERA : PREUVE = DEPLACEMENT OBSERVE ===")

    class Nav(object):
        def __init__(self, angle):
            self.angle = angle
            self.last_read = {"position": {"x": 0, "y": 0}}
            self.actions = None
            self.forward_world_angle = 1.0
            self.forward_screen_angle = 1.0

        def _calibrate_forward(self):
            self.forward_world_angle = self.angle
            return self.angle

    s = sk.CalibrateCamera()
    r = s.run({"bot": FakeBot(), "navigation": Nav(0.7)})
    check("une calibration avec un angle observe est un succes", r.ok is True,
          "(%s)" % r.error)
    r2 = s.run({"bot": FakeBot(), "navigation": Nav(None)})
    check("une calibration sans deplacement observe est un echec",
          r2.ok is False, "(%s)" % r2.error)


def test_catalogue():
    print()
    print("=== 7. CATALOGUE ===")
    mem = sk.SkillMemory(None)
    skills = sk.default_skills(mem)
    for name in ("cast_fishing", "move_to_spot", "calibrate_camera",
                 "recover_missed_detection", "resume_after_interruption"):
        check("la competence '%s' existe" % name, name in skills)
    rep = sk.skill_report(mem, skills)
    check("le rapport decrit chaque competence",
          len(rep["skills"]) == len(skills))
    for name, info in rep["skills"].items():
        check("'%s' declare un timeout" % name, info["timeout"] > 0)
        check("'%s' declare des criteres d'echec via la verification" % name,
              hasattr(skills[name], "verify"))


def main():
    print("=== TESTS ARCHITECTURE DE COMPETENCES ===")
    test_preconditions()
    test_proof_required()
    test_timeout_and_recovery()
    test_memory_and_confidence()
    test_move_to_spot_proof()
    test_calibrate_camera()
    test_catalogue()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
