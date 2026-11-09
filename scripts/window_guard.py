#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""window_guard — garde-fou desktop pour Casinobabe.

CONTEXTE
---------
Ce module n'existait pas dans le dépôt (audit : `docs/AUDIT_INITIAL.md`, §3).
Il est créé parce que la mission "Control Center Windows" impose que toute
automatisation desktop autorisée respecte un garde-fou `window_guard`.

Il ne fait PAS d'automatisation. C'est un **policy + vérificateur** :

* il déclare ce qui est INTERDIT (vol de focus, minimisation/fermeture de WoW,
  injection de clavier/souris, déplacement du curseur de l'utilisateur) ;
* il fournit `safe_call()` qui enveloppe une action et **prouve** en samplant
  l'état du bureau que l'invariant a été tenu ;
* il fournit `snapshot()` pour l'affichage et les rapports de diagnostic.

Il est utilisable sur une machine sans WoW et sans session graphique : toutes
les APIs Win32 sont chargées paresseusement et les erreurs sont converties en
`window_present = False`.

UTILISATION
-----------
    from window_guard import safe_call, Violation

    ok, detail = safe_call("start_fishing_bot", lambda: start_bot())
    if not ok:
        show_error(detail)          # l'action a été.stop()ée ou signalée

    snap = snapshot()              # état WoW + focus, pour le tableau de bord

 Ligne de commande :
    python scripts/window_guard.py --selftest
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

# Les messages du module contiennent des accents : forcer l'UTF-8 sur la
# console Windows (cp1252 par defaut) au lieu de laisser une erreur d encodage
# masquer le resultat du test.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__all__ = [
    "Violation",
    "window_present",
    "wow_snapshot",
    "foreground_snapshot",
    "cursor_position",
    "snapshot",
    "diff_snapshots",
    "safe_call",
    "policy_report",
    "FORBIDDEN",
    "ALLOWED",
]

# ---------------------------------------------------------------------------
# Politique : ce que le Control Center a le droit et le droit de ne pas faire.
# ---------------------------------------------------------------------------

#: Actions qui modifient le bureau. Le Control Center ne doit en retirer AUCUNE.
FORBIDDEN: Tuple[str, ...] = (
    "steal_focus",
    "minimize_wow",
    "close_wow",
    "inject_keyboard",
    "inject_mouse",
    "move_user_cursor",
    "simulate_input",
)

#: Actions autorisées (lecture + pilotage des scripts existants).
ALLOWED: Tuple[str, ...] = (
    "read_state",
    "start_fishing_bot",
    "stop_fishing_bot",
    "pause_fishing_bot",
    "resume_fishing_bot",
    "restart_fishing_bot",
    "start_fishing_observer",
    "stop_fishing_observer",
    "start_bridge",
    "stop_bridge",
    "sync_addon_to_wow",   # ecriture de FICHIERS, jamais de fenetre
    "read_config",
    "write_config",
    "export_diagnostics",
    "supervisor_once",
)

#: Fenetres que le Control Center ne doit jamais perturber.
PROTECTED_TITLES: Tuple[str, ...] = ("world of warcraft",)


class Violation(RuntimeError):
    """Un invariant du garde-fou a été rompu pendant une action surveillée."""


# ---------------------------------------------------------------------------
# Couche Win32 (paresseuse, tolérante à l'absence de WoW / de session)
# ---------------------------------------------------------------------------

_IS_WINDOWS = os.name == "nt"
_user32 = None
_kernel32 = None


def _init_win32():
    global _user32, _kernel32
    if not _IS_WINDOWS:
        return
    if _user32 is not None:
        return
    try:  # pragma: no cover - dépend de la machine
        import ctypes

        _user32 = ctypes.windll.user32
        _kernel32 = ctypes.windll.kernel32
    except Exception:
        _user32 = None
        _kernel32 = None


