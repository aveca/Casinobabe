#!/usr/bin/env python3
"""Casinobabe Fishing Assistant - Standalone Windows Application"""

import argparse
import ctypes
import datetime
import json
import os
import sys
import threading
import time
import json as json_mod
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mss
from PIL import Image
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
LOGS_DIR = os.path.join(ROOT, "logs")
CAPTURES_DIR = os.path.join(ROOT, "captures")
REPORTS_DIR = os.path.join(ROOT, "reports")

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(CAPTURES_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

PID_FILE = os.path.join(STATE_DIR, "fishing-observer.pid")
CONFIG_FILE = os.path.join(STATE_DIR, "fishing-bot-config.json")
LOG_FILE = os.path.join(REPORTS_DIR, "fishing-assistant.log")

DEFAULT_CONFIG = {
    "castKey": "1",
    "castSettleSec": 3.0,
    "maxBiteWaitSec": 32.0,
    "clickAfterBiteMs": 180,
    "bobberMinConf": 0.55,
    "biteDiffThresh": 14,
    "biteConsecutive": 2,
    "captureIntervalSec": 0.35,
    "detectScale": 1.0,
}

STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_PAUSED = "PAUSED"
STATE_STOPPED = "STOPPED"

config = dict(DEFAULT_CONFIG)
app_state = STATE_IDLE
app_state_lock = threading.Lock()
observer_thread = None
bot_thread = None
stop_event = threading.Event()


def _safe_print(s):
    s = str(s)
    try:
        print(s)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, errors="replace").decode(enc))


def load_config():
    global config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        config.update(cfg)
        _safe_print("Configuration chargee : %s" % json.dumps(cfg, indent=2))
    except (OSError, ValueError) as e:
        _safe_print("Impossible de charger la config : %s - utilisation par defaut" % e)
        save_config(dict(DEFAULT_CONFIG))


def save_config(cfg=None):
    global config
    if cfg is None:
        cfg = config
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        _safe_print("Configuration sauvegeeee : %s" % json.dumps(cfg, indent=2))
    except OSError as e:
        _safe_print("Erreur sauvegarde config : %s" % e)


def is_wow_running():
    try:
        import subprocess
        output = subprocess.check_output(
            ["tasklist", "/FI", "IMAGE eq WowClassic.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL, timeout=8).decode("utf-8", "replace")
        return "WowClassic.exe" in output
    except Exception:
        try:
            output = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"], stderr=subprocess.DEVNULL, timeout=8).decode(
                    "utf-8", "replace")
            return "World of Warcraft" in output or "Wow" in output
        except Exception:
            return False


def find_wow_window():
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "World of Warcraft")
    if hwnd:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return hwnd, pid
    return None, None


def observe_once():
    ts = datetime.datetime.now()
    obs = {
        "ts": ts.isoformat(timespec="seconds"),
        "windowFound": False,
        "windowTitle": "",
        "rect": (0, 0, 0, 0),
        "capture": None,
        "captureVerifiedWow": False,
        "error": None,
        "inGameState": "non verifie par ce script (les compteurs exacts vivent dans l'addon)",
    }
    hwnd, title, rect = find_wow_window()
    obs["windowFound"] = hwnd is not None
    obs["windowTitle"] = title or ""
    obs["rect"] = rect or (0, 0, 0, 0)
    if not hwnd:
        obs["error"] = "fenetre 'World of Warcraft' introuvable (processus ferme ou minimise invisible)"
        return obs, {"status": "no_wow"}
    try:
        sct = mss.monitor[-1]
        import ctypes.windll.user32 as u32
        r = __import__("wintypes").RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            obs["error"] = "GetWindowRect failed"
            return obs, {"status": "capture_fail"}
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            obs["error"] = "taille fenetre invalide %dx%d" % (w, h)
            return obs, {"status": "capture_fail"}
        mon = {"top": r.top, "left": r.left, "width": w, "height": h}
        img_np = np.array(sct.grab(mon))
        img_rgb = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB)
        regions = [
            (int(w * 0.02), int(h * 0.10), int(w * 0.45), int(h * 0.60)),
            (int(w * 0.25), int(h * 0.55), int(w * 0.75), int(h * 0.95)),
            (int(w * 0.55), int(h * 0.30), int(w * 0.95), int(h * 0.60)),
        ]
        min_colors = 20000
        wow_frame = False
        for (rx0, ry0, rx1, ry1) in regions:
            crop = img_rgb[ry0:ry1, rx0:rx1]
            if crop.size > 0:
                colors = crop.reshape(-1, 3)
                n_colors = len(set(tuple(c) for c in colors))
                if n_colors > min_colors:
                    wow_frame = True
                    break
        obs["captureVerifiedWow"] = wow_frame
        if not obs["captureVerifiedWow"]:
            obs["error"] = "capture lisible mais contenu non reconnu comme frame WoW"
        else:
            path = os.path.join(CAPTURES_DIR, "wow-%s.png" % ts.strftime("%Y%m%d-%H%M%S"))
            cv2.imwrite(path, img_np)
            obs["capture"] = os.path.relpath(path, ROOT)
    except Exception as e:
        obs["error"] = "erreur capture : %s" % str(e)
    obs["observationStatus"] = ("live" if obs["captureVerifiedWow"]
                                else "uncertain" if obs["capture"] else "no-capture")
    return obs, {"status": "ok"}


def set_app_state(new_state):
    global app_state
    with app_state_lock:
        old = app_state
        app_state = new_state
        _safe_print("Etat %s -> %s" % (old, new_state))


def log_event(event, **kw):
    row = {"ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
           "event": event, "state": app_state}
    row.update(kw)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json_mod.dumps(row, ensure_ascii=False) + "\n")


def observer_worker():
    global stop_event
    while not stop_event.is_set():
        obs, ctx = observe_once()
        if obs["capture"]:
            log_event("CAPTURE_SAVE", capturePath=obs["capture"])
        if obs["error"] and "introuvable" in obs["error"]:
            log_event("WOW_NOT_RUNNING")
            stop_event.wait(timeout=5.0)
            continue
        stop_event.wait(timeout=config.get("captureIntervalSec", 0.35))


def bot_worker():
    global stop_event
    try:
        _safe_print("Demarrage bot en mode --cli")
        log_event("BOT_START")
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "-u",
             os.path.join(ROOT, "scripts", "fishing_bot.py"), "--cli"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        while not stop_event.is_set():
            poll = proc.poll()
            if poll is not None:
                stdout, stderr = proc.communicate(timeout=5)
                log_event("BOT_EXIT", exitCode=poll,
                         stdout=stdout.decode(errors="replace")[:500],
                         stderr=stderr.decode(errors="replace")[:500])
                break
            stop_event.wait(timeout=1.0)
        _safe_print("Bot arrete")
        log_event("BOT_STOPPED")
    except Exception as e:
        log_event("BOT_THREAD_ERROR", error=str(e))


def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        HAS_TK = True
    except ImportError:
        HAS_TK = False

    if not HAS_TK:
        _safe_print("Tkinter non disponible - mode CLI uniquement")
        return run_cli()

    root = tk.Tk()
    root.title("Casinobabe - Assistant Peche")
    root.geometry("420x420")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: on_close_gui(root))

    style = ttk.Style()
    style.theme_use('clam')

    status_var = tk.StringVar(value="Etat: %s" % app_state)
    status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor="w")
    status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    main_frame = ttk.Frame(root, padding=(10, 10))
    main_frame.pack(fill=tk.BOTH, expand=True)

    wow_label = ttk.Label(main_frame, text="WoW: non detecte", foreground="red")
    wow_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=5)

    state_label = ttk.Label(main_frame, text="Assistant: IDLE", foreground="gray")
    state_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=5)

    btn_style = {"width": 20, "padding": (5, 2)}

    btn_start = ttk.Button(main_frame, text="DEMARRER ASSISTANT",
                          command=lambda: start_assistant_gui(), **btn_style)
    btn_start.grid(row=2, column=0, padx=5, pady=8)

    btn_pause = ttk.Button(main_frame, text="PAUSER",
                          command=lambda: pause_assistant_gui(), state=tk.DISABLED, **btn_style)
    btn_pause.grid(row=2, column=1, padx=5, pady=8)

    btn_stop = ttk.Button(main_frame, text="ARRERER",
                        command=lambda: stop_assistant_gui(), state=tk.DISABLED, **btn_style)
    btn_stop.grid(row=3, column=0, columnspan=2, padx=5, pady=8)

    # --- 1 bouton : replay integral de la sequence lancee a la main -------
    # sync addon -> lance WoW (Battle.net) -> positionne les fenetres ->
    # lance le bot en arriere-plan -> attend la porte "perso en jeu".
    # Rejouable : le script est idempotent (il detecte l'etat reel avant
    # d'agir) et s'arrete proprement si Battle.net n'est pas connecte
    # (identifiants saisis par l'utilisateur, jamais par le script).
    btn_oneclick = ttk.Button(main_frame, text="1-CLIC : WoW + BOT",
                              command=lambda: on_oneclick())
    btn_oneclick.grid(row=4, column=0, columnspan=2, padx=5, pady=(4, 10))

    metrics_frame = ttk.LabelFrame(main_frame, text="Metriques", padding=(5, 5))
    metrics_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=5)

    casts_var = tk.StringVar(value="Lances: 0")
    bites_var = tk.StringVar(value="Morsures: 0")
    errors_var = tk.StringVar(value="Erreurs: 0")

    ttk.Label(metrics_frame, textvariable=casts_var).grid(row=0, column=0, sticky="w")
    ttk.Label(metrics_frame, textvariable=bites_var).grid(row=0, column=1, sticky="w", padx=(10, 0))
    ttk.Label(metrics_frame, textvariable=errors_var).grid(row=0, column=2, sticky="w", padx=(10, 0))

    log_frame = ttk.LabelFrame(main_frame, text="Journaux", padding=(5, 5))
    log_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=5)

    log_text = tk.Text(log_frame, height=8, width=40, state=tk.DISABLED)
    scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=log_text.yview)
    log_text.configure(yscrollcommand=scroll.set)
    log_text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")

    def log_gui(msg):
        log_text.configure(state=tk.NORMAL)
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        log_text.configure(state=tk.DISABLED)

    msg_queue = queue.Queue()

    def process_queue():
        try:
            while True:
                msg = msg_queue.get_nowait()
                if msg.startswith("LOG:"):
                    log_gui(msg[5:])
                elif msg.startswith("STATE:"):
                    parts = msg[6:].split("|")
                    if len(parts) >= 2:
                        state, val = parts[0], parts[1:]
                        if state == "wow":
                            wow_label.configure(text="WoW: %s" % val,
                                               foreground={"running": "green", "idle": "gray", "paused": "orange", "stopped": "red"}.get(val, "gray"))
                        if state == "assistant":
                            state_label.configure(text="Assistant: %s" % val,
                                               foreground={"running": "green", "idle": "gray", "paused": "orange", "stopped": "red"}.get(val, "gray"))
                        if state == "casts":
                            try:
                                casts_var.set("Lances: %s" % val)
                            except:
                                pass
                        if state == "bites":
                            try:
                                bites_var.set("Morsures: %s" % val)
                            except:
                                pass
                        if state == "errors":
                            try:
                                errors_var.set("Erreurs: %s" % val)
                            except:
                                pass
                elif msg.startswith("CAPTURE:"):
                    log_gui("Capture : " + msg[8:])
        except queue.Empty:
            pass
        root.after(100, process_queue)

    root.after(100, process_queue)

    def oneclick_worker():
        """Rejoue la sequence complete via scripts/fishing-oneclick.ps1.

        Le script fait tout le travail Rejouable : sync addon, lancement WoW,
        focus fenetre, bot en arriere-plan, attente porte in-game. Aucun
        identifiant n'est saisi ici : si Battle.net n'est pas connecte, le
        script s'arrete et le dit (l'utilisateur se connecte lui-meme).
        """
        import subprocess
        script = os.path.join(ROOT, "scripts", "fishing-oneclick.ps1")
        if not os.path.isfile(script):
            msg_queue.put("LOG:1-CLIC impossible : %s introuvable" % script)
            return
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", script, "-NoWait"]
        msg_queue.put("LOG:=== 1-CLIC : %s ===" % " ".join(args[4:]))
        try:
            proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            for raw in proc.stdout:
                # La console PowerShell sort en cp1252 : on decode comme le
                # fait _safe_print() cote bot, sinon les accents Cassent.
                msg_queue.put("LOG:" + raw.decode("cp1252", errors="replace").rstrip())
            proc.wait()
            msg_queue.put("LOG:=== 1-CLIC termine (code %d) ===" % proc.returncode)
            if proc.returncode != 0:
                msg_queue.put("LOG:voir logs et state/oneclick-last-run.json")
        except Exception as exc:
            msg_queue.put("LOG:1-CLIC erreur : %s" % exc)

    def on_oneclick():
        btn_oneclick.configure(state=tk.DISABLED)
        threading.Thread(target=oneclick_worker, daemon=True).start()
        root.after(2000, lambda: btn_oneclick.configure(state=tk.NORMAL))

    def start_assistant_gui():
        global app_state, observer_thread, bot_thread, stop_event
        with app_state_lock:
            if app_state in (STATE_IDLE, STATE_STOPPED):
                app_state = STATE_RUNNING
                stop_event = threading.Event()
                observer_thread = threading.Thread(target=observer_worker, daemon=True)
                observer_thread.start()
                if config.get("castKey"):
                    bot_thread = threading.Thread(target=bot_worker, daemon=True)
                    bot_thread.start()
                set_app_state(STATE_RUNNING)
                btn_start.configure(state=tk.DISABLED)
                btn_pause.configure(state=tk.NORMAL)
                btn_stop.configure(state=tk.NORMAL)
                log_gui("Etat: Assistant DEMARRE")
            else:
                _safe_print("Assistant deja arrete")

    def pause_assistant_gui():
        global app_state
        with app_state_lock:
            if app_state == STATE_RUNNING:
                app_state = STATE_PAUSED
                set_app_state(STATE_PAUSED)
                log_gui("Etat: Assistant PAUSE")
            else:
                _safe_print("Assistant non en cours dexecution")

    def stop_assistant_gui():
        global app_state, observer_thread, bot_thread, stop_event
        with app_state_lock:
            app_state = STATE_STOPPED
            stop_event.set()
            log_gui("Etat: ARRET de lassistant")
            set_app_state(STATE_STOPPED)
            btn_start.configure(state=tk.NORMAL)
            btn_pause.configure(state=tk.DISABLED)
            btn_stop.configure(state=tk.DISABLED)

    def on_close_gui():
        stop_assistant_gui()
        root.destroy()

    root.columnconfigure(0, weight=1)
    root.rowconfigure(5, weight=1)
    main_frame.columnconfigure(0, weight=1)
    main_frame.columnconfigure(1, weight=1)
    main_frame.rowconfigure(5, weight=1)
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)

    load_config()
    process_queue()

    _safe_print("Interface graphique demmarreee")
    log_gui("Application demmarreee - configuration chargee")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        stop_assistant_gui()
        _safe_print("Application fermee proprement")


def run_cli():
    _safe_print("Mode CLI - demarrage assistant")
    load_config()
    stop_event = threading.Event()
    try:
        while True:
            obs, ctx = observe_once()
            time.sleep(config.get("captureIntervalSec", 0.35))
            if not stop_event.is_set():
                import msvcrt
                if msvcrt.kbhit() and msvcrt.getch() == b'q':
                    break
            else:
                break
    except KeyboardInterrupt:
        pass
    finally:
        _safe_print("Arrret en mode CLI")


def main():
    parser = argparse.ArgumentParser(
        description="Casinobabe Fishing Assistant - Superviseur local")
    parser.add_argument("--cli", action="store_true",
                       help="Mode interface en ligne de commande (sans GUI)")
    parser.add_argument("--once", action="store_true",
                       help="Une seule observation, puis sortie")
    parser.add_argument("--init-config", action="store_true",
                       help="Initialiser la configuration par defaut")
    args = parser.parse_args()
    if args.init_config:
        save_config(dict(DEFAULT_CONFIG))
        _safe_print("Configuration par defaut initialisee")
        return
    if args.once:
        obs, ctx = observe_once()
        _safe_print(json.dumps(obs, indent=2, ensure_ascii=False))
        return
    if args.cli:
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()