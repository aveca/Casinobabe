# -*- coding: utf-8 -*-
"""
recovery_state_machine.py — Machine à états de récupération (corpse-run).

Implémentation HORS-JEU de la chaîne de mort :
  IDLE -> (mort détectée) -> DEATH_DETECTED
             -> (fantôme confirmé) -> GHOST_CONFIRMED
             -> (cadavre localisé) -> CORPSE_NAVIGATING
             -> (résurrection) -> RESURRECTING
             -> (succès) -> IDLE

Chaque attente/action est bornée (timeouts). En cas d'échec ou d'ambiguïté,
la machine bascule en SAFE_STOP : le bot s'arrête et attend un humain —
comportement par défaut tant que la validation en jeu n'a pas lieu.

AUCUN accès au jeu ici : les entrées sont des événements déclarés
(détection de mort, confirmation fantôme, position du cadavre, succès de
résurrection) provenant d'une source extérieure (addon ou opérateur).
Le module est désactivé par défaut (enabled = False).

Statut : implémenté hors-ligne, unit-testé. Validation en jeu en attente.
Voir scripts/botwow_modules.py (id "recovery", status development).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------- ÉTATS ---
IDLE = "IDLE"
DEATH_DETECTED = "DEATH_DETECTED"
GHOST_CONFIRMED = "GHOST_CONFIRMED"
CORPSE_NAVIGATING = "CORPSE_NAVIGATING"
RESURRECTING = "RESURRECTING"
SAFE_STOP = "SAFE_STOP"

ALL_STATES = (IDLE, DEATH_DETECTED, GHOST_CONFIRMED, CORPSE_NAVIGATING,
              RESURRECTING, SAFE_STOP)

# ------------------------------------------------------------- ÉVÉNEMENTS ---
EVENT_DEATH_DETECTED = "death_detected"
EVENT_GHOST_CONFIRMED = "ghost_confirmed"
EVENT_CORPSE_LOCATED = "corpse_located"
EVENT_RESURRECT_OK = "resurrect_ok"
EVENT_TIMEOUT = "timeout"
EVENT_AMBIGUOUS = "ambiguous"      # signal de résurrection ambigu
EVENT_STOP_REQUESTED = "stop_requested"  # opérateur / F9

# ---------------------------------------------------------------- CONFIG --
@dataclass
class RecoveryConfig:
    enabled: bool = False                 # désactivé par défaut : arrêt sûr
    death_confirm_timeout_s: float = 15.0 # max pour confirmer la mort
    ghost_confirm_timeout_s: float = 20.0 # max pour confirmation fantôme
    corpse_nav_timeout_s: float = 120.0   # max pour atteindre le cadavre
    resurrect_timeout_s: float = 30.0     # max pour la résurrection
    ambiguous_retries: int = 2            # relais sur signal ambigu avant safe stop

    def __post_init__(self):
        if not self.enabled:
            return
        for name in ("death_confirm_timeout_s", "ghost_confirm_timeout_s",
                     "corpse_nav_timeout_s", "resurrect_timeout_s"):
            v = getattr(self, name)
            if v <= 0:
                raise ValueError(f"{name} doit être > 0")


# --------------------------------------------------------------- ÉVÉNEMENT --
@dataclass
class Event:
    kind: str
    payload: dict = field(default_factory=dict)
    time: float = field(default_factory=time.time)


# -------------------------------------------------------- MACHINE À ÉTATS ---
class RecoveryStateMachine:
    def __init__(self, config: Optional[RecoveryConfig] = None,
                 now: Callable[[], float] = time.time):
        self.config = config or RecoveryConfig()
        self._now = now
        self.state = IDLE
        self.history: list = []  # (from, to, t)
        self.retry_count = 0
        self._deadline: Optional[float] = None
        self.last_error: Optional[str] = None
        self.log: list = []

    # ------------------------------------------------------------- helpers --
    def _log(self, message: str) -> None:
        self.log.append("[%s] %s" % (self.state, message))

    def _set_deadline(self, seconds: float) -> None:
        self._deadline = self._now() + seconds

    def _expired(self) -> bool:
        return self._deadline is not None and self._now() > self._deadline

    def _transition(self, to: str, reason: str) -> None:
        self._log("transition %s -> %s (%s)" % (self.state, to, reason))
        self.history.append((self.state, to, self._now()))
        self.state = to
        self._deadline = None

    def _safe_stop(self, reason: str) -> None:
        self.last_error = reason
        self._log("SAFE STOP: %s" % reason)
        self._transition(SAFE_STOP, reason)

    # ------------------------------------------------------------ API ---
    def is_enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> dict:
        """État lisible par l'interface / les logs du bot."""
        return {
            "enabled": self.config.enabled,
            "state": self.state,
            "last_error": self.last_error,
            "retry_count": self.retry_count,
            "deadline_remaining_s": (
                round(self._deadline - self._now(), 1)
                if self._deadline else None
            ),
            "history": [(f, t_, round(ts, 2)) for f, t_, ts in self.history[-5:]],
        }

    def enable(self) -> None:
        """Active la récupération automatique. À n'activer qu'après validation en jeu."""
        self.config.enabled = True
        self._log("module activé (désactivé par défaut)")

    def disable(self) -> None:
        self.config.enabled = False
        self.state = IDLE
        self._log("module désactivé")

    def reset(self) -> None:
        """Ramène la machine à l'état initial (après intervention humaine)."""
        self.state = IDLE
        self.retry_count = 0
        self.last_error = None
        self._deadline = None
        self._log("réinitialisation manuelle")

    # ------------------------------------------------------------ PILOTAGE --
    def feed(self, event: Event) -> None:
        """Injecte un événement externe. Aucun accès au jeu n'est effectué ici."""
        if not self.config.enabled:
            self._log("événement %s ignoré (module désactivé)" % event.kind)
            return

        if self.state == SAFE_STOP:
            if event.kind == EVENT_STOP_REQUESTED:
                self.reset()
            self._log("événement %s ignoré (safe stop)" % event.kind)
            return

        # Gestion des timeouts avant tout traitement
        if self._deadline is not None and self._expired():
            self.retry_count += 1
            self._safe_stop("timeout dans l'état %s" % self.state)
            return

        handler = {
            IDLE: self._on_idle,
            DEATH_DETECTED: self._on_death_detected,
            GHOST_CONFIRMED: self._on_ghost_confirmed,
            CORPSE_NAVIGATING: self._on_corpse_navigating,
            RESURRECTING: self._on_resurrecting,
        }.get(self.state)

        if handler is None:
            self._safe_stop("état inconnu %s" % self.state)
            return

        try:
            handler(event)
        except Exception as exc:  # pragma: no cover — défaut de conception
            self._safe_stop("exception : %s" % exc)

    # ------------------------------------------------------------ ÉTATS ---
    def _on_idle(self, event: Event) -> None:
        if event.kind == EVENT_DEATH_DETECTED:
            self._set_deadline(self.config.death_confirm_timeout_s)
            self._transition(DEATH_DETECTED, "mort détectée")
        elif event.kind == EVENT_STOP_REQUESTED:
            self._log("arrêt demandé en idle — rien à faire")
        else:
            self._log("événement %s ignoré en idle" % event.kind)

    def _on_death_detected(self, event: Event) -> None:
        if event.kind == EVENT_GHOST_CONFIRMED:
            self._set_deadline(self.config.ghost_confirm_timeout_s)
            self._transition(GHOST_CONFIRMED, "fantôme confirmé")
        elif event.kind == EVENT_AMBIGUOUS:
            self.retry_count += 1
            if self.retry_count > self.config.ambiguous_retries:
                self._safe_stop("signal de mort ambigu trop de fois")
            else:
                self._log("signal ambigu — nouvelle vérification demandée")
        elif event.kind == EVENT_TIMEOUT:
            self._safe_stop("mort non confirmée dans le délai")
        elif event.kind == EVENT_STOP_REQUESTED:
            self._safe_stop("arrêt demandé pendant la détection de mort")
        else:
            self._log("événement %s ignoré pendant la détection de mort" % event.kind)

    def _on_ghost_confirmed(self, event: Event) -> None:
        if event.kind == EVENT_CORPSE_LOCATED:
            self._set_deadline(self.config.corpse_nav_timeout_s)
            self._transition(CORPSE_NAVIGATING, "cadavre localisé")
        elif event.kind == EVENT_TIMEOUT:
            self._safe_stop("fantôme confirmé mais cadavre non localisé")
        elif event.kind == EVENT_STOP_REQUESTED:
            self._safe_stop("arrêt demandé pendant la navigation fantôme")
        else:
            self._log("événement %s ignoré (fantôme confirmé)" % event.kind)

    def _on_corpse_navigating(self, event: Event) -> None:
        if event.kind == EVENT_RESURRECT_OK:
            self._set_deadline(self.config.resurrect_timeout_s)
            self._transition(RESURRECTING, "résurrection en cours")
        elif event.kind == EVENT_TIMEOUT:
            self._safe_stop("navigation vers le cadavre échouée (délai)")
        elif event.kind == EVENT_STOP_REQUESTED:
            self._safe_stop("arrêt demandé pendant la navigation cadavre")
        else:
            self._log("événement %s ignoré (navigation cadavre)" % event.kind)

    def _on_resurrecting(self, event: Event) -> None:
        if event.kind == EVENT_RESURRECT_OK:
            self._transition(IDLE, "résurrection réussie")
        elif event.kind == EVENT_AMBIGUOUS:
            self.retry_count += 1
            if self.retry_count > self.config.ambiguous_retries:
                self._safe_stop("résurrection ambiguë trop de fois")
            else:
                self._log("résurrection ambiguë — nouvelle tentative")
        elif event.kind == EVENT_TIMEOUT:
            self._safe_stop("résurrection non confirmée dans le délai")
        elif event.kind == EVENT_STOP_REQUESTED:
            self._safe_stop("arrêt demandé pendant la résurrection")
        else:
            self._log("événement %s ignoré (résurrection)" % event.kind)
