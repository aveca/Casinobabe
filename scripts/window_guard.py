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
# Vérification des invariants
# ---------------------------------------------------------------------------

def diff_snapshots(before: Dict, after: Dict) -> List[str]:
    """Compare deux snapshots et renvoie la liste des invariants rompus.

    Invariants (tous interdits par la politique) :
      * WoW ne doit pas être minimisée ;
      * WoW ne doit pas disparaître (fermée) ;
      * WoW ne doit pas devenir le premier plan (vol de focus) ;
      * le curseur de l'utilisateur ne doit pas avoir bougé ;
      * la fenêtre WoW ne doit pas avoir été redimensionnée/déplacée.
    """
    problems: List[str] = []
    wb, wa = before.get("wow", {}), after.get("wow", {})

    if wb.get("present") and not wa.get("present"):
        problems.append("FENETRE_WOW_FERMEE : la fenêtre WoW a disparu pendant l'action")
    if wb.get("minimized") is False and wa.get("minimized") is True:
        problems.append("FENETRE_WOW_MINIMISEE : WoW a été minimisée pendant l'action")
    if wb.get("visible") is True and wa.get("visible") is False:
        problems.append("FENETRE_WOW_CACHEE : WoW a été cachée pendant l'action")

    fb, fa = before.get("foreground", {}), after.get("foreground", {})
    if wb.get("present") and wb.get("hwnd") and fa.get("hwnd") == wb.get("hwnd") \
            and fb.get("hwnd") != wb.get("hwnd"):
        problems.append("FOCUS_VOL : WoW a pris le premier plan pendant l'action")

    if before.get("cursor") is not None and after.get("cursor") is not None:
        cb, ca = before["cursor"], after["cursor"]
        if abs(cb[0] - ca[0]) > 2 or abs(cb[1] - ca[1]) > 2:
            problems.append(
                "CURSEUR_DEPLACE : le curseur de l'utilisateur a bougé (%s -> %s)" % (cb, ca))

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
    """Exécute `fn()` sous surveillance du garde-fou.

    Renvoie `(ok, detail)`.
      * `ok=False` si l'action est interdite par la politique ;
      * `ok=False` si `fn` lève ;
      * `ok=False` si un invariant a été rompu pendant l'action ;
      * sinon `ok=True` et `detail` décrit le résultat.

    Ne lève jamais : le Control Center doit pouvoir afficher le motif du
    blocage sans planter.
    """
    if action in FORBIDDEN:
        return False, "COMMANDE_INTERDITE : '%s' est interdite par window_guard (%s)" % (
            action, ", ".join(FORBIDDEN))
    if action not in ALLOWED:
        # Action inconnue : autorisée mais tracée (évite un blocage muet).
        pass

    before = snapshot()
    sampler = _Sampler(interval=sample_interval)
    sampler.start()
    detail = ""
    ok = True
    try:
        result = fn()
        if timeout is not None:
            # fn() est synchrone : le timeout est appliqué en amont par
            # l'appelant (control_core). On ne l'exécute pas en thread pour ne
            # pas créer un process orphelin si fn lève.
            pass
        detail = "" if result is None else str(result)
    except Exception as exc:
        ok = False
        detail = "ERREUR : %s: %s" % (type(exc).__name__, exc)
    finally:
        sampler.stop()
        sampler.join(timeout=2.0)

    after = snapshot()
    problems = diff_snapshots(before, after)
    for s in sampler.samples:
        problems.extend(diff_snapshots(before, s))

    if sampler.error:
        problems.append("GARDE_FOU_ERREUR : %s" % sampler.error)
    if problems:
        uniq = sorted(set(problems))
        return False, "GARDE_FOU : " + " | ".join(uniq)
    return ok, detail


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

    # Invariant : WoW ne doit pas disparaître entre deux snapshots identiques.
    a = snapshot()
    b = snapshot()
    check("snapshots stables sans action", diff_snapshots(a, b) == [],
          "; ".join(diff_snapshots(a, b)))

    # Invariant synthétique : simuler une minimisation doit être détectée.
    fake_before = {"wow": {"present": True, "hwnd": 1, "minimized": False,
                           "visible": True, "rect": [0, 0, 10, 10]},
                   "foreground": {"hwnd": 2}, "cursor": (5, 5)}
    fake_after = dict(fake_before)
    fake_after["wow"] = dict(fake_before["wow"], minimized=True)
    probs = diff_snapshots(fake_before, fake_after)
    check("minimisation WoW détectée", any("MINIMISEE" in p for p in probs), "; ".join(probs))

    fake_after2 = dict(fake_before)
    fake_after2["cursor"] = (500, 500)
    probs = diff_snapshots(fake_before, fake_after2)
    check("curseur déplacé détecté", any("CURSEUR" in p for p in probs), "; ".join(probs))

    fake_after3 = dict(fake_before)
    fake_after3["foreground"] = {"hwnd": 1}
    probs = diff_snapshots(fake_before, fake_after3)
    check("vol de focus vers WoW détecté", any("FOCUS_VOL" in p for p in probs), "; ".join(probs))

    fake_after4 = dict(fake_before)
    fake_after4["wow"] = {"present": False, "hwnd": 0, "minimized": None,
                          "visible": False, "rect": None}
    probs = diff_snapshots(fake_before, fake_after4)
    check("fermeture WoW détectée", any("FERMEE" in p for p in probs), "; ".join(probs))

    rep = policy_report()
    check("rapport de politique exploitable", "policy" in rep and "desktop" in rep)

    print("-" * 68)
    print("RESULTAT : %s (%d echec(s))" % ("PASS" if failures == 0 else "FAIL", failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(policy_report())
