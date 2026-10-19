#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""casino_bot — moteur casino AUTONOME (processus Windows independant).

Ce process est le premier « bot casino » reel du depot. Il ne depend NI de
l'addon Lua, NI de son panneau d'interface : il tourne seul, avec son propre
verrou, son heartbeat et ses journaux. L'addon peut disparaitre : le moteur
continue.

    python scripts/casino_bot.py --cli        # boucle de service (bout en bout)
    python scripts/casino_bot.py --once       # un tick watchdog puis sortie
    python scripts/casino_bot.py --status     # etat relu depuis le disque
    python scripts/casino_bot.py --selftest   # auto-test hors ligne

Ressources (strictement separees du bot peche)
==============================================
  verrou      state/casino-bot.pid
  config      state/casino-bot-config.json
  journal     reports/casino-bot-log.jsonl
  etat        state/casino-bot-state.json
  jeton IPC   state/casino-ipc-token.txt
  pipe        \\.\pipe\casinobabe-casino

Aucune de ces ressources n'est partagee avec le bot peche : arreter le casino
n'arrete jamais la peche, et reciproquement.

FRONTIERE AVEC LE CLIENT WOC
============================
Le moteur ne simule AUCUNE interaction en jeu. Il ne connait pas WoW. Les
actions qui exigent le client (`SendChatMessage`, `InitiateTrade`, `InviteUnit`)
sont calculees puis empilees dans `pending_actions`, que l'addon vient
recuperer par `drain_actions`. Si l'addon est absent, les actions s'accumulent
et le moteur continue : il calcule juste.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import casino_engine as engine  # noqa: E402
import casino_ipc as ipc  # noqa: E402
import window_guard  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(_HERE)
STATE_DIR = os.path.join(ROOT, "state")
REPORTS_DIR = os.path.join(ROOT, "reports")
LOGS_DIR = os.path.join(ROOT, "logs")
for _d in (STATE_DIR, REPORTS_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)

PID_FILE = os.path.join(STATE_DIR, "casino-bot.pid")
CONFIG_FILE = os.path.join(STATE_DIR, "casino-bot-config.json")
STATE_FILE = os.path.join(STATE_DIR, "casino-bot-state.json")
LOG_FILE = os.path.join(REPORTS_DIR, "casino-bot-log.jsonl")
STDERR_LOG = os.path.join(LOGS_DIR, "casino-bot.err.log")

#: Evenements qui prouvent que le moteur TRAVAILLE (pas seulement qu'il vit).
PROGRESS_EVENTS = (
    "HEARTBEAT", "SESSION_CREATED", "GAME_SELECTED", "STAKE_ARMED",
    "ROLL_RECORDED", "RESOLVED", "PAYOUT_ARMED", "PAID", "CLOSED",
    "RECOVERED", "WATCHDOG_TICK", "TIMEOUT_FLAGGED", "INTEGRITY_FIXED",
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "heartbeatSec": 2.0,          # un HEARTBEAT toutes les N secondes
    "watchdogSec": 5.0,          # balayage timeouts / integrite
    "autoRecoverOnTimeout": False,  # MI MIS : la decision reste humaine
    "maxSessionsRetained": 50,
    "ipcTimeoutSec": 5.0,
    "shutdownGraceSec": 5.0,
}

#: Parametres exposes a l'interface : (type, min, max). Toute autre cle du
#: fichier est conservee telle quelle a l'enregistrement.
CONFIG_SCHEMA: Dict[str, Tuple[type, float, float]] = {
    "heartbeatSec": (float, 0.2, 60.0),
    "watchdogSec": (float, 1.0, 600.0),
    "autoRecoverOnTimeout": (bool, 0, 1),
    "maxSessionsRetained": (int, 1, 1000),
    "ipcTimeoutSec": (float, 0.5, 60.0),
    "shutdownGraceSec": (float, 1.0, 120.0),
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json_atomic(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_config() -> Dict[str, Any]:
    disk = load_json(CONFIG_FILE, {})
    if not isinstance(disk, dict):
        disk = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in disk.items() if k in DEFAULT_CONFIG})
    return merged