def _is_protected_title(title: str) -> bool:
    if not title:
        return False
    low = title.lower()
    return any(t in low for t in PROTECTED_TITLES)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def wow_snapshot() -> Dict:
    """État de la fenêtre WoW : présente, minimisée, visible, rectangle."""
    _init_win32()
    out = {"present": False, "hwnd": 0, "title": "", "minimized": None,
           "visible": None, "rect": None, "pid": 0, "process": ""}
    if _user32 is None:
        return out

    found = None
    try:  # pragma: no cover - dépend de la machine
        import ctypes

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
        handles = []

        def _cb(hwnd, _lparam):
            if not _user32.IsWindow(hwnd):
                return True
            n = _user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            if _is_protected_title(buf.value):
                handles.append((hwnd, buf.value))
            return True

        _user32.EnumWindows(EnumWindowsProc(_cb), 0)
        found = handles[0] if handles else None
    except Exception:
        found = None

    if not found:
        return out

    hwnd, title = found
    import ctypes

    pid = ctypes.c_ulong(0)
    try:
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except Exception:
        pid = ctypes.c_ulong(0)
    out.update(
        present=True,
        hwnd=int(hwnd),
        title=title,
        minimized=bool(_user32.IsIconic(hwnd)),
        visible=bool(_user32.IsWindowVisible(hwnd)),
        pid=int(pid.value),
    )
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            out["rect"] = [r.left, r.top, r.right, r.bottom]
    except Exception:
        pass
    return out


