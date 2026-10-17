#!/usr/bin/env python3
"""Tests du bail de focus (scripts/focus_broker.py).

La decision "l'utilisateur est-il actif ?" est une fonction PURE : elle se
teste sans Windows et sans fenetre. C'est la SEULE regle du module qui decide
si le bot vole la main a l'utilisateur, donc elle est verifiee explicitement.

    python tests/test_focus_broker.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

from focus_broker import should_yield_to_user  # noqa: E402

FAILED = []


def check(name, got, expected):
    if got == expected:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s : attendu %r, obtenu %r" % (name, expected, got))
        FAILED.append(name)


def main():
    print("focus_broker : cede la main a l'utilisateur")

    # L'utilisateur vient de taper / bouger la souris -> on cede.
    check("actif 0 ms", should_yield_to_user(0), True)
    check("actif 300 ms", should_yield_to_user(300), True)
    check("juste sous le seuil", should_yield_to_user(699), True)

    # Inactif -> le bot peut prendre le focus.
    check("seuil pile", should_yield_to_user(700), False)
    check("inactif 1.5 s", should_yield_to_user(1500), False)
    check("inactif 60 s", should_yield_to_user(60000), False)

    # API indisponible : on ne bloque PAS la peche (choix assume).
    check("API KO -> pas de deference", should_yield_to_user(None), False)

    # Un seuil plus strict doit rester coherent (bail non preemptible cote
    # cycle de morsure : seuil long = bot plus prudent).
    check("seuil long : actif refuse",
          should_yield_to_user(1200, idle_threshold_ms=2000), True)
    check("seuil long : inactif accepte",
          should_yield_to_user(2500, idle_threshold_ms=2000), False)

    print("focus_broker : %d echec(s)" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())