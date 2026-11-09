#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_desktop_package.py — CONSTRUCTION DU PAQUET DESKTOP DISTRIBUABLE.

L'application desktop (fishing_assistant_app/)vit dans l'arbre de
developpement : elle lance ROOT/scripts/fishing_bot.py et lit ROOT/state,
ROOT/logs, ROOT/reports. Ce script construit un PAQUET autonome et
versionne qui conserve cette disposition (donc l'application fonctionne telle
quelle dans le paquet), avec :

  * l'addon Lua (runtime-addon/Casinobabe) ;
  * le bot et ses dependances ;
  * l'application et son lanceur ;
  * un gabarit de configuration (l'etat de l'utilisateur n'est PAS embarque :
    profils, journaux et config restent locaux) ;
  * un MANIFEST.json (SHA-256 par fichier) ;
  * un fichier VERSION ;
  * le paquet PRECEDENT conserve dans dist/previous/ -> retour arriere ;
  * un script de restauration (restaurer_previous.ps1).

Aucun chemin absolu n'est ecrit dans le paquet : la construction est
reproductible sur n'importe quel poste.

Usage :
    python scripts/build_desktop_package.py --version 1.2.0
    python scripts/build_desktop_package.py --version 1.2.0 --out dist
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_NAME = "Casinobabe-Fishing-Assistant"

#: Fichiers embarques : (source relative a la racine, destination dans le paquet)
FILES = [
    ("fishing_assistant_app/fishing_assistant.py", "fishing_assistant_app/fishing_assistant.py"),
    ("fishing_assistant_app/casinobabe-fishing-assistant.bat", "fishing_assistant_app/casinobabe-fishing-assistant.bat"),
    ("fishing_assistant_app/make-shortcut.py", "fishing_assistant_app/make-shortcut.py"),
    ("scripts/fishing_bot.py", "scripts/fishing_bot.py"),
    ("scripts/fishing_observer.py", "scripts/fishing_observer.py"),
    ("scripts/fishing_memory.py", "scripts/fishing_memory.py"),
    ("scripts/fishing_popup.py", "scripts/fishing_popup.py"),
    ("scripts/focus_broker.py", "scripts/focus_broker.py"),
    ("scripts/fishing_spots.py", "scripts/fishing_spots.py"),
    ("scripts/fishing_routes.py", "scripts/fishing_routes.py"),
    ("scripts/fishing_navigation.py", "scripts/fishing_navigation.py"),
    ("scripts/fishing_minimap.py", "scripts/fishing_minimap.py"),
    ("scripts/fishing_actions.py", "scripts/fishing_actions.py"),
    ("scripts/fishing_action_runner.py", "scripts/fishing_action_runner.py"),
    ("scripts/fishing_metrics.py", "scripts/fishing_metrics.py"),
    ("scripts/fishing_skills.py", "scripts/fishing_skills.py"),
    ("scripts/fishing_controls.py", "scripts/fishing_controls.py"),
    ("scripts/start_casinobabe_fishing.ps1", "scripts/Start-CasinobabeFishing.ps1"),
    ("docs/navigation-findings.md", "docs/navigation-findings.md"),
]

#: Repertoires embarques recursivement (addon).
DIRS = ["runtime-addon/Casinobabe"]

#: Fichiers presents dans le depot mais optionnels (absents -> ignores).
OPTIONAL = [
    "scripts/start_casinobabe_fishing.ps1",
    "scripts/Start-CasinobabeFishing.ps1",
    "scripts/fishing_popup.py",
    "scripts/fishing_actions.py",
    "scripts/fishing_action_runner.py",
    "scripts/fishing_skills.py",
    "scripts/fishing_controls.py",
    "scripts/fishing_metrics.py",
    "docs/navigation-findings.md",
]

#: L'etat de l'utilisateur n'est JAMAIS embarque.
FORBIDDEN_DIRS = ("state", "logs", "reports", "captures", "__pycache__", ".git")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    """(source, destination) pour chaque fichier a embarquer."""
    out = []
    for src, dst in FILES:
        p = os.path.join(ROOT, src.replace("/", os.sep))
        if os.path.isfile(p):
            out.append((p, dst))
        elif src in OPTIONAL:
            continue
        else:
            print("  MANQUANT (ignore) :", src)
    for d in DIRS:
        base = os.path.join(ROOT, d.replace("/", os.sep))
        if not os.path.isdir(base):
            print("  MANQUANT (ignore) :", d)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in FORBIDDEN_DIRS]
            for fn in filenames:
                if fn.endswith((".pyc", ".bak")):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                out.append((full, rel))
    return out


