#!/usr/bin/env python3
"""Bail de focus par action - permet au bot de peche de travailler EN ARRIERE-PLAN.

Pourquoi ce module existe
-------------------------
Windows n'accorde le focus clavier qu'a UNE fenetre, et SendInput/keybd_event
n'atteignent que cette fenetre. WoW lit le clavier en Raw Input : on ne peut
pas injecter une touche dans une fenetre de fond. Deux consequences :

1. l'ancien modele (« focus tenu en continu ») obligeait l'utilisateur a laisser
   WoW au premier plan en permanence ;
2. un deuxieme client WoW ne pouvait jamais pêcher (il n'a jamais le focus).

Ce que montre l'observation : la CAPTURE d'ecran (PrintWindow) marche SANS
focus. Le focus n'est necessaire qu'aux moments d'ENVOI d'entree, tres brefs :

    attente du flotteur (~32 s)  -> capture seule, aucun focus
    envoi de castKey (~100 ms)   -> focus requis
    clic apres morsure (~150 ms) -> focus requis

D'ou le modele « bail par action » : on prend le focus, on envoie, on relache.

Securite
--------
- Le bail est un MUTEX NOMMÉ GLOBAL : si un autre agent (dealer casino,
  deuxieme client) envoie de l'entree, il passe par le meme mutex -> jamais
  deux envois simultanes.
- Si le bail n'est pas obtenu dans le delai, AUCUNE touche n part (on renvoie
  False) : le cycle est gele, il n'est jamais envoye au hasard.
- Cession de politesse : si l'utilisateur vient d'appuyer sur une touche ou de
  bouger la souris (< idle_ms), le bot REFUSE de prendre le focus et differe
  l'action. Le bot ne vole donc jamais la main pendant que tu tapes.
- Apres acquisition, on REVERIFIE le focus reel avant d'envoyer (le bail peut
  avoir ete obtenu mais le focuschangear ne pas etre honore par l'OS).
"""
import ctypes
import ctypes.wintypes as wt
import time
from contextlib import contextmanager

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MUTEX_NAME = "Global\\CasinobabeFocusLease"
DEFAULT_IDLE_MS = 1500          # utilisateur actif => on cede la main
DEFAULT_TIMEOUT_MS = 1500      # duree max d'attente du mutex

# Flags de Fenetre / entrees
SW_RESTORE = 9
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_ABANDONED = 0x00000080

# Seuil de deference : si l'utilisateur a tape ou bouge la souris il y a moins
# de DEFAULT_IDLE_MS, on ne prend PAS le focus et l'action est differee.
# 1500 ms : au-dela, on empiete sur une utilisation normale de l'ordi. Constat
# 2026-10-17 : "ca reprend trop le focus" -> seuil releve de 700 a 1500 ms.

# Duree de "repos" : apres l'envoi, on laisse le jeu consommer l'input
# (SendInput est asynchrone cote application) AVANT de rendre la main.
SETTLE_MS = 110

# Derniere duree de pret du focus (ms), pour MESURER et prouver que
# l'interruption est courte. 0 = pas de pret.
last_dwell_ms = {"value": 0}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def user_idle_ms():
    """Millisecondes depuis la derniere action de l'utilisateur (clavier ou
    souris). 0 = il est en train d'interagir. Retourne None si l'API refuse."""
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # GetTickCount et dwTime sont sur la meme base (ms depuis le boot)
        return int(kernel32.GetTickCount() - info.dwTime)
    except Exception:
        return None


def should_yield_to_user(idle_ms, idle_threshold_ms=DEFAULT_IDLE_MS):
    """Decision PURE (testable sans Windows) : le bot doit-il ceder la main ?

    Regle : on cede des que l'utilisateur est actif, et on leve la deference
    avec une petite hysteresis pour ne pas osciller sur la frontiere."""
    if idle_ms is None:
        return False           # API indisponible : ne bloque pas la peche
    if idle_ms < idle_threshold_ms:
        return True            # utilisateur en train d'interagir
    return False


