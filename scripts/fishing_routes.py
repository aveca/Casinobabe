#!/usr/bin/env node
"""
fishing_routes.py — graphe des trajets et calcul de rondes optimisees (V2).

PRINCIPE CENTRAL (honnetete) : on n'invente JAMAIS un chemin. Un segment
entre deux points n'est « utilisable » que s'il a ete MESURE (au moins une
traversee reelle enregistree dans ``fishing_spots.record_segment``). Les
segments non mesures sont listes comme « non verifies » et ne sont proposes
qu'en mode EXPLORATION, avec le drapeau ``verified=False`` — jamais comme un
chemin fiable.

LE SCORE D'UN SEGMENT ne mesure pas la distance : il mesure le COUT
ATTENDU (temps de deplacement + risque + probabilite de ne rien pêcher) et
poids donc ce qui compte pour la productivity : prises par heure.

Trois modes :
  ``prudent``      : chemins verifies uniquement, risque minimal ;
  ``equilibre``    : compromis productivite / risque ;
  ``exploration``  : decouvre de nouveaux bancs, accepte les segments non
                     verifies (explicitement signales).
"""

import math
import time

try:
    import fishing_spots as spots
except ImportError:  # pragma: no cover
    spots = None

#: Cout par unite de distance quand le segment n'a JAMAIS ete mesure
#: (uniquement en exploration, et le resultat est marque non verifie).
COST_PER_UNIT_UNMEASURED = 1.2

#: Cout d'un segment a traverser un mob connu.
COST_OBSTACLE = 6.0

#: Poids du risque (mobs connus) selon le mode.
RISK_WEIGHT = {"prudent": 3.0, "equilibre": 1.0, "exploration": 0.4}

#: Poids de la fiabilite du banc (taux de prises reelles) selon le mode.
YIELD_WEIGHT = {"prudent": 1.0, "equilibre": 1.6, "exploration": 0.6}

#: Poids de la disponibilite (banc probablement vide) selon le mode.
AVAILABILITY_WEIGHT = {"prudent": 1.2, "equilibré": 1.2, "exploration": 0.5}

MODES = ("prudent", "equilibre", "exploration")


def _mode(mode):
    m = (mode or "equilibre").lower()
    return m if m in MODES else "equilibre"


def spot_position(spot, reference="minimap"):
    c = (spot.get("coords") or {}).get(reference)
    return c if c else None


def distance(a, b):
    if not a or not b:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def build_graph(db, zone=None, reference=None, mode="equilibre"):
    """Graphe des points connus et des segments etablis.

    Retourne :
      ``nodes``  : {spot_id: {spot, pos, meta}}
      ``edges``  : {(a,b): {dist, verified, meanDuration, successRate, obstacles}}
    """
    mode = _mode(mode)
    reference = reference or db.get("referenceName", "minimap")
    nodes = {}
    for s in spots.all_spots(db, zone):
        pos = spot_position(s, reference)
        if not pos:
            continue
        nodes[s["id"]] = {
            "spot": s,
            "pos": pos,
            "meta": {
                "zone": s.get("zone"),
                "kind": s.get("kind"),
                "score": round(s.get("confirmScore", 0.0), 3),
                "observations": s.get("observations", 0),
                "visits": s.get("visits", 0),
                "catches": (s.get("results") or {}).get("catch", 0),
                "reliability": round(spots.reliability(s), 3),
                "expectedCatchRate": round(spots.expected_catch_rate(s), 3),
                "availability": round(spots.availability(s), 3),
                "mobs": s.get("mobs", []),
                "lastSeenAt": s.get("lastSeenAt"),
                "accessMean": spots.mean(s.get("accessTimes")),
                "fishMean": spots.mean(s.get("fishTimes")),
            },
        }

    edges = {}
    for key, seg in (db.get("segments") or {}).items():
        a, b = seg.get("a"), seg.get("b")
        if a not in nodes or b not in nodes:
            continue
        stats = spots.segment_stats(db, a, b)
        d = distance(nodes[a]["pos"], nodes[b]["pos"])
        edges[(a, b)] = {
            "dist": d,
            "verified": bool(seg.get("runs", 0) > 0),
            "meanDuration": stats.get("meanDuration") if stats else None,
            "successRate": stats.get("successRate") if stats else 0.0,
            "obstacles": (stats.get("obstacles") if stats else []) or [],
            "runs": seg.get("runs", 0),
        }
        edges[(b, a)] = dict(edges[(a, b)])
    # En mode EXPLORATION, on ajoute les liaisons geometriques non encore
    # mesurees (decouverte de trajets). Elles sont explicitement marquees
    # verified=False et ne sont proposees dans AUCUN autre mode.
    if mode == "exploration":
        known = set(edges)
        for a, na in nodes.items():
            cands = []
            for b, nb in nodes.items():
                if a == b or (a, b) in known:
                    continue
                d = distance(na["pos"], nb["pos"])
                if d is not None:
                    cands.append((d, b))
            cands.sort()
            for d, b in cands[:3]:          # 3 voisins geometriques au plus
                e = {"dist": d, "verified": False, "meanDuration": None,
                     "successRate": 0.0, "obstacles": [], "runs": 0,
                     "hypothetique": True}
                edges[(a, b)] = e
                edges[(b, a)] = dict(e)
    return {"nodes": nodes, "edges": edges, "reference": reference, "mode": mode}


