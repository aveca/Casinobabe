#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cree le raccourci Bureau « Casinobabe Control Center ».

Sans danger :
  * n'ecrit QUE le fichier .lnk du raccourci ;
  * ne touche ni a la configuration du bot, ni au depot, ni a WoW ;
  * refuse d'ecraser un raccourci existant sans `--force` ;
  * n'ecrase jamais le raccourci « Casinobabe Launcher » (l'ancien launcher
    est conserve).

    python scripts/create_control_center_shortcut.py            # creer
    python scripts/create_control_center_shortcut.py --force    # recreer
    python scripts/create_control_center_shortcut.py --verify   # verifier
    python scripts/create_control_center_shortcut.py --remove   # supprimer
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
TARGET = os.path.join(_HERE, "control_center.py")
LINK_NAME = "Casinobabe Control Center.lnk"
PROTECTED = {"Casinobabe Launcher.lnk", "Casinobabe Fishing Assistant.lnk"}

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def desktop_dir() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        with key:
            return winreg.QueryValueEx(key, "Desktop")[0]
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def pythonw_exe() -> str:
    """pythonw.exe du meme interpreteur : aucune fenetre console."""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    if "pythonw" in os.path.basename(exe).lower():
        return exe
    return exe      # fallback : le lanceur .bat/.vbs peut masquer la console


def is_scratch_checkout(root: str) -> bool:
    """Vrai si `root` est un worktree jetable de la factory (C:/factory/worktrees).

    Un raccourci pointe sur un chemin ABSOLU : si le worktree est supprime, le
    raccourci devient mort sans que rien ne le signale. On detecte donc ce cas
    et on le dit plutot que de laisser une bombe a retardement.
    """
    low = os.path.abspath(root).lower().replace("/", "\\")
    return "\\factory\\worktrees\\" in low


def describe_target() -> None:
    """Affiche ou en est le raccourci, et signale les chemins fragiles."""
    link = os.path.join(desktop_dir(), LINK_NAME)
    print("Raccourci        : %s" % link)
    print("Existant         : %s" % ("oui" if os.path.exists(link) else "NON"))
    if not os.path.exists(link):
        return
    try:
        from win32com.client import Dispatch
        sc = Dispatch("WScript.Shell").CreateShortCut(link)
    except Exception as exc:
        print("Lecture impossible : %s" % exc)
        return
    target = os.path.normpath(sc.Arguments.strip('"'))
    print("Cible            : %s" % target)
    print("Cible presente   : %s" % ("oui" if os.path.isfile(target) else "NON - RACCOURCI MORT"))
    print("Interpreteur     : %s" % sc.TargetPath)
    print("Sans console     : %s" % ("oui" if "pythonw" in sc.TargetPath.lower() else "NON"))
    print("Repertoire       : %s" % sc.WorkingDirectory)
    if os.path.isfile(target) and is_scratch_checkout(os.path.dirname(target)):
        print("")
        print("AVERTISSEMENT : la cible est dans un worktree jetable de la factory")
        print("  (%s)." % os.path.dirname(target))
        print("  Ce worktree peut etre supprime : le raccourci deviendra mort.")
        print("  Recreez le raccourci depuis le depot canonique une fois la branche")
        print("  fusionnee :  python scripts\\create_control_center_shortcut.py --force")


def verify() -> int:
    link = os.path.join(desktop_dir(), LINK_NAME)
    if not os.path.exists(link):
        print("ECHEC : aucun raccourci sur le Bureau.")
        return 1
    try:
        from win32com.client import Dispatch
        sc = Dispatch("WScript.Shell").CreateShortCut(link)
    except Exception as exc:
        print("ECHEC : lecture impossible : %s" % exc)
        return 1
    target = os.path.normpath(sc.Arguments.strip('"'))
    problems = []
    if not os.path.isfile(target):
        problems.append("la cible n'existe plus : %s" % target)
    if not os.path.isfile(sc.TargetPath):
        problems.append("l'interpreteur n'existe plus : %s" % sc.TargetPath)
    if "pythonw" not in sc.TargetPath.lower():
        problems.append("l'interpreteur ouvre une fenetre console : %s" % sc.TargetPath)
    if os.path.isfile(target) and not target.endswith("control_center.py"):
        problems.append("la cible ne pointe pas sur control_center.py : %s" % target)
    describe_target()
    if problems:
        print("")
        for p in problems:
            print("ECHEC : %s" % p)
        return 1
    print("")
    print("OK : le raccourci est valide.")
    return 0


def make(force: bool = False) -> int:
    if not os.path.isfile(TARGET):
        print("ERREUR : application introuvable : %s" % TARGET)
        return 2
    desk = desktop_dir()
    os.makedirs(desk, exist_ok=True)
    link = os.path.join(desk, LINK_NAME)
    if os.path.basename(link) in PROTECTED:      # garde-fou supplémentaire
        print("REFUS : ce raccourci est protege.")
        return 1
    if os.path.exists(link) and not force:
        print("Le raccourci existe deja : %s" % link)
        print("Utilisez --force pour le recreer, --remove pour le supprimer.")
        return 1
    try:
        from win32com.client import Dispatch
    except ImportError:
        print("ERREUR : pywin32 absent. Installer avec :  pip install pywin32")
        return 2
    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(link)
    sc.Targetpath = pythonw_exe()
    sc.Arguments = '"%s"' % TARGET
    sc.WorkingDirectory = ROOT
    sc.Description = "Casinobabe Control Center (peche + casino)"
    sc.save()
    print("Raccourci cree : %s" % link)
    print("  cible      : %s" % pythonw_exe())
    print("  arguments  : \"%s\"" % TARGET)
    if is_scratch_checkout(ROOT):
        print("")
        print("AVERTISSEMENT : ce depot est un worktree jetable de la factory")
        print("  (%s)." % ROOT)
        print("  Le raccourci ne survivra pas a sa suppression. Recreer le depuis")
        print("  le depot canonique apres fusion de la branche.")
    return 0


def remove() -> int:
    link = os.path.join(desktop_dir(), LINK_NAME)
    if not os.path.exists(link):
        print("Aucun raccourci a supprimer.")
        return 0
    if os.path.basename(link) in PROTECTED:
        print("REFUS : ce raccourci est protege.")
        return 1
    os.remove(link)
    print("Raccourci supprime : %s" % link)
    return 0


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(verify())
    if "--remove" in sys.argv:
        sys.exit(remove())
    sys.exit(make("--force" in sys.argv))
