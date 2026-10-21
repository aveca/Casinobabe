# -*- coding: utf-8 -*-
"""
test_botwow_modules.py — tests hors-ligne du REGISTRE des modules produit
(scripts/botwow_modules.py). Aucune connexion, aucun jeu, aucun I/O réseau.

Le registre est la source unique des statuts affichés sur le site
(botwow-saas / modules.json). Si ces tests ne passent pas, le site ne doit
pas prétendre à des statuts cohérents. Mode script (comme fishing_bot_test) :
exit 0 si tout passe, 1 sinon.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "botwow_modules.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import botwow_modules as reg  # noqa: E402

RESULTS = []


def ck(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# ------------------------------------------------------------- contenu ----
fails = reg.selftest()
ck("registre:selftest interne", fails == 0, "%d echec(s) interne(s)" % fails)

mods = reg.sorted_modules()
ck("registre: 8 modules", len(mods) == 8, "trouve %d" % len(mods))
ids = [m["id"] for m in mods]
ck("registre: ids uniques", len(ids) == len(set(ids)), ",".join(ids))

ALLOWED = ("available", "development", "planned")
ck("registre: statuts autorises",
   all(m["status"] in ALLOWED for m in mods),
   " ".join(m["status"] for m in mods))

order = {"available": 0, "development": 1, "planned": 2}
seq = [order[m["status"]] for m in mods]
ck("registre: tri available<development<planned", seq == sorted(seq), str(seq))

bilingual_problems = []
for m in mods:
    for lang in ("en", "fr"):
        for field in ("title", "summary"):
            if not m.get(field, {}).get(lang):
                bilingual_problems.append("%s/%s/%s" % (m["id"], field, lang))
        # shipped/pending : les CLEFS doivent exister dans les 2 langues,
        # mais une liste VIDE est legitime (planned -> shipped = []).
        for field in ("shipped", "pending"):
            if field not in m or lang not in m.get(field, {}):
                bilingual_problems.append("%s/%s/%s(clef)" % (m["id"], field, lang))
ck("registre: bilingue (title/summary non vides, shipped/pending presents)",
   not bilingual_problems, ",".join(bilingual_problems) or "8x2 langues ok")

ck("registre: version = 1.2.0 (= version.txt du site)", reg.VERSION == "1.2.0", reg.VERSION)

# un module "planned" ne vend pas de contenu livre (EN et FR)
planned_honest = all(
    not m["shipped"]["en"] and not m["shipped"]["fr"]
    for m in mods if m["status"] == "planned"
)
ck("registre: planned sans contenu livre", planned_honest,
   "%d module(s) planned" % len([m for m in mods if m["status"] == "planned"]))

# un module "available" prouve par des suites reelles
available_with_tests = all(
    len(m["tests"]) > 0 for m in mods if m["status"] == "available"
)
ck("registre: available avec suites de tests", available_with_tests)

# interdits : aucune promesse d'indetectabilite dans les descriptions modules
banned = ("undetectab", "indétectab", "indetectab", "unbanable", "ban-safe", "ban safe")
text_blob = ""
for m in mods:
    text_blob += " ".join([
        m["title"]["en"], m["title"]["fr"],
        m["summary"]["en"], m["summary"]["fr"],
        " ".join(m["shipped"]["en"] + m["shipped"]["fr"]),
        " ".join(m["pending"]["en"] + m["pending"]["fr"]),
        m["notes"]["en"], m["notes"]["fr"],
    ]).lower()
hits = [w for w in banned if w in text_blob]
ck("registre: aucune promesse interdite dans les modules", not hits, ",".join(hits) or "ok")

# la liste "never" existe et cite les trois interdits du mandat
never_en = " ".join(reg.build_record()["never"]["en"]).lower()
ck("registre: never-list couvre indetectable + sanctions + memoire",
   all(w in never_en for w in ("undetectable", "sanction", "injection")))

# ------------------------------------------------------ determinisme ------
r1, r2 = reg.render_json(), reg.render_json()
ck("registre: rendu deterministe (pas de timestamp)", r1 == r2)

rec = json.loads(r1)
ck("registre: JSON valide + clefs racine",
   all(k in rec for k in ("product", "version", "statuses", "honesty", "never", "modules")))
ck("registre: labels de statut bilingues",
   all(lang in rec["statuses"]["available"] for lang in ("en", "fr")))

# ------------------------------------------------------------ CLI ---------
with tempfile.TemporaryDirectory() as td:
    out = os.path.join(td, "modules.json")
    p1 = subprocess.run([sys.executable, SCRIPT, "--export", out],
                        capture_output=True, text=True, cwd=ROOT)
    ck("cli: --export exit 0", p1.returncode == 0, (p1.stderr or "")[-200:])
    if os.path.exists(out):
        with open(out, "r", encoding="utf-8") as fh:
            exported = fh.read()
        ck("cli: export == render_json()", exported == r1)
        p2 = subprocess.run([sys.executable, SCRIPT, "--check", out],
                            capture_output=True, text=True, cwd=ROOT)
        ck("cli: --check exit 0 sur fichier canonique", p2.returncode == 0)
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("CHANGED\n")
        p3 = subprocess.run([sys.executable, SCRIPT, "--check", out],
                            capture_output=True, text=True, cwd=ROOT)
        ck("cli: --check exit 1 sur fichier altere", p3.returncode == 1)
    else:
        ck("cli: export == render_json()", False, "fichier absent")
        ck("cli: --check exit 0 sur fichier canonique", False, "fichier absent")
        ck("cli: --check exit 1 sur fichier altere", False, "fichier absent")

# ------------------------------------------------------------- bilan ------
ok_count = sum(1 for _, ok, _ in RESULTS if ok)
print("---- %d/%d PASS ----" % (ok_count, len(RESULTS)))
sys.exit(0 if ok_count == len(RESULTS) else 1)