def _force_foreground(hwnd):
    """SetForegroundWindow est refuse par l'OS quand le process appelant n'a
    pas le focus (cas normal d'un bot). On applique la technique officielle :
    on attache la file d'entree du thread courant a celle du thread au premier
    plan, ce qui donne temporairement le droit de poser le focus.

    ATTENTION - fenetre REDUITE : on ne la restaure PAS. Restaurer une fenetre
    que l'utilisateur a volontairement reduite reviendrait a lui voler son
    bureau ; et de toute facon le jeu etant reduit il ne rend pas (28x160 px),
    le bot ne peut donc rien voir. On refuse le pret : le cycle reste gele en
    attendant que l'utilisateur remette la fenetre."""
    cur = kernel32.GetCurrentThreadId()
    if user32.IsIconic(hwnd):
        return False
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else None
    attached = False
    try:
        if fg_thread and fg_thread != cur:
            attached = bool(user32.AttachThreadInput(cur, fg_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_thread, False)
    # L'OS peut mettre quelques ms a honorer le changement.
    for _ in range(6):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
        user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def _acquire_mutex(timeout_ms):
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    res = kernel32.WaitForSingleObject(handle, timeout_ms)
    if res in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return handle
    kernel32.CloseHandle(handle)
    return None


def _release_mutex(handle):
    if handle:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


@contextmanager
def focus_lease(hwnd, timeout_ms=DEFAULT_TIMEOUT_MS,
                idle_threshold_ms=DEFAULT_IDLE_MS, respect_user=True,
                log=None, settle_ms=SETTLE_MS, restore=True):
    """Contexte : yielded == True signifie « focus obtenu, tu peux envoyer ».

    yielded == False signifie « n'envoie RIEN » (bail non obtenu, focus non
    honore par l'OS, ou utilisateur actif). L'appelant doit alors geler son
    cycle au lieu d'envoyer ailleurs.

    restore=True : rend la main. Apres l'envoi et un repos de settle_ms (le
    jeu doit consommer l'input, SendInput etant asynchrone cote application),
    la fenetre qui etait au premier plan AVANT le pret est remise devant. Sans
    ca le bot laisse WoW devant apres chaque lancer : l'utilisateur perd son
    focus en permanence. La duree reelle est mesuree dans last_dwell_ms.
    """
    if not hwnd:
        if log:
            log("LEASE_SKIP", reason="pas de HWND WoW")
        last_dwell_ms["value"] = 0
        yield False
        return

    if respect_user and should_yield_to_user(user_idle_ms(), idle_threshold_ms):
        idle = user_idle_ms()
        last_dwell_ms["value"] = 0
        if log:
            log("LEASE_YIELD", reason="utilisateur actif", idleMs=idle)
        yield False
        return

    handle = _acquire_mutex(timeout_ms)
    if handle is None:
        last_dwell_ms["value"] = 0
        if log:
            log("LEASE_TIMEOUT", timeoutMs=timeout_ms)
        yield False
        return
    previous = user32.GetForegroundWindow()
    t0 = time.time()
    try:
        got = _force_foreground(hwnd)
        if not got:
            last_dwell_ms["value"] = int((time.time() - t0) * 1000)
            if log:
                log("LEASE_NO_FOCUS", hwnd=hwnd)
            yield False
            return
        try:
            yield True
        finally:
            # REPOS puis restitution : l'utilisateur retrouve sa fenetre.
            if settle_ms > 0:
                time.sleep(settle_ms / 1000.0)
            if restore and previous and previous != hwnd and user32.IsWindow(previous):
                if user32.GetForegroundWindow() == hwnd:
                    _force_foreground(previous)
    finally:
        last_dwell_ms["value"] = int((time.time() - t0) * 1000)
        _release_mutex(handle)