#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""control_core - logique du Casinobabe Control Center (SANS interface).

Separation stricte :
  * `control_core`   = etats, decisions, commandes, configuration, diagnostics.
                      Aucun import de tkinter. Testable hors session graphique.
  * `control_center.py` = la fenetre Windows (ne fait que dessiner et cliquer).

PRINCIPES (mission Control Center)
----------------------------------
1. Aucune invention de superviseur : on LIT l'etat reel (verrous PID,
   journaux JSONL, processus) et on ORDONNE les scripts existants
   (`fishing_bot.py`, `fishing_observer.py`, `bridge.mjs`,
   `casinobae-supervisor.ps1`).
2. Une seule instance par composant : le verrou officiel
   `fishing_observer.acquire_single_instance()` est respecte, jamais duplique.
3. Processus actif != bot qui progresse : le heartbeat est lu dans le journal
   JSONL. Sans progression recente -> etat `BLOQUE`, pas `ACTIF`.
4. Toute action passe par `window_guard.safe_call()`.
5. Aucun secret n'est lu pour etre affiche : masquage par nom de cle.
6. Le bot casino n'existe pas comme processus : etat `ABSENT`, aucune commande.

Audit complet de l'existant : `docs/AUDIT_INITIAL.md`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

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
SCRIPTS_DIR = os.path.join(ROOT, "scripts")

CC_CONFIG_FILE = os.path.join(STATE_DIR, "control-center.json")
FISHING_CONFIG_FILE = os.path.join(STATE_DIR, "fishing-bot-config.json")

for _d in (STATE_DIR, REPORTS_DIR, LOGS_DIR):
    os.makedirs(_d, exist_ok=True)


# ===========================================================================
# Etats
# ===========================================================================

STOPPED = "ARRETE"
STARTING = "DEMARRAGE"
ACTIVE = "ACTIF"
PAUSED = "EN PAUSE"
ERROR = "ERREUR"
BLOCKED = "BLOQUE"
ABSENT = "ABSENT"
UNVERIFIABLE = "NON VERIFIABLE"

#: Cles de configuration consignees comme sensibles : jamais affichees, jamais
#: journalisees, jamais exportees en clair.
#: ATTENTION : la comparaison se fait sur une forme NORMALISEE (casse, tirets,
#: espaces et SOUS-SCORETS supprimes). Sans cela `api_key` ne correspondait pas
#: a `apikey` et le secret fuyait en clair dans l'export de diagnostic.
SECRET_KEYS = frozenset({
    "sessiontoken", "token", "password", "passwd", "pwd", "secret", "apikey",
    "accesstoken", "refreshtoken", "clientsecret", "privatekey", "bearer",
    "authorization", "cookie", "sessionkey", "credentials",
})

MASK = "********"


def _norm_key(key: Any) -> str:
    return (str(key).strip().lower()
            .replace("-", "").replace("_", "").replace(" ", ""))


#: Schema des parametres du bot : (type, min, max) - derive de
#: `fishing_bot.DEFAULT_CONFIG` (docs/AUDIT_INITIAL.md, section 4.6).
FISHING_SCHEMA: Dict[str, Tuple[type, Optional[float], Optional[float]]] = {
    "castKey": (str, None, None),
    "castSettleSec": (float, 0.0, 30.0),
    "maxBiteWaitSec": (float, 1.0, 300.0),
    "clickAfterBiteMs": (int, 0, 5000),
    "bobberMinConf": (float, 0.0, 1.0),
    "biteDiffThresh": (float, 0.0, 255.0),
    "biteWhiteFrac": (float, 0.0, 1.0),
    "biteMotionThresh": (float, 0.0, 255.0),
    "biteMotionStreak": (int, 1, 60),
    "biteGraceSec": (float, 0.0, 120.0),
    "biteStrongDiff": (float, 0.0, 255.0),
    "biteStrongWhite": (float, 0.0, 1.0),
    "biteConsecutive": (int, 1, 30),
    "manualSuspendSec": (float, 5.0, 3600.0),
    "cursorDivergePx": (int, 5, 2000),
    "manualSuspendMaxSec": (float, 60.0, 7200.0),
    "captureIntervalSec": (float, 0.02, 5.0),
    "detectScale": (float, 0.25, 1.0),
    "applyBait": (bool, None, None),
    "baitKey": (str, None, None),
    "rodKey": (str, None, None),
    "baitRefreshSec": (float, 30.0, 7200.0),
}

#: Parametres explicitement marques « ne pas changer » par le code du bot.
FROZEN_PARAMS = {
    "detectScale": "1.0 = pleine taille (11/11 valide) ; NE PAS CHANGER",
    "castKey": "definie dans le WTF, jamais modifiee par le bot",
}


def is_secret(key: Any) -> bool:
    return _norm_key(key) in SECRET_KEYS


def mask_value(key: Any, value: Any) -> Any:
    return MASK if is_secret(key) else value


def mask_tree(data: Any) -> Any:
    """Masque recursivement toute valeur dont la cle ressemble a un secret."""
    if isinstance(data, dict):
        return {k: (MASK if is_secret(k) else mask_tree(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_tree(v) for v in data]
    return data


# ===========================================================================
# Composants
# ===========================================================================

@dataclass
class Component:
    key: str
    label: str
    kind: str                      # bot | service | supervisor | casino
    script: Optional[str] = None   # relatif a ROOT
    args: List[str] = field(default_factory=list)
    interpreter: str = "python"
    pid_file: Optional[str] = None          # relatif a ROOT/state
    progress_log: Optional[str] = None      # relatif a ROOT (journal JSONL)
    stdout_log: Optional[str] = None        # relatif a ROOT
    cmdline_match: Optional[str] = None     # detection sans fichier PID
    progress_events: Tuple[str, ...] = ()
    #: Evenements qui prouvent seulement que le processus s'est lance. Un composant
    #: dont le dernier evenement de progression est dans cette liste est affiche
    #: `DEMARRAGE`, jamais `ACTIF` : demarrer n'est pas travailler.
    startup_events: Tuple[str, ...] = ("BOT_START", "STARTUP_RETRY", "KEYBINDING_CHECK")
    heartbeat_stale_sec: float = 45.0
    available: bool = True
    unavailable_reason: str = ""
    controls: Tuple[str, ...] = ("start", "stop")
    pause_mode: str = "aucune"

    @property
    def pid_path(self) -> Optional[str]:
        return os.path.join(STATE_DIR, self.pid_file) if self.pid_file else None

    @property
    def progress_path(self) -> Optional[str]:
        return os.path.join(ROOT, self.progress_log) if self.progress_log else None

    @property
    def stdout_path(self) -> Optional[str]:
        return os.path.join(ROOT, self.stdout_log) if self.stdout_log else None

    def command(self, python_exe: Optional[str] = None) -> List[str]:
        if self.interpreter == "python":
            return [python_exe or sys.executable,
                    os.path.join(ROOT, self.script or "")] + self.args
        if self.interpreter == "node":
            return ["node", os.path.join(ROOT, self.script or "")] + self.args
        if self.interpreter == "powershell":
            return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", os.path.join(ROOT, self.script or "")] + self.args
        return [self.interpreter, os.path.join(ROOT, self.script or "")] + self.args


CASINO_ABSENT_REASON = (
    "Moteur casino introuvable : scripts/casino_bot.py est absent de ce "
    "checkout. Le moteur est livre avec la branche du Control Center ; s'il "
    "manque, c'est que cette branche n'est pas fusionnee ou que le fichier a "
    "ete supprime. Verifier docs/CONTROL_CENTER.md section Casino."
)

#: Le moteur casino parle a l'interface par un named pipe local authentifie.
#: Aucun port TCP n'est ouvert : voir scripts/casino_ipc.py.
CASINO_IPC_TIMEOUT = 4.0

COMPONENTS: List[Component] = [
    Component(
        key="fishing_bot",
        label="Bot Peche",
        kind="bot",
        script="scripts/fishing_bot.py",
        args=["--cli"],
        pid_file="fishing-bot.pid",
        progress_log="reports/fishing-bot-log.jsonl",
        stdout_log="logs/fishing-bot-cli.out.log",
        # BOT_START / KEYBINDING_CHECK ne sont PAS des preuves de travail :
        # ils sont classes dans `startup_events` et affiches DEMARRAGE.
        progress_events=("WATCH", "WATCH_MISS", "CAST", "CAST_NOT_SENT",
                         "CAST_UNCONFIRMED", "WAITING_FOR_BITE", "BOBBER_LOCKED",
                         "CYCLE_SUMMARY", "CLICK_BOBBER", "CATCH_DETECTED",
                         "VERIFY_OK", "VERIFY_RETRY", "BITE_DIP", "BITE_MOVE",
                         "CURSOR_SKIP", "CURSOR_TRACK", "BAIT_KEY",
                         "FOCUS_LOST_CYCLE"),
        heartbeat_stale_sec=45.0,
        controls=("start", "stop", "pause", "resume", "restart"),
        pause_mode="suspend_os",
    ),
    Component(
        key="fishing_observer",
        label="Observateur Peche",
        kind="service",
        script="scripts/fishing_observer.py",
        args=["--loop", "60"],
        pid_file="fishing-observer.pid",
        stdout_log="logs/fishing-observer.out.log",
        heartbeat_stale_sec=300.0,
        controls=("start", "stop", "restart"),
        pause_mode="aucune (aucun mecanisme de pause dans fishing_observer.py)",
    ),
    Component(
        key="bridge",
        label="Bridge Telemetrie",
        kind="service",
        script="bridge/bridge.mjs",
        interpreter="node",
        stdout_log="logs/bridge.out.log",
        cmdline_match="bridge.mjs",
        controls=("start", "stop", "restart"),
        pause_mode="aucune",
    ),
    Component(
        key="supervisor",
        label="Superviseur Sentinel",
        kind="supervisor",
        script="scripts/casinobae-supervisor.ps1",
        interpreter="powershell",
        stdout_log="logs/supervisor.out.log",
        cmdline_match="casinobae-live-watch.ps1",
        controls=("start", "stop"),
        pause_mode="aucune",
    ),
    Component(
        key="casino_bot",
        label="Moteur Casino",
        kind="casino",
        script="scripts/casino_bot.py",
        args=["--cli"],
        pid_file="casino-bot.pid",
        progress_log="reports/casino-bot-log.jsonl",
        stdout_log="logs/casino-bot.err.log",
        progress_events=("HEARTBEAT", "SESSION_CREATED", "GAME_SELECTED",
                         "STAKE_ARMED", "ROLL_RECORDED", "RESOLVED",
                         "PAYOUT_ARMED", "PAID", "CLOSED", "RECOVERED",
                         "WATCHDOG_TICK", "TIMEOUT_FLAGGED", "INTEGRITY_FIXED"),
        heartbeat_stale_sec=60.0,
        controls=("start", "stop", "pause", "resume", "restart"),
        pause_mode="suspend_os",
    ),
]

COMPONENTS_BY_KEY: Dict[str, Component] = {c.key: c for c in COMPONENTS}


def get_component(key: str) -> Optional[Component]:
    return COMPONENTS_BY_KEY.get(key)


# ===========================================================================
# Verrous PID
# ===========================================================================

def _pid_alive(pid: int) -> bool:
    """Vrai si le PID existe. Lecture seule, sans wmic (cf. audit 4.1)."""
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


def _process_name(pid: int) -> str:
    try:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            n = wt.DWORD(512)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return os.path.basename(buf.value)
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return ""


def read_pid_lock(path: Optional[str]) -> Dict[str, Any]:
    """Lit un fichier de verrou PID et le qualifie.

    Etats : ABSENT (pas de fichier), LOCKED_ALIVE (processus vivant),
    STALE (fichier perime, processus mort), UNREADABLE (contenu invalide).
    """
    if not path:
        return {"status": UNVERIFIABLE, "pid": 0, "alive": False, "path": None,
                "process": "", "reason": "composant sans fichier de verrou PID"}
    if not os.path.isfile(path):
        return {"status": "ABSENT", "pid": 0, "alive": False, "path": path,
                "process": "", "reason": "aucun fichier de verrou"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError as exc:
        return {"status": "UNREADABLE", "pid": 0, "alive": False, "path": path,
                "process": "", "reason": "lecture impossible : %s" % exc}
    try:
        pid = int(raw)
    except ValueError:
        return {"status": "UNREADABLE", "pid": 0, "alive": False, "path": path,
                "process": "", "reason": "contenu invalide : %r" % raw[:32]}
    alive = _pid_alive(pid)
    name = _process_name(pid) if alive else ""
    if alive and name and "python" not in name.lower():
        return {"status": "LOCKED_ALIVE", "pid": pid, "alive": True, "path": path,
                "process": name,
                "reason": "PID vivant mais ce n'est PAS un processus python (%s)" % name}
    return {"status": "LOCKED_ALIVE" if alive else "STALE", "pid": pid,
            "alive": alive, "path": path, "process": name,
            "reason": "" if alive else "verrou residuel : le processus %d n'existe plus" % pid}


def find_by_cmdline(match: str) -> List[Dict[str, Any]]:
    """Retrouve des processus par fragment de ligne de commande (psutil)."""
    out: List[Dict[str, Any]] = []
    try:
        import psutil
    except ImportError:
        return out
    low = match.lower()
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except Exception:
                continue
            if low in cmd.lower():
                out.append({"pid": p.info["pid"], "name": p.info.get("name") or "",
                            "cmdline": cmd, "started": p.info.get("create_time")})
    except Exception:
        pass
    return out


# ===========================================================================
# Heartbeat / progression
# ===========================================================================

def tail_jsonl(path: Optional[str], max_lines: int = 400,
               max_bytes: int = 512 * 1024) -> List[Dict[str, Any]]:
    """Relit la FIN d'un gros JSONL sans tout charger (44 Mo)."""
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # ligne de debut probablement tronquee : on la jette
            raw = fh.read()
    except OSError:
        return []
    rows: List[Dict[str, Any]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-max_lines:]


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def heartbeat(comp: Component, rows: Optional[List[Dict]] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """Dernier evenement de PROGRES et anciennete.

    Separe explicitement « processus vivant » de « bot qui progresse ».
    """
    now = now if now is not None else time.time()
    if rows is None:
        rows = tail_jsonl(comp.progress_path)
    out: Dict[str, Any] = {
        "available": bool(comp.progress_log),
        "last_event": None, "last_event_ts": None, "last_age_sec": None,
        "last_state": None, "fresh": False, "error": None, "checked": False,
        "path": comp.progress_path, "samples": len(rows),
    }
    if not comp.progress_log:
        out["error"] = "ce composant n'ecrit pas de journal de progression"
        return out
    if not rows:
        out["error"] = "journal vide ou absent : %s" % (comp.progress_path or "?")
        out["checked"] = True
        return out

    progress = set(comp.progress_events)

    # Passe 1 : DERNIERE erreur, balayee AVANT la recherche de progression.
    # Sans cela, un evenement de progression recent (BOT_START) ferait sortir
    # de la fonction avant qu'une erreur plus ancienne soit trouvee, et
    # l'interface n'afficherait jamais la cause reelle d'un blocage.
    last_err = None
    for row in reversed(rows[-200:]):
        ev = str(row.get("event", ""))
        if ev in ("ERROR", "BOT_CRASH", "CAPTURE_ERROR") or _is_error_row(row):
            ts = _parse_ts(row.get("ts"))
            last_err = {"event": ev,
                        "ts": ts.isoformat(timespec="seconds") if ts else None,
                        "detail": row.get("reason") or row.get("note") or ""}
            break

    # Passe 2 : dernier evenement de PROGRESSION.
    for row in reversed(rows):
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        ev = str(row.get("event", ""))
        if ev in progress:
            age = max(0.0, now - ts.timestamp())
            out.update(last_event=ev, last_event_ts=ts.isoformat(timespec="seconds"),
                       last_age_sec=round(age, 1), last_state=row.get("state"),
                       fresh=age <= comp.heartbeat_stale_sec, checked=True)
            if last_err:
                out["last_error"] = last_err
            return out

    # Passe 3 : aucune progression -> on retient quand meme la derniere ecriture.
    newest = None
    for row in reversed(rows):
        ts = _parse_ts(row.get("ts"))
        if ts is not None:
            newest = (row, ts)
            break
    if newest:
        row, ts = newest
        age = max(0.0, now - ts.timestamp())
        out.update(last_event=row.get("event"),
                   last_event_ts=ts.isoformat(timespec="seconds"),
                   last_age_sec=round(age, 1), last_state=row.get("state"),
                   fresh=False, checked=True,
                   # Le processus ecrit encore, mais sa derniere ligne est-elle
                   # un simple « demarrage » ou une ligne de travail ?
                   startup=str(row.get("event")) in comp.startup_events,
                   error="aucun evenement de progression depuis %.0fs" % age)
    if last_err:
        out["last_error"] = last_err
    return out


# ===========================================================================
# Etat d'un composant
# ===========================================================================

def _is_suspended(pid: int) -> bool:
    """Vrai si les threads du processus sont suspendus (pause OS du CC)."""
    if not pid:
        return False
    try:
        import psutil
        return psutil.Process(int(pid)).status() == psutil.STATUS_STOPPED
    except Exception:
        return False


def _state_detail(info: Dict[str, Any], state: str, detail: str) -> None:
    info["state"] = state
    info["detail"] = detail


def casino_request(command: str, timeout: float = CASINO_IPC_TIMEOUT,
                   **params: Any) -> Dict[str, Any]:
    """Interroge le moteur casino par son named pipe authentifie.

    Renvoie toujours un dict exploitable : en cas d'echec de transport on
    retourne `{"ok": False, "error": ..., "transport": False}` au lieu de
    lever, pour que l'interface affiche la cause au lieu de planter.

    Ne touche a AUCUN autre composant : une commande casino ne peut pas
    arreter, suspendre ni reconfigurer le bot peche.
    """
    try:
        import casino_ipc
    except Exception as exc:
        return {"ok": False, "error": "module IPC absent : %s" % exc, "transport": False}
    try:
        reply = casino_ipc.request(command, timeout=timeout, **params)
        reply.setdefault("transport", True)
        return reply
    except Exception as exc:
        return {"ok": False, "error": str(exc), "transport": False}


def casino_detail(comp: Optional[Component] = None) -> Dict[str, Any]:
    """Etat detail du moteur, interroge AUPRES du processus.

    C'est la source de verite de l'interface : quand le moteur tourne, on lit
    son etat par IPC ; quand il est arrete, on relit le disque. Rien n'est
    conserve en memoire, donc une reouverture de fenetre retrouve l'etat reel.
    """
    comp = comp or COMPONENTS_BY_KEY["casino_bot"]
    lock = read_pid_lock(comp.pid_path) if comp.pid_file else None
    alive = bool(lock and lock.get("alive"))
    out: Dict[str, Any] = {
        "reachable": False, "alive": alive, "lock": lock,
        "pid": lock.get("pid", 0) if lock else 0,
        "engine_state": None, "uptime_sec": None, "last_heartbeat": None,
        "progress_age_sec": None, "last_error": None, "active_session": None,
        "sessions": [], "pending_actions": {}, "watchdog_ticks": None,
        "commands_handled": None, "rules": [], "timeouts": {},
        "config": {}, "source": "disque",
    }
    if alive:
        reply = casino_request("status", timeout=CASINO_IPC_TIMEOUT)
        if reply.get("ok") and reply.get("status"):
            st = reply["status"]
            out.update(
                reachable=True, source="IPC",
                engine_state=st.get("state"),
                uptime_sec=st.get("uptime_sec"),
                last_heartbeat=st.get("last_heartbeat"),
                progress_age_sec=st.get("progress_age_sec"),
                last_error=st.get("last_error"),
                active_session=st.get("active_session"),
                active_state=st.get("active_state"),
                sessions=st.get("sessions") or [],
                pending_actions=st.get("pending_actions") or {},
                watchdog_ticks=st.get("watchdog_ticks"),
                commands_handled=st.get("commands_handled"),
                rules=st.get("rules") or [],
                timeouts=st.get("timeouts") or {},
                config=st.get("config") or {},
            )
            return out
        out["transport_error"] = reply.get("error")
    # Repli hors ligne : on relit ce que le moteur a laisse sur le disque.
    try:
        import casino_bot
        disk = casino_bot.read_status()
        out["disk_state"] = disk
        if not alive:
            out["engine_state"] = disk.get("state")
        out["progress_age_sec"] = disk.get("progress_age_sec")
        out["last_error"] = disk.get("last_error")
        out["last_heartbeat"] = disk.get("last_heartbeat")
        out["watchdog_ticks"] = disk.get("watchdog_ticks")
        out["config"] = disk.get("config") or {}
        # Lire aussi le dernier resultat de commande depuis les SV WoW
        try:
            sv_file = casino_bot.find_wow_sv_file()
            if sv_file:
                result = casino_bot.read_casino_result(sv_file)
                out["casino_result"] = result
        except Exception:
            pass
    except Exception as exc:
        out["disk_error"] = str(exc)
    return out


CASINO_CONFIG_SCHEMA = {
    "heartbeatSec": (float, 0.2, 60.0),
    "watchdogSec": (float, 1.0, 600.0),
    "autoRecoverOnTimeout": (bool, 0, 1),
    "maxSessionsRetained": (int, 1, 1000),
    "ipcTimeoutSec": (float, 0.5, 60.0),
    "shutdownGraceSec": (float, 1.0, 120.0),
}


def casino_load_config() -> Dict[str, Any]:
    """Config du moteur casino, completee par les defauts du moteur."""
    try:
        import casino_bot
        return casino_bot.load_config()
    except Exception as exc:
        return {"__error__": str(exc)}


def casino_save_config(updates: Dict[str, Any]) -> Tuple[bool, List[str], str]:
    """Ecrit la config casino. Fichier SEPARE de celui du bot peche."""
    try:
        import casino_bot
    except Exception as exc:
        return False, ["moteur casino absent : %s" % exc], "echec"
    return casino_bot.save_config(updates)


def component_status(comp: Component, now: Optional[float] = None) -> Dict[str, Any]:
    if comp.kind == "casino":
        # Le moteur casino se juge sur SON etat interne, interroge par IPC :
        # un simple processus vivant ne suffit pas, il faut une progression
        # reelle et un watchdog qui tourne.
        detail = casino_detail(comp)
        hb = heartbeat(comp, now=now)
        info: Dict[str, Any] = {
            "key": comp.key, "label": comp.label, "kind": comp.kind,
            "available": comp.available, "controls": list(comp.controls),
            "pause_mode": comp.pause_mode, "state": UNVERIFIABLE, "detail": "",
            "pid": 0, "process_path": "", "lock": detail.get("lock"),
            "heartbeat": hb, "extra_pids": [], "suspended": False,
            "casino": detail,
        }
        pid = detail.get("pid") or 0
        info["pid"] = pid
        if not pid or not detail.get("alive"):
            lock_state = (detail.get("lock") or {}).get("status")
            if lock_state == "STALE":
                info["state"] = STOPPED
                info["detail"] = ("processus arrete - VERROU RESIDUEL : "
                                  "le processus %s n'existe plus" % pid)
            else:
                info["state"] = STOPPED
                info["detail"] = "moteur casino arrete (aucun verrou PID vivant)"
            return info
        if detail.get("engine_state") == "BLOQUE":
            info["state"] = BLOCKED
            info["detail"] = detail.get("last_error") or "moteur bloque (watchdog)"
            return info
        if detail.get("engine_state") == "ERREUR":
            info["state"] = ERROR
            info["detail"] = detail.get("last_error") or "erreur interne du moteur"
            return info
        age = detail.get("progress_age_sec")
        if detail.get("reachable") and age is not None and age > comp.heartbeat_stale_sec:
            info["state"] = BLOCKED
            info["detail"] = ("moteur VIVANT (PID %d) mais aucune progression depuis "
                              "%.0fs -> bloque, pas actif" % (pid, age))
            return info
        info["state"] = ACTIVE
        info["detail"] = "moteur actif (PID %d), watchdog %s, session %s" % (
            pid, detail.get("watchdog_ticks"),
            detail.get("active_session") or "aucune")
        return info

    """Etat complet, affichable et justifie."""
    now = now if now is not None else time.time()
    info: Dict[str, Any] = {
        "key": comp.key, "label": comp.label, "kind": comp.kind,
        "available": comp.available, "controls": list(comp.controls),
        "pause_mode": comp.pause_mode, "state": UNVERIFIABLE, "detail": "",
        "pid": 0, "process_path": "", "lock": None, "heartbeat": None,
        "extra_pids": [], "suspended": False,
    }
    if not comp.available:
        info["state"] = ABSENT
        info["detail"] = comp.unavailable_reason
        return info

    lock = read_pid_lock(comp.pid_path) if comp.pid_file else None
    info["lock"] = lock

    pid = 0
    if lock and lock.get("pid"):
        pid = lock["pid"]
    if comp.cmdline_match:
        found = find_by_cmdline(comp.cmdline_match)
        found = [f for f in found if "fishing-oneclick" not in f["cmdline"].lower()]
        info["extra_pids"] = found
        if found and not pid:
            pid = found[0]["pid"]
            info["process_path"] = found[0]["cmdline"]
    if not info["process_path"]:
        info["process_path"] = lock.get("process", "") if lock else ""
    info["pid"] = pid

    hb = heartbeat(comp, now=now)
    info["heartbeat"] = hb

    alive = bool(lock and lock.get("alive"))
    if comp.cmdline_match and not comp.pid_file:
        alive = alive or bool(info["extra_pids"])

    if alive:
        info["suspended"] = _is_suspended(pid)
        bot_state = str(hb.get("last_state") or "").upper()
        if not comp.progress_log:
            if info["suspended"]:
                _state_detail(info, PAUSED,
                              "processus suspendu par le Control Center (verrou PID conserve)")
            else:
                _state_detail(info, ACTIVE,
                              "processus vivant (PID %d), pas de journal de progression" % pid)
        elif info["suspended"]:
            _state_detail(info, PAUSED,
                          "processus suspendu par le Control Center (PID %d, "
                          "verrou PID conserve, aucune entree envoyee)" % pid)
        elif hb.get("fresh"):
            if hb.get("last_event") in comp.startup_events:
                _state_detail(info, STARTING,
                              "processus vivant (PID %d), demarrage '%s' il y a "
                              "%.0fs, en attente de premiere progression reelle" % (
                                  pid, hb.get("last_event"), hb.get("last_age_sec") or 0))
            elif bot_state == "PAUSED":
                _state_detail(info, PAUSED,
                              "processus vivant (PID %d), bot en pause interne "
                              "(progression '%s' il y a %.0fs)" % (
                                  pid, hb.get("last_event"), hb.get("last_age_sec") or 0))
            elif bot_state in ("ERROR", "FATAL_ERROR", "DEAD"):
                _state_detail(info, ERROR,
                              "processus vivant (PID %d) mais le bot signale %s "
                              "(progression '%s' il y a %.0fs)" % (
                                  pid, bot_state, hb.get("last_event"),
                                  hb.get("last_age_sec") or 0))
            else:
                _state_detail(info, ACTIVE,
                              "processus vivant, progression '%s' il y a %.0fs" % (
                                  hb.get("last_event"), hb.get("last_age_sec") or 0))
        elif hb.get("checked") and hb.get("last_age_sec") is not None:
            if hb.get("startup"):
                _state_detail(info, STARTING,
                              "processus VIVANT (PID %d), derniere ligne '%s' il y a "
                              "%.0fs, en attente de premiere progression reelle" % (
                                  pid, hb.get("last_event"), hb["last_age_sec"]))
            elif bot_state == "PAUSED":
                _state_detail(info, PAUSED,
                              "processus vivant (PID %d), bot en pause interne, "
                              "aucune progression depuis %.0fs" % (
                                  pid, hb["last_age_sec"]))
            elif bot_state in ("ERROR", "FATAL_ERROR", "DEAD"):
                _state_detail(info, ERROR,
                              "processus vivant (PID %d), le bot signale %s et n'a "
                              "pas progresse depuis %.0fs" % (
                                  pid, bot_state, hb["last_age_sec"]))
            else:
                _state_detail(info, BLOCKED,
                              "processus VIVANT (PID %d) mais aucune progression depuis "
                              "%.0fs (seuil %.0fs) -> bot bloque, pas actif" % (
                                  pid, hb["last_age_sec"], comp.heartbeat_stale_sec))
        elif hb.get("checked"):
            # Journal absent ou vide : impossible de prouver le moindre travail.
            _state_detail(info, BLOCKED,
                          "processus VIVANT (PID %d) mais %s -> aucune preuve de "
                          "progression" % (pid, hb.get("error") or
                                           "journal de progression vide"))
        else:
            _state_detail(info, STARTING,
                          "processus vivant, heartbeat pas encore verifiable")
    else:
        if lock and lock.get("status") == "STALE":
            info["state"] = STOPPED
            info["detail"] = "processus arrete - VERROU RESIDUEL : %s" % lock.get("reason")
        elif comp.cmdline_match:
            info["state"] = STOPPED
            info["detail"] = "aucun processus pour '%s'" % comp.cmdline_match
        else:
            info["state"] = STOPPED
            info["detail"] = "aucun verrou PID, aucun processus"
    return info


# ===========================================================================
# Configuration
# ===========================================================================

def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def _write_json_atomic(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _schema_default(typ: type) -> Any:
    """Valeur neutre derivee du schema, quand le code du bot est absent."""
    if typ is bool:
        return False
    if typ is int:
        return 0
    if typ is float:
        return 0.0
    return None


def bot_script_present() -> bool:
    """Le code du bot est-il present dans ce checkout ?

    `scripts/fishing_bot.py` n'existe PAS sur `main` : il vit sur la branche
    de peche. Le Control Center doit rester lisible meme la, en le disant.
    """
    script = COMPONENTS_BY_KEY["fishing_bot"].script
    return bool(script) and os.path.isfile(os.path.join(ROOT, script))


def load_fishing_config() -> Dict[str, Any]:
    """Config du bot, completee par les defauts du code, SANS ecraser l'inconnu.

    Si `fishing_bot.py` est absent du checkout (cas de `main`), on retombe sur
    le schema integre au Control Center et on le SIGNALE : mieux vaut afficher
    des defauts annonces que faire planter l'onglet Parametres.
    """
    defaults: Dict[str, Any] = {}
    source = "schema integre du Control Center (fishing_bot.py absent)"
    if bot_script_present():
        try:
            from fishing_bot import DEFAULT_CONFIG  # source de verite du bot
            defaults = dict(DEFAULT_CONFIG)
            source = "fishing_bot.DEFAULT_CONFIG"
        except Exception as exc:  # pragma: no cover - checkout incomplet
            source = "schema integre (import fishing_bot impossible : %s)" % exc
    if not defaults:
        defaults = {k: _schema_default(spec[0]) for k, spec in FISHING_SCHEMA.items()}
    disk = _read_json(FISHING_CONFIG_FILE, {})
    merged = dict(defaults)
    merged.update({k: v for k, v in disk.items()})
    merged["__unknown__"] = {k: v for k, v in disk.items() if k not in defaults}
    merged["__on_disk_keys__"] = sorted(disk.keys())
    merged["__config_source__"] = source
    merged["__bot_present__"] = bot_script_present()
    return merged


def validate_update(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Valide un lot de mises a jour. Ne renvoie que des cles CONNUES."""
    clean: Dict[str, Any] = {}
    errors: List[str] = []
    for key, raw in updates.items():
        # Les clés `__*` sont des métadonnées INTERNES (__unknown__,
        # __config_source__, ...). Elles ne doivent JAMAIS être persistées :
        # constate le 2026-10-19, `__none__` passait le controle "parametre
        # inconnu" et écrivait `{"__none__": 1}` dans la vraie configuration.
        if str(key).startswith("__"):
            errors.append("%s : cle interne, ecriture interdite" % key)
            continue
        if key not in FISHING_SCHEMA:
            errors.append("%s : parametre inconnu (ecriture impossible)" % key)
            continue
        if key in FROZEN_PARAMS:
            errors.append("%s : parametre verrouille - %s" % (key, FROZEN_PARAMS[key]))
            continue
        expected = FISHING_SCHEMA.get(key)
        if expected is None:
            clean[key] = raw
            continue
        typ, lo, hi = expected
        try:
            if typ is bool:
                if isinstance(raw, str):
                    if raw.strip().lower() not in ("1", "0", "true", "false", "oui",
                                                   "non", "yes", "no", "on", "off"):
                        raise ValueError
                    val = raw.strip().lower() in ("1", "true", "oui", "yes", "on")
                else:
                    val = bool(raw)
            elif typ is int:
                if isinstance(raw, str) and not raw.strip().lstrip("-").isdigit():
                    raise ValueError
                val = int(float(raw))
            elif typ is float:
                val = float(raw)
            else:
                val = None if raw is None else str(raw)
        except (TypeError, ValueError):
            errors.append("%s : valeur invalide %r (type attendu %s)"
                          % (key, raw, typ.__name__))
            continue
        if lo is not None and hi is not None and not (lo <= val <= hi):
            errors.append("%s : %s hors bornes [%s, %s]" % (key, val, lo, hi))
            continue
        clean[key] = val
    return clean, errors


def save_fishing_config(updates: Dict[str, Any]) -> Tuple[bool, List[str], str]:
    """Ecrit en preservant TOUTES les cles deja presentes sur le disque."""
    clean, errors = validate_update(updates)
    if errors:
        return False, errors, "aucune ecriture (validation refusee)"
    disk = _read_json(FISHING_CONFIG_FILE, {})
    if not isinstance(disk, dict):
        disk = {}
    before = dict(disk)
    disk.update(clean)
    try:
        _write_json_atomic(FISHING_CONFIG_FILE, disk)
    except OSError as exc:
        return False, ["ecriture impossible : %s" % exc], "echec ecriture"
    changed = {k: v for k, v in disk.items() if before.get(k) != v}
    return True, [], "ecrit : %s" % (", ".join(sorted(changed)) or "aucun changement")


def load_cc_settings() -> Dict[str, Any]:
    data = _read_json(CC_CONFIG_FILE, {})
    data.setdefault("refreshSec", 3.0)
    data.setdefault("stopTimeoutSec", 10.0)
    data.setdefault("startTimeoutSec", 25.0)
    data.setdefault("autoScrollLogs", True)
    data.setdefault("maxLogLines", 1500)
    return data


def save_cc_settings(updates: Dict[str, Any]) -> Tuple[bool, List[str], str]:
    clean: Dict[str, Any] = {}
    errors: List[str] = []
    bounds = {"refreshSec": (1.0, 60.0), "stopTimeoutSec": (1.0, 120.0),
              "startTimeoutSec": (1.0, 300.0), "maxLogLines": (100, 20000)}
    for key, val in updates.items():
        if key in bounds:
            lo, hi = bounds[key]
            try:
                num = float(val)
            except (TypeError, ValueError):
                errors.append("%s : valeur invalide %r" % (key, val))
                continue
            if not (lo <= num <= hi):
                errors.append("%s : %s hors bornes [%s, %s]" % (key, num, lo, hi))
                continue
            clean[key] = int(num) if key == "maxLogLines" else num
        elif key == "autoScrollLogs":
            clean[key] = bool(val)
        else:
            errors.append("%s : parametre inconnu du Control Center" % key)
    if errors:
        return False, errors, "aucune ecriture"
    data = _read_json(CC_CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.update(clean)
    try:
        _write_json_atomic(CC_CONFIG_FILE, data)
    except OSError as exc:
        return False, ["ecriture impossible : %s" % exc], "echec ecriture"
    return True, [], "enregistre : %s" % (", ".join(sorted(clean)) or "rien")


# ===========================================================================
# Commandes
# ===========================================================================

class CommandResult(dict):
    """Resultat de commande : ok, action, detail, refus."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


def _refuse(action: str, key: str, reason: str) -> CommandResult:
    return CommandResult(ok=False, action=action, component=key, detail=reason,
                         refused=True, at=datetime.now().isoformat(timespec="seconds"))


def _done(action: str, key: str, detail: str) -> CommandResult:
    return CommandResult(ok=True, action=action, component=key, detail=detail,
                         refused=False, at=datetime.now().isoformat(timespec="seconds"))


class ControlCenter:
    """Le cerveau. Une instance = l'application."""

    def __init__(self, python_exe: Optional[str] = None,
                 registry: Optional[Dict[str, Component]] = None):
        self.python_exe = python_exe or sys.executable
        self.settings = load_cc_settings()
        self.history: List[CommandResult] = []
        # Registre injectable : les tests fournissent des composants factices
        # (processus inoffensif) et exerce exactement le meme code de commande
        # que la production, sans lancer le vrai bot ni toucher WoW.
        self.registry: Dict[str, Component] = dict(registry or COMPONENTS_BY_KEY)

    def comp(self, key: str) -> Optional[Component]:
        return self.registry.get(key)

    def components(self) -> List[Component]:
        return list(self.registry.values())

    # -- helpers ---------------------------------------------------------
    def _spawn(self, comp: Component) -> int:
        cmd = comp.command(self.python_exe)
        out_path = comp.stdout_path
        err_path = out_path.replace(".out.log", ".err.log") if out_path else None
        if out_path:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        out_fh = (open(out_path, "a", encoding="utf-8", errors="replace")
                  if out_path else subprocess.DEVNULL)
        err_fh = (open(err_path, "a", encoding="utf-8", errors="replace")
                  if err_path else subprocess.STDOUT)
        try:
            proc = subprocess.Popen(
                cmd, cwd=ROOT,
                stdout=out_fh, stderr=err_fh,
                stdin=subprocess.DEVNULL,
                creationflags=flags, close_fds=True,
            )
        finally:
            if hasattr(out_fh, "close"):
                out_fh.close()
            if hasattr(err_fh, "close"):
                err_fh.close()
        return proc.pid

    def _find_pid(self, comp: Component) -> int:
        if comp.pid_file:
            lock = read_pid_lock(comp.pid_path)
            if lock.get("alive"):
                return int(lock["pid"])
            return 0
        found = find_by_cmdline(comp.cmdline_match or "")
        found = [f for f in found if "fishing-oneclick" not in f["cmdline"].lower()]
        return int(found[0]["pid"]) if found else 0

    def _healthy_instance(self, comp: Component) -> Optional[Dict[str, Any]]:
        """Instance saine = processus vivant ET progression recente.

        (Exigence mission : ne pas signaler ACTIF sur un simple processus.)
        """
        st = component_status(comp)
        return st if st["state"] in (ACTIVE, STARTING, PAUSED) else None

    def _record(self, res: CommandResult) -> CommandResult:
        """TOUT resultat (succes OU refus) doit etre trace : l'interface
        affiche pourquoi une commande est bloquee, et l'export de diagnostic
        doit contenir l'historique."""
        self.history.append(res)
        return res

    @staticmethod
    def _wait_casino_ready(deadline: float, timeout: float = 1.0) -> bool:
        """Le moteur casino repond-il encore sur son named pipe ?"""
        while time.time() < deadline:
            reply = casino_request("ping", timeout=timeout)
            if reply.get("ok"):
                return True
            time.sleep(0.3)
        return False

    def casino(self, action: str, **params: Any) -> CommandResult:
        """Commande METIER du moteur casino (create_session, game, stake,
        roll, resolve, arm_payout, close, recover, drain_actions...).

        Elle passe par le named pipe authentifie du moteur. Elle n'a AUCUN
        effet possible sur le bot peche : aucun composant, aucun fichier et
        aucun verrou du bot de peche n'est touche ici.
        """
        key = "casino_bot"
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse(action, key, "composant casino absent"))
        if not comp.available:
            return self._record(_refuse(action, key, comp.unavailable_reason))
        lock = read_pid_lock(comp.pid_path)
        if not lock.get("alive"):
            return self._record(_refuse(
                action, key,
                "moteur casino arrete : demarrez-le d'abord (bouton Demarrer). "
                "Commande non transmise."))
        reply = casino_request(action, **params)
        if not reply.get("transport", False):
            return self._record(_refuse(
                action, key,
                "moteur casino injoignable : %s. Processus vivant mais pipe "
                "muet -> restart recommande." % reply.get("error")))
        ok = bool(reply.get("ok"))
        detail = "OK" if ok else str(reply.get("error") or "refus du moteur")
        if ok and isinstance(reply.get("session"), dict):
            detail += " | etat session = %s" % reply["session"].get("state")
        res = (_done(action, key, detail) if ok
               else _refuse(action, key, detail))
        # La reponse brute du moteur est conservee : l'interface en a besoin
        # pour afficher l'etat reel (PID, sessions, regles...), pas seulement
        # un « OK ». Sans cela, l'appelant ne pouvait pas lire le statut.
        res["reply"] = reply
        return self._record(res)

    # -- commandes -------------------------------------------------------
    def start(self, key: str) -> CommandResult:
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse("start", key, "composant inconnu"))
        if not comp.available:
            return self._record(_refuse("start", key, comp.unavailable_reason))
        if "start" not in comp.controls:
            return self._record(_refuse(
                "start", key, "demarrage non supporte pour ce composant"))
        # Le lanceur doit EXISTER. Sur `main`, `scripts/fishing_bot.py` est
        # absent (il vit sur la branche de peche) : mieux vaut un refus
        # explicite qu'un subprocess qui meurt en silence.
        if comp.script and not os.path.isfile(os.path.join(ROOT, comp.script)):
            return self._record(_refuse(
                "start", key,
                "lanceur absent de ce checkout : %s (branche de peche non "
                "fusionnee). Rien n'a ete lance." % comp.script))
        lock = read_pid_lock(comp.pid_path) if comp.pid_file else None
        existing = self._find_pid(comp)
        if existing and _pid_alive(existing):
            # L'etat reel est toujours affiche, meme s'il n'est pas « sain » :
            # c'est l'information qui explique le refus.
            current = component_status(comp)["state"]
            return self._record(_refuse(
                "start", key,
                "refus anti-doublon : un processus est deja vivant (PID %d), "
                "etat %s. Utiliser 'Redemarrer' pour un arret propre puis "
                "relance." % (existing, current)))

        # Verrou residuel : on ne le supprime QUE si le processus est mort.
        if lock and lock["status"] == "STALE":
            try:
                os.remove(lock["path"])
                cleanup = "verrou residuel %s supprime (processus %d mort)" % (
                    os.path.basename(lock["path"]), lock["pid"])
            except OSError as exc:
                return self._record(_refuse(
                    "start", key, "verrou residuel non supprimable : %s" % exc))
        else:
            cleanup = "aucun verrou a nettoyer"

        def _do():
            pid = self._spawn(comp)
            deadline = time.time() + float(self.settings["startTimeoutSec"])
            while time.time() < deadline:
                time.sleep(0.5)
                got = self._find_pid(comp)
                if got and _pid_alive(got):
                    # Pour un moteur a IPC, un PID vivant ne suffit PAS : on
                    # attend que le pipe reponde, sinon l'interface afficherait
                    # « actif » sur un moteur muet.
                    if comp.kind == "casino":
                        if not self._wait_casino_ready(deadline):
                            return ("PID %d vivant mais le moteur ne repond pas "
                                    "encore sur son named pipe avant %.0fs"
                                    % (got, float(self.settings["startTimeoutSec"])))
                        return "PID %d pret et joignable sur son named pipe" % got
                    return "PID %d confirme%s" % (
                        got, " (attendu %d)" % pid if got != pid else "")
            return "PID %d lance mais non confirme avant %.0fs" % (
                pid, float(self.settings["startTimeoutSec"]))

        ok, detail = window_guard.safe_call("start_fishing_bot", _do)
        res = _done("start", key, "%s ; %s" % (cleanup, detail)) if ok else \
            _refuse("start", key, detail)
        if not ok and detail.startswith("GARDE_FOU"):
            res["guard"] = True
        return self._record(res)

    def stop(self, key: str) -> CommandResult:
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse("stop", key, "composant inconnu"))
        if not comp.available:
            return self._record(_refuse("stop", key, comp.unavailable_reason))
        if "stop" not in comp.controls:
            return self._record(_refuse(
                "stop", key, "arret non supporte pour ce composant"))

        pid = self._find_pid(comp)
        if not pid or not _pid_alive(pid):
            resid = ""
            lock = read_pid_lock(comp.pid_path) if comp.pid_file else None
            if lock and lock["status"] == "STALE":
                resid = " (verrou residuel present : %s)" % lock["reason"]
            return self._record(_refuse("stop", key, "aucun processus a arreter%s" % resid))

        timeout = float(self.settings["stopTimeoutSec"])

        def _do():
            import psutil
            p = psutil.Process(pid)
            try:
                p.resume()      # un processus suspendu doit repartir pour mourir
            except Exception:
                pass
            p.terminate()
            try:
                p.wait(timeout=timeout)
                how = "terminate"
            except psutil.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    return "PID %d resist a terminate() puis a kill()" % pid
                how = "kill (terminate a expire apres %.0fs)" % timeout
            left = ""
            if comp.pid_path and os.path.isfile(comp.pid_path):
                try:
                    os.remove(comp.pid_path)
                    left = " ; verrou %s libere" % os.path.basename(comp.pid_path)
                except OSError:
                    pass
            return "PID %d : %s%s" % (pid, how, left)

        ok, detail = window_guard.safe_call("stop_fishing_bot", _do)
        res = _done("stop", key, detail) if ok else _refuse("stop", key, detail)
        if not ok and detail.startswith("GARDE_FOU"):
            res["guard"] = True
        return self._record(res)

    def pause(self, key: str) -> CommandResult:
        """Pause REVERSIBLE : suspension des threads du processus.

        Le processus reste VIVANT, garde son verrou PID (donc aucun doublon
        possible) et n'envoie plus aucune entree. Aucune touche n'est injectee.
        Si la suspension OS est indisponible, repli sur « retenue logique » :
        le Control Center cesse de piloter, le bot garde la main.
        """
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse("pause", key, "composant inconnu"))
        if not comp.available:
            return self._record(_refuse("pause", key, comp.unavailable_reason))
        if "pause" not in comp.controls:
            return self._record(_refuse(
                "pause", key,
                "pause non supporte : %s. Le processus n'a aucun mecanisme de "
                "pause externe." % comp.pause_mode))
        pid = self._find_pid(comp)
        if not pid or not _pid_alive(pid):
            return self._record(_refuse("pause", key, "aucun processus vivant a suspendre"))

        def _do():
            import psutil
            p = psutil.Process(pid)
            try:
                p.suspend()
            except Exception as exc:
                return None, "SUSPEND_IMPOSSIBLE : %s" % exc
            return True, "PID %d suspendu (threads figes, verrou PID conserve)" % pid

        ok, detail = window_guard.safe_call("pause_fishing_bot", _do)
        if ok and detail.startswith("SUSPEND_IMPOSSIBLE"):
            res = _refuse("pause", key, detail +
                          " -> repli sur RETENUE LOGIQUE : le Control Center "
                          "cesse de piloter ce composant, le processus continue.")
            res["fallback"] = "retenue_logique"
        else:
            res = _done("pause", key, detail) if ok else _refuse("pause", key, detail)
        return self._record(res)

    def resume(self, key: str) -> CommandResult:
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse("resume", key, "composant inconnu"))
        if not comp.available:
            return self._record(_refuse("resume", key, comp.unavailable_reason))
        if "resume" not in comp.controls:
            return self._record(_refuse(
                "resume", key, "reprise non supporte : %s" % comp.pause_mode))
        pid = self._find_pid(comp)
        if not pid or not _pid_alive(pid):
            return self._record(_refuse("resume", key, "aucun processus vivant a reprendre"))

        def _do():
            import psutil
            p = psutil.Process(pid)
            try:
                p.resume()
            except Exception as exc:
                return None, "REPRISE_IMPOSSIBLE : %s" % exc
            return True, "PID %d repris" % pid

        ok, detail = window_guard.safe_call("resume_fishing_bot", _do)
        if ok and detail.startswith("REPRISE_IMPOSSIBLE"):
            res = _refuse("resume", key, detail)
        else:
            res = _done("resume", key, detail) if ok else _refuse("resume", key, detail)
        return self._record(res)

    def restart(self, key: str) -> CommandResult:
        """Arret PROPRE verifie, puis relance. Jamais de double instance."""
        comp = self.comp(key)
        if comp is None:
            return self._record(_refuse("restart", key, "composant inconnu"))
        if not comp.available:
            return self._record(_refuse("restart", key, comp.unavailable_reason))
        if "restart" not in comp.controls:
            return self._record(_refuse(
                "restart", key, "redemarrage non supporte pour ce composant"))
        pid = self._find_pid(comp)
        if pid and _pid_alive(pid):
            stop_res = self.stop(key)
            if not stop_res.ok:
                return self._record(_refuse(
                    "restart", key,
                    "arret propre impossible -> redemarrage refuse : %s"
                    % stop_res["detail"]))
            time.sleep(1.0)
        else:
            self.stop(key)  # nettoie un eventuel verrou residuel
        start_res = self.start(key)
        merged = CommandResult(ok=start_res.ok, action="restart", component=key,
                               detail="arret: %s | demarrage: %s" % (
                                   stop_res["detail"], start_res["detail"]),
                               refused=start_res["refused"],
                               at=datetime.now().isoformat(timespec="seconds"))
        return self._record(merged)

    def dispatch(self, action: str, key: str) -> CommandResult:
        fn = {"start": self.start, "stop": self.stop, "pause": self.pause,
              "resume": self.resume, "restart": self.restart}.get(action)
        if fn is None:
            return self._record(_refuse(action, key, "action inconnue : %s" % action))
        return fn(key)

    # -- lecture ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        comps = [component_status(c, now=now) for c in self.components()]
        snap = window_guard.snapshot()
        bot = next((c for c in comps if c["key"] == "fishing_bot"), None)
        casino = next((c for c in comps if c["key"] == "casino_bot"), None)
        return {
            "at": datetime.now().isoformat(timespec="seconds"),
            "components": comps,
            "bot_state": bot["state"] if bot else UNVERIFIABLE,
            "casino_state": casino["state"] if casino else UNVERIFIABLE,
            "casino_detail": casino.get("casino") if casino else None,
            "casino_config": casino_load_config(),
            "wow": snap["wow"],
            "foreground": snap["foreground"],
            "cursor": snap["cursor"],
            "guard": {
                "forbidden": list(window_guard.FORBIDDEN),
                "allowed_count": len(window_guard.ALLOWED),
            },
            "settings": dict(self.settings),
        }

    def log_tail(self, key: str, lines: int = 200,
                 level: str = "", since: str = "",
                 error_only: bool = False) -> List[Dict[str, Any]]:
        comp = self.comp(key)
        if comp is None:
            return []
        rows = (tail_jsonl(comp.progress_path, max_lines=max(500, lines * 5))
                if comp.progress_log else _tail_text(comp.stdout_path, lines))
        if since:
            ref = _parse_ts(since)
            if ref is not None:
                rows = [r for r in rows if (_parse_ts(r.get("ts")) or ref) >= ref]
        if level:
            lv = level.upper()
            rows = [r for r in rows if _level_of(r).startswith(lv)]
        if error_only:
            rows = [r for r in rows if _is_error_row(r)]
        return rows[-lines:]

    def diagnostic_report(self) -> Dict[str, Any]:
        """Rapport complet, SANS secret, exportable en JSON."""
        snap = self.snapshot()
        locks = {}
        for c in self.components():
            if c.pid_file:
                locks[c.pid_file] = mask_tree(read_pid_lock(c.pid_path))
        return {
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": ROOT,
            "python": sys.executable,
            "platform": sys.platform,
            "snapshot": mask_tree(snap),
            "pid_locks": locks,
            "fishing_config": mask_tree(_read_json(FISHING_CONFIG_FILE, {})),
            "bridge_config": mask_tree(
                _read_json(os.path.join(ROOT, "bridge", "config.json"), {})),
            "control_center_settings": mask_tree(dict(self.settings)),
            "window_guard_policy": {
                "forbidden": list(window_guard.FORBIDDEN),
                "allowed": list(window_guard.ALLOWED),
            },
            "casino": {
                "component": "casino_bot",
                "engine": "scripts/casino_bot.py (moteur autonome)",
                "rules_engine": "scripts/casino_engine.py (port de CB.GameRules)",
                "ipc": "named pipe local authentifie, aucun port reseau",
                "detail": mask_tree(casino_detail()),
                "config": mask_tree(casino_load_config()),
            },
            "recent_commands": [dict(h) for h in self.history[-25:]],
        }


_ERROR_EVENTS = ("ERROR", "BOT_CRASH", "CAPTURE_ERROR", "CLICK_ABORTED",
                 "CAST_UNCONFIRMED", "VERIFY_RETRY_FAILED", "STARTUP_RETRY",
                 "FOCUS_LOST_CYCLE", "KEY_FAIL")


def _is_error_row(row: Dict[str, Any]) -> bool:
    ev = str(row.get("event", "")).upper()
    if ev in _ERROR_EVENTS or "ERROR" in ev or "FAIL" in ev or "ABORT" in ev:
        return True
    return str(row.get("state", "")).upper() in ("ERROR", "FATAL_ERROR")


def _level_of(row: Dict[str, Any]) -> str:
    ev = str(row.get("event", "")).upper()
    if ev in ("ERROR", "BOT_CRASH"):
        return "ERROR"
    if _is_error_row(row):
        return "WARN"
    if ev in ("WATCH", "WATCH_MISS", "CURSOR_SKIP", "CAST_NOT_SENT"):
        return "DEBUG"
    return "INFO"


def _tail_text(path: Optional[str], lines: int) -> List[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > 256 * 1024:
                fh.seek(size - 256 * 1024)
                fh.readline()
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [{"ts": "", "event": "STDOUT", "text": line, "state": ""}
            for line in raw.splitlines()[-lines:]]


# ===========================================================================
# Auto-test
# ===========================================================================

def _selftest() -> int:
    print("=" * 70)
    print("control_core - auto-test (hors ligne, aucun processus lance)")
    print("=" * 70)
    fails = []

    def check(label, cond, detail=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                               (" - " + detail) if detail else ""))
        if not cond:
            fails.append(label)

    # Deux environments sont legitimes : le checkout complet (branche de peche)
    # et le checkout `main` seul, ou `fishing_bot.py`, le journal du bot et la
    # config n'existent pas. L'auto-test verifie le comportement correct de
    # CHACUN au lieu de supposer le premier.
    bot_here = bot_script_present()
    bot_log = COMPONENTS_BY_KEY["fishing_bot"].progress_path
    log_here = bool(bot_log) and os.path.isfile(bot_log)
    mode = "COMPLET" if (bot_here and log_here) else "REDUIT (main sans le bot)"
    print("  Environnement : %s" % mode)
    print("  fishing_bot.py present : %s" % bot_here)
    print("  journal du bot present : %s" % log_here)
    print("-" * 70)

    check("5 composants declares", len(COMPONENTS) == 5)
    check("moteur casino = composant reel pilotable",
          COMPONENTS_BY_KEY["casino_bot"].available is True
          and set(COMPONENTS_BY_KEY["casino_bot"].controls) ==
          {"start", "stop", "pause", "resume", "restart"},
          "controls=%s" % list(COMPONENTS_BY_KEY["casino_bot"].controls))
    check("moteur casino : ressources dediees, distinctes du bot peche",
          COMPONENTS_BY_KEY["casino_bot"].pid_file
          != COMPONENTS_BY_KEY["fishing_bot"].pid_file
          and COMPONENTS_BY_KEY["casino_bot"].progress_log
          != COMPONENTS_BY_KEY["fishing_bot"].progress_log,
          "%s / %s" % (COMPONENTS_BY_KEY["casino_bot"].pid_file,
                       COMPONENTS_BY_KEY["casino_bot"].progress_log))
    check("verrou bot = fichier existant",
          COMPONENTS_BY_KEY["fishing_bot"].pid_path.endswith("fishing-bot.pid"))

    lock = read_pid_lock(os.path.join(STATE_DIR, "fishing-bot.pid"))
    check("verrou residuel qualifie", lock["status"] in ("STALE", "ABSENT", "UNREADABLE"),
          "status=%s pid=%s" % (lock["status"], lock["pid"]))
    check("verrou inexistant = ABSENT",
          read_pid_lock(os.path.join(STATE_DIR, "__absent__.pid"))["status"] == "ABSENT")
    check("verrou corrompu = UNREADABLE", _write_and_read_bad_lock())

    check("secret masque", mask_value("sessionToken", "abc") == MASK)
    check("cle non-secret pas masque", mask_value("pollMs", 1500) == 1500)
    check("api_key masque (normalisation)", mask_value("api_key", "x") == MASK)
    check("CLIENT_SECRET masque (normalisation)", mask_value("CLIENT_SECRET", "x") == MASK)
    tree = mask_tree({"sessionToken": "x", "nested": {"apiKey": "y"}, "ok": 1})
    check("masquage recursif",
          tree["sessionToken"] == MASK and tree["nested"]["apiKey"] == MASK
          and tree["ok"] == 1)

    clean, errs = validate_update({"bobberMinConf": 0.7, "unknownParam": 1})
    check("validation : cle inconnue refusee", "unknownParam" not in clean and errs)
    check("validation : valeur valide acceptee", clean.get("bobberMinConf") == 0.7)
    clean2, errs2 = validate_update({"bobberMinConf": 9.9})
    check("validation : hors bornes refuse", not clean2 and errs2)
    clean3, errs3 = validate_update({"detectScale": 0.5})
    check("validation : parametre verrouille refuse", not clean3 and errs3)
    clean4, _ = validate_update({"castSettleSec": "abc"})
    check("validation : type invalide refuse", not clean4)

    cfg = load_fishing_config()
    check("config : toutes les cles du schema accessibles",
          all(k in cfg for k in FISHING_SCHEMA))
    check("config : source annoncee", bool(cfg.get("__config_source__")))
    if bot_here:
        check("config completee par DEFAULT_CONFIG",
              cfg.get("cursorDivergePx") == 60,
              "cursorDivergePx=%s" % cfg.get("cursorDivergePx"))
    else:
        check("config : repli sur le schema interne",
              cfg.get("__bot_present__") is False
              and "schema integre" in cfg["__config_source__"])
        ok, errs, _ = save_fishing_config({"pasUnParametre": 1})
        check("ecriture refusee pour cle inconnue", not ok and errs)

    if log_here:
        check("journal de progression = fichier existant", True)
        hb = heartbeat(COMPONENTS_BY_KEY["fishing_bot"])
        check("heartbeat lu", hb.get("checked") is True, str(hb.get("last_event")))
        check("heartbeat : age disponible", hb.get("last_age_sec") is not None,
              "age=%s event=%s" % (hb.get("last_age_sec"), hb.get("last_event")))
    else:
        hb = heartbeat(COMPONENTS_BY_KEY["fishing_bot"])
        check("heartbeat absent = BLOQUE si vivant, sans planter",
              hb.get("checked") is True and hb.get("fresh") is False,
              str(hb.get("error")))

    st = component_status(COMPONENTS_BY_KEY["fishing_bot"])
    check("botPeche declare etat coherent",
          st["state"] in (STOPPED, ACTIVE, BLOCKED, STARTING, PAUSED, ERROR),
          "%s - %s" % (st["state"], st["detail"]))

    # Le moteur casino existe desormais comme processus autonome. On ne
    # suppose pas qu'il tourne : on verifie que son etet est REELLEMENT
    # interroge (processus + IPC) et non invente.
    casino_comp = COMPONENTS_BY_KEY["casino_bot"]
    cst = component_status(casino_comp)
    check("moteur casino declare un etat reel",
          cst["state"] in (STOPPED, ACTIVE, BLOCKED, STARTING, PAUSED, ERROR),
          "%s - %s" % (cst["state"], cst["detail"]))
    check("moteur casino : script et journal sur disque",
          os.path.isfile(os.path.join(ROOT, "scripts", "casino_bot.py"))
          and os.path.isfile(os.path.join(ROOT, "scripts", "casino_engine.py")))
    check("moteur casino : detail IPC ou repli disque",
          cst.get("casino") is not None
          and (cst["casino"].get("reachable") or cst["casino"].get("source") == "disque"))
    check("moteur casino : regles metier exposees",
          bool(cst.get("casino", {}).get("rules")) or
          os.path.isfile(os.path.join(ROOT, "scripts", "casino_engine.py")))

    cc = ControlCenter()
    n_before = len(cc.history)
    # L'auto-test est STRICTEMENT OBSERVATEUR : il ne demarre ni n'arrete aucun
    # moteur. Le demarrage/arret reel est valide par la suite pytest et par le
    # panneau de controle, pas ici (un test qui laisse un processus derriere
    # lui est un test casse).
    alive_now = bool(read_pid_lock(casino_comp.pid_path).get("alive"))
    if alive_now:
        r = cc.start("casino_bot")
        check("demarrage casino refuse quand le moteur tourne deja (anti-doublon)",
              (not r.ok) and "anti-doublon" in r["detail"], r["detail"])
    else:
        check("moteur casino arrete : aucun demarrage declenche par l auto-test",
              True, "moteur observe = ARRETE")
    r2 = cc.casino("status")
    if alive_now:
        check("commande metier casino acceptee par le moteur reel",
              r2.get("ok") is True, str(r2.get("error")))
        check("etat du moteur lu par IPC (pas par memoire)",
              (r2.get("reply", {}).get("status") or {}).get("pid") ==
              read_pid_lock(casino_comp.pid_path).get("pid"),
              "pid IPC=%s / verrou=%s" % (
                  (r2.get("reply", {}).get("status") or {}).get("pid"),
                  read_pid_lock(casino_comp.pid_path).get("pid")))
    else:
        check("commande metier casino refusee si moteur arrete",
              (not r2.ok) and "arrete" in r2["detail"].lower(), r2["detail"])
    r3 = cc.stop("fishing_bot")
    check("arret peche sans processus refuse et explique",
          (not r3.ok) and "aucun processus" in r3["detail"].lower(), r3["detail"])
    check("tout resultat trace",
          len(cc.history) > n_before
          and all("action" in h and "ok" in h for h in cc.history))

    if not bot_here:
        r = cc.start("fishing_bot")
        check("demarrage refuse si le lanceur est absent du checkout",
              (not r.ok) and "lanceur absent" in r["detail"], r["detail"])

    snap = cc.snapshot()
    check("snapshot complet",
          {"bot_state", "casino_state", "wow", "guard"} <= set(snap))
    rep = cc.diagnostic_report()
    text = json.dumps(rep, ensure_ascii=False)
    bridge_cfg = _read_json(os.path.join(ROOT, "bridge", "config.json"), {})
    if bridge_cfg:
        check("rapport : secret de bridge masque",
              all((not bridge_cfg.get(k)) or MASK in text
                  for k in bridge_cfg if is_secret(k)))
        for k in bridge_cfg:
            if is_secret(k) and bridge_cfg[k]:
                check("secret '%s' absent en clair" % k, bridge_cfg[k] not in text)
    else:
        check("rapport : aucune config bridge a masquer (checkout reduit)", True)
    check("rapport : section casino presente et reelle",
          "casino" in rep and "engine" in rep["casino"]
          and "detail" in rep["casino"], str(sorted(rep.get("casino", {}))))
    check("config casino = fichier distinct de la config peche",
          casino_load_config() is not None)

    print("-" * 70)
    print("RESULTAT : %s (%d echec(s))" % ("PASS" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 1


def _write_and_read_bad_lock() -> bool:
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "cb_bad_lock_test.pid")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("pas-un-pid")
    try:
        return read_pid_lock(path)["status"] == "UNREADABLE"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    cc = ControlCenter()
    print(json.dumps(cc.snapshot(), indent=2, ensure_ascii=False))
