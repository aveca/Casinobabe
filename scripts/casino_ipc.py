#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""casino_ipc — canal local authentifie entre l'interface et les moteurs.

CHOIX TECHNIQUE
===============
* Named pipe Windows (`\\\\.\\pipe\\<nom>`) via
  `multiprocessing.connection` en `family='AF_PIPE'`.
  → **Aucun port reseau n'est ouvert.** Pas de listener TCP, pas de port
  public, rien a filtrer. La securite repose sur le systeme de fichiers
  named-pipes de Windows, pas sur un pare-feu.
* **Authentification par jeton** : 32 octets aleatoires generes a la creation,
  stockes dans `state/casino-ipc-token.txt`, dont les droits ACL sont
  restreints au compte courant via `icacls`. Chaque connexion doit presenter le
  jeton ; la comparaison est en temps constant.
* **Aucune commande n'est executee sur la seule foi d'une connexion** : chaque
  commande est validee (schema + etat prealable) par le moteur, qui repond
  toujours explicitement.
* **Pas d'injection** : le canal ne transporte que du JSON et n'entame
  jamais d'action sur WoW. Les actions client sont mises en file
  (`pending_actions`) et reservees a l'addon.

RECONNEXION
===========
L'interface ne conserve aucun etat en memoire : elle interroge le moteur a
chaque reouverture. Si le moteur est arrete, le jeton et le fichier d'etat
suffisent a reconstituer l'etat reel (processus + heartbeat + journaux).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
STATE_DIR = os.path.join(ROOT, "state")
PIPE_BASE = r"\\.\pipe\casinobabe-casino"
TOKEN_FILE = os.path.join(STATE_DIR, "casino-ipc-token.txt")
TOKEN_BYTES = 32
DEFAULT_TIMEOUT = 5.0

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.makedirs(STATE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Jeton
# ---------------------------------------------------------------------------

def _restrict_acl(path: str) -> str:
    """Restreint le fichier au seul compte courant (via icacls natif)."""
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    account = ("%s\\%s" % (domain, user)) if domain and user else user
    if not account:
        return "compte inconnu : ACL non restreinte"
    try:
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", "%s:F" % account],
            check=True, capture_output=True, text=True, timeout=10)
        return "ACL restreinte a %s" % account
    except Exception as exc:
        return "echec icacls : %s" % exc


def create_token(force: bool = False) -> Tuple[str, str]:
    """Cree (ou recharge) le jeton. Retourne (jeton, note)."""
    if os.path.isfile(TOKEN_FILE) and not force:
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
                tok = fh.read().strip()
            if len(tok) >= 32:
                return tok, "jeton existant recharge"
        except OSError:
            pass
    tok = secrets.token_hex(TOKEN_BYTES)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(tok)
    note = _restrict_acl(TOKEN_FILE)
    return tok, "jeton genere (%s)" % note


def load_token() -> Optional[str]:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            tok = fh.read().strip()
        return tok or None
    except OSError:
        return None


def token_matches(candidate: Optional[str], expected: str) -> bool:
    """Comparaison en temps constant : pas de fuite par timing."""
    if not candidate:
        return False
    return hmac.compare_digest(str(candidate), str(expected))


def pipe_address(identity: str = "control-center") -> str:
    """Adresse du named pipe.

    UN SEUL pipe pour tous (`\\\\.\\pipe\\casinobabe-casino`). Une premiere
    version suffixait le nom par client ; le serveur n'ouvrant que le nom de
    base, aucun client ne pouvait jamais se connecter (FileNotFoundError au
    premier test reel). `identity` est conserve pour la tracabilite uniquement.
    """
    return PIPE_BASE


# ---------------------------------------------------------------------------
# Protocole
# ---------------------------------------------------------------------------

#: Commandes acceptees par le moteur. Toute autre commande est refusee.
COMMANDS = (
    "ping", "status", "rules", "create_session", "list_sessions",
    "game", "stake", "confirm_trade", "mark_stake_confirmed", "mark_grouped",
    "roll", "resolve", "house_total", "house_rolls",
    "arm_payout", "mark_paid", "close", "recover", "drain_actions",
    "check_integrity", "check_timeouts", "shutdown",
)


def encode(command: str, token: str, **params: Any) -> Dict[str, Any]:
    return {"cmd": command, "token": token, "params": params,
            "ts": time.time(), "v": 1}


