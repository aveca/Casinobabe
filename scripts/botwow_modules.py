# -*- coding: utf-8 -*-
"""
botwow_modules.py — REGISTRE CANONIQUE DES MODULES DU PRODUIT BOT-WOW.

Source unique de vérité des statuts (Available / In development / Planned)
 telle qu'affichée sur le site (modules.json). Règles :
  - "available"      : code implémenté + tests qui passent + preuve réelle.
  - "development"    : code réel partiellement implémenté et testé HORS-LIGNE ;
                       dire franchement ce qui manque (ex: validation en jeu).
  - "planned"        : conception écrite seulement. AUCUNE implémentation,
                       AUCUN bouton qui prétend le contraire.
  - interdits        : "undetectable", promesse d'absence de sanction,
                       statuts décoratifs sans code derrière.

Sortie deterministe : pas d'horodatage (le test de parité compare les octets).
Usage :
  python scripts/botwow_modules.py --export chemin/sortie/modules.json
  python scripts/botwow_modules.py --check  chemin/modules.json   (exit 0/1)
  python scripts/botwow_modules.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys

PRODUCT = "BOT-WOW"
VERSION = "1.2.0"  # doit rester = version.txt du site (test de parité le vérifie)

ALLOWED_STATUS = ("available", "development", "planned")

MODULES = [
    {
        "id": "fish",
        "status": "available",
        "title": {"en": "Fish — assistant + optimised rounds", "fr": "Pêche — assistant + rondes optimisées"},
        "summary": {
            "en": "The working core: measured float detection, persistent spot memory, planner with parity-verified rounds, background mode, F9 stop.",
            "fr": "Le noyau qui fonctionne : détection mesurée du flotteur, mémoire persistante des spots, planificateur à parité vérifiée, mode arrière-plan, arrêt F9.",
        },
        "shipped": {
            "en": [
                "Cast → bite → catch cycle instrumented per run (median logged bite→click 308 ms)",
                "Persistent memory for spots (migrations, atomic writes, export/import)",
                "Route planner: nearest-neighbour + 2-opt over measured legs; 3 modes",
                "Windows background capture (PrintWindow) and window-message clicks",
            ],
            "fr": [
                "Cycle lancer → touche → prise instrumenté (médiane mesurée 308 ms)",
                "Mémoire persistante des spots (migrations, écritures atomiques, export/import)",
                "Planificateur : plus proche voisin + 2-opt sur couts mesurés ; 3 modes",
                "Capture d'arrière-plan Windows (PrintWindow) et clics par messages",
            ],
        },
        "pending": {
            "en": [
                "Nothing pending for the fishing role itself; limits listed in the module's bounds",
            ],
            "fr": [
                "Rien en attente pour le rôle pêche proprement dit ; limites documentées",
            ],
        },
        "tests": ["python tests/fishing_bot_test.py", "node tests/run-fishing-harness.mjs"],
        "files": ["scripts/fishing_bot.py", "scripts/fishing_memory.py"],
        "notes": {
            "en": "The runtime addon observes game state only; it never simulates input.",
            "fr": "L'addon runtime observe l'état du jeu ; il ne simule jamais d'entrées.",
        },
    },
    {
        "id": "navigate",
        "status": "development",
        "title": {"en": "Navigate — spot-to-spot movement", "fr": "Navigation — déplacements spot à spot"},
        "summary": {
            "en": "Minimap odometer and bounded movement controller exist as tested offline code; end-to-end travel is still being validated in game.",
            "fr": "Odomètre minimap et contrôleur de déplacement bornés existent, testés hors-ligne ; le trajet complet reste à valider en jeu.",
        },
        "shipped": {
            "en": [
                "Odometry from the minimap ghost marker (unit tests: zero drift at rest)",
                "Arrival checks by observed position; bounded holds (never unchecked loops)",
            ],
            "fr": [
                "Odométrie via le marqueur fantôme minimap (tests : dérive nulle à l'arrêt)",
                "Contrôles d'arrivée par position observée ; attentes bornées (jamais de boucles non bornées)",
            ],
        },
        "pending": {
            "en": [
                "Whole-route runs validated in game (screens + logs) before any 'available' claim",
                "Profiles for non-fishing destinations once travel is proven",
            ],
            "fr": [
                "Trajets complets validés en jeu (écrans + journaux) avant tout statut 'available'",
                "Profils pour destinations hors pêche une fois les trajets prouvés",
            ],
        },
        "tests": ["python tests/fishing_bot_test.py"],
        "files": ["scripts/fishing_bot.py"],
        "notes": {
            "en": "Disabled by default in the GUI until in-game validation lands.",
            "fr": "Désactivé par défaut dans l'interface tant que la validation en jeu n'est pas faite.",
        },
    },
    {
        "id": "inventory",
        "status": "development",
        "title": {"en": "Inventory & vendor", "fr": "Inventaire & vendeur"},
        "summary": {
            "en": "Offline planner implemented and unit-tested (bag math, sell/restock lists, vendor-trip plan); the in-game data feed is the documented pending step.",
            "fr": "Planificateur hors-ligne implémenté et testé (math des sacs, listes de vente/réappro, plan de tournée vendeur) ; le flux de données en jeu est l'étape restante.",
        },
        "shipped": {
            "en": [
                "scripts/inventory_vendor.py: free-slot math, sell list, restock list, trip plan — pure functions, offline tests",
                "Defined BagSnapshot data contract (addon emitter or manual declaration)",
            ],
            "fr": [
                "scripts/inventory_vendor.py : math des sacs, liste de vente, liste de réappro, plan de tournée — fonctions pures, tests hors-ligne",
                "Contrat de données BagSnapshot défini (émetteur addon ou déclaration manuelle)",
            ],
        },
        "pending": {
            "en": [
                "BagSnapshot feed from the game (addon emitter + bridge consumption) — in-game wiring",
                "In-game validation of one full vendor round before any 'available' claim",
            ],
            "fr": [
                "Flux BagSnapshot depuis le jeu (émetteur addon + consommation côté bridge) — câblage en jeu",
                "Validation en jeu d'une tournée vendeur complète avant tout statut 'available'",
            ],
        },
        "tests": ["python tests/test_inventory_vendor.py"],
        "files": ["scripts/inventory_vendor.py", "tests/test_inventory_vendor.py"],
        "notes": {
            "en": "No bag API exists in the runtime addon today; diligence first, wiring second.",
            "fr": "Aucune API de sacs n'existe aujourd'hui dans l'addon runtime ; diligence d'abord, câblage ensuite.",
        },
    },
    {
        "id": "recovery",
        "status": "development",
        "title": {"en": "Recovery — the death chain", "fr": "Récupération — chaîne de mort"},
        "summary": {
            "en": "State machine fully specified AND implemented offline (unit-tested): death detection → ghost confirmation → corpse navigation → resurrection, with bounded timeouts and safe-stop default. Being validated in game; disabled by default.",
            "fr": "Machine à états entièrement spécifiée ET implémentée hors-ligne (testée unitairement) : détection de mort → confirmation fantôme → navigation cadavre → résurrection, avec attentes bornées et arrêt sûr par défaut. En validation en jeu ; désactivée par défaut.",
        },
        "shipped": {
            "en": [
                "Death detection + dialog handling primitives already tested (they stop the bot safely today)",
                "Recovery state machine implemented offline with unit tests: bounded transitions, ambiguous-signal handling, safe-stop default",
            ],
            "fr": [
                "Détection de mort + gestion de boîte de dialogue déjà testées (elles arrêtent le bot en sécurité aujourd'hui)",
                "Machine à états de récupération implémentée hors-ligne avec tests unitaires : transitions bornées, gestion de signal ambigu, arrêt sûr par défaut",
            ],
        },
        "pending": {
            "en": [
                "Corpse-run chain end-to-end (state machine transitions + in-game proof)",
            ],
            "fr": [
                "Chaîne complète de retour au cadavre (transitions + preuve en jeu)",
            ],
        },
        "tests": ["python tests/fishing_bot_test.py", "python tests/test_recovery_state_machine.py"],
        "files": ["scripts/fishing_bot.py", "scripts/recovery_state_machine.py", "tests/test_recovery_state_machine.py"],
        "notes": {
            "en": "Until it is proven, the product's behaviour after character death is: stop and ask a human.",
            "fr": "Tant que ce n'est pas prouvé, le comportement après mort reste : arrêt et demande à un humain.",
        },
    },
    {
        "id": "grind",
        "status": "planned",
        "title": {"en": "Grind & combat rotations", "fr": "Grind & rotations de combat"},
        "summary": {
            "en": "Design phase only. Depends on a combat-detection layer (target state, spell cooldowns) that does not exist yet. No implementation shipped; nothing pretended.",
            "fr": "Phase de conception seulement. Dépend d'une couche de détection de combat (état de cible, cooldowns) qui n'existe pas encore. Aucune implémentation livrée ; rien de simulé.",
        },
        "shipped": {"en": [], "fr": []},
        "pending": {
            "en": [
                "Combat-detection layer (design doc first, then offline-testable primitives)",
                "Class profiles can only be designed on top of proven primitives",
            ],
            "fr": [
                "Couche de détection de combat (doc de conception d'abord, primitives testables hors-ligne ensuite)",
                "Les profils de classe ne se conçoivent qu'au-dessus de primitives prouvées",
            ],
        },
        "tests": [],
        "files": [],
        "notes": {
            "en": "Nothing in the interface will show this role as running outside honest design notes.",
            "fr": "Rien dans l'interface ne présentera ce rôle comme fonctionnant,hors notes de conception honnêtes.",
        },
    },
    {
        "id": "scripting",
        "status": "planned",
        "title": {"en": "Scripting — bounded custom tasks", "fr": "Scripting — tâches personnalisées bornées"},
        "summary": {
            "en": "Design phase only: a bounded task runner reusing the module primitives (movement, bag checks, waypoints), sandboxed first.",
            "fr": "Phase de conception : un exécuteur de tâches bornées réutilisant les primitives des modules (déplacement, sacs, jalons), bac à sable d'abord.",
        },
        "shipped": {"en": [], "fr": []},
        "pending": {
            "en": [
                "Module-runner foundations must land first; script safety model before any user script",
            ],
            "fr": [
                "Les fondations du runner de modules d'abord ; modèle de sécurité des scripts avant tout script utilisateur",
            ],
        },
        "tests": [],
        "files": [],
        "notes": {
            "en": "No 'script editor' UI will be shipped while the concept is unproved.",
            "fr": "Aucune interface d'éditeur de scripts ne sera livrée tant que le concept n'est pas prouvé.",
        },
    },
    {
        "id": "follower",
        "status": "planned",
        "title": {"en": "Follower & quest assistance", "fr": "Follower & assistance aux quêtes"},
        "summary": {
            "en": "Not committed. Would require navigation AND combat layers proven first. No date, no promise.",
            "fr": "Non engagé. Exigerait des couches navigation ET combat prouvées d'abord. Pas de date, pas de promesse.",
        },
        "shipped": {"en": [], "fr": []},
        "pending": {
            "en": ["Depends on: navigate (available) + grind (beyond design)"],
            "fr": ["Dépend de : navigation (available) + grind (au-delà de la conception)"],
        },
        "tests": [],
        "files": [],
        "notes": {
            "en": "Listed so the ledger is complete, not to market a feature.",
            "fr": "Listé pour que le registre soit complet, pas pour vendre une fonction.",
        },
    },
    {
        "id": "clients",
        "status": "planned",
        "title": {"en": "More game clients (TBC/WotLK/Retail)", "fr": "Autres clients (TBC/WotLK/Retail)"},
        "summary": {
            "en": "Classic 1.13 is the tested scope today. A runtime addon for TBC Anniversary exists in the repository, but client support follows core validation, per profile.",
            "fr": "Classic 1.13 est le périmètre testé aujourd'hui. Un addon runtime TBC Anniversary existe dans le dépôt, mais la couverture suit la validation du noyau, profil par profil.",
        },
        "shipped": {"en": [], "fr": []},
        "pending": {
            "en": [
                "Per-client profile validation; interface versions and captures per build",
            ],
            "fr": [
                "Validation par client ; versions d'interface et captures par build",
            ],
        },
        "tests": [],
        "files": [],
        "notes": {
            "en": "The compatibility page stays the reference for what runs today.",
            "fr": "La page compatibilité reste la référence de ce qui tourne aujourd'hui.",
        },
    },
]


def sorted_modules(modules=None):
    order = {"available": 0, "development": 1, "planned": 2}
    return sorted(
        modules if modules is not None else MODULES,
        key=lambda m: (order[m["status"]], m["id"]),
    )


def build_record():
    """Construit l'enregistrement entier, déterministe (aucun timestamp)."""
    return {
        "product": PRODUCT,
        "version": VERSION,
        "statuses": {
            "available": {"en": "Available", "fr": "Disponible"},
            "development": {"en": "In development", "fr": "En développement"},
            "planned": {"en": "Planned", "fr": "Planifié"},
        },
        "honesty": {
            "en": "A status is only as strong as the tests behind it. Nothing is 'available' without passing tests, and nothing is presented as working while it is still a design.",
            "fr": "Un statut ne vaut que par les tests derrière lui. Rien n'est 'disponible' sans tests qui passent, et rien n'est présenté comme fonctionnant tant que ce n'est qu'une conception.",
        },
        "never": {
            "en": [
                "Claims of undetectable operation",
                "Any promise that your account is safe from sanctions",
                "Memory reading or process injection",
            ],
            "fr": [
                "Toute prétention d'indétectabilité",
                "Toute promesse que votre compte serait à l'abri de sanctions",
                "Lecture mémoire ou injection de processus",
            ],
        },
        "modules": sorted_modules(),
    }


def render_json(indent=2):
    return json.dumps(build_record(), indent=indent, ensure_ascii=False, sort_keys=True) + "\n"


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="BOT-WOW module registry")
    ap.add_argument("--export", metavar="OUT", help="write canonical modules.json")
    ap.add_argument("--check", metavar="PATH", help="verify an existing modules.json matches")
    ap.add_argument("--selftest", action="store_true", help="run internal checks")
    args = ap.parse_args(argv)

    if args.selftest:
        fails = selftest()
        print("SELFTEST %d failure(s)" % fails)
        return 1 if fails else 0

    if args.export:
        data = render_json()
        with open(args.export, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
        print("exported %s (%d modules)" % (args.export, len(MODULES)))
        return 0

    if args.check:
        target = _load(args.check)
        if target.strip() == render_json().strip():
            print("check OK: %s matches the registry" % args.check)
            return 0
        print("check FAIL: %s differs from the registry" % args.check)
        return 1

    data = render_json()
    try:
        print(data)
    except UnicodeEncodeError:
        # console Windows (cp1252) : écrire les octets UTF-8 sans planter
        sys.stdout.buffer.write(data.encode("utf-8") + b"\n")
    return 0


def selftest():
    """Retourne le nombre d'échecs internes (utilisé par tests/test_botwow_modules.py)."""
    fails = 0
    ids = [m["id"] for m in sorted_modules()]
    if len(ids) != len(set(ids)):
        fails += 1
        print("FAIL unique module ids")
    for m in MODULES:
        if m["status"] not in ALLOWED_STATUS:
            fails += 1
            print("FAIL statut inconnu:", m["id"])
        for lang in ("en", "fr"):
            if not m["title"].get(lang) or not m["summary"].get(lang):
                fails += 1
                print("FAIL bilingue incomplet:", m["id"], lang)
        if m["status"] == "planned" and m["shipped"].get("en"):
            fails += 1
            print("FAIL 'planned' ne peut pas se vanter de choses livrées:", m["id"])
        if m["status"] == "available" and not m["tests"]:
            fails += 1
            print("FAIL 'available' exige la liste de tests:", m["id"])
    record = build_record()
    if record["version"] != VERSION:
        fails += 1
        print("FAIL version incohérente")
    # le registre doit rester trié : available, development, planned
    order = {"available": 0, "development": 1, "planned": 2}
    seq = [order[m["status"]] for m in record["modules"]]
    if seq != sorted(seq):
        fails += 1
        print("FAIL tri des statuts")
    return fails


if __name__ == "__main__":
    sys.exit(main())
