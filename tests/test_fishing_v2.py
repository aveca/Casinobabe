#!/usr/bin/env node
# -*- coding: utf-8 -*-
"""
test_fishing_v2.py — tests de la memoire des bancs, de la carte et du
planificateur de rondes (Fishing V2).

Ces tests sont HORS JEU : ils utilisent des cartes synthetiques et des
positions minimap de synthese. Ils ne valident PAS le deplacement reel
(celui-ci se mesure en session) — ils valident la logique, la persistence et
la reproductibilite.

Execution : python tests/test_fishing_v2.py   (ou pytest)
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fishing_spots as fs      # noqa: E402
import fishing_routes as fr     # noqa: E402

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


def mk_db(n=3, zone="Lushwater Oasis", score=0.85):
    """Base synthetique : n bancs confirmes, 2 observations chacun.

    Les positions sont espacees de 40 unites de minicarte : au-dela du rayon
    de deduplication (25), donc chaque point est un banc DISTINCT.
    """
    db = fs._empty_db()
    for i in range(n):
        x, y = 40.0 * i, 20.0 * i
        for _ in range(2):
            s, st = fs.observe_spot(db, zone, coords=(x, y), score=score)
        fs.record_result(db, s["id"], "catch", access_sec=10.0 + i, fish_sec=20.0 + i)
    return db


# ---------------------------------------------------------------- 1. memoire
def test_spot_memory():
    print("=== 1. MEMOIRE DES BANCS ===")
    db = fs._empty_db()

    # deduplication : deux observations proches = le MEME banc
    s1, st1 = fs.observe_spot(db, "Lushwater Oasis", coords=(10.0, -5.0), score=0.8)
    s2, st2 = fs.observe_spot(db, "Lushwater Oasis", coords=(11.0, -5.4), score=0.85)
    check("deux observations proches -> un seul banc", s1["id"] == s2["id"])
    check("1re observation = candidat (pas encore confirme)", st1 == "new")
    check("2e observation -> banc confirme", st2 == "confirmed")

    # une observation eloignee = un AUTRE banc
    s3, st3 = fs.observe_spot(db, "Lushwater Oasis", coords=(80.0, 40.0), score=0.9)
    check("observation eloignee -> nouveau banc", s3["id"] != s1["id"])
    check("le banc eloigne n'est PAS confirmé (1 seule observation)",
          s3["id"] not in [x["id"] for x in fs.confirmed_spots(db)])

    # score faible = rejete, rien n'est stocke
    before = len(fs.all_spots(db))
    s4, st4 = fs.observe_spot(db, "Lushwater Oasis", coords=(5.0, 5.0), score=0.2)
    check("observation peu fiable -> rejete", st4 == "rejected" and s4 is None)
    check("un rejet n'ajoute aucun banc", len(fs.all_spots(db)) == before)

    # resultats et statistiques
    fs.record_result(db, s1["id"], "catch", access_sec=12.0, fish_sec=30.0)
    fs.record_result(db, s1["id"], "empty", access_sec=8.0)
    check("les visites sont comptees", s1["visits"] == 2, "(visits=%s)" % s1["visits"])
    check("la fiabilite reflete les resultats reels",
          abs(fs.reliability(s1) - 0.5) < 1e-6, "(rel=%s)" % fs.reliability(s1))
    check("le temps d'acces moyen est mesure",
          abs((fs.mean(s1["accessTimes"]) or 0) - 10.0) < 1e-6)
    check("le delai de reapparition est mesure apres un banc vide",
          s1.get("revisitAfterSec") is not None)

    # mobs et incidents
    fs.record_result(db, s1["id"], "catch", mob="loup")
    check("un mob observe est memorise", "loup" in s1["mobs"])
    fs.record_error(db, "move", "bloque sur un rocher", spot_id=s1["id"])
    check("un incident de deplacement est compte", s1["errors"]["move"] == 1)
    check("le dernier incident est trace", (s1.get("lastError") or {}).get("kind") == "move")


# ------------------------------------------------------------ 2. persistance
def test_persistence():
    print()
    print("=== 2. PERSISTANCE (sauvegarde / migrations / corruption) ===")
    tmpdir = tempfile.mkdtemp(prefix="fb-v2-")
    path = os.path.join(tmpdir, "spots.json")

    db = mk_db(2)
    fs.save(db, path)
    check("le fichier de carte est ecrit", os.path.isfile(path))

    db2 = fs.load(path)
    check("la carte se recharge", len(fs.all_spots(db2)) == 2, "(%d)" % len(fs.all_spots(db2)))
    check("le schema est conserve", db2.get("schema") == fs.SCHEMA_VERSION)

    # migration d'une base ancienne
    old = {"schema": 0, "spots": {"x": {"id": "x", "zone": "Z", "observations": 3}}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(old, fh)
    migrated = fs.load(path)
    check("une base ancienne est migree", migrated.get("schema") == fs.SCHEMA_VERSION)
    check("la migration ajoute les champs manquants",
          "referenceName" in migrated and "reference" in migrated)

    # fichier corrompu : on ne perd pas tout, on repart proprement
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ ceci n'est pas du json")
    broken = fs.load(path)
    check("un fichier corrompu ne casse pas le chargement",
          isinstance(broken, dict) and "spots" in broken)
    check("une sauvegarde du fichier corrompu est conservee",
          len(os.listdir(fs.BACKUP_DIR)) >= 1)

    # sauvegarde avant ecriture
    n_before = len(os.listdir(fs.BACKUP_DIR))
    fs.save(db, path)
    check("une sauvegarde est prise avant ecriture",
          len(os.listdir(fs.BACKUP_DIR)) >= n_before)


# ------------------------------------------------------------------ 3. carte
def test_map():
    print()
    print("=== 3. CARTE (export / referentiel / limites) ===")
    db = mk_db(3)
    m = fs.spot_map(db)
    check("la carte exporte les bancs", len(m["spots"]) == 3)
    check("la carte declare son referentiel", m["reference"] == "minimap")
    check("la carte documente ses limites", bool(m.get("limits", {}).get("limites")))
    check("chaque banc a une position minimap",
          all(s["x"] is not None and s["y"] is not None for s in m["spots"]))
    check("la carte est serialisable en JSON", isinstance(json.dumps(m), str))
    # les coordonnees ecran ne doivent PAS etre melangees au referentiel
    s = fs.all_spots(db)[0]
    s["coords"]["screen"] = [1, 2]
    m2 = fs.spot_map(db)
    check("les coordonnees ecran ne sont pas melangees au referentiel",
          all(sp["x"] != 1 or sp["y"] != 2 for sp in m2["spots"]))


# --------------------------------------------------------- 4. planificateur
def test_routes():
    print()
    print("=== 4. PLANIFICATEUR DE RONDES ===")
    db = mk_db(3)
    ids = [s["id"] for s in fs.confirmed_spots(db)]
    fs.record_segment(db, ids[0], ids[1], duration_sec=12.0, distance=32.0, success=True)
    fs.record_segment(db, ids[1], ids[2], duration_sec=15.0, distance=26.0, success=True)

    r = fr.plan_round(db, "Lushwater Oasis", "equilibre")
    check("une ronde est calculee", len(r["order"]) == 3, "(%d)" % len(r["order"]))
    check("la ronde utilise les segments mesures", len(r["legs"]) == 2)
    check("aucun segment non verifie en mode equilibre", len(r["unverified"]) == 0)
    check("la ronde est explicable", "Mode" in fr.explain(r))

    # determinisme
    r2 = fr.plan_round(db, "Lushwater Oasis", "equilibre")
    check("le calcul est reproductible", r["order"] == r2["order"])

    # un segment en echec (mob) doit etre evite
    db2 = mk_db(3)
    ids2 = [s["id"] for s in fs.confirmed_spots(db2)]
    fs.record_segment(db2, ids2[0], ids2[1], duration_sec=12.0, distance=32.0, success=True)
    fs.record_segment(db2, ids2[1], ids2[2], duration_sec=15.0, distance=26.0, success=True)
    fs.record_segment(db2, ids2[0], ids2[2], duration_sec=40.0, distance=40.0,
                      success=False, obstacle="loup")
    r3 = fr.plan_round(db2, "Lushwater Oasis", "equilibre")
    check("un segment en echec (mob) n'est pas choisi",
          all(leg["obstacles"] == [] for leg in r3["legs"]), "%s" % r3["legs"])

    # exploration : segments non mesures proposes MAIS signales
    db3 = mk_db(3)
    ids3 = [s["id"] for s in fs.confirmed_spots(db3)]
    fs.record_segment(db3, ids3[0], ids3[1], duration_sec=12.0, distance=32.0, success=True)
    rp = fr.plan_round(db3, "Lushwater Oasis", "prudent")
    re_ = fr.plan_round(db3, "Lushwater Oasis", "exploration")
    check("prudent : que des segments verifies", len(rp["unverified"]) == 0)
    check("exploration : propose des segments non verifies",
          len(re_["unverified"]) >= 1, "(%d)" % len(re_["unverified"]))
    check("exploration : les segments non verifies sont signales",
          any(not leg["verified"] for leg in re_["legs"]))

    # 2-opt : le cout ne doit pas augmenter
    db4 = mk_db(4)
    ids4 = [s["id"] for s in fs.confirmed_spots(db4)]
    for a, b, dur in ((0, 1, 30.0), (1, 2, 10.0), (2, 3, 30.0), (0, 3, 10.0)):
        fs.record_segment(db4, ids4[a], ids4[b], duration_sec=dur, success=True)
    graph = fr.build_graph(db4, "Lushwater Oasis", mode="equilibre")
    order = fr.nearest_neighbour(graph, ids4[0], "equilibre")
    improved = fr.two_opt(order, graph, "equilibre")
    c1 = sum(fr.edge_cost(graph, improved[i], improved[i + 1], "equilibre")
             for i in range(len(improved) - 1))
    c0 = sum(fr.edge_cost(graph, order[i], order[i + 1], "equilibre")
             for i in range(len(order) - 1))
    check("2-opt n'augmente jamais le cout", c1 <= c0 + 1e-9, "(%.1f -> %.1f)" % (c0, c1))

    # carte vide : aucun crash
    empty = fr.plan_round(fs._empty_db(), "Lushwater Oasis", "equilibre")
    check("une carte vide ne fait pas planter le planificateur", empty["order"] == [])


# ------------------------------------------------- 5. export / import
def test_export_import():
    print()
    print("=== 5. EXPORT / IMPORT (contrat de la carte partagee) ===")
    db = mk_db(3)
    ids = [s["id"] for s in fs.confirmed_spots(db)]
    fs.record_segment(db, ids[0], ids[1], duration_sec=12.0, distance=40.0, success=True)

    payload = fs.export_map(db)
    check("l'export est serialisable", isinstance(json.dumps(payload), str))
    check("l'export porte le nombre d'observations (contrat)",
          all("observations" in s for s in payload["spots"]),
          "(sans ce champ, le consommateur ne peut pas distinguer "
          "un banc confirme d'un candidat)")
    check("l'export porte le referentiel", payload.get("reference") == "minimap")

    # reimport : les identifiants doivent etre PRESERVES, sinon les segments
    # importes ne pointent sur aucun noeud (bug corrige, detecte par la parite)
    db2 = fs.import_map(json.loads(json.dumps(payload)), simulated=True)
    ids2 = [s["id"] for s in fs.all_spots(db2)]
    check("les identifiants sont preserves a l'import",
          set(ids).issubset(set(ids2)), "(%s vs %s)" % (ids, ids2))
    check("les segments sont reimportes", len(db2.get("segments") or {}) == 1)
    st = fs.segment_stats(db2, ids[0], ids[1])
    check("un segment importe est utilisable (duree mesuree)",
          st is not None and abs(st["meanDuration"] - 12.0) < 1e-6, "(%s)" % st)

    # la carte importee doit permettre de calculer une ronde
    import fishing_routes as fr
    r = fr.plan_round(db2, None, "equilibre")
    check("une carte importee permet de calculer une ronde", len(r["order"]) >= 2,
          "(%d bancs)" % len(r["order"]))
    check("la ronde importee utilise les segments importes", len(r["legs"]) >= 1)


# ------------------------------------------------------------------- main
def main():
    print("=== TESTS FISHING V2 (hors jeu) ===")
    test_spot_memory()
    test_persistence()
    test_map()
    test_routes()
    test_export_import()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