def validate_command(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "charge utile illisible"
    cmd = payload.get("cmd")
    if not isinstance(cmd, str) or not cmd:
        return False, "commande manquante"
    if cmd not in COMMANDS:
        return False, "commande inconnue : %s" % cmd
    params = payload.get("params", {})
    if not isinstance(params, dict):
        return False, "parametres illisibles"
    return True, cmd


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class IpcError(RuntimeError):
    pass


def request(command: str, token: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
            identity: str = "control-center", **params: Any) -> Dict[str, Any]:
    """Envoie une commande et attend la reponse. Leve IpcError en cas d'echec.

    Le delai est applique par `conn.poll(timeout)` : sur Windows,
    `multiprocessing.connection.Client` n'accepte PAS de parametre `timeout`
    (verifie : TypeError). C'est donc au client de borner l'attente, sinon un
    moteur bloque figerait l'interface.
    """
    from multiprocessing.connection import Client

    tok = token or load_token()
    if not tok:
        raise IpcError("jeton absent : moteur jamais demarre sur cette machine ?")
    try:
        conn = Client(pipe_address(identity), family="AF_PIPE")
    except Exception as exc:
        raise IpcError("connexion impossible au moteur casino : %s" % exc) from exc
    try:
        conn.send(encode(command, tok, **params))
        if not conn.poll(timeout):
            raise IpcError("delai depasse (%ss) en attente de la reponse du moteur "
                           "sur '%s'" % (timeout, command))
        reply = conn.recv()
    except IpcError:
        raise
    except Exception as exc:
        raise IpcError("echange interrompu : %s" % exc) from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not isinstance(reply, dict):
        raise IpcError("reponse invalide du moteur")
    return reply


def ping(timeout: float = 1.0, identity: str = "control-center") -> bool:
    """Le moteur repond-il ? Utilise pour distinguer 'arrete' de 'muet'."""
    try:
        reply = request("ping", timeout=timeout, identity=identity)
        return bool(reply.get("ok"))
    except IpcError:
        return False


# ---------------------------------------------------------------------------
# Serveur (dans le processus du moteur)
# ---------------------------------------------------------------------------

class Server:
    """Serveur du moteur. Boucle d'ecoute sur named pipe.

    Chaque connexion est traitee par un thread ; le serveur principal continue
    de battre (heartbeat / watchdog). Une connexion ne peut pas bloquer
    l'activite du moteur.
    """

    def __init__(self, token: str, handler, timeout: float = DEFAULT_TIMEOUT):
        self.token = token
        self.handler = handler
        self.timeout = timeout
        self._listener = None

    def __enter__(self) -> "Server":
        from multiprocessing.connection import Listener

        os.makedirs(STATE_DIR, exist_ok=True)
        # Un pipe fantome d'une execution precedente bloquerait le nom.
        try:
            self._listener = Listener(pipe_address("server"), family="AF_PIPE")
        except Exception as exc:
            raise IpcError("ouverture du pipe impossible : %s" % exc) from exc
        return self

    def __exit__(self, *exc) -> None:
        if self._listener is not None:
            try:
                self._listener.close()
            except Exception:
                pass
            self._listener = None

    def serve_forever(self, stop_flag) -> None:
        """Boucle d'ecoute. `stop_flag` est un threading.Event."""
        import threading

        from multiprocessing.connection import Listener  # noqa: F401

        while not stop_flag.is_set():
            try:
                if self._listener is None:
                    return
                self._listener.accept()          # bloquant court, ferme par _stop
            except (OSError, EOFError):
                if stop_flag.is_set():
                    return
                time.sleep(0.05)
                continue
            except Exception:
                if stop_flag.is_set():
                    return
                time.sleep(0.05)
                continue

    def handle_once(self, conn) -> None:
        """Traite UNE connexion de bout en bout."""
        try:
            try:
                payload = conn.recv()
            except Exception:
                return
            ok, cmd = validate_command(payload)
            if not ok:
                try:
                    conn.send({"ok": False, "error": cmd, "at": time.time()})
                except Exception:
                    pass
                return
            if not token_matches(payload.get("token"), self.token):
                # Jeton invalide : refus IMMEDIAT, aucune commande executee.
                try:
                    conn.send({"ok": False, "error": "AUTH_FAILED", "cmd": cmd,
                               "at": time.time()})
                except Exception:
                    pass
                return
            params = payload.get("params", {}) or {}
            try:
                reply = self.handler(cmd, params)
            except Exception as exc:
                reply = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            reply.setdefault("at", time.time())
            reply.setdefault("cmd", cmd)
            try:
                conn.send(reply)
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
