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
    if "--remove" in sys.argv:
        sys.exit(remove())
    sys.exit(make("--force" in sys.argv))
