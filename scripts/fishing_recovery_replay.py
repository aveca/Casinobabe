#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_recovery_replay.py — rejeu INSTRUMENTE du cycle de recuperation.

Utilise EXACTEMENT la meme chaine de production que fishing_bot.py
(WowActions -> Screen -> Executor -> RecoverySession) : ce qui est
rejoue ici est ce qui tournera en production.

Avant de lancer quoi que ce soit, les PRECONDITIONS sont verifiees et
journalisees :
  1. client WoW present ;
  2. focus clavier reellement sur la fenetre du jeu ;
  3. carte du monde fermee et monde 3D visible (capture) ;
  4. maintien/libelation d'ESPACE operationnels ;
  5. conversion capture -> ecran coherente avec le rectangle de la fenetre ;
  6. detection de l'etat de vie (voile cyan) operationnelle ;
  7. le personnage est-il bien FANTOME (sinon le rejeu n'a pas d'objet) ?

Journal : reports/fishing-recovery-replay-<date>.jsonl (horodate).
Captures : reports/recovery-evidence/<date>/ (avant/apres chaque action).

Ce script ne relance JAMAIS la peche et ne provoque JAMAIS une mort.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_ROOT = os.path.dirname(_HERE)

import fishing_recovery as FR  # noqa: E402


class Journal(object):
    """Journal horodate JSONL (une ligne par evenement)."""

    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def __call__(self, event, **kw):
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        rec.update(kw)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        print("  [%-19s] %s" % (rec["at"], {k: v for k, v in rec.items()
                                            if k not in ("at", "event")}),
              flush=True)
        return rec


def build_session(journal, evidence_dir, config=None):
    """Meme construction que fishing_bot.py (chaine de production)."""
    from fishing_client import WowActions
    from fishing_process import (ActionLog, Blackboard, Executor,
                                 ProcedureMemory, Screen, StateReader)
    from fishing_safety import IncidentMemory, RecoveryStateMachine
    acts = WowActions()
    scr = Screen(acts.capture, 0.05)
    board = Blackboard()
    st_reader = StateReader()
    ex = Executor(scr, st_reader, acts, log=ActionLog(None),
                  memory=ProcedureMemory(None), board=board)
    safety = RecoveryStateMachine(log=journal)
    incidents = IncidentMemory(os.path.join(evidence_dir, "incidents.json"))
    cfg = {"maxAttempts": 60, "maxMapAttempts": 3, "stepPx": 900,
           "relocalizeEvery": 6, "clickPx": 900, "flySeconds": 3.0}
    cfg.update(config or {})
    sess = FR.RecoverySession(ex, state_reader=st_reader, safety=safety,
                              memory=incidents, log=journal, config=cfg,
                              evidence_dir=evidence_dir)
    # le rejeu RECOIT les evenements d'action du moteur (clics, vols,
    # corrections de cap, decisions) : le journal est alors complet.
    sess.trace = journal
    return sess, ex


