#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cible de test INOFFENSIVE pour le Control Center.

Ce processus imite la STRUCTURE observable du bot de peche (verrou PID +
journal JSONL de progression) sans faire RIEN du tout : il n'ouvre aucune
fenetre, n'envoie aucune entree, ne regarde pas WoW. Il sert a valider le
chemin de code START / PAUSE / REPRISE / STOP / anti-doublon sans lancer le
vrai bot (interdit pendant une session de jeu).

Modes :
  progress  ecrit regulierement des evenements de progression -> ACTIF
  silent    ecrit un banal heartbeat NON progressif            -> BLOQUE
  error     ecrit un evenement ERROR                          -> erreur
  quiet     n'ecrit rien du tout                               -> BLOQUE

Usage :
    python tests/fake_bot_target.py --pid-file X --progress-log Y --mode progress
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime

_running = True


def _stop(_signum, _frame):
    global _running
    _running = False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid-file", required=True)
    ap.add_argument("--progress-log", default="")
    ap.add_argument("--mode", default="progress",
                    choices=["progress", "silent", "error", "quiet"])
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--seconds", type=float, default=300.0)
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    os.makedirs(os.path.dirname(os.path.abspath(args.pid_file)), exist_ok=True)
    with open(args.pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    log = args.progress_log
    if log:
        os.makedirs(os.path.dirname(os.path.abspath(log)), exist_ok=True)

    def emit(event: str, **kw) -> None:
        if not log:
            return
        row = {"ts": datetime.now().isoformat(timespec="milliseconds"),
               "event": event, "state": "WAITING FOR BITE"}
        row.update(kw)
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    emit("BOT_START", pid=os.getpid(), mode=args.mode)
    deadline = time.time() + args.seconds
    tick = 0
    while _running and time.time() < deadline:
        tick += 1
        if args.mode == "progress":
            emit("WATCH", conf=0.7, tick=tick)
        elif args.mode == "silent":
            emit("HEARTBEAT_SEUL", tick=tick)
        elif args.mode == "error":
            emit("ERROR", reason="echec simule", tick=tick)
        time.sleep(args.interval)

    try:
        os.remove(args.pid_file)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
