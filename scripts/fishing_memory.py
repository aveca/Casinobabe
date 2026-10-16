# -*- coding: utf-8 -*-
"""
fishing_memory.py — Mémoire persistante + apprentissage du fishing assistant.

Réutilise les conventions existantes (JSONL append-only, écritures atomiques)
— aucun nouveau serveur, aucune Factory bis, SQLite non requis.

Contenu (Phase 2) :
  state/mem-sessions.jsonl      sessions (id, job, versions, début/fin, états, résumé)
  state/mem-observations.jsonl  observations (banc, flotteur, déplacement, UI, erreur, butin, spot, appât)
  state/mem-actions.jsonl       actions (attente, échéance, résultat observé, cause, params)
  state/mem-spots.json          snapshot spots (fiabilité + taille d'échantillon)
  state/mem-params.json         snapshot paramètres (valeur, référence, historique, auto_adapt)
  state/mem-experiments.json    expériences versionnées (hypothèse → décision)

Règles d'honnêteté :
  * position estimée != position exacte ; première observation != apparition réelle ;
  * outcomes incertains restent "unknown" (jamais convertis en succès/échec) ;
  * données simulées (harness) N'ENTRENT JAMAIS ici (source != "sim") ;
  * déplacement autonome du personnage : NON supporté par conception — les
    transitions enregistrées sont constatées (joueur), jamais commandées.
"""
import datetime
import hashlib
import json
import math
import os
import shutil
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")

SESSIONS = "mem-sessions.jsonl"
OBSERVATIONS = "mem-observations.jsonl"
ACTIONS = "mem-actions.jsonl"
SPOTS = "mem-spots.json"
PARAMS = "mem-params.json"
EXPERIMENTS = "mem-experiments.json"

RESULTS = ("success", "fail", "timeout", "unknown")
KINDS = ("pool", "bobber", "move", "ui", "error", "loot", "spot", "bait",
         "connectivity", "session", "param", "experiment")