def save_config(updates: Dict[str, Any]) -> Tuple[bool, List[str], str]:
    """Valide puis ecrit SANS ecraser les cles inconnues du fichier."""
    clean: Dict[str, Any] = {}
    errors: List[str] = []
    for key, raw in updates.items():
        spec = CONFIG_SCHEMA.get(key)
        if spec is None:
            errors.append("%s : parametre inconnu du moteur casino" % key)
            continue
        typ, lo, hi = spec
        try:
            if typ is bool:
                if isinstance(raw, str):
                    if raw.strip().lower() not in ("1", "0", "true", "false",
                                                   "oui", "non", "yes", "no",
                                                   "on", "off"):
                        raise ValueError
                    val = raw.strip().lower() in ("1", "true", "oui", "yes", "on")
                else:
                    val = bool(raw)
            elif typ is int:
                val = int(float(raw))
            else:
                val = float(raw)
        except (TypeError, ValueError):
            errors.append("%s : valeur invalide %r" % (key, raw))
            continue
        if lo is not None and not (lo <= val <= hi):
            errors.append("%s : %s hors bornes [%s, %s]" % (key, val, lo, hi))
            continue
        clean[key] = val
    if errors:
        return False, errors, "aucune ecriture"
    disk = load_json(CONFIG_FILE, {})
    if not isinstance(disk, dict):
        disk = {}
    disk.update(clean)
    try:
        write_json_atomic(CONFIG_FILE, disk)
    except OSError as exc:
        return False, ["ecriture impossible : %s" % exc], "echec"
    return True, [], "enregistre : %s" % (", ".join(sorted(clean)) or "rien")


# ---------------------------------------------------------------------------
# Verrou (mecanisme officiel, partage avec le bot peche)
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    except Exception:
        return False


def acquire_pid_lock(path: str = PID_FILE) -> Tuple[bool, str]:
    """Reutilise EXACTEMENT le mecanisme officiel du bot peche
    (fishing_observer.acquire_single_instance) : fichier PID + verification
    que le processus est bien vivant ET bien un python."""
    try:
        import fishing_observer
        if fishing_observer.acquire_single_instance(pid_path=path):
            return True, "verrou pris (PID %d)" % os.getpid()
        lock = read_pid_lock(path)
        return False, ("une instance est deja vivante (PID %s) ; "
                       "verrou %s" % (lock["pid"], path))
    except ImportError:
        pass
    if os.path.isfile(path):
        try:
            old = int(open(path, encoding="utf-8").read().strip())
            if old != os.getpid() and _pid_alive(old):
                return False, "une instance est deja vivante (PID %d)" % old
        except (OSError, ValueError):
            pass
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True, "verrou pris (PID %d)" % os.getpid()