def edge_cost(graph, a, b, mode="equilibre", now=None):
    """Cout attendu d'un segment (plus petit = meilleur)."""
    mode = _mode(mode)
    now = now or time.time()
    e = graph["edges"].get((a, b))
    nodes = graph["nodes"]
    if not e:
        return None
    d = e.get("dist") or 0.0
    # 1. temps de deplacement : mesure s'il existe, sinon estimation (uniquement
    #    acceptee en exploration, sinon le segment est considere comme non
    #    traversable).
    if e.get("meanDuration") is not None:
        travel = float(e["meanDuration"])
    else:
        # Segment jamais mesure :.traversable UNIQUEMENT en exploration
        # (et le resultat est marque non verifie). Dans les autres modes on
        # refuse : on n'invente pas de chemin.
        if mode != "exploration":
            return None
        travel = d * COST_PER_UNIT_UNMEASURED
    # 2. risque : mobs connus sur le trajet
    risk = len(e.get("obstacles") or []) * COST_OBSTACLE
    # 3. probabilite de succes du segment
    if e.get("runs"):
        travel *= (1.0 + (1.0 - float(e.get("successRate") or 0.0)) * 2.0)
    return travel + risk * RISK_WEIGHT.get(mode, 1.0)


def node_value(node, mode="equilibre", now=None):
    """Valeur d'un banc (a maximiser) : rendement attendu pondere."""
    mode = _mode(mode)
    m = node["meta"]
    rate = float(m.get("expectedCatchRate", 0.5))
    avail = float(m.get("availability", 1.0))
    rel = float(m.get("reliability", 0.0))
    fish_mean = m.get("fishMean")
    # temps de peche moyen mesure : un banc lent est moins rentable
    penalty = 0.0
    if fish_mean:
        penalty = max(0.0, (float(fish_mean) - 20.0)) * 0.02
    return (rate * AVAILABILITY_WEIGHT.get(mode, 1.0) * (0.5 + 0.5 * rel)
            - penalty)


def nearest_neighbour(graph, start, mode="equilibre", now=None):
    """Ronde du plus proche voisin (methode de reference, explicable)."""
    mode = _mode(mode)
    remaining = set(graph["nodes"])
    if start in remaining:
        remaining.discard(start)
    order = [start] if start in graph["nodes"] else []
    cur = start
    while remaining:
        best, best_c = None, None
        for sid in sorted(remaining):
            c = edge_cost(graph, cur, sid, mode, now) if cur else \
                (distance(graph["nodes"][cur]["pos"], graph["nodes"][sid]["pos"])
                 if cur else None)
            if c is None:
                continue
            if best_c is None or c < best_c:
                best, best_c = sid, c
        if best is None:
            break
        order.append(best)
        remaining.discard(best)
        cur = best
    return order


def two_opt(order, graph, mode="equilibre", now=None):
    """Amelioration locale 2-opt (inversion de segments), deterministe.

    On n'echange que si le COUT total de la ronde diminue : le resultat est
    reproductible sur les memes donnees (meme ordre de parcours des candidats).
    """
    mode = _mode(mode)
    if len(order) < 4:
        return list(order)

    def total(seq):
        t = 0.0
        for i in range(len(seq) - 1):
            c = edge_cost(graph, seq[i], seq[i + 1], mode, now)
            if c is None:
                return None
            t += c
        return t

    best = list(order)
    best_cost = total(best)
    if best_cost is None:
        return list(order)
    improved = True
    guard = 0
    while improved and guard < 50:
        improved = False
        guard += 1
        for i in range(len(best) - 1):
            for j in range(i + 2, len(best)):
                cand = best[:i + 1] + list(reversed(best[i + 1:j + 1])) + best[j + 1:]
                c = total(cand)
                if c is not None and c < best_cost - 1e-9:
                    best, best_cost = cand, c
                    improved = True
    return best


