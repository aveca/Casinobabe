#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_desktop_package.py — tests du paquet desktop distribuable.

On construit un paquet (dans un dossier temporaire) et on verifie :
  - le paquet existe, il a une version, et son MANIFEST est complet ;
  - chaque SHA-256 du manifeste correspond au fichier livre ;
  - l'application, l'addon, le bot et le lanceur sont bien presents ;
  - AUCUNE donnee utilisateur (state/, logs/, reports/, captures/) n'est
    embarquee ;
  - l'application lit sa version dans le paquet (VERSION) ;
  - le lanceur et le script de retour arriere sont presents ;
  - le paquet se reconstruit de facon deterministe (memes fichiers, memes
    empreintes).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

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


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=== TESTS DU PAQUET DESKTOP ===")
    out = tempfile.mkdtemp(prefix="fb-pkg-")
    version = "9.9.9-test"
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "scripts", "build_desktop_package.py"),
                        "--version", version, "--out", out],
                       capture_output=True, text=True, timeout=600)
    check("la construction du paquet reussit", r.returncode == 0,
          (r.stderr or "")[-300:])
    if r.returncode != 0:
        print(r.stdout[-800:])
        print(r.stderr[-800:])
        return 1

    zip_path = os.path.join(out, "Casinobabe-Fishing-Assistant-%s.zip" % version)
    check("le zip du paquet existe", os.path.isfile(zip_path))
    if not os.path.isfile(zip_path):
        return 1
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    root = "Casinobabe-Fishing-Assistant-%s/" % version
    rel = [n[len(root):] for n in names if n.startswith(root)]

    check("le VERSION est present", root + "VERSION" in names)
    check("le MANIFEST.json est present", root + "MANIFEST.json" in names)
    check("le lanceur racine est present",
          root + "Lancer-Casinobabe-Fishing-Assistant.bat" in names)
    check("le script de retour arriere est present",
          root + "restaurer_previous.ps1" in names)
    check("l'application desktop est embarquee",
          any(x.endswith("fishing_assistant_app/fishing_assistant.py") for x in rel))
    check("l'addon Lua est embarque",
          any("runtime-addon/Casinobabe/" in x and x.endswith(".lua") for x in rel))
    check("le bot est embarque", any(x.endswith("scripts/fishing_bot.py") for x in rel))
    check("l'observateur est embarque",
          any(x.endswith("scripts/fishing_observer.py") for x in rel))
    check("le module de memoire est embarque",
          any(x.endswith("scripts/fishing_memory.py") for x in rel))
    check("les modules de navigation sont embarques",
          all(any(x.endswith("scripts/%s.py" % m) for x in rel)
              for m in ("fishing_spots", "fishing_routes", "fishing_navigation",
                        "fishing_minimap")))

    # aucune donnee utilisateur
    leaks = [x for x in rel if x.split("/")[0] in
             ("state", "logs", "reports", "captures") or x.endswith(".pyc")]
    check("aucune donnee utilisateur dans le paquet", not leaks, "(%s)" % leaks[:5])

    # integrite du manifeste
    manifest = json.loads(z.read(root + "MANIFEST.json").decode("utf-8"))
    check("le manifeste porte la version", manifest.get("version") == version)
    bad = []
    for rel_path, info in manifest["files"].items():
        entry = root + rel_path
        if entry not in names:
            bad.append(rel_path + " (absent)")
            continue
        import io
        data = z.read(entry)
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            bad.append(rel_path + " (sha256)")
    check("tous les SHA-256 du manifeste correspondent", not bad, "(%s)" % bad[:4])
    check("le manifeste couvre tous les fichiers livres (hors manifeste/lanceur)",
          len(manifest["files"]) >= len(rel) - 4,
          "(%d fichiers declares / %d livres)" % (len(manifest["files"]), len(rel)))

    # la version lue par l'application
    import io
    version_txt = z.read(root + "VERSION").decode("utf-8").strip()
    check("le VERSION contient la version construite", version_txt == version,
          "(%s)" % version_txt)
    app_src = z.read(root + "fishing_assistant_app/fishing_assistant.py").decode("utf-8", "replace")
    check("l'application lit sa version (VERSION)", "VERSION_FILE" in app_src)
    check("l'application propose la verification de mise a jour",
          "check_update_gui" in app_src and "UPDATE_URL" in app_src)

    # reconstruction deterministe
    out2 = tempfile.mkdtemp(prefix="fb-pkg2-")
    subprocess.run([sys.executable,
                    os.path.join(ROOT, "scripts", "build_desktop_package.py"),
                    "--version", version, "--out", out2],
                   capture_output=True, text=True, timeout=600)
    z2 = zipfile.ZipFile(os.path.join(out2, "Casinobabe-Fishing-Assistant-%s.zip" % version))
    m2 = json.loads(z2.read(root + "MANIFEST.json").decode("utf-8"))
    same = all(m2["files"].get(k, {}).get("sha256") == v["sha256"]
               for k, v in manifest["files"].items())
    check("la reconstruction est deterministe (memes empreintes)", same)

    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(out2, ignore_errors=True)
    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
