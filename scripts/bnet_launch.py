#!/usr/bin/env python3
"""Lance un jeu via l'Agent Battle.net DEJA CONNECTE (aucun nouveau client).

Pourquoi : `Battle.net.exe --launch=<uid>` DEMARRE UN PROCESSUS. Si le client
n'est pas pret (ou pas authentifie), ca cree une 2e instance -> crash
"Blizzard Error" et aucun lancement. L'Agent expose en revanche un socket local
et repond a `Agent.request_launch` : le client existant lance le jeu, rien de
nouveau n'est cree.

 usages :
   python scripts/bnet_launch.py wow_classic_anniversary
   python scripts/bnet_launch.py wow_classic_anniversary --wait 240

Aucun identifiant n'est transmis : l'Agent utilise la session deja
authentifiee du client.
"""
import argparse
import json
import socket
import struct
import subprocess
import sys
import time
import uuid


def agent_ports():
    """Ports TCP en ecoute des processus Agent.exe (le 1120 par defaut est
    souvent pris par une autre instance : l'Agent choisit alors un port
    aleatoire, il ne faut donc pas coder 1120 en dur)."""
    out = []
    try:
        pids = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Agent.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "replace")
        for line in pids.splitlines():
            cols = [c.strip('" ') for c in line.split('","')]
            if len(cols) > 1 and cols[0].lower() == "agent.exe":
                out.append(int(cols[1]))
    except Exception as exc:
        print("tasklist impossible : %s" % exc)
    if not out:
        return []
    ports = []
    for pid in out:
        try:
            res = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-NetTCPConnection -State Listen -OwningProcess %d "
                 "-ErrorAction SilentlyContinue).LocalPort" % pid],
                stderr=subprocess.DEVNULL, timeout=15).decode()
            ports += [int(p) for p in res.split() if p.strip().isdigit()]
        except Exception:
            pass
    return ports


def request_launch(port, product_id, timeout=20):
    """Envoie Agent.request_launch. Le protocole de l'Agent est du JSON termine
    par un octet NUL (certaines versions prependent une longueur 4 octets)."""
    msg = {"jsonrpc": "2.0", "id": str(uuid.uuid4()),
           "method": "Agent.request_launch", "params": {"productId": product_id}}
    payload = json.dumps(msg).encode("utf-8")
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        for frame in (payload + b"\0", struct.pack(">I", len(payload)) + payload):
            try:
                sock.sendall(frame)
            except OSError:
                continue
            buf = b""
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\0" in buf:
                        break
            except socket.timeout:
                pass
            if buf:
                return buf
    finally:
        sock.close()
    return b""


def wow_windows():
    sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
    from fishing_observer import list_wow_windows
    return list_wow_windows()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("product", nargs="?", default="wow_classic_anniversary")
    ap.add_argument("--wait", type=int, default=240,
                    help="secondes d'attente de la fenetre WoW (0 = ne pas attendre)")
    a = ap.parse_args()

    ports = agent_ports()
    print("ports Agent en ecoute : %s" % (ports or "AUCUN"))
    if not ports:
        print("ECHEC : aucun Agent Battle.net en ecoute. "
              "L'Agent demarre avec le client ; s'il manque, redemarrer "
              "Battle.net (une seule instance).")
        return 2

    ok = False
    for p in ports:
        print("request_launch -> port %d ..." % p)
        try:
            resp = request_launch(p, a.product)
        except Exception as exc:
            print("  echec : %s" % exc)
            continue
        text = resp.decode("utf-8", "replace").strip("\0").strip()
        # BUG CORRIGE le 2026-10-17 : le test "pas de '\"error\"' => succes"
        # disait OK sur une reponse "HTTP/1.0 400 Bad Request / Malformed
        # message". Le client affirmait avoir lance le jeu alors que l'Agent
        # avait refuse la requete. Toute reponse HTTP ou contenant une erreur
        # est donc un ECHEC, jamais un succes.
        if not text:
            print("  reponse vide (Agent muet) : inconclus")
            continue
        if text.startswith("HTTP/") or "Malformed message" in text:
            print("  REJET : %s" % text.replace("\n", " ")[:160])
            print("  -> le framing de l'Agent n'est pas reconnu (null-termine ou")
            print("     longueur 4 octets). Utilise `Battle.net.exe --launch=`,")
            print("     qui passe par le client deja connecte.")
            continue
        if '"error"' in text:
            print("  ERREUR Agent : %s" % text[:200])
            continue
        print("  reponse : %s" % text[:200])
        ok = True

    if not ok:
        print("ECHEC : l'Agent n'a pas accepte la demande de lancement.")
        return 1

    if a.wait <= 0:
        return 0
    target = len(wow_windows()) + 1
    deadline = time.time() + a.wait
    while time.time() < deadline:
        wins = wow_windows()
        if len(wins) >= target:
            print("OK : %d fenetre(s) WoW visible(s)." % len(wins))
            return 0
        time.sleep(2)
    print("ECHEC : fenetre WoW absente apres %d s." % a.wait)
    return 1


if __name__ == "__main__":
    sys.exit(main())