#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_fishing_controls.py — tests de la cartographie des commandes.

On verifie :
  - le parsing d'un `bindings-cache.wtf` (fxture locale, aucun acces requis) ;
  - l'agregation multi-comptes (les raccourcis different d'un compte a
    l'autre : c'est le cas REEL sur la machine de developpement) ;
  - le controle « la touche de lancer configuree est-elle reellement liee ? » ;
  - que la lecture reelle de la machine ne modifie AUCUN fichier.

Si aucun client WoW n'est installe, les tests bases sur la machine sont
ignores (ils ne doivent pas faire echouer la CI).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import fishing_controls as fc      # noqa: E402

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


FIXTURE = """# fixture de bindings
bind BUTTON3 MULTIACTIONBAR2BUTTON1
bind W ACTIONBUTTON1
bind UP NONE
bind S ACTIONBUTTON7
bind A MULTIACTIONBAR1BUTTON1
bind D STRAFERIGHT
bind Z TOGGLEAUTORUN
bind 2 ACTIONBUTTON10
bind X ACTIONBUTTON11
bind PADDRIGHT ACTIONBUTTON10
bind F1 BONUSACTIONBUTTON1
modifiedclick NONE SELFCAST
"""


def write_fixture(text):
    fd, path = tempfile.mkstemp(suffix=".wtf")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_parsing():
    print("=== 1. PARSING DU CACHE DE RACCOURCIS ===")
    path = write_fixture(FIXTURE)
    try:
        b = fc.parse_bindings_file(path)
        check("une touche explicitement liberee est ignoree", "UP" not in b)
        check("la touche d'auto-run est lue", b.get("Z") == "TOGGLEAUTORUN")
        check("le strafe droit est lu", b.get("D") == "STRAFERIGHT")
        check("une touche liee a une barre d'actions est lue comme telle",
              b.get("W") == "ACTIONBUTTON1")
        check("les touches d'actions multiples sont lues",
              b.get("PADDRIGHT") == "ACTIONBUTTON10")
        check("les lignes non 'bind' sont ignorees", "SELFCAST" not in b.values())
        check("un fichier absent ne leve pas", fc.parse_bindings_file(None) == {})
    finally:
        os.unlink(path)


def test_multi_account():
    print()
    print("=== 2. AGREGATION MULTI-COMPTES ===")
    c = fc.Controls({
        "compteA": {"Z": "TOGGLEAUTORUN", "2": "ACTIONBUTTON10", "D": "STRAFERIGHT"},
        "compteB": {"Z": "TOGGLEAUTORUN", "X": "ACTIONBUTTON10", "D": "STRAFERIGHT"},
    })
    check("l'auto-run commun aux deux comptes est trouve",
          c.auto_run() == "Z")
    check("la touche de lancer commune est absente (les comptes different)",
          c.cast_key_bound() is None, "(%s)" % c.cast_key_bound())
    check("le detail par compte est expose",
          c.cast_keys() == {"compteA": "2", "compteB": "X"},
          "(%s)" % c.cast_keys())
    ok, details = c.configured_cast_ok("2")
    check("une touche configuree non liee partout est signalee", ok is False,
          "(%s)" % details)
    ok2, details2 = c.configured_cast_ok("Z")
    check("une touche configuree qui n'est pas une action de lancer est refusee",
          ok2 is False, "(%s)" % details2)

    c2 = fc.Controls({"seul": {"Z": "TOGGLEAUTORUN", "2": "ACTIONBUTTON10"}})
    check("avec un seul compte, la touche commune est trouvee",
          c2.cast_key_bound() == "2")
    check("la touche configuree est validee sur un compte unique",
          c2.configured_cast_ok("2")[0] is True)


def test_report():
    print()
    print("=== 3. RAPPORT DE CARTOGRAPHIE ===")
    c = fc.Controls({"compteA": {"Z": "TOGGLEAUTORUN", "2": "ACTIONBUTTON10"}})
    r = c.report()
    for key in ("clavier", "camera", "orientation", "cycle_de_peche",
                "deplacement_reel", "arret_et_reprise", "reservees_par_le_bot"):
        check("le rapport contient la section '%s'" % key, key in r)
    check("la camera est explicitement pilotee par la souris",
          "SOURIS" in r["camera"]["source"])
    check("l'orientation du personnage est distinguee de celle de la camera",
          "camera" in r["orientation"] and "personnage" in r["orientation"])
    check("le mecanisme de deplacement reel (auto-run) est documente",
          "auto-run" in r["deplacement_reel"]["mecanisme"])
    check("l'arret d'urgence est documente", "F9" in r["arret_et_reprise"]["arret_urgence"])


def test_resolution():
    print()
    print("=== 4. RESOLUTION DE LA TOUCHE DE LANCER ===")
    c = fc.Controls({
        "compteA": {"2": "ACTIONBUTTON10", "Z": "TOGGLEAUTORUN"},
        "compteB": {"X": "ACTIONBUTTON10", "Z": "TOGGLEAUTORUN"},
    })
    key, diag = c.resolve_cast_key("2", account="compteB")
    check("la touche liee au compte joue est utilisee", key == "X", "(%s)" % key)
    check("le diagnostic explique la correction",
          "n'est pas liee" in (diag.get("reason") or ""), "(%s)" % diag.get("reason"))
    key2, _d2 = c.resolve_cast_key("2", account="compteA")
    check("la touche configuree est conservee si elle est correcte", key2 == "2",
          "(%s)" % key2)
    key3, diag3 = c.resolve_cast_key(None, account="compteA")
    check("sans touche configuree, la touche du compte joue est reprise",
          key3 == "2", "(%s)" % key3)
    key4, diag4 = c.resolve_cast_key("9", account="compteInconnu")
    check("sans compte identifiable, la touche configuree est conservee",
          key4 == "9", "(%s)" % key4)
    check("le compte actif est signale", "activeAccount" in diag4)


def test_live_readonly():
    print()
    print("=== 5. LECTURE REELLE (lecture seule, sans client = ignore) ===")
    accounts = fc._wow_account_dirs()
    if not accounts:
        print("  (ignore : aucun client WoW detecte sur cette machine)")
        return
    before = {}
    for d in accounts:
        f = os.path.join(d, "bindings-cache.wtf")
        if os.path.isfile(f):
            try:
                before[f] = (os.path.getmtime(f), os.path.getsize(f))
            except OSError:
                pass
    c = fc.Controls.load()
    after = {}
    for d in accounts:
        f = os.path.join(d, "bindings-cache.wtf")
        if os.path.isfile(f):
            try:
                after[f] = (os.path.getmtime(f), os.path.getsize(f))
            except OSError:
                pass
    check("aucun fichier de raccourcis n'a ete modifie", before == after)
    check("au moins un compte a ete lu", len(c.per_account) >= 1,
          "(%s)" % list(c.per_account))
    rep = c.report()
    check("le rapport reel indique l'auto-run par compte",
          bool(rep["clavier"]["auto_run_par_compte"]) or
          rep["clavier"]["auto_run_commun"] != "NON LIE / DIFFERE PAR COMPTE")


def main():
    print("=== TESTS CARTOGRAPHIE DES COMMANDES ===")
    test_parsing()
    test_multi_account()
    test_report()
    test_resolution()
    test_live_readonly()
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