def preconditions(sess, journal):
    """Verifie les preconditions, chacune tracee.  Retourne un dict."""
    import ctypes
    u = ctypes.windll.user32
    out = {}

    # 1. client present
    try:
        from fishing_observer import find_wow_window
        hwnd, _t, rect = find_wow_window()
    except Exception as exc:
        hwnd, rect = None, None
        journal("PRECOND_CLIENT", error=repr(exc))
    # fenetre REDUITE : le client ne rend rien (capture impossible) et
    # le rejeu ne peut rien observer -> on la restaure.
    if hwnd:
        import ctypes as _ct
        _u = _ct.windll.user32
        if _u.IsIconic(hwnd):
            _u.ShowWindow(hwnd, 9)
            time.sleep(2.0)
            hwnd, _t, rect = find_wow_window()
            journal("PRECOND_WINDOW_RESTORED",
                    rect=list(rect) if rect else None)
    out["client"] = bool(hwnd)
    journal("PRECOND_CLIENT", present=bool(hwnd), rect=list(rect) if rect else None)
    if not hwnd:
        return out

    # 2. focus reel sur la fenetre du jeu
    fg = u.GetForegroundWindow()
    out["focus"] = bool(fg == hwnd)
    journal("PRECOND_FOCUS", foreground=int(fg or 0), game=int(hwnd),
            ok=bool(fg == hwnd))

    # 3. carte fermee + monde visible
    img = sess._capture()
    from fishing_map import map_is_open
    map_open = map_is_open(FR._bgr(img)) if img is not None else None
    world = sess._world_visible(img) if img is not None else False
    out["map_open"] = map_open
    out["world_visible"] = bool(world)
    journal("PRECOND_MAP_CLOSED", mapOpen=map_open, worldVisible=bool(world))
    if not world:
        closed = sess._ensure_world()
        img = sess._capture()
        world = sess._world_visible(img) if img is not None else False
        out["world_visible"] = bool(world)
        journal("PRECOND_MAP_CLOSED_AFTER_CLOSE", closed=bool(closed),
                worldVisible=bool(world))

    # 4. ESPACE : maintien + liberation
    keys = []
    h2 = sess._hwnd()
    try:
        u.SendMessageW(h2, 0x0100, 0x20, 0x00000001 | (0x20 << 16) | (1 << 30))
        time.sleep(0.15)
        u.SendMessageW(h2, 0x0101, 0x20, 0x00000001 | (0x20 << 16) | (1 << 31))
        keys = ["SPACE"]
    except Exception as exc:
        journal("PRECOND_SPACE", error=repr(exc))
    out["space"] = bool(keys)
    journal("PRECOND_SPACE", pressed=keys, released=True)

    # 5. conversion capture -> ecran
    sx, sy = sess._to_screen(1293, 327)
    out["to_screen"] = [sx, sy]
    journal("PRECOND_COORDS", capture=[1293, 327], screen=[sx, sy],
            windowRect=list(sess._window_rect()))

    # 6/7. etat de vie : le voile cyan
    tint = sess._ghost_tint(img) if img is not None else None
    out["tint"] = tint
    out["is_ghost"] = bool(tint is not None and tint > 0.5)
    journal("PRECOND_LIFE", tint=tint, isGhost=out["is_ghost"])
    return out


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = _ROOT
    jpath = os.path.join(root, "reports",
                         "fishing-recovery-replay-%s.jsonl" % stamp)
    evid = os.path.join(root, "reports", "recovery-evidence", stamp)
    os.makedirs(evid, exist_ok=True)
    journal = Journal(jpath)
    journal("REPLAY_START", journal=jpath, evidence=evid)

    sess, ex = build_session(journal, evid)
    pre = preconditions(sess, journal)
    journal("PRECOND_SUMMARY", **{k: v for k, v in pre.items()})

    if not pre.get("client"):
        journal("REPLAY_ABORT", reason="client WoW absent")
        return 2
    if not pre.get("world_visible"):
        journal("REPLAY_ABORT", reason="la carte du monde intercepte encore "
                                      "l'ecran : aucun deplacement possible")
        return 3
    if not pre.get("is_ghost"):
        journal("REPLAY_SKIPPED", reason="personnage VIVANT : le cycle de "
                                         "recuperation n'a pas d'objet "
                                         "(ne pas provoquer de mort)")
        print("\n  Rejeu NON applicable : le personnage est vivant.")
        print("  Pour un rejeu reel, lancer le bot pendant une session de "
              "peche :\n"
              "      python scripts/fishing_bot.py --once\n"
              "  (la recuperation est automatique : fishing_bot.py instancie "
              "RecoverySession, cf. ligne ~1730)")
        return 0

    journal("REPLAY_RUN", maxTransitions=40)
    res = sess.run(max_transitions=40)
    journal("REPLAY_RESULT", ok=bool(res.get("ok")),
            outcome=res.get("outcome"), reason=res.get("reason"),
            state=sess.state, attempts=sess.attempts,
            distance=sess.last_distance, best=sess.best_distance)
    # RAPPORT : ce qui est REELLEMENT observe (captures du client) et ce
    # qui ne l'est pas.  Aucune donnee synthetique n'est produite ici :
    # le rejeu ne manipule que des captures du client.
    captures = sorted(os.listdir(evid)) if os.path.isdir(evid) else []
    rapport = {
        "source": "captures REELLES du client (scripts/fishing_recovery_replay.py)",
        "donnees_synthetiques": "AUCUNE dans ce rejeu",
        "journal": jpath,
        "evidence_dir": evid,
        "captures": len(captures),
        "resultat": res,
        "statut": "CYCLE REEL OBSERVE" if res.get("ok") else "ECHEC",
    }
    rpath = os.path.join(os.path.dirname(jpath),
                          "fishing-recovery-replay-%s-report.json" % stamp)
    try:
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        rpath = None
    journal("REPLAY_REPORT", **{k: v for k, v in rapport.items()
                                if k != "resultat"})
    print("\n  resultat : %s" % json.dumps(res, ensure_ascii=False,
                                            default=str))
    print("  journal   : %s" % jpath)
    print("  rapport   : %s" % rpath)
    print("  captures  : %s (%d fichiers, conservees)" % (evid, len(captures)))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