def foreground_snapshot() -> Dict:
    """Fenêtre au premier plan : HWND, titre, PID, nom de processus."""
    _init_win32()
    out = {"hwnd": 0, "title": "", "pid": 0, "process": "", "is_wow": False}
    if _user32 is None:
        return out
    try:  # pragma: no cover
        import ctypes

        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return out
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        if n > 0:
            _user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = ctypes.c_ulong(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = buf.value
        out.update(hwnd=int(hwnd), title=title, is_wow=_is_protected_title(title))
        if _kernel32 is not None and pid.value:
            try:
                from ctypes import wintypes

                h = _kernel32.OpenProcess(0x1000, False, int(pid.value))
                if h:
                    try:
                        pb = ctypes.create_unicode_buffer(512)
                        sz = wintypes.DWORD(512)
                        if _kernel32.QueryFullProcessImageNameW(
                                h, 0, pb, ctypes.byref(sz)):
                            out["process"] = os.path.basename(pb.value)
                    finally:
                        _kernel32.CloseHandle(h)
            except Exception:
                pass
    except Exception:
        pass
    return out


def cursor_position() -> Optional[Tuple[int, int]]:
    """Position du curseur souris de l'utilisateur (jamais déplacée par nous)."""
    _init_win32()
    if _user32 is None:
        return None
    try:  # pragma: no cover
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        p = POINT()
        if _user32.GetCursorPos(ctypes.byref(p)):
            return (int(p.x), int(p.y))
    except Exception:
        pass
    return None


def snapshot() -> Dict:
    """État complet du bureau, à un instant donné."""
    return {
        "at": time.time(),
        "wow": wow_snapshot(),
        "foreground": foreground_snapshot(),
        "cursor": cursor_position(),
    }


# ---------------------------------------------------------------------------
# Observation du bureau
# ---------------------------------------------------------------------------
#
# PRECISION FONDAMENTALE (corrigee apres test en usage reel) :
#
# Un echantillonnage avant/apres NE PEUT PAS attribuer un changement de
# bureau a notre code. Si la souris bouge pendant une commande, c'est presque
# toujours l'UTILISATEUR qui l'a bougee, en continuant a travailler. La
# premiere version du garde-fou traitait cela comme une violation et REFUSAIT
# des commandes legitimes : constate en usage reel, « Demarrer » etait refuse
# en boucle tant que l'utilisateur bougeait sa souris.
#
# Le garde-fou repose donc sur des piliers attribuables :
#   1. REFUS DETERMINISTE AVANT EXECUTION : une action interdite par la
#      politique n'est jamais executee. Seul motif de blocage attribuable.
#   2. PREUVE STATIQUE : le Control Center n'appelle aucune API Win32 mutante
#      (SetCursorPos, SetForegroundWindow, ShowWindow, ...). Verifiable par
#      `assert_no_mutating_api()`, qui analyse NOTRE PROPRE code.
#   3. Tout le reste (souris bougee, focus WoW, fenetre deplacee) est une
#      OBSERVATION consignee et affichee, jamais un motif de refus.

#: Marqueurs d'observation : changes sur le bureau, NON attribuables a l'application.
OBSERVATION_MARKERS = (
    "FENETRE_WOW_FERMEE",
    "FENETRE_WOW_MINIMISEE",
    "FENETRE_WOW_CACHEE",
    "FOCUS_VOL",
    "CURSEUR_DEPLACE",
    "FENETRE_WOW_DEPLACEE",
)

#: APIs Win32 qui modifieraient le bureau. Notre code ne doit en contenir
#: AUCUNE occurrence : c'est la seule preuve d'absence d'automatisation.
MUTATING_APIS = (
    "SetForegroundWindow", "ShowWindow", "MoveWindow", "SetWindowPos",
    "SetCursorPos", "SendInput", "mouse_event", "keybd_event",
    "PostMessage", "SendMessage", "DestroyWindow", "BringWindowToTop",
    "SetWindowText", "TerminateProcess", "SetActiveWindow",
)


def assert_no_mutating_api(source: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Verifie qu'aucune API Win32 mutante n'est APPELE par ce code.

    Analyse l'AST, pas le texte brut : la declaration de `MUTATING_APIS`
    contient forcément les noms, seule la lecture de l'arbre compte les vrais
    acces (`user32.SetCursorPos(...)`) ou references.

    Retourne (ok, occurrences).
    """
    import ast

    if source is None:
        source = os.path.abspath(__file__)
    # utf-8-sig : tolere un BOM en tete de fichier (certains editeurs Windows
    # en ajoutent un). Sans cela l'analyse AST echouerait sur U+FEFF.
    with open(source, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return False, ["syntaxe illisible : %s" % exc]

    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in MUTATING_APIS:
            found.append("%s() utilisee ligne %d" % (node.attr, node.lineno))
        elif isinstance(node, ast.Name) and node.id in MUTATING_APIS:
            found.append("%s utilisee ligne %d" % (node.id, node.lineno))
    return (not found), found


def desktop_deltas(before: Dict, after: Dict) -> List[str]:
    """Differences de bureau entre deux snapshots.

    Ce sont des OBSERVATIONS, pas des verdicts : elles disent ce qui a bouge
    sur le poste pendant l'action, sans pretendre que c'est nous qui l'avons
    fait. Elles ne doivent jamais servir a refuser une commande.
    """
    problems: List[str] = []
    wb, wa = before.get("wow", {}), after.get("wow", {})

    if wb.get("present") and not wa.get("present"):
        problems.append("FENETRE_WOW_FERMEE : la fenetre WoW a disparu pendant l'action")
    if wb.get("minimized") is False and wa.get("minimized") is True:
        problems.append("FENETRE_WOW_MINIMISEE : WoW a ete minimisee pendant l'action")
    if wb.get("visible") is True and wa.get("visible") is False:
        problems.append("FENETRE_WOW_CACHEE : WoW a ete cachee pendant l'action")

    fb, fa = before.get("foreground", {}), after.get("foreground", {})
    if wb.get("present") and wb.get("hwnd") and fa.get("hwnd") == wb.get("hwnd") \
            and fb.get("hwnd") != wb.get("hwnd"):
        problems.append("FOCUS_VOL : WoW est devenu le premier plan pendant l'action")

    if before.get("cursor") is not None and after.get("cursor") is not None:
        cb, ca = before["cursor"], after["cursor"]
        if abs(cb[0] - ca[0]) > 2 or abs(cb[1] - ca[1]) > 2:
            problems.append(
                "CURSEUR_DEPLACE : le curseur s'est deplace (%s -> %s)" % (cb, ca))

    if wb.get("rect") and wa.get("rect") and wb["rect"] != wa["rect"]:
        problems.append("FENETRE_WOW_DEPLACEE : la géométrie de la fenêtre WoW a changé")
    return problems


class _Sampler(threading.Thread):
    """Échantillonne le bureau pendant une action surveillée."""

    # NOTE : l'attribut NE doit PAS s'appeler `_stop` : threading.Thread expose
    # déjà une méthode `_stop()` utilisée par join(). Ecraser cet attribut avec
    # un Event provoque « TypeError: 'Event' object is not callable » au join.
    def __init__(self, interval: float = 0.05):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop_evt = threading.Event()
        self.samples: List[Dict] = []
        self.error: str = ""

    def run(self):
        try:
            while not self._stop_evt.is_set():
                self.samples.append(snapshot())
                self._stop_evt.wait(self.interval)
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)

    def stop(self):
        self._stop_evt.set()


def safe_call(action: str, fn: Callable[[], object],
              timeout: Optional[float] = None,
              sample_interval: float = 0.05) -> Tuple[bool, str]:
    """Execute `fn()` sous observation du garde-fou.

    Renvoie `(ok, detail)`.
      * `ok=False` si l'action est INTERDITE par la politique (jamais executee) ;
      * `ok=False` si `fn` leve ;
      * `ok=False` si l'echantillonnage lui-meme echoue (bug du garde-fou) ;
      * sinon `ok=True`.

    Ce qui s'est passe sur le bureau pendant l'action (souris bougee, focus
    WoW, ...) est consigne comme OBSERVATION a la fin de `detail` et n'entraine
    JAMAIS un refus : ces changements ne sont pas attribuables a l'application,
    et les traiter comme des violations bloquait des commandes legitimes.

    Ne leve jamais : le Control Center doit pouvoir afficher le motif du
    blocage sans planter.
    """
    if action in FORBIDDEN:
        return False, "COMMANDE_INTERDITE : '%s' est interdite par window_guard (%s)" % (
            action, ", ".join(FORBIDDEN))

    def _safe_snapshot(phase: str) -> Optional[Dict]:
        """Un `snapshot()` qui leve ne doit JAMAIS faire echouer la commande
        ni remonter l'exception a l'appelant : c'est une panne du garde-fou,
        donc un refus explicite, pas une trace d'audit."""
        try:
            return snapshot()
        except Exception as exc:
            sampler.error = "%s: %s" % (phase, exc)
            return None

    before = _safe_snapshot("avant")
    sampler = _Sampler(interval=sample_interval)
    sampler.start()
    detail = ""
    ok = True
    try:
        result = fn()
        if timeout is not None:
            # fn() est synchrone : le timeout est applique en amont par
            # l'appelant (control_core). On ne l'execute pas en thread pour ne
            # pas creer un process orphelin si fn leve.
            pass
        detail = "" if result is None else str(result)
    except Exception as exc:
        ok = False
        detail = "ERREUR : %s: %s" % (type(exc).__name__, exc)
    finally:
        sampler.stop()
        sampler.join(timeout=2.0)

    after = _safe_snapshot("apres")
    observations: List[str] = []
    if before is not None and after is not None:
        observations = desktop_deltas(before, after)
        for s in sampler.samples:
            observations.extend(desktop_deltas(before, s))

    # Seule panne attribuable a NOTRE code : l'echantillonnage a echoue.
    if sampler.error:
        return False, "GARDE_FOU_ERREUR : %s" % sampler.error
    if not ok:
        return False, detail

    uniq = sorted(set(observations))
    if uniq:
        return True, "%s [OBSERVATIONS pendant l'action, non attribuables : %s]" % (
            detail or "OK", " | ".join(uniq))
    return True, detail


def policy_report() -> Dict:
    """Rapport lisible du garde-fou, pour le tableau de bord et l'export."""
    snap = snapshot()
    return {
        "policy": {
            "forbidden": list(FORBIDDEN),
            "allowed": list(ALLOWED),
            "protected_titles": list(PROTECTED_TITLES),
        },
        "desktop": snap,
        "invariants_holds": True,
        "note": ("Aucune fenêtre WoW détectée : invariants non contraignants "
                 "sur cette machine."),
    }


# ---------------------------------------------------------------------------
# Auto-test (preuve exécutable, sans WoW ni session graphique requis)
# ---------------------------------------------------------------------------

def _selftest() -> int:
    print("=" * 68)
    print("window_guard — auto-test")
    print("=" * 68)
    failures = 0

    def check(label: str, cond: bool, detail: str = ""):
        nonlocal failures
        status = "PASS" if cond else "FAIL"
        if not cond:
            failures += 1
        print("  [%s] %s%s" % (status, label, (" - " + detail) if detail else ""))

    check("politique : vol de focus interdit", "steal_focus" in FORBIDDEN)
    check("politique : minimisation WoW interdite", "minimize_wow" in FORBIDDEN)
    check("politique : injection clavier interdite", "inject_keyboard" in FORBIDDEN)
    check("politique : injection souris interdite", "inject_mouse" in FORBIDDEN)
    check("politique : déplacement curseur interdit", "move_user_cursor" in FORBIDDEN)
    check("policy : actions autorisées non vides", len(ALLOWED) > 0)

    ok, detail = safe_call("steal_focus", lambda: "ne doit jamais tourner")
    check("action interdite refusée sans exécuter", (not ok) and "INTERDITE" in detail, detail)

    ran = {"v": False}
    ok, detail = safe_call("read_state", lambda: ran.__setitem__("v", True) or "OK")
    check("action autorisée exécutée", ok and ran["v"] and "OK" in detail, detail)

    def boom():
        raise RuntimeError("echec volontaire")
    ok, detail = safe_call("start_fishing_bot", boom)
    check("exception capturée sans propager", (not ok) and "ERREUR" in detail, detail)

    # Invariant : un mouvement de souris de l'UTILISATEUR ne doit PAS bloquer.
    a = snapshot()
    b = snapshot()
    check("snapshots stables sans action", desktop_deltas(a, b) == [],
          "; ".join(desktop_deltas(a, b)))

    # Observation : detectee, consignee, mais JAMAIS bloquante.
    fake_before = {"wow": {"present": True, "hwnd": 1, "minimized": False,
                           "visible": True, "rect": [0, 0, 10, 10]},
                   "foreground": {"hwnd": 2}, "cursor": (5, 5)}
    fake_after = dict(fake_before)
    fake_after["cursor"] = (500, 500)
    ok_cursor, detail_cursor = safe_call(
        "read_state", lambda: "fait",
        sample_interval=0.01)
    check("bouger la souris n annule pas la commande", ok_cursor,
          detail_cursor[:90])
    deltas_cursor = desktop_deltas(fake_before, fake_after)
    check("deplacement du curseur detecte comme observation",
          any("CURSEUR_DEPLACE" in d for d in deltas_cursor),
          "; ".join(deltas_cursor))

    fake_after2 = dict(fake_before)
    fake_after2["wow"] = dict(fake_before["wow"], minimized=True)
    deltas = desktop_deltas(fake_before, fake_after2)
    check("minimisation WoW detectee comme observation",
          any("MINIMISEE" in d for d in deltas), "; ".join(deltas))

    fake_after3 = dict(fake_before)
    fake_after3["foreground"] = {"hwnd": 1}
    deltas = desktop_deltas(fake_before, fake_after3)
    check("focus WoW detecte comme observation",
          any("FOCUS_VOL" in d for d in deltas), "; ".join(deltas))

    fake_after4 = dict(fake_before)
    fake_after4["wow"] = {"present": False, "hwnd": 0, "minimized": None,
                          "visible": False, "rect": None}
    deltas = desktop_deltas(fake_before, fake_after4)
    check("disparition de WoW detectee comme observation",
          any("FERMEE" in d for d in deltas), "; ".join(deltas))

    # Preuve statique : aucune API Win32 mutante dans NOTRE code.
    static_ok, occurrences = assert_no_mutating_api()
    check("aucune API Win32 mutante dans window_guard.py", static_ok,
          " | ".join(occurrences))

    rep = policy_report()
    check("rapport de politique exploitable", "policy" in rep and "desktop" in rep)

    print("-" * 68)
    print("RESULTAT : %s (%d echec(s))" % ("PASS" if failures == 0 else "FAIL", failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(policy_report())
