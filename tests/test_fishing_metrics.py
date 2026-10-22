#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_fishing_metrics.py — tests de la mesure des captures par niveau.

On verifie surtout ce qui compte vraiment : qu'un clic ou une interaction
confirmee ne soit JAMAIS comptabilise comme une capture, et que le niveau
« poisson capture » ne soit annonce que depuis la source independante
(l'addon), avec la mention qu'il n'est pas temps reel.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fishing_metrics as fm      # noqa: E402

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


def write_log(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


def test_levels():
    print("=== 1. SEPARATION DES NIVEAUX ===")
    rows = [
        {"ts": "2026-10-21T00:00:00", "event": "CAST", "cycle": "c1"},
        {"ts": "2026-10-21T00:00:01", "event": "CATCH_DETECTED", "cycle": "c1"},
        {"ts": "2026-10-21T00:00:02", "event": "CLICK_BOBBER", "cycle": "c1"},
        {"ts": "2026-10-21T00:00:03", "event": "VERIFY_OK", "cycle": "c1"},
        {"ts": "2026-10-21T00:00:04", "event": "CAST", "cycle": "c2"},
        {"ts": "2026-10-21T00:00:05", "event": "CAST_UNCONFIRMED", "cycle": "c2"},
        {"ts": "2026-10-21T00:00:06", "event": "BITE_TIMEOUT", "cycle": "c2"},
        {"ts": "2026-10-21T00:00:07", "event": "CAST_NOT_SENT", "cycle": "c3"},
    ]
    path = write_log(rows)
    try:
        a = fm.analyse(path)
        n = a["niveaux"]
        check("les tentatives de lancer sont comptees", n["cast_attempted"]["compteur"] == 3,
              "(%s)" % n["cast_attempted"]["compteur"])
        check("les lancers envoyes sont comptes", n["cast_sent"]["compteur"] == 2,
              "(%s)" % n["cast_sent"]["compteur"])
        check("les lancers non confirmes sont comptes", n["cast_unconfirmed"]["compteur"] == 1)
        check("les morsures detectees sont comptees", n["bite_detected"]["compteur"] == 1)
        check("les clics envoyes sont comptes", n["click_sent"]["compteur"] == 1)
        check("les interactions confirmees sont comptees",
              n["interaction_confirmed"]["compteur"] == 1)
        check("les timeouts de morsure sont comptes", n["bite_timeout"]["compteur"] == 1)
        check("le nombre de cycles est calcule", a["fenetre"]["cycles"] == 3,
              "(%s)" % a["fenetre"]["cycles"])
    finally:
        os.unlink(path)


def test_capture_not_inferred():
    print()
    print("=== 2. UN CLIC N'EST PAS UNE CAPTURE ===")
    rows = [{"ts": "2026-10-21T00:00:0%d" % i, "event": "CLICK_BOBBER", "cycle": "c1"}
            for i in range(5)]
    rows.append({"ts": "2026-10-21T00:00:09", "event": "VERIFY_OK", "cycle": "c1"})
    path = write_log(rows)
    try:
        a = fm.analyse(path)
        n = a["niveaux"]
        check("le compteur de captures reste inconnu sans l'addon",
              n["fish_captured"]["compteur"] is None,
              "(%s)" % n["fish_captured"]["compteur"])
        check("aucun clic n'est converti en capture",
              n["fish_captured"]["compteur"] != 5)
        check("le niveau capture est marque non temps reel",
              n["fish_captured"]["temps_reel"] is False)
        check("l'avertissement est present", "n'est PAS une capture" in a["avertissement"])
        check("le taux captures/interactions est inconnu sans l'addon",
              a["repartition"]["captures_sur_interactions"] is None)
    finally:
        os.unlink(path)


def test_addon_source():
    print()
    print("=== 3. SOURCE INDEPENDANTE : L'ADDON ===")
    fd, path = tempfile.mkstemp(suffix=".lua")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write('["totals"] = { catches = 12, targets = 20, castObserved = 0,\n'
                 '  castEvents = 34, castNameResolved = 34,\n'
                 '  castLastUnit = "player", castLastArgs = "guid=1-2-3 id=131",\n'
                 '  castLastName = "Fishing", castLastState = "EN_COURS" }\n')
    try:
        a = fm.analyse(None, path)
        n = a["niveaux"]
        check("les captures sont lues depuis l'addon", n["fish_captured"]["compteur"] == 12,
              "(%s)" % n["fish_captured"]["compteur"])
        check("la source est indiquee", "addon" in n["fish_captured"]["source"])
        check("le niveau addon est marque non temps reel",
              n["fish_captured"]["temps_reel"] is False)
        check("la date de derniere ecriture est fournie",
              bool(a["addon"]["meta"]["lastWrite"]))
        check("la limite de temps reel est documentee",
              "deconnexion" in a["addon"]["meta"]["note"])
        check("les diagnostics du cast sont exposes",
              a["addon"]["totals"].get("castEvents") == 34 and
              a["addon"]["totals"].get("castLastName") == "Fishing")
    finally:
        os.unlink(path)


def test_report():
    print()
    print("=== 4. RENDU DU RAPPORT ===")
    rows = [{"ts": "2026-10-21T00:00:00", "event": "CAST", "cycle": "c1"}]
    path = write_log(rows)
    try:
        rep = fm.format_report(fm.analyse(path))
        check("le rapport liste les niveaux", "niveau" in rep)
        check("le rapport contient l'avertissement", "ATTENTION" in rep)
        check("le rapport est lisible", len(rep.splitlines()) > 5)
    finally:
        os.unlink(path)


def main():
    print("=== TESTS MESURE DES CAPTURES ===")
    test_levels()
    test_capture_not_inferred()
    test_addon_source()
    test_report()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