def plan_round(db, zone=None, mode="equilibre", start=None, reference=None,
               now=None, max_spots=None):
    """Calcule une ronde et l'EXPLIQUE.

    Retourne un dict serialisable :
      order        : sequence des spot_id
      legs         : detail de chaque deplacement (cout, distance, verifie)
      totalCost    : cout total (unites de temps)
      estimated    : estimation en PRISES (rendement attendu, pas une mesure)
      mode         : mode employe
      unverified   : segments utilises mais JAMAIS mesures
      metrics      : metriques de la ronde (temps de deplacement, temps de
                     peche, rendement attendu)
    """
    mode = _mode(mode)
    now = now or time.time()
    graph = build_graph(db, zone, reference, mode)
    nodes = graph["nodes"]
    if not nodes:
        return {"order": [], "legs": [], "totalCost": 0.0, "estimated": 0.0,
                "mode": mode, "unverified": [], "metrics": {}, "graph": {"nodes": 0, "edges": 0}}

    if start not in nodes:
        # depart : le banc le plus fiable (ou le premier confirme)
        cands = sorted(nodes.items(),
                       key=lambda kv: -node_value(kv[1], mode, now))
        start = cands[0][0] if cands else None
    if start is None:
        return {"order": [], "legs": [], "totalCost": 0.0, "estimated": 0.0,
                "mode": mode, "unverified": [], "metrics": {}}

    order = nearest_neighbour(graph, start, mode, now)
    order = two_opt(order, graph, mode, now)
    if max_spots:
        order = order[:max_spots]

    legs = []
    total = 0.0
    unverified = []
    travel_total = 0.0
    fish_total = 0.0
    for i in range(len(order) - 1):
        a, b = order[i], order[i + 1]
        e = graph["edges"].get((a, b))
        c = edge_cost(graph, a, b, mode, now)
        if c is None:
            continue
        d = e.get("dist")
        dur = e.get("meanDuration")
        legs.append({
            "from": a, "to": b,
            "distance": round(d, 2) if d is not None else None,
            "durationSec": round(float(dur), 1) if dur is not None else None,
            "verified": bool(e.get("verified")),
            "successRate": round(float(e.get("successRate") or 0.0), 3),
            "obstacles": e.get("obstacles") or [],
            "cost": round(c, 2),
        })
        total += c
        if not e.get("verified"):
            unverified.append({"from": a, "to": b})
        if dur:
            travel_total += float(dur)

    # valeur attendue de la ronde (RENDEMENT ATTENDU, pas une mesure)
    expected = 0.0
    for sid in order:
        m = nodes[sid]["meta"]
        rate = float(m.get("expectedCatchRate", 0.5))
        avail = float(m.get("availability", 1.0))
        expected += rate * avail
        if m.get("fishMean"):
            fish_total += float(m["fishMean"])

    metrics = {
        "spots": len(order),
        "travelSec": round(travel_total, 1),
        "fishSec": round(fish_total, 1),
        "expectedCatches": round(expected, 2),
        "expectedCatchesPerHour": round(expected * 3600.0 / max(1.0, travel_total + fish_total), 2)
        if (travel_total + fish_total) > 0 else None,
        "unverifiedLegs": len(unverified),
    }
    return {
        "order": order,
        "legs": legs,
        "totalCost": round(total, 2),
        "estimated": round(expected, 2),
        "mode": mode,
        "unverified": unverified,
        "metrics": metrics,
        "graph": {"nodes": len(nodes), "edges": len(graph["edges"])},
    }


def explain(round_result, graph=None):
    """Explication lisible et deterministe de la ronde calcule."""
    lines = []
    lines.append("Mode : %s" % round_result.get("mode"))
    lines.append("Bancs : %d" % len(round_result.get("order") or []))
    for i, leg in enumerate(round_result.get("legs") or [], 1):
        lines.append(
            "  %d. %s -> %s : %s unites, %s s%s" % (
                i, leg["from"][:9], leg["to"][:9],
                leg.get("distance"),
                leg.get("durationSec"),
                "  [NON VERIFIE]" if not leg.get("verified") else ""))
    m = round_result.get("metrics") or {}
    if m:
        lines.append("Deplacement : %s s | peche : %s s" % (m.get("travelSec"), m.get("fishSec")))
        lines.append("Prises ATTENDUES : %s (%s/h)" % (m.get("expectedCatches"),
                                                     m.get("expectedCatchesPerHour")))
    if round_result.get("unverified"):
        lines.append("Segments non mesures utilises : %d" % len(round_result["unverified"]))
    return "\n".join(lines)
