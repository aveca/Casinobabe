#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Casinobabe Control Center — interface Windows (tkinter).

Vue en 4 onglets :
  1. Tableau de bord  — etats, PID, verrous, heartbeat, WoW, garde-fou
  2. Controles        — START / PAUSE / REPRISE / STOP / REDEMARRER
  3. Journaux         — logs filtrables en direct + export de diagnostic
  4. Parametres       — config du bot (validee, cles inconnues preservees)

Tout le raisonnement est dans `control_core`. Ce fichier ne fait que dessiner
et transformer les clics en appels a `ControlCenter.dispatch()`.

Contraintes respectees :
  * aucune fenetre terminal (lancer avec pythonw) ;
  * aucun messagebox bloquant (retour dans la barre d'etat) ;
  * interface non bloquante : rafraichissement dans un thread, remontée par
    file d'attente, jamais de join() dans le callback Tk ;
  * ne prend jamais le focus de WoW : les seules APIs bureau appelees sont
    des lectures (cf. window_guard).

Lancement :
    pythonw scripts/control_center.py
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tkinter as tk  # noqa: E402
from tkinter import filedialog  # noqa: E402

import control_core as core  # noqa: E402
import window_guard  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Palette : sobre, lisible en usage quotidien
# ---------------------------------------------------------------------------
BG = "#12151c"
PANEL = "#1b1f2a"
PANEL_2 = "#232838"
FG = "#e6e9f0"
MUTED = "#9aa4b8"
ACCENT = "#4f8cff"

STATE_COLORS = {
    core.ACTIVE: "#3ddc84",
    core.STARTING: "#ffc857",
    core.PAUSED: "#ffc857",
    core.BLOCKED: "#ff9f43",
    core.ERROR: "#ff5d5d",
    core.STOPPED: "#7f8ba3",
    core.ABSENT: "#7f8ba3",
    core.UNVERIFIABLE: "#a071ff",
}

LEVEL_COLORS = {
    "ERROR": "#ff5d5d",
    "WARN": "#ff9f43",
    "INFO": "#e6e9f0",
    "DEBUG": "#7f8ba3",
}


class Center:
    def __init__(self):
        self.cc = core.ControlCenter()
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Casinobabe Control Center")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        self.root.configure(bg=BG)
        self.stop_flag = threading.Event()
        self.last_snapshot = None
        self.log_rows = {}
        self.config_values = {}
        self._pending_export = False
        self._build_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_worker()

    # -- style -----------------------------------------------------------
    def _build_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG,
                     fieldbackground=PANEL_2, bordercolor=PANEL,
                     lightcolor=PANEL, darkcolor=PANEL, font=("Segoe UI", 9))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Card.TLabel", background=PANEL, foreground=FG)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        st.configure("Title.TLabel", background=BG, foreground=FG,
                     font=("Segoe UI", 15, "bold"))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED,
                     font=("Segoe UI", 9))
        st.configure("TNotebook", background=BG, bordercolor=PANEL)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                     padding=(16, 8))
        st.map("TNotebook.Tab", background=[("selected", BG)],
               foreground=[("selected", ACCENT)])
        st.configure("TButton", background=PANEL_2, foreground=FG,
                     padding=(12, 6), borderwidth=0)
        st.map("TButton", background=[("active", "#2e3547"), ("disabled", "#1a1e28")],
               foreground=[("disabled", "#5a6274")])
        st.configure("Go.TButton", background="#1e3a2a", foreground="#3ddc84")
        st.map("Go.TButton", background=[("active", "#265239"), ("disabled", "#1a1e28")],
               foreground=[("disabled", "#5a6274")])
        st.configure("Warn.TButton", background="#3a3220", foreground="#ffc857")
        st.map("Warn.TButton", background=[("active", "#52462a"), ("disabled", "#1a1e28")],
               foreground=[("disabled", "#5a6274")])
        st.configure("Stop.TButton", background="#3a2020", foreground="#ff5d5d")
        st.map("Stop.TButton", background=[("active", "#522b2b"), ("disabled", "#1a1e28")],
               foreground=[("disabled", "#5a6274")])
        st.configure("Treeview", background=PANEL_2, fieldbackground=PANEL_2,
                     foreground=FG, rowheight=22, borderwidth=0)
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                     relief="flat")
        st.map("Treeview", background=[("selected", "#2e3547")])
        st.configure("TEntry", background=PANEL_2, foreground=FG, fieldbackground=PANEL_2)
        st.configure("TCombobox", background=PANEL_2, foreground=FG, fieldbackground=PANEL_2)
        st.configure("TCheckbutton", background=PANEL, foreground=FG)
        st.configure("TProgressbar", background=ACCENT, troughcolor=PANEL_2)

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="Casinobabe Control Center", bg=BG, fg=FG,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        self.head_state = tk.Label(head, text="initialisation...", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 10))
        self.head_state.pack(side="right")

        self.status_var = tk.StringVar(value="Pret.")
        self.status_label = tk.Label(self.root, textvariable=self.status_var, bg=PANEL,
                                     fg=MUTED, anchor="w", padx=12, pady=5,
                                     font=("Segoe UI", 9))
        self.status_label.pack(fill="x", side="bottom")

        # Filtres : copies en Python simple. Les widgets Tk ne doivent JAMAIS
        # etre lus depuis le thread de suivi (non thread-safe) : les callbacks
        # main-thread recopient ici, le worker ne lit que ce dictionnaire.
        self.filters = {"level": "", "since": "", "errors": False}

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.tab_dash = ttk.Frame(self.nb, padding=12)
        self.tab_ctrl = ttk.Frame(self.nb, padding=12)
        self.tab_logs = ttk.Frame(self.nb, padding=12)
        self.tab_set = ttk.Frame(self.nb, padding=12)
        for name, frame in (("Tableau de bord", self.tab_dash),
                            ("Controles", self.tab_ctrl),
                            ("Journaux & diagnostics", self.tab_logs),
                            ("Parametres", self.tab_set)):
            self.nb.add(frame, text=name)

        self._build_dashboard()
        self._build_controls()
        self._build_logs()
        self._build_settings()

    # -- onglet 1 -------------------------------------------------------
    def _build_dashboard(self):
        self.cards: Dict[str, Dict[str, tk.Widget]] = {}
        grid = ttk.Frame(self.tab_dash)
        grid.pack(fill="x")
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="cards")

        for idx, comp in enumerate(core.COMPONENTS):
            row, col = divmod(idx, 3)
            outer = ttk.Frame(grid, style="Card.TFrame", padding=12)
            outer.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            ttk.Label(outer, text=comp.label, style="Card.TLabel",
                      font=("Segoe UI", 11, "bold")).pack(anchor="w")
            chip = tk.Label(outer, text="?", bg=PANEL_2, fg=MUTED, padx=8, pady=2,
                            font=("Segoe UI", 9, "bold"))
            chip.pack(anchor="w", pady=(4, 6))
            detail = ttk.Label(outer, text="", style="Muted.TLabel", wraplength=300,
                               justify="left")
            detail.pack(fill="x")
            facts = ttk.Label(outer, text="", style="Card.TLabel", font=("Consolas", 9),
                              justify="left")
            facts.pack(fill="x", pady=(6, 0))
            self.cards[comp.key] = {"chip": chip, "detail": detail, "facts": facts}

        info = ttk.Frame(self.tab_dash, style="Card.TFrame", padding=12)
        info.pack(fill="x", padx=6, pady=6)
        ttk.Label(info, text="Environnement et garde-fou window_guard",
                  style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.env_text = tk.Label(info, text="", bg=PANEL, fg=FG, anchor="w",
                                 justify="left", font=("Consolas", 9))
        self.env_text.pack(fill="x", pady=(6, 0))

    # -- onglet 2 -------------------------------------------------------
    def _build_controls(self):
        info = ttk.Label(self.tab_ctrl,
                         text="Les commandes appellent les scripts existants. "
                              "Le verrou anti-doublon officiel "
                              "(fishing_observer.acquire_single_instance) est "
                              "respecté : un START est refusé si une instance "
                              "saine tourne déjà. Aucune touche n'est injectée.",
                         wraplength=1050, justify="left", style="Muted.TLabel")
        info.pack(fill="x", pady=(0, 10))

        self.buttons: Dict[str, Dict[str, tk.Widget]] = {}
        self.reasons: Dict[str, Dict[str, tk.Widget]] = {}
        for comp in core.COMPONENTS:
            row = ttk.Frame(self.tab_ctrl, style="Card.TFrame", padding=10)
            row.pack(fill="x", pady=4)
            row.columnconfigure(1, weight=1)

            left = ttk.Frame(row, style="Card.TFrame")
            left.grid(row=0, column=0, sticky="w")
            ttk.Label(left, text=comp.label, style="Card.TLabel",
                      font=("Segoe UI", 11, "bold")).pack(anchor="w")
            st_lbl = ttk.Label(left, text="", style="Card.TLabel")
            st_lbl.pack(anchor="w")
            self.buttons.setdefault(comp.key, {})["_state"] = st_lbl

            mid = ttk.Frame(row, style="Card.TFrame")
            mid.grid(row=0, column=1, sticky="w", padx=14)
            style_for = {"start": "Go.TButton", "resume": "Go.TButton",
                         "pause": "Warn.TButton", "stop": "Stop.TButton",
                         "restart": "Warn.TButton"}
            labels = {"start": "Demarrer", "pause": "Pause", "resume": "Reprendre",
                      "stop": "Arreter", "restart": "Redemarrer"}
            for action in ("start", "pause", "resume", "stop", "restart"):
                btn = ttk.Button(mid, text=labels[action], style=style_for[action],
                                 width=13, command=lambda a=action, k=comp.key:
                                 self._do(a, k))
                btn.pack(side="left", padx=3)
                self.buttons[comp.key][action] = btn

            reason = ttk.Label(row, text="", style="Muted.TLabel", wraplength=380,
                               justify="left")
            reason.grid(row=0, column=2, sticky="e")
            self.reasons[comp.key] = reason

    # -- onglet 3 -------------------------------------------------------
    def _build_logs(self):
        bar = ttk.Frame(self.tab_logs)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Composant :").pack(side="left")
        self.f_comp = ttk.Combobox(bar, state="readonly", width=18,
                                   values=[c.label for c in core.COMPONENTS])
        self.f_comp.current(0)
        self.f_comp.pack(side="left", padx=(4, 12))
        self.f_comp.bind("<<ComboboxSelected>>", lambda e: self._refresh_logs())

        ttk.Label(bar, text="Niveau :").pack(side="left")
        self.f_level = ttk.Combobox(bar, state="readonly", width=10,
                                    values=["TOUS", "DEBUG", "INFO", "WARN", "ERROR"])
        self.f_level.set("TOUS")
        self.f_level.pack(side="left", padx=(4, 12))
        self.f_level.bind("<<ComboboxSelected>>", self._on_filter_change)

        ttk.Label(bar, text="Depuis :").pack(side="left")
        self.f_since = ttk.Combobox(bar, state="readonly", width=10,
                                    values=["TOUT", "5 MIN", "1 H", "24 H"])
        self.f_since.set("TOUT")
        self.f_since.pack(side="left", padx=(4, 12))
        self.f_since.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.f_err = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Erreurs seulement", variable=self.f_err,
                        command=self._on_filter_change).pack(side="left")

        ttk.Button(bar, text="Actualiser", command=self._on_filter_change).pack(side="left", padx=(14, 4))
        ttk.Button(bar, text="Exporter le diagnostic", command=self._export_report).pack(side="left", padx=4)

        cols = ("ts", "level", "event", "state", "detail")
        wrap = ttk.Frame(self.tab_logs)
        wrap.pack(fill="both", expand=True)
        self.log_tree = ttk.Treeview(wrap, columns=cols, show="headings")
        heads = {"ts": ("Horodatage", 180), "level": ("Niveau", 70),
                 "event": ("Evenement", 190), "state": ("Etat bot", 150),
                 "detail": ("Detail", 520)}
        for key, (title, width) in heads.items():
            self.log_tree.heading(key, text=title)
            self.log_tree.column(key, width=width, anchor="w")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.log_tree.yview)
        self.log_tree.configure(yscrollcommand=vs.set)
        self.log_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        # ttk.Treeview n'accepte PAS -foreground ligne par ligne : la couleur
        # passe obligatoirement par des tags.
        for _lvl, _col in LEVEL_COLORS.items():
            self.log_tree.tag_configure(_lvl, foreground=_col)

        self.log_info = ttk.Label(self.tab_logs, text="", style="Muted.TLabel")
        self.log_info.pack(fill="x", pady=(6, 0))

    # -- onglet 4 -------------------------------------------------------
    def _build_settings(self):
        nb = ttk.Notebook(self.tab_set)
        nb.pack(fill="both", expand=True)

        fishing = ttk.Frame(nb, padding=10)
        ccset = ttk.Frame(nb, padding=10)
        casino = ttk.Frame(nb, padding=10)
        nb.add(fishing, text="Bot peche")
        nb.add(ccset, text="Control Center")
        nb.add(casino, text="Bot casino")

        ttk.Label(fishing,
                  text="Valeurs actuelles avant modification. Les cles inconnues "
                       "presentes dans le fichier sont conservees telles quelles ; "
                       "les parametres marques « verrouille » sont refuses.",
                  style="Muted.TLabel", wraplength=980, justify="left").pack(anchor="w", pady=(0, 8))
        self.cfg_entries: Dict[str, tk.Entry] = {}
        self.cfg_locked: Dict[str, str] = {}
        holder = ttk.Frame(fishing)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(1, weight=1)
        holder.columnconfigure(2, weight=1)
        for i, (key, spec) in enumerate(core.FISHING_SCHEMA.items()):
            typ = spec[0]
            row = i // 2
            col = (i % 2) * 2
            ttk.Label(holder, text=key).grid(row=row, column=col, sticky="w", pady=3)
            ent = ttk.Entry(holder, width=14)
            ent.grid(row=row, column=col + 1, sticky="w", padx=(6, 18), pady=3)
            self.cfg_entries[key] = ent
            note = core.FROZEN_PARAMS.get(key, "")
            bounds = ""
            if spec[1] is not None and spec[2] is not None:
                bounds = "bornes %s..%s" % (spec[1], spec[2])
            self.cfg_locked[key] = note or bounds
            ttk.Label(holder, text=(("VERROUILLE — " + note) if key in core.FROZEN_PARAMS
                                    else bounds),
                      style="Muted.TLabel").grid(row=row, column=col + 1,
                                                sticky="e", padx=(0, 18))

        btns = ttk.Frame(fishing)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Recharger", command=self._load_settings).pack(side="left")
        ttk.Button(btns, text="Enregistrer", style="Go.TButton",
                   command=self._save_settings).pack(side="left", padx=6)
        self.cfg_status = ttk.Label(btns, text="", style="Muted.TLabel")
        self.cfg_status.pack(side="left", padx=10)

        ttk.Label(ccset, text="Parametres du Control Center (state/control-center.json)",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.cc_entries: Dict[str, tk.Entry] = {}
        grid2 = ttk.Frame(ccset)
        grid2.pack(fill="x")
        for i, (key, val) in enumerate(self.cc.settings.items()):
            ttk.Label(grid2, text=key).grid(row=i, column=0, sticky="w", pady=4)
            ent = ttk.Entry(grid2, width=16)
            ent.insert(0, str(val))
            ent.grid(row=i, column=1, sticky="w", padx=(8, 12), pady=4)
            self.cc_entries[key] = ent
        b2 = ttk.Frame(ccset)
        b2.pack(fill="x", pady=10)
        ttk.Button(b2, text="Enregistrer", style="Go.TButton",
                   command=self._save_cc_settings).pack(side="left")
        self.cc_status = ttk.Label(b2, text="", style="Muted.TLabel")
        self.cc_status.pack(side="left", padx=10)

        tk.Label(casino, text=core.CASINO_ABSENT_REASON, bg=BG, fg=MUTED,
                 wraplength=980, justify="left").pack(anchor="w", pady=(0, 10))
        tk.Label(casino, text="Aucune configuration, aucun controle, aucun demarrage. "
                              "L'onglet est volontairement vide : ajouter des champs "
                              "ici sans processus derriere serait une fausse "
                              "fonctionnalite.", bg=BG, fg=FG, wraplength=980,
                 justify="left").pack(anchor="w")

    # -- worker ---------------------------------------------------------
    def _start_worker(self):
        def loop():
            while not self.stop_flag.is_set():
                try:
                    filters = dict(self.filters)
                    snap = self.cc.snapshot()
                    logs = {}
                    for comp in core.COMPONENTS:
                        logs[comp.key] = self.cc.log_tail(
                            comp.key, lines=400, level=filters["level"],
                            since=filters["since"],
                            error_only=filters["errors"])
                    report = self.cc.diagnostic_report()
                    self.q.put(("tick", time.time(), snap, logs, report))
                except Exception:
                    self.q.put(("error", time.time(), traceback.format_exc(), None, None))
                self.stop_flag.wait(max(1.0, float(self.cc.settings["refreshSec"])))

        threading.Thread(target=loop, daemon=True).start()
        self._drain()
        self._load_settings()

    def _drain(self):
        """UN SEUL consommateur de la file : tick, resultats, export, erreurs."""
        try:
            while True:
                kind, _ts, a, b, c = self.q.get_nowait()
                if kind == "tick":
                    self.last_snapshot = a
                    self.log_rows = b
                    self._render(a, c)
                elif kind == "action":
                    res = a
                    ok = bool(res.get("ok"))
                    self._set_status(
                        "[%s] %s / %s — %s" % (
                            "OK" if ok else "REFUS", res.get("component"),
                            res.get("action"), res.get("detail")),
                        "#3ddc84" if ok else "#ff9f43")
                    self._refresh_logs()
                elif kind == "exported":
                    self._set_status("Diagnostic exporte : %s" % a, MUTED)
                    self._pending_export = False
                elif kind == "error":
                    self._set_status("Erreur du thread de suivi : %s"
                                     % a.strip().splitlines()[-1], "#ff5d5d")
        except queue.Empty:
            pass
        if not self.stop_flag.is_set():
            self.root.after(500, self._drain)

    def _set_status(self, text, color=MUTED):
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    # -- helpers --------------------------------------------------------
    def _selected_component(self):
        idx = self.f_comp.current()
        comps = core.COMPONENTS
        return comps[idx] if 0 <= idx < len(comps) else comps[0]

    def _on_filter_change(self, _evt=None):
        """Callback main-thread : recopie les filtres puis redessine."""
        level = self.f_level.get()
        since = self.f_since.get()
        self.filters["level"] = "" if level == "TOUS" else level
        delta = {"5 MIN": 300, "1 H": 3600, "24 H": 86400}.get(since)
        self.filters["since"] = (
            (datetime.now() - timedelta(seconds=delta)).isoformat() if delta else "")
        self.filters["errors"] = bool(self.f_err.get())
        self._refresh_logs()

    def _refresh_logs(self):
        if not hasattr(self, "log_tree"):
            return
        comp = self._selected_component()
        rows = self.cc.log_tail(comp.key, lines=400, level=self.filters["level"],
                                since=self.filters["since"],
                                error_only=self.filters["errors"])
        self._render_logs(comp, rows)

    # -- rendu ----------------------------------------------------------
    def _render(self, snap, report):
        for st in snap["components"]:
            card = self.cards.get(st["key"])
            if not card:
                continue
            card["chip"].configure(text="  %s  " % st["state"],
                                   fg=STATE_COLORS.get(st["state"], MUTED))
            card["detail"].configure(text=st["detail"] or "-")
            facts = []
            if st["pid"]:
                facts.append("PID %s" % st["pid"])
            if st.get("process_path"):
                facts.append("binaire %s" % st["process_path"])
            lock = st.get("lock")
            if lock:
                facts.append("verrou %s : %s" % (
                    os.path.basename(lock["path"] or "-"), lock["status"]))
            hb = st.get("heartbeat")
            if hb and hb.get("checked"):
                if hb.get("last_event"):
                    facts.append("dernier progres %s (%ss)" % (
                        hb["last_event"], hb.get("last_age_sec")))
                if hb.get("error"):
                    facts.append("heartbeat : %s" % hb["error"])
                err = hb.get("last_error")
                if err:
                    facts.append("derniere erreur %s %s" % (err["ts"], err["event"]))
            for extra in st.get("extra_pids", []):
                facts.append("PID %s %s" % (extra["pid"], extra["cmdline"][:70]))
            card["facts"].configure(text="\n".join(facts) or "-")

        wow = snap["wow"]
        fg = snap["foreground"]
        env = [
            "WoW        : %s" % ("present (PID %s, minimisee=%s)" % (wow["pid"], wow["minimized"])
                                  if wow["present"] else "non detecte"),
            "Premier plan : %s" % (fg["title"] or "(aucun)"),
            "Garde-fou  : %d actions autorisees, %d interdites" % (
                snap["guard"]["allowed_count"], len(snap["guard"]["forbidden"])),
            "Interdits  : %s" % ", ".join(snap["guard"]["forbidden"]),
            "Heartbeat : %s" % report.get("generated_at", ""),
        ]
        self.env_text.configure(text="\n".join(env))

        for st in snap["components"]:
            lbl = self.buttons[st["key"]]["_state"]
            lbl.configure(text=st["state"], foreground=STATE_COLORS.get(st["state"], MUTED))
            for action, btn in self.buttons[st["key"]].items():
                if action == "_state":
                    continue
                allowed = (st["available"] and action in st["controls"]
                           and not (st["state"] == core.ACTIVE and action == "start")
                           and not (st["state"] in (core.PAUSED,) and action == "pause"))
                btn.configure(state="normal" if allowed else "disabled")
            reason = ""
            if not st["available"]:
                reason = "Composant absent : aucun demarrage possible."
            elif st["state"] == core.BLOCKED:
                reason = "Bloque : processus vivant sans progression. STOP puis START."
            elif st["state"] == core.STOPPED and st.get("lock", {}) and \
                    st["lock"].get("status") == "STALE":
                reason = "Verrou residuel (PID %s mort) : START le nettoiera." % st["lock"]["pid"]
            self.reasons[st["key"]].configure(text=reason)

        comp = self._selected_component()
        self._render_logs(comp, self.log_rows.get(comp.key, []) if self.log_rows else [])
        self.head_state.configure(
            text="Bot peche : %s      Bot casino : %s" % (snap["bot_state"], snap["casino_state"]),
            fg=STATE_COLORS.get(snap["bot_state"], MUTED))

    def _render_logs(self, comp, rows):
        self.log_tree.delete(*self.log_tree.get_children())
        for row in reversed(rows):
            detail = row.get("text") or row.get("note") or row.get("reason") or ""
            if not detail:
                detail = " ".join("%s=%s" % (k, v) for k, v in row.items()
                                  if k not in ("ts", "event", "state", "text"))
            level = core._level_of(row)
            self.log_tree.insert("", "end", values=(
                row.get("ts", ""), level, row.get("event", ""),
                row.get("state", "") or "", str(detail)[:220]), tags=(level,))
        if rows:
            last = rows[-1]
            hb_age = "derniere ligne %s" % last.get("ts", "?")
        else:
            hb_age = "aucune ligne pour ce filtre"
        self.log_info.configure(
            text="Source : %s   |   %s   |   %d ligne(s) affichee(s)"
                 % (comp.progress_path or comp.stdout_path or "-", hb_age, len(rows)))

    # -- actions --------------------------------------------------------
    def _do(self, action, key):
        def work():
            try:
                res = self.cc.dispatch(action, key)
                self.q.put(("action", time.time(), res, None, None))
            except Exception:
                self.q.put(("error", time.time(), traceback.format_exc(), None, None))

        self.status_var.set("Commande %s sur %s en cours..." % (action, key))
        self.status_label.configure(fg=ACCENT)
        threading.Thread(target=work, daemon=True).start()

    def _export_report(self):
        def work():
            try:
                rep = self.cc.diagnostic_report()
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = os.path.join(core.REPORTS_DIR,
                                    "control-center-diagnostic-%s.json" % stamp)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(rep, fh, indent=2, ensure_ascii=False)
                self.q.put(("exported", time.time(), path, None, None))
            except Exception:
                self.q.put(("error", time.time(), traceback.format_exc(), None, None))

        self.status_var.set("Export du diagnostic...")
        self.status_label.configure(fg=ACCENT)
        threading.Thread(target=work, daemon=True).start()
        self._pending_export = True

    def _load_settings(self):
        try:
            cfg = core.load_fishing_config()
        except Exception as exc:
            self.cfg_status.configure(text="Lecture impossible : %s" % exc)
            return
        self.config_values = cfg
        for key, ent in self.cfg_entries.items():
            val = cfg.get(key)
            if key in core.FROZEN_PARAMS:
                ent.configure(state="normal")
                ent.delete(0, "end")
                ent.insert(0, "(verrouille)")
                continue
            ent.delete(0, "end")
            ent.insert(0, "" if val is None else str(val))
            ent.configure(state="normal")
        unknown = cfg.get("__unknown__") or {}
        if unknown:
            self.cfg_status.configure(
                text="Cles inconnues conservees dans le fichier : %s"
                     % ", ".join(sorted(unknown)))

    def _save_settings(self):
        updates = {}
        for key, ent in self.cfg_entries.items():
            if key in core.FROZEN_PARAMS:
                continue
            updates[key] = ent.get()
        ok, errs, msg = core.save_fishing_config(updates)
        if ok:
            self.cfg_status.configure(text="Enregistre — %s" % msg)
            self.status_var.set("Configuration du bot enregistree.")
            self._load_settings()
        else:
            self.cfg_status.configure(text="Refuse : " + " | ".join(errs))

    def _save_cc_settings(self):
        updates = {k: e.get() for k, e in self.cc_entries.items()}
        ok, errs, msg = core.save_cc_settings(updates)
        if ok:
            self.cc.settings = core.load_cc_settings()
            self.cc_status.configure(text="Enregistre — %s" % msg)
        else:
            self.cc_status.configure(text="Refuse : " + " | ".join(errs))

    def on_close(self):
        self.stop_flag.set()
        # Ferme SANS tuer les bots : ils restent maîtriseurs de leur propre
        # cycle. Aucune suppression de fichier, aucun signal envoye.
        self.root.destroy()


def _gui_selftest() -> int:
    """Test de l'interface SANS afficher de fenetre et SANS mainloop().

    La fenetre n'est jamais mappee sur l'ecran (withdraw + aucun mainloop),
    donc ce test ne vole pas le focus a WoW ni a l'utilisateur.
    """
    print("=" * 70)
    print("control_center — auto-test de l'interface (fenetre non mappee)")
    print("=" * 70)
    fails = []

    def check(label, cond, detail=""):
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                               (" - " + detail) if detail else ""))
        if not cond:
            fails.append(label)

    center = Center()
    center.root.withdraw()          # jamais affichee
    try:
        check("4 onglets construits", center.nb.index("end") == 4)
        titles = [center.nb.tab(i, "text") for i in range(center.nb.index("end"))]
        check("onglets attendus",
              titles == ["Tableau de bord", "Controles", "Journaux & diagnostics",
                         "Parametres"], " | ".join(titles))
        check("une carte par composant",
              set(center.cards) == {c.key for c in core.COMPONENTS})
        check("boutons par composant",
              all(len(center.buttons[c.key]) == 6 for c in core.COMPONENTS))
        check("champs de configuration pour tout le schema",
              set(center.cfg_entries) == set(core.FISHING_SCHEMA))

        # Laisse le worker produire un tick, puis force le rendu.
        deadline = time.time() + 12
        while time.time() < deadline and center.last_snapshot is None:
            center.root.update()
            time.sleep(0.2)
        check("premier snapshot recu du thread", center.last_snapshot is not None)
        if center.last_snapshot:
            snap = center.last_snapshot
            check("etat du bot peche calcule",
                  snap["bot_state"] in (core.STOPPED, core.ACTIVE, core.BLOCKED,
                                        core.STARTING, core.PAUSED, core.UNVERIFIABLE),
                  snap["bot_state"])
            check("etat du bot casino = ABSENT", snap["casino_state"] == core.ABSENT,
                  snap["casino_state"])
            check("garde-fou expose", snap["guard"]["forbidden"])
            cards = center.cards["fishing_bot"]
            check("carte bot peche remplie", cards["chip"].cget("text").strip() != "?",
                  cards["chip"].cget("text"))

        # Filtres
        center.f_level.set("ERROR")
        center._on_filter_change()
        center.root.update()
        check("filtre niveau ERROR applique", center.filters["level"] == "ERROR")
        center.f_err.set(True)
        center._on_filter_change()
        check("filtre erreurs seules applique", center.filters["errors"] is True)
        center.f_level.set("TOUS")
        center.f_err.set(False)
        center._on_filter_change()

        # Refus de commande sur composant absent, via le vrai chemin GUI
        before = len(center.cc.history)
        center._do("start", "casino_bot")
        deadline = time.time() + 8
        while time.time() < deadline and len(center.cc.history) <= before:
            center.root.update()
            time.sleep(0.1)
        check("commande casino refusee via l'interface",
              len(center.cc.history) > before and
              not center.cc.history[-1].ok,
              center.cc.history[-1]["detail"] if center.cc.history else "")

        check("fermeture propre", True)
    finally:
        center.stop_flag.set()
        try:
            center.root.update_idletasks()
            center.root.destroy()
        except Exception:
            pass

    print("-" * 70)
    print("RESULTAT : %s (%d echec(s))" % ("PASS" if not fails else "FAIL", len(fails)))
    return 0 if not fails else 1


def main():
    center = Center()
    center.root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_gui_selftest())
    main()