def release_pid_lock(path: str = PID_FILE) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def read_pid_lock(path: str = PID_FILE) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"status": "ABSENT", "pid": 0, "alive": False}
    try:
        pid = int(open(path, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return {"status": "UNREADABLE", "pid": 0, "alive": False}
    alive = _pid_alive(pid)
    return {"status": "LOCKED_ALIVE" if alive else "STALE", "pid": pid, "alive": alive}


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

class Journal:
    """Journal JSONL + heartbeat. Distinct de celui du bot peche."""

    def __init__(self, path: str = LOG_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.last_event: str = ""
        self.last_ts: float = 0.0
        self.last_error: Optional[Dict[str, Any]] = None

    def log(self, event: str, **kw) -> None:
        row = {"ts": datetime.now().isoformat(timespec="milliseconds"),
               "event": event, "pid": os.getpid()}
        row.update(kw)
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError:
                pass
            self.last_event = event
            self.last_ts = time.time()
            if event in ("ERROR", "CRASH", "WATCHDOG_ERROR"):
                self.last_error = {"event": event, "ts": row["ts"],
                                   "detail": kw.get("reason", "")}

    def tail(self, lines: int = 200) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.path):
            return []
        try:
            size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                if size > 256 * 1024:
                    fh.seek(size - 256 * 1024)
                    fh.readline()
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            return []
        out = []
        for line in raw.splitlines()[-lines:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def progress_age(self, events: Tuple[str, ...] = PROGRESS_EVENTS) -> Optional[float]:
        """Age du dernier evenement de PROGRES (et non du dernier heartbeat
        quand celui-ci n'est pas un evenement de travail)."""
        best: Optional[float] = None
        now = time.time()
        for row in reversed(self.tail(400)):
            if row.get("event") in events:
                try:
                    dt = datetime.fromisoformat(str(row.get("ts")))
                    best = max(0.0, now - dt.timestamp())
                    break
                except Exception:
                    continue
        return best


#: Alias historique conserve (utilise comme defaut avant renommage).
TUPLE_PROGRESS = PROGRESS_EVENTS


# ---------------------------------------------------------------------------
# Le moteur
# ---------------------------------------------------------------------------

class CasinoBot:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = dict(DEFAULT_CONFIG, **(config or load_config()))
        self.store = engine.SessionStore(enabled=True)
        self.journal = Journal()
        self.started_at = time.time()
        self.stop_flag = threading.Event()
        self.state = "DEMARRAGE"
        self.last_error: Optional[str] = None
        self.watchdog_ticks = 0
        self.commands_handled = 0
        self._lock = threading.RLock()

    # -- persistance ---------------------------------------------------
    def save_state(self) -> None:
        try:
            write_json_atomic(STATE_FILE, {
                "schema": 1,
                "pid": os.getpid(),
                "started_at": datetime.fromtimestamp(
                    self.started_at, timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "state": self.state,
                "commands_handled": self.commands_handled,
                "watchdog_ticks": self.watchdog_ticks,
                "last_error": self.last_error,
                "engine": self.store.to_dict(),
            })
        except OSError as exc:
            self.journal.log("STATE_WRITE_ERROR", reason=str(exc))

    def restore_state(self) -> int:
        """Reprise propre apres arret/redemarrage du processus."""
        blob = load_json(STATE_FILE, {})
        if not isinstance(blob, dict) or not blob.get("engine"):
            return 0
        n = self.store.load_dict(blob["engine"])
        self.watchdog_ticks = int(blob.get("watchdog_ticks", 0) or 0)
        if n:
            self.journal.log("RESTORE", sessions=n)
        return n

    # -- activite ------------------------------------------------------
    def tick_heartbeat(self) -> None:
        self.journal.log("HEARTBEAT", state=self.state,
                         active_session=(self.store.active().player
                                         if self.store.active() else None))

    def tick_watchdog(self) -> List[str]:
        self.watchdog_ticks += 1
        flagged = self.store.check_timeouts()
        for entry in flagged:
            self.journal.log("TIMEOUT_FLAGGED", detail=entry)
        fixed = self.store.check_integrity()
        for entry in fixed:
            self.journal.log("INTEGRITY_FIXED", detail=entry)
        if flagged:
            if self.config.get("autoRecoverOnTimeout"):
                # Comportement OPT-IN, absent du code Lua : par defaut on
                # signale et on attend une decision humaine.
                self.store.recover(None, "autoRecoverOnTimeout")
                self.journal.log("RECOVERED", detail="auto (option)")
            else:
                self.state = "BLOQUE"
                self.last_error = "session(s) en timeout : %s" % "; ".join(flagged)
        elif not fixed:
            if self.state == "BLOQUE":
                self.state = "ACTIF"
                self.last_error = None
        self.journal.log("WATCHDOG_TICK", flagged=len(flagged), fixed=len(fixed))
        return flagged

    def status(self) -> Dict[str, Any]:
        active = self.store.active()
        return {
            "state": self.state,
            "pid": os.getpid(),
            "uptime_sec": round(time.time() - self.started_at, 1),
            "started_at": datetime.fromtimestamp(
                self.started_at, timezone.utc).isoformat(),
            "last_heartbeat": self.journal.last_ts,
            "last_heartbeat_event": self.journal.last_event,
            "progress_age_sec": self.journal.progress_age(),
            "last_error": self.last_error,
            "watchdog_ticks": self.watchdog_ticks,
            "commands_handled": self.commands_handled,
            "active_session": active.player if active else None,
            "active_state": active.state if active else None,
            "sessions": {k: v.to_dict() for k, v in self.store.sessions.items()},
            "pending_actions": {p: len(s.pending_actions)
                                for p, s in self.store.sessions.items()
                                if s.pending_actions},
            "config": dict(self.config),
            "rules": engine.game_keys(),
            "timeouts": dict(engine.CORE_TIMEOUTS),
        }

    # -- commandes IPC -------------------------------------------------
    def handle(self, cmd: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Toute commande est validee et repond explicitement. Jamais d'exception
        non rattrapee : une erreur isole ce moteur, n'arrete ni le bot peche ni
        la Factory."""
        with self._lock:
            self.commands_handled += 1
            try:
                reply = self._dispatch(cmd, params)
            except Exception as exc:
                # Les erreurs metier doivent remonter leur CODE, pas le nom de
                # l'exception Python : l'interface affiche le code.
                code = exc.args[0] if isinstance(exc, ValueError) and exc.args \
                    else "%s: %s" % (type(exc).__name__, exc)
                self.last_error = str(code)
                self.journal.log("ERROR", cmd=cmd, reason=str(code))
                self.state = "ERREUR"
                return {"ok": False, "error": str(code), "cmd": cmd}
            self.journal.log("COMMAND", cmd=cmd, ok=bool(reply.get("ok")),
                             error=reply.get("error"))
            if self.state != "BLOQUE":
                self.state = "ACTIF"
            return reply

    def _need(self, params: Dict[str, Any]) -> str:
        player = params.get("player")
        if not player or not str(player).strip():
            raise ValueError(engine.ERRORS["ERR_NO_PLAYER"])
        return str(player).strip()

    def _dispatch(self, cmd: str, p: Dict[str, Any]) -> Dict[str, Any]:
        if cmd == "ping":
            return {"ok": True, "pong": True, "pid": os.getpid()}
        if cmd == "status":
            return {"ok": True, "status": self.status()}
        if cmd == "rules":
            return {"ok": True, "rules": {
                k: {"name": r.name, "roll_min": r.roll_min, "roll_max": r.roll_max,
                    "stake_min": r.stake_min, "stake_max": r.stake_max,
                    "colors": list(r.colors), "choices": list(r.choices),
                    "single_roll": r.single_roll, "description": r.description}
                for k, r in engine.GAME_RULES.items()}}
        if cmd == "create_session":
            s, err = self.store.create(p.get("player"))
            if s is None:
                return {"ok": False, "error": err}
            self.journal.log("SESSION_CREATED", player=s.player,
                             session_id=s.session_id)
            return {"ok": True, "session": s.to_dict()}
        if cmd == "list_sessions":
            return {"ok": True, "sessions": {k: v.to_dict()
                                             for k, v in self.store.sessions.items()}}
        if cmd == "game":
            player = self._need(p)
            ok, err = self.store.game(player, p.get("game"), p.get("extra"))
            self.journal.log("GAME_SELECTED", player=player, game=p.get("game"),
                             extra=p.get("extra"), ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "stake":
            player = self._need(p)
            ok, err = self.store.stake(player, p.get("amount"))
            self.journal.log("STAKE_ARMED", player=player, amount=p.get("amount"),
                             ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "confirm_trade":
            player = self._need(p)
            pm = p.get("player_money")
            tm = p.get("target_money")
            if pm is None or tm is None:
                return {"ok": False, "error": "player_money/target_money manquants"}
            ok, verif, msg = self.store.confirm_trade(player, pm, tm)
            self.journal.log("TRADE_VERIFIED" if ok else "TRADE_MISMATCH",
                             player=player, verified=verif, msg=msg)
            return {"ok": ok, "error": None if ok else msg,
                    "verification": verif,
                    "state": msg if isinstance(msg, str) and msg in engine.STATES.values() else None}
        if cmd == "mark_stake_confirmed":
            player = self._need(p)
            ok, err = self.store.mark_stake_confirmed(player)
            self.journal.log("STAKE_CONFIRMED", player=player, ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "mark_grouped":
            player = self._need(p)
            ok, err = self.store.mark_grouped(player)
            self.journal.log("GROUPED", player=player, ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "roll":
            player = self._need(p)
            ok, err = self.store.record_roll(player, p.get("roll"))
            s = self.store.get(player)
            self.journal.log("ROLL_RECORDED", player=player, roll=p.get("roll"),
                             ok=ok, error=err)
            # NE PAS vider la file d'actions ici : une commande en echec ne doit
            # jamais consommer les actions en attente de l'addon. Seul
            # `drain_actions` les recupere, explicitement.
            return {"ok": ok, "error": err,
                    "session": s.to_dict() if s else None,
                    "pending_action_count": len(s.pending_actions) if s else 0}
        if cmd == "resolve":
            player = self._need(p)
            ok, err = self.store.resolve(player)
            s = self.store.get(player)
            return {"ok": ok, "error": err, "session": s.to_dict() if s else None}
        if cmd == "house_total":
            player = self._need(p)
            if p.get("house_total") is None:
                return {"ok": False, "error": "house_total manquant"}
            ok, err = self.store.set_house(player, p["house_total"])
            return {"ok": ok, "error": err}
        if cmd == "house_rolls":
            player = self._need(p)
            d1, d2 = p.get("die1"), p.get("die2")
            if d1 is None or d2 is None:
                return {"ok": False, "error": "die1/die2 manquants"}
            ok, err = self.store.set_house_rolls(player, d1, d2)
            return {"ok": ok, "error": err}
        if cmd == "arm_payout":
            player = self._need(p)
            ok, err = self.store.arm_payout(player)
            return {"ok": ok, "error": err}
        if cmd == "mark_paid":
            player = self._need(p)
            ok, err = self.store.mark_paid(player)
            return {"ok": ok, "error": err}
        if cmd == "close":
            ok, err = self.store.close(p.get("player"))
            self.journal.log("CLOSED", player=p.get("player"), ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "recover":
            ok, err = self.store.recover(p.get("player"),
                                         p.get("reason", "recover command"))
            self.journal.log("RECOVERED", player=p.get("player"), ok=ok, error=err)
            return {"ok": ok, "error": err}
        if cmd == "drain_actions":
            player = p.get("player")
            out: List[Dict[str, Any]] = []
            if player:
                s = self.store.get(player)
                if s:
                    out.extend(s.drain_actions())
            else:
                for s in list(self.store.sessions.values()):
                    out.extend(s.drain_actions())
            return {"ok": True, "actions": out}
        if cmd == "check_integrity":
            problems = self.store.check_integrity()
            return {"ok": True, "problems": problems}
        if cmd == "check_timeouts":
            return {"ok": True, "flagged": self.store.check_timeouts()}
        if cmd == "shutdown":
            self.stop_flag.set()
            return {"ok": True, "stopping": True, "grace_sec":
                    self.config.get("shutdownGraceSec")}
        return {"ok": False, "error": "commande non implementee : %s" % cmd}

    # -- boucle --------------------------------------------------------
    def run(self) -> int:
        acquired, why = acquire_pid_lock()
        if not acquired:
            sys.stderr.write("ERREUR : %s\n" % why)
            return 2
        atexit.register(release_pid_lock)

        token, note = ipc.create_token()
        self.journal.log("BOT_START", pid=os.getpid(), token_note=note,
                         rules=engine.game_keys())
        restored = self.restore_state()
        self.state = "ACTIF"
        self.save_state()

        guard_ok, guard_detail = window_guard.safe_call(
            "export_diagnostics", lambda: "casino_bot pret")

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        server = ipc.Server(token, self.handle,
                            timeout=float(self.config["ipcTimeoutSec"]))
        try:
            with server:
                self.journal.log("IPC_READY", pipe=ipc.pipe_address("server"),
                                 guard=guard_detail)
                # L'ecoute part dans un thread DAEMON. Motif : sur Windows,
                # `Listener.accept()` bloque indefiniment ; s'il tournait dans
                # le thread principal, la commande `shutdown` serait bien
                # recue (traitee dans un thread separe) mais `run()` ne
                # repartirait JAMAIS : le verrou PID ne serait jamais libere
                # et le moteur resterait vivant apres un arret « reussi ».
                # Constate en test reel le 2026-10-19.
                threading.Thread(target=self._accept_loop, args=(server,),
                                 daemon=True).start()
                while not self.stop_flag.is_set():
                    self.stop_flag.wait(0.2)
        except ipc.IpcError as exc:
            self.last_error = str(exc)
            self.journal.log("IPC_ERROR", reason=str(exc))
            self.state = "ERREUR"

        self.state = "ARRETE"
        self.save_state()
        self.journal.log("BOT_STOP", pid=os.getpid(),
                         uptime=round(time.time() - self.started_at, 1),
                         restored=restored, commands=self.commands_handled)
        release_pid_lock()
        # Sortie deterministe : le thread d'ecoute est bloque dans accept(),
        # on ne peut pas l'attendre. Verrno libere, etat sauvegarde : aucune
        # donnee n'est perdue.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def _accept_loop(self, server) -> None:
        """Accepte les connexions dans des threads : une connexion lente ne
        doit jamais arreter le heartbeat ni le watchdog."""
        from multiprocessing.connection import Listener
        while not self.stop_flag.is_set():
            try:
                conn = server._listener.accept()
            except Exception:
                if self.stop_flag.is_set():
                    return
                time.sleep(0.1)
                continue
            t = threading.Thread(target=server.handle_once, args=(conn,), daemon=True)
            t.start()

    def _heartbeat_loop(self) -> None:
        hb = max(0.2, float(self.config["heartbeatSec"]))
        wd = max(1.0, float(self.config["watchdogSec"]))
        next_hb = 0.0
        next_wd = 0.0
        while not self.stop_flag.is_set():
            now = time.time()
            if now >= next_hb:
                try:
                    self.tick_heartbeat()
                except Exception as exc:
                    self.journal.log("HEARTBEAT_ERROR", reason=str(exc))
                next_hb = now + hb
            if now >= next_wd:
                try:
                    self.tick_watchdog()
                    self.save_state()
                except Exception as exc:
                    self.journal.log("WATCHDOG_ERROR", reason=str(exc))
                next_wd = now + wd
            self.stop_flag.wait(0.2)
        # derniers soins
        try:
            self.save_state()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modes hors service
# ---------------------------------------------------------------------------

def read_status() -> Dict[str, Any]:
    """Etat relu depuis le DISQUE : c'est ce que voit une interface qui
    vient d'etre rouverte."""
    lock = read_pid_lock()
    journal = Journal()
    blob = load_json(STATE_FILE, {})
    alive = bool(lock.get("alive"))
    age = journal.progress_age()
    if not alive:
        state = "ARRETE"
    elif age is None:
        state = "DEMARRAGE"
    elif age > 60:
        state = "BLOQUE"
    else:
        state = "ACTIF"
    return {
        "state": state,
        "pid": lock.get("pid", 0),
        "lock": lock["status"],
        "last_heartbeat": journal.last_ts,
        "last_event": journal.last_event,
        "progress_age_sec": age,
        "last_error": journal.last_error,
        "started_at": blob.get("started_at") if isinstance(blob, dict) else None,
        "watchdog_ticks": blob.get("watchdog_ticks") if isinstance(blob, dict) else None,
        "active_session": blob.get("engine", {}).get("sessions", {})
        if isinstance(blob, dict) else None,
    }


# ---------------------------------------------------------------------------
# Auto-test
# ---------------------------------------------------------------------------

def _ipc_raises(fn) -> bool:
    try:
        fn()
        return False
    except ipc.IpcError:
        return True
    except Exception:
        return False


def _selftest() -> int:
    print("=" * 72)
    print("casino_bot — auto-test du moteur AUTONOME (aucun WoW, aucune reseau)")
    print("=" * 72)
    fails: List[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                               (" - " + detail) if detail else ""))
        if not cond:
            fails.append(label)

    # --- regles reelles portees depuis le Lua ---
    check("6 jeux extraits", engine.game_keys() ==
          ["blackjack", "dice", "high", "lucky7", "normal", "roulette"])
    check("normal 59 -> x2", engine.GAME_RULES["normal"].resolve(59) == 2)
    check("normal 100 -> x3", engine.GAME_RULES["normal"].resolve(100) == 3)
    check("normal 58 -> perte", engine.GAME_RULES["normal"].resolve(58) == 0)
    check("high 76 -> x3", engine.GAME_RULES["high"].resolve(76) == 3)
    check("high 100 -> x4", engine.GAME_RULES["high"].resolve(100) == 4)
    check("lucky7 27 -> x7", engine.GAME_RULES["lucky7"].resolve(27) == 7)
    check("lucky7 28 -> perte", engine.GAME_RULES["lucky7"].resolve(28) == 0)
    check("roulette rouge 45 -> x2", engine.GAME_RULES["roulette"].resolve(45, "red") == 2)
    check("roulette rouge 46 -> perte", engine.GAME_RULES["roulette"].resolve(46, "red") == 0)
    check("roulette vert 50 -> x5", engine.GAME_RULES["roulette"].resolve(50, "green") == 5)
    check("roulette noir 100 -> x2", engine.GAME_RULES["roulette"].resolve(100, "black") == 2)
    check("blackjack bust maison -> joueur gagne",
          engine.GAME_RULES["blackjack"].resolve(80, 120) == 1)
    check("blackjack egalite -> push",
          engine.GAME_RULES["blackjack"].resolve(90, 90) == 2)
    check("dice seven 3+4 -> x4", engine.GAME_RULES["dice"].resolve("seven", [3, 4]) == 4)
    check("dice over 5+5 -> x2", engine.GAME_RULES["dice"].resolve("over", [5, 5]) == 2)

    # --- payouts (convention nette du Lua) ---
    p, _ = engine.compute_payout("normal", 100, 70)          # x2 -> net +100
    check("payout normal x2 net = stake", p == 100, str(p))
    p, _ = engine.compute_payout("normal", 100, 100)         # x3 -> net +200
    check("payout normal x3 net = 2 x stake", p == 200, str(p))
    p, _ = engine.compute_payout("normal", 100, 10)          # perte
    check("payout perte = -stake", p == -100, str(p))
    p, _ = engine.compute_payout("blackjack", 100, 80, 120)  # maison bust
    check("payout blackjack gain = +stake", p == 100, str(p))
    p, _ = engine.compute_payout("blackjack", 100, 90, 90)   # push
    check("payout blackjack push = 0", p == 0, str(p))
    p, err = engine.compute_payout("inexistant", 10, 1)
    check("payout jeu inconnu refuse", p is None and err == "ERR_INVALID_GAME")
    # Bug reel de l'addon : roulette gagnante payee comme une perte.
    p_bad, _ = engine.compute_payout_lua_faithful("roulette", 50, "red")
    check("BUG ADDON reproduit : roulette rouge payee -stake", p_bad == -50, str(p_bad))
    p_ok, _ = engine.compute_payout("roulette", 50, 30, "red")
    check("BUG ADDON corrige : roulette rouge 30 payee +stake", p_ok == 50, str(p_ok))

    # --- validations ---
    check("mise 0 refusee", engine.validate_stake("normal", 0)[0] is False)
    check("mise 1001 refusee", engine.validate_stake("normal", 1001)[0] is False)
    check("mise 1000 acceptee", engine.validate_stake("normal", 1000)[0] is True)
    check("mise sans jeu = union des bornes",
          engine.validate_stake(None, 1000)[0] is True)
    check("roll dice 13 refuse",
          engine.validate_roll("dice", 13)[1] == "ERR_ROLL_OUT_OF_RANGE")
    check("couleur roulette invalide refusee", engine.validate_roulette_color("bleu") is False)
    check("choix dice invalide refuse", engine.validate_dice_choice("cote") is False)

    # --- machine a etats ---
    ok, _ = engine.validate_transition("NEW", "CONTACTED")
    check("NEW -> CONTACTED autorise", ok)
    ok, err = engine.validate_transition("CLOSED", "CONTACTED")
    check("CLOSED est terminal", not ok and err == "ERR_INVALID_STATE")
    ok, err = engine.validate_transition("PAYOUT_PENDING", "ERROR")
    check("PAYOUT_PENDING -> ERROR autorise", ok)
    ok, err = engine.validate_transition("ROLLING", "ERROR")
    check("ROLLING -> ERROR refuse (comme le Lua)", not ok, str(err))

    # --- flux complet reel, en suivant la VRAIE machine a etats ---
    # Le Lua impose : game -> stake(TRADE_PENDING) -> trade verifie
    # (TRADE_VERIFIED) -> STAKE_CONFIRMED -> re-choix du jeu -> roll.
    st = engine.SessionStore()
    s, err = st.create("Alice")
    check("session creee", s is not None and s.state == "CONTACTED", str(err))
    ok, err = st.create("Bob")
    check("seconde session refusee (session unique)", not ok and err == "ERR_DUPLICATE_SESSION")
    ok, err = st.game("Alice", "roulette", "red")
    check("jeu roulette rouge", ok, str(err))
    ok, err = st.stake("Alice", "50g")
    check("mise 50g armee", ok and st.get("Alice").stake == 50, str(err))
    check("etat apres mise = TRADE_PENDING",
          st.get("Alice").state == "TRADE_PENDING", st.get("Alice").state)
    ok, err = st.record_roll("Alice", 30)
    check("rouler avant verification de mise refuse (machine a etats)",
          not ok and err == "ERR_INVALID_STATE", str(err))
    ok, verif, msg = st.confirm_trade("Alice", 0, 50 * 10000)
    check("trade verifie avec le bon montant", ok and verif == "VERIFIED", str(msg))
    check("etat apres verification = TRADE_VERIFIED",
          st.get("Alice").state == "TRADE_VERIFIED", st.get("Alice").state)
    ok, verif, msg = st.confirm_trade("Alice", 0, 10 * 10000)
    check("mauvais montant = MISMATCH", not ok and verif == "MISMATCH", str(msg))
    st.recover("Alice", "reset apres mismatch")
    st.create("Alice")
    st.game("Alice", "roulette", "red")
    st.stake("Alice", "50g")
    st.confirm_trade("Alice", 0, 50 * 10000)
    st.mark_stake_confirmed("Alice")
    check("etat apres confirmation de mise = STAKE_CONFIRMED",
          st.get("Alice").state == "STAKE_CONFIRMED", st.get("Alice").state)
    st.game("Alice", "roulette", "red")
    check("retour possible vers GAME_SELECTED", st.get("Alice").state == "GAME_SELECTED",
          st.get("Alice").state)
    ok, err = st.record_roll("Alice", 30)
    check("lancer 30 enregistre et resolu", ok, str(err))
    sess = st.get("Alice")
    check("auto-resolution : resultat", sess.result == "WIN", str(sess.result))
    check("auto-resolution : multiplicateur x2", sess.multiplier == 2)
    check("auto-resolution : payout net +50", sess.payout == 50, str(sess.payout))
    check("paiement arme automatiquement",
          sess.state in ("PAYOUT_PENDING", "PAID"), sess.state)
    check("actions client mises en file (INITIATE_TRADE)",
          any(a["kind"] == "INITIATE_TRADE" for a in sess.pending_actions))
    ok, err = st.mark_paid("Alice")
    check("paiement marque", ok and sess.state == "PAID", str(err))
    ok, err = st.close("Alice")
    check("session fermee", ok, str(err))

    # --- blackjack : la maison est obligatoire ---
    st2 = engine.SessionStore()
    st2.create("Bob")
    st2.game("Bob", "blackjack")
    st2.stake("Bob", 100)
    st2.confirm_trade("Bob", 0, 100 * 10000)
    st2.mark_stake_confirmed("Bob")
    st2.game("Bob", "blackjack")
    st2.record_roll("Bob", 85)
    ok, err = st2.resolve("Bob")
    check("blackjack refuse sans total maison",
          not ok and err == "House total not set", str(err))
    ok, err = st2.set_house("Bob", 70)
    check("total maison enregistre", ok, str(err))
    ok, err = st2.resolve("Bob")
    check("blackjack resolu apres total maison", ok, str(err))
    check("blackjack : joueur 85 > maison 70 = WIN",
          st2.get("Bob").result == "WIN", str(st2.get("Bob").result))

    # --- near miss : anomalie de precedence Lua consignee ---
    check("near-miss faithful : lucky7 finit par 8 (bug Lua conserve)",
          engine.near_miss("lucky7", 28, "LOSS") is True)
    check("near-miss lu : 28 vaut pour n'importe quel jeu",
          engine.near_miss("normal", 28, "LOSS") is True)
    check("version intentionnelle differente pour 'normal' 28",
          engine.near_miss_intended("normal", 28, "LOSS") is False)
    check("near-miss normal 58", engine.near_miss("normal", 58, "LOSS") is True)

    # --- watchdog / integrite ---
    st3 = engine.SessionStore()
    s3, _ = st3.create("Carol")
    s3.state_time = time.time() - (engine.CORE_TIMEOUTS["CONTACTED"] + 60)
    flagged = st3.check_timeouts()
    check("timeout CONTACTED detecte", len(flagged) == 1, "; ".join(flagged))
    s3.state = "ETAT_INVENTE"
    problems = st3.check_integrity()
    check("etat impossible detecte", len(problems) == 1, "; ".join(problems))
    check("etat impossible recupere", st3.get("Carol") is None)

    # --- persistance / reprise ---
    st4 = engine.SessionStore()
    st4.create("Diane")
    st4.get("Diane").state = "ROLLING"
    blob = st4.to_dict()
    st5 = engine.SessionStore()
    n = st5.load_dict(blob)
    check("session restauree apres redemarrage", n == 1 and st5.active() is not None)
    st6 = engine.SessionStore()
    st6.load_dict({"version": 1, "counter": 1, "sessions":
                   {"Eve": {"player": "Eve", "state": "ETAT_POURRI"}}})
    check("etat illisible marque RECOVERY_REQUIRED",
          st6.get("Eve").state == "RECOVERY_REQUIRED", str(st6.get("Eve").state))

    # --- moteur : commandes ---
    bot = CasinoBot()
    check("commande inconnue refusee", bot.handle("hack", {})["ok"] is False)
    r = bot.handle("game", {"game": "normal"})
    check("commande sans joueur refusee avec le code attendu",
          not r["ok"] and r.get("error") == "ERR_NO_PLAYER", str(r.get("error")))
    r = bot.handle("create_session", {"player": "Zoe"})
    check("session creee via IPC interne", r["ok"] and r["session"]["state"] == "CONTACTED")
    r = bot.handle("roll", {"player": "Zoe", "roll": 10})
    check("roll sans jeu refuse", not r["ok"], str(r.get("error")))
    r = bot.handle("status", {})
    check("status complet", r["ok"] and r["status"]["pid"] == os.getpid())
    check("aucune exception ne tue le moteur", bot.handle("ping", {})["ok"] is True)
    check("etat du moteur = ACTIF apres activite", bot.status()["state"] == "ACTIF")
    r = bot.handle("house_total", {"player": "Zoe", "house_total": None})
    check("parametre manquant refuse proprement",
          not r["ok"] and r["error"] == "house_total manquant", str(r.get("error")))

    # --- securite ---
    import secrets as _secrets
    tok = _secrets.token_hex(32)
    check("jeton correct accepte", ipc.token_matches(tok, tok) is True)
    check("mauvais jeton rejete", ipc.token_matches("faux", tok) is False)
    check("jeton vide rejete", ipc.token_matches("", tok) is False)
    check("jeton None rejete", ipc.token_matches(None, tok) is False)
    check("commande non listee rejetee au protocole",
          ipc.validate_command({"cmd": "rm -rf", "token": "x"})[0] is False)
    check("charge utile non-dict rejetee",
          ipc.validate_command("salut")[0] is False)
    # Si un moteur tourne deja sur cette machine, on ne peut pas pretendre que
    # le pipe est muet : on verifie alors que le mauvais jeton est refuse.
    if ipc.ping(timeout=1.0):
        check("moteur deja en service : mauvais jeton refuse par le vrai moteur",
              ipc.request("ping", token="faux")["error"] == "AUTH_FAILED")
    else:
        check("aucun moteur en service : connexion refusee proprement",
              not ipc.ping(timeout=0.5))
        check("jeton absent : IpcError explicite",
              _ipc_raises(lambda: ipc.request("ping")))
    # Le canal ne doit contenir aucun socket TCP : on analyse le module IPC
    # lui-meme (et PAS casino_bot.py, ou le nom apparaitrait dans ce test).
    ipc_src = open(os.path.join(_HERE, "casino_ipc.py"), encoding="utf-8").read()
    check("aucun socket TCP dans le module IPC",
          "AF_INET" not in ipc_src and "socket.socket" not in ipc_src)
    check("le canal est un named pipe Windows",
          "AF_PIPE" in ipc_src and "\\\\.\\pipe\\" in ipc_src)

    # --- lecture seule depuis le disque ---
    s_disk = read_status()
    check("etat relu du disque", s_disk["state"] in ("ARRETE", "DEMARRAGE",
                                                     "ACTIF", "BLOQUE"),
          s_disk["state"])

    print("-" * 72)
    print("RESULTAT : %s (%d echec(s))"
          % ("PASS" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Moteur casino autonome Casinobabe")
    ap.add_argument("--cli", action="store_true", help="boucle de service")
    ap.add_argument("--once", action="store_true", help="un tick watchdog puis sortie")
    ap.add_argument("--status", action="store_true", help="etat relu du disque")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--enabled", default="1", help="0 pour lancer en lecture seule")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.status:
        print(json.dumps(read_status(), indent=2, ensure_ascii=False))
        return 0
    if args.once:
        bot = CasinoBot()
        bot.tick_heartbeat()
        bot.tick_watchdog()
        bot.save_state()
        bot.journal.log("BOT_ONCE", commands=bot.commands_handled)
        print(json.dumps(bot.status(), indent=2, ensure_ascii=False))
        return 0
    return CasinoBot().run()


if __name__ == "__main__":
    sys.exit(main())