# Paramètres auto-adaptables (liste blanche + bornes). Tout le reste exige
# une modification de code relue (diff + tests + rollback).
ADAPTABLE = {
    "biteDiffThresh": (2.0, 12.0),
    "biteWhiteFrac": (0.01, 0.15),
    "biteMotionThresh": (2.0, 8.0),
    "biteMotionStreak": (2, 8),
    "maxBiteWaitSec": (20.0, 45.0),
    "captureIntervalSec": (0.10, 0.60),
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(prefix):
    return "%s-%s-%s" % (prefix, _now().replace(":", "").replace("-", ""),
                         uuid.uuid4().hex[:6])


def _path(name, root=None):
    return os.path.join(root or STATE, name)


def read_jsonl(name, root=None):
    """Lit un JSONL en ignorant/isolant les lignes malformées (récupération)."""
    path = _path(name, root)
    rows, bad = [], 0
    if not os.path.isfile(path):
        return rows, bad
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    if bad:
        q = path + ".quarantine"
        with open(q, "a", encoding="utf-8") as fh:
            fh.write("# %s lignes malformées isolées\n" % bad)
    return rows, bad


def append_jsonl(name, row, root=None):
    os.makedirs(root or STATE, exist_ok=True)
    with open(_path(name, root), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    return row


def write_snapshot(name, data, root=None, backup=True):
    """Écriture atomique + backup avant changement de schéma/contenu."""
    base = root or STATE
    os.makedirs(base, exist_ok=True)
    path = _path(name, root)
    if backup and os.path.isfile(path):
        bdir = os.path.join(base, "memory-backups")
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, os.path.join(bdir, "%s.%s.bak" % (name, stamp)))
        olds = sorted(os.listdir(bdir))
        for old in olds[:-5]:
            try:
                os.remove(os.path.join(bdir, old))
            except OSError:
                pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return data


def read_snapshot(name, default, root=None):
    try:
        with open(_path(name, root), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Sessions / observations / actions
# ---------------------------------------------------------------------------
def session_start(job="manual", program_version="?", addon_version="?",
                  state_initial="?", root=None):
    row = {"id": _uid("sess"), "job": job, "program_version": program_version,
           "addon_version": addon_version, "started_at": _now(),
           "ended_at": None, "interruptions": [], "restarts": 0,
           "state_initial": state_initial, "state_final": None, "summary": {}}
    # Reprise après interruption : les sessions du même job restées ouvertes
    # sont closes comme "interrupted" (pas de doublon, pas de perte).
    rows, _ = read_jsonl(SESSIONS, root)
    dirty = False
    for r in rows:
        if r.get("job") == job and not r.get("ended_at"):
            r["ended_at"] = _now()
            r["state_final"] = "INTERRUPTED"
            r["summary"] = {"recovered": "interrupted, reprise par %s" % row["id"]}
            dirty = True
    if dirty:
        path = _path(SESSIONS, root)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return append_jsonl(SESSIONS, row, root)


def session_end(session_id, state_final="?", summary=None, root=None):
    rows, _ = read_jsonl(SESSIONS, root)
    for r in rows:
        if r.get("id") == session_id:
            r["ended_at"] = _now()
            r["state_final"] = state_final
            r["summary"] = summary or {}
    write_snapshot(SESSIONS + ".closed", {"closed": [r["id"] for r in rows if r.get("ended_at")]}, root)
    # réécriture complète honnête : on ne touche qu'à la session close
    path = _path(SESSIONS, root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return session_id


def observe(kind, zone=None, coords=None, screen=None, source="bot",
            confidence="?", evidence=None, ts=None, root=None):
    assert kind in KINDS, "kind inconnu: %s" % kind
    assert source != "sim", "les données simulées n'entrent pas en mémoire"
    row = {"id": _uid("obs"), "ts": ts or _now(), "kind": kind, "zone": zone,
           "coords": coords, "screen": screen, "source": source,
           "confidence": confidence, "evidence": evidence or {}}
    return append_jsonl(OBSERVATIONS, row, root)


def action_start(session_id, spot_id, state_from, action, expected,
                 deadline_s, params=None, root=None):
    row = {"action_id": _uid("act"), "session_id": session_id,
           "spot_id": spot_id, "state_from": state_from, "action": action,
           "expected": expected, "deadline_s": deadline_s,
           "started_at": _now(), "ended_at": None, "result": None,
           "evidence": {}, "fail_cause": None, "confidence": "?",
           "params": params or {}}
    return append_jsonl(ACTIONS, row, root)


def action_end(action_id, result, evidence=None, fail_cause=None,
               confidence="?", root=None):
    assert result in RESULTS, "résultat inconnu: %s" % result
    rows, _ = read_jsonl(ACTIONS, root)
    for r in rows:
        if r.get("action_id") == action_id:
            r["ended_at"] = _now()
            r["result"] = result
            r["evidence"] = evidence or {}
            r["fail_cause"] = fail_cause
            r["confidence"] = confidence
    path = _path(ACTIONS, root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return action_id


# ---------------------------------------------------------------------------
# Statistiques (petits échantillons : intervalle de Wilson, médiane, n)
# ---------------------------------------------------------------------------
def wilson(success, total, z=1.96):
    if total <= 0:
        return 0.0, 0.0, 0
    p = success / total
    d = 1 + z * z / total
    c = p + z * z / (2 * total)
    m = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d), total


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def timer_estimate(intervals_s):
    """Estimation honnête d'un intervalle : n, min/médiane/max, confiance.
    n < 2 → inconnu (jamais de durée inventée)."""
    xs = [x for x in intervals_s if x is not None]
    if len(xs) < 2:
        return {"n": len(xs), "min": None, "median": None, "max": None,
                "confidence": "insuffisant (inconnu)", "estimate_s": None}
    conf = "faible" if len(xs) < 5 else ("modérée" if len(xs) < 12 else "bonne")
    return {"n": len(xs), "min": min(xs), "median": _median(xs),
            "max": max(xs), "confidence": conf, "estimate_s": _median(xs)}


# ---------------------------------------------------------------------------
# Déplacements observés (joueur-conduits, jamais commandés : le bot ne sait
# pas déplacer le personnage). Un nœud = zone/spot vu ; une arête = transition
# constatée entre deux observations successives. max 3 tentatives / timeout
# 10 min / abandon si pas de progrès — documented ici, appliqué par l'agent.
# ---------------------------------------------------------------------------
MOVE_POLICY = {"max_attempts": 3, "timeout_s": 600,
               "rule": "aucun deplacement autonome : transitions constatees uniquement"}


def move_transition(from_zone, to_zone, ts=None, context=None, root=None):
    assert from_zone and to_zone and from_zone != to_zone
    snap = read_snapshot(SPOTS, {"spots": {}}, root)
    moves = snap.setdefault("moves", {"edges": {}, "policy": MOVE_POLICY})
    key = "%s>%s" % (from_zone, to_zone)
    e = moves["edges"].get(key) or {"n": 0, "last": None, "contexts": []}
    e["n"] += 1
    e["last"] = ts or _now()
    if context:
        e["contexts"] = (e["contexts"] + [context])[-10:]
    moves["edges"][key] = e
    write_snapshot(SPOTS, snap, root)
    return e
# ---------------------------------------------------------------------------
# Spots / expériences / paramètres
# ---------------------------------------------------------------------------
def spot_get(spot_id, root=None):
    snap = read_snapshot(SPOTS, {"spots": {}}, root)
    return snap["spots"].get(spot_id)


def spot_update(spot_id, zone=None, coords=None, access=None,
                event=None, ts=None, enemy=None, root=None):
    """event: seen|gone|cast|catch|fail|check|death|aggro. N'écrase jamais l'historique.
    death/aggro alimentent le risque (pas de jugement 'dangereux' sur 1 cas isolé)."""
    snap = read_snapshot(SPOTS, {"spots": {}}, root)
    s = snap["spots"].get(spot_id) or {
        "id": spot_id, "zone": zone, "coords": coords, "access": access,
        "first_seen": None, "last_seen": None, "last_check": None,
        "appearances": [], "casts": 0, "confirmed": 0, "fails": 0,
        "observed_s": 0.0,
        "risk": {"deaths": 0, "aggro": 0, "enemies": {}, "lost_s": 0.0}}
    ts = ts or _now()
    if event in ("seen", "cast", "catch", "fail", "check", "gone"):
        s["last_seen"] = ts
        if not s["first_seen"]:
            s["first_seen"] = ts
    if event == "seen":
        if not s["appearances"] or s["appearances"][-1].get("gone_at"):
            s["appearances"].append({"seen_at": ts, "gone_at": None})
    if event == "gone" and s["appearances"] and not s["appearances"][-1].get("gone_at"):
        s["appearances"][-1]["gone_at"] = ts
    if event == "cast":
        s["casts"] += 1
    if event == "catch":
        s["confirmed"] += 1
    if event == "fail":
        s["fails"] += 1
    if event == "death":
        s["risk"]["deaths"] += 1
    if event == "aggro":
        s["risk"]["aggro"] += 1
        e = enemy or "inconnu"
        s["risk"]["enemies"][e] = s["risk"]["enemies"].get(e, 0) + 1
    if zone and not s["zone"]:
        s["zone"] = zone
    if coords and not s["coords"]:
        s["coords"] = coords
    s["last_check"] = ts
    snap["spots"][spot_id] = s
    write_snapshot(SPOTS, snap, root)
    return s


def spot_reliability(spot_id, root=None):
    s = spot_get(spot_id, root) or {}
    casts = s.get("casts", 0)
    lo, hi, n = wilson(s.get("confirmed", 0), casts)
    risk = (s.get("risk") or {})
    deaths, aggro = risk.get("deaths", 0), risk.get("aggro", 0)
    verdict = "insuffisant" if casts < 5 else ("faible" if lo < 0.3 else "correct")
    if deaths + aggro >= 3 and verdict == "correct":
        verdict = "correct (risque observe: %d morts, %d aggro)" % (deaths, aggro)
    return {"casts": casts, "confirmed": s.get("confirmed", 0),
            "rate": (s.get("confirmed", 0) / casts) if casts else None,
            "wilson95": [round(lo, 3), round(hi, 3)], "n": n,
            "deaths": deaths, "aggro": aggro,
            "enemies": risk.get("enemies", {}),
            "verdict": verdict if not (deaths + aggro >= 1 and casts < 5)
            else "insuffisant (mais risque observe: %d morts, %d aggro)" % (deaths, aggro)}


def experiment_record(hypothesis, baseline, variable, sample, expected,
                      result, confidence, limitations, decision,
                      old_value=None, new_value=None, root=None):
    exps = read_snapshot(EXPERIMENTS, {"experiments": []}, root)
    row = {"id": _uid("exp"), "ts": _now(), "hypothesis": hypothesis,
           "baseline": baseline, "variable": variable, "sample": sample,
           "expected": expected, "result": result, "confidence": confidence,
           "limitations": limitations, "decision": decision,
           "old_value": old_value, "new_value": new_value}
    exps["experiments"].append(row)
    write_snapshot(EXPERIMENTS, exps, root)
    return row


def param_get(name, root=None):
    snap = read_snapshot(PARAMS, {"params": {}, "auto_adapt": False}, root)
    return snap["params"].get(name), snap.get("auto_adapt", False)


def param_propose(name, new_value, reason, evidence, root=None):
    """Stade une proposition (borne blanche + un seul changement à la fois).
    L'application exige param_apply explicite ; auto_adapt=False par défaut."""
    if name not in ADAPTABLE:
        return {"ok": False, "error": "paramètre non auto-adaptable : %s" % name}
    lo, hi = ADAPTABLE[name]
    if not (lo <= new_value <= hi):
        return {"ok": False, "error": "hors bornes [%s, %s]" % (lo, hi)}
    snap = read_snapshot(PARAMS, {"params": {}, "auto_adapt": False}, root)
    cur = snap["params"].get(name, {})
    staged = [p for p in snap["params"].values() if p.get("staged")]
    if staged:
        return {"ok": False, "error": "un changement est déjà en cours d'évaluation"}
    snap["params"][name] = {"value": cur.get("value"), "staged": new_value,
                            "reason": reason, "evidence": evidence,
                            "staged_at": _now(), "baseline_result": None}
    write_snapshot(PARAMS, snap, root)
    return {"ok": True, "staged": new_value}


def param_apply(name, baseline_result, root=None):
    snap = read_snapshot(PARAMS, {"params": {}, "auto_adapt": False}, root)
    p = snap["params"].get(name) or {}
    if not p.get("staged"):
        return {"ok": False, "error": "rien en cours pour %s" % name}
    p["history"] = p.get("history", []) + [{
        "at": _now(), "old": p.get("value"), "new": p["staged"],
        "reason": p.get("reason"), "baseline_result": baseline_result}]
    p["value"] = p.pop("staged")
    p["baseline_result"] = baseline_result
    write_snapshot(PARAMS, snap, root)
    return {"ok": True, "value": p["value"]}


def param_revert(name, reason, root=None):
    snap = read_snapshot(PARAMS, {"params": {}, "auto_adapt": False}, root)
    p = snap["params"].get(name) or {}
    hist = p.get("history") or []
    if not hist:
        p.pop("staged", None)
        write_snapshot(PARAMS, snap, root)
        return {"ok": True, "value": p.get("value"), "note": "staged annulé, pas d'historique"}
    last = hist[-1]
    p["history"] = hist + [{
        "at": _now(), "old": p.get("value"), "new": last["old"],
        "reason": "REVERT: %s" % reason, "baseline_result": None}]
    p["value"] = last["old"]
    write_snapshot(PARAMS, snap, root)
    return {"ok": True, "value": p["value"]}


# ---------------------------------------------------------------------------
# Rapport tableau de bord (mesures réelles vs estimations, jamais de simulé)
# ---------------------------------------------------------------------------
def report(root=None):
    obs, bad_obs = read_jsonl(OBSERVATIONS, root)
    acts, bad_act = read_jsonl(ACTIONS, root)
    sess, _ = read_jsonl(SESSIONS, root)
    snaps = read_snapshot(SPOTS, {"spots": {}}, root)
    exps = read_snapshot(EXPERIMENTS, {"experiments": []}, root)
    params = read_snapshot(PARAMS, {"params": {}, "auto_adapt": False}, root)

    done = [a for a in acts if a.get("result") in ("success", "fail", "timeout")]
    ok = sum(1 for a in done if a.get("result") == "success")
    lo, hi, n = wilson(ok, len(done))
    fails = {}
    for a in acts:
        if a.get("result") == "fail" and a.get("fail_cause"):
            fails[a["fail_cause"]] = fails.get(a["fail_cause"], 0) + 1
    kinds = {}
    for o in obs:
        kinds[o.get("kind", "?")] = kinds.get(o.get("kind", "?"), 0) + 1
    durs = []
    for a in done:
        try:
            t0 = datetime.datetime.fromisoformat(a["started_at"].replace("Z", "+00:00"))
            t1 = datetime.datetime.fromisoformat(a["ended_at"].replace("Z", "+00:00"))
            durs.append((t1 - t0).total_seconds())
        except (ValueError, TypeError, KeyError):
            pass
    stops = {}
    for sid, s in snaps.get("spots", {}).items():
        if sid == "moves":
            continue
        stops[sid] = {"casts": s.get("casts", 0), "confirmed": s.get("confirmed", 0),
                      "last_seen": s.get("last_seen"),
                      "reliability": spot_reliability(sid, root)["verdict"],
                      "deaths": (s.get("risk") or {}).get("deaths", 0),
                      "aggro": (s.get("risk") or {}).get("aggro", 0)}
    last_save = None
    for f in (SESSIONS, OBSERVATIONS, ACTIONS):
        p = _path(f, root)
        if os.path.isfile(p):
            t = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)
            s = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            if not last_save or s > last_save:
                last_save = s
    return {
        "kind": "real_measurements",
        "observations": len(obs),
        "observations_by_kind": kinds,
        "actions_attempted": len(acts),
        "actions_resolved": len(done),
        "results_confirmed": ok,
        "success_rate": round(ok / len(done), 3) if done else None,
        "success_wilson95": [round(lo, 3), round(hi, 3)],
        "success_n": n,
        "frequent_errors": fails,
        "action_duration_s": {"median": _median(durs), "mean": round(sum(durs) / len(durs), 1) if durs else None, "n": len(durs)},
        "spots": stops,
        "moves": (snaps.get("moves") or {}).get("edges", {}),
        "spots_unconfirmed": [sid for sid, s in snaps.get("spots", {}).items()
                              if sid != "moves" and not s.get("casts")],
        "missing_data": ["positions exactes (jamais inventées)"] if True else [],
        "param_changes": sum(len(p.get("history", [])) for p in params.get("params", {}).values()),
        "auto_adapt": params.get("auto_adapt", False),
        "experiments": len(exps.get("experiments", [])),
        "sessions": len(sess),
        "malformed_lines_quarantined": bad_obs + bad_act,
        "last_memory_save": last_save,
    }