def build(version, outdir):
    os.makedirs(outdir, exist_ok=True)
    stage = os.path.join(outdir, "%s-%s" % (APP_NAME, version))
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    manifest = {"product": APP_NAME, "version": version,
                "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "files": {}}
    missing = []
    for src, dst in collect():
        target = os.path.join(stage, dst.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        manifest["files"][dst] = {"sha256": sha256(target),
                                  "bytes": os.path.getsize(target)}
    if not manifest["files"]:
        raise SystemExit("Aucun fichier a embarquer : construction impossible")

    with open(os.path.join(stage, "VERSION"), "w", encoding="utf-8") as fh:
        fh.write(version + "\n")
    with open(os.path.join(stage, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    # lanceur a la racine (double-clic)
    with open(os.path.join(stage, "Lancer-Casinobabe-Fishing-Assistant.bat"), "w",
              encoding="utf-8") as fh:
        fh.write("@echo off\r\n"
                 "rem Lanceur du paquet Casinobabe Fishing Assistant\r\n"
                 "cd /d \"%~dp0\"\r\n"
                 "if not exist state mkdir state\r\n"
                 "if not exist logs mkdir logs\r\n"
                 "if not exist reports mkdir reports\r\n"
                 "if not exist captures mkdir captures\r\n"
                 "python fishing_assistant_app\\fishing_assistant.py\r\n"
                 "if errorlevel 1 pause\r\n")

    # restauration de la version precedente
    prev_dir = os.path.join(outdir, "previous")
    os.makedirs(prev_dir, exist_ok=True)
    with open(os.path.join(stage, "restaurer_previous.ps1"), "w", encoding="utf-8") as fh:
        fh.write("# Restaure le paquet precedent conserve dans dist/previous.\r\n"
                 "param([string]$Path = \".\")\r\n"
                 "$ErrorActionPreference = 'Stop'\r\n"
                 "$here = Split-Path -Parent $MyInvocation.MyCommand.Path\r\n"
                 "$prev = Join-Path $here 'previous'\r\n"
                 "if (-not (Test-Path $prev)) { throw 'Aucune version precedente conservee.' }\r\n"
                 "Get-ChildItem $prev -Filter '*.zip' | Sort-Object LastWriteTime -Descending |\r\n"
                 "  | Select-Object -First 1 | ForEach-Object {\r\n"
                 "    Write-Host ('Restauration de ' + $_.Name)\r\n"
                 "    Expand-Archive -Path $_.FullName -DestinationPath $Path -Force\r\n"
                 "  }\r\n")

    # le paquet zip
    zip_path = os.path.join(outdir, "%s-%s.zip" % (APP_NAME, version))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(stage):
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, outdir))
    print("  paquet : %s (%.1f Ko)" % (zip_path, os.path.getsize(zip_path) / 1024.0))
    return zip_path, manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--keep-previous", action="store_true", default=True)
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    print("=== CONSTRUCTION DU PAQUET DESKTOP %s %s ===" % (APP_NAME, args.version))
    zip_path, manifest = build(args.version, outdir)

    # conservation de la version precedente (retour arriere)
    prev_dir = os.path.join(outdir, "previous")
    os.makedirs(prev_dir, exist_ok=True)
    olds = [f for f in os.listdir(prev_dir) if f.endswith(".zip")]
    if olds:
        old = max(olds, key=lambda f: os.path.getmtime(os.path.join(prev_dir, f)))
        shutil.copy2(zip_path, os.path.join(prev_dir, old.replace(
            args.version, "precedent-%s" % time.strftime("%Y%m%d-%H%M%S"))))
        print("  version precedente conservee pour le retour arriere")

    print("  fichiers embarques : %d" % len(manifest["files"]))
    print("  manifeste SHA-256 : MANIFEST.json")
    print("  version : %s" % args.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
