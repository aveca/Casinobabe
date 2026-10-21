# -*- coding: utf-8 -*-
"""
test_recovery_state_machine.py — tests hors-ligne de la machine à états de
récupération (scripts/recovery_state_machine.py). Aucun jeu, aucun clic.
Mode script : exit 0 si tout passe, 1 sinon.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import recovery_state_machine as rsm  # noqa: E402

RESULTS = []


def ck(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def ev(kind, payload=None):
    return rsm.Event(kind, payload or {})


# ------------------------------------------------------- états initiaux -----
m = rsm.RecoveryStateMachine()
ck("état initial IDLE", m.state == rsm.IDLE)
ck("désactivé par défaut", m.is_enabled() is False)
ck("status expose l'état", m.status()["state"] == rsm.IDLE)

# ----------------------------------------------------------- événement hors --
m.feed(ev(rsm.EVENT_DEATH_DETECTED))
ck("événement ignoré si désactivé", m.state == rsm.IDLE)

# ------------------------------------------------------------ activation ---
m.enable()
ck("activation", m.is_enabled() is True)

# ----------------------------------------------------- cycle heureux -------
m.reset()
m.feed(ev(rsm.EVENT_DEATH_DETECTED))
ck("mort détectée -> DEATH_DETECTED", m.state == rsm.DEATH_DETECTED)
m.feed(ev(rsm.EVENT_GHOST_CONFIRMED))
ck("fantôme confirmé -> GHOST_CONFIRMED", m.state == rsm.GHOST_CONFIRMED)
m.feed(ev(rsm.EVENT_CORPSE_LOCATED))
ck("cadavre localisé -> CORPSE_NAVIGATING", m.state == rsm.CORPSE_NAVIGATING)
m.feed(ev(rsm.EVENT_RESURRECT_OK))
ck("résurrection lancée -> RESURRECTING", m.state == rsm.RESURRECTING)
m.feed(ev(rsm.EVENT_RESURRECT_OK))
ck("résurrection terminée -> IDLE", m.state == rsm.IDLE)
ck("dernières 5 transitions dans l'historique", len(m.status()["history"]) == 5)

# ----------------------------------------------------------- safe stops -----
# timeout en DEATH_DETECTED
m.reset()
m.enable()
m.feed(ev(rsm.EVENT_DEATH_DETECTED))
m._deadline = m._now() - 1  # deadline déjà expirée
m.feed(ev(rsm.EVENT_GHOST_CONFIRMED))  # timeout traité avant l'événement
ck("safe stop sur timeout mort", m.state == rsm.SAFE_STOP)
ck("dernière erreur renseignée", m.last_error is not None)

# mort ambiguë répétée -> safe stop
m.reset()
m.enable()
m.config.ambiguous_retries = 1
for _ in range(3):
    m.state = rsm.DEATH_DETECTED
    m._deadline = m._now() + 100
    m.feed(ev(rsm.EVENT_AMBIGUOUS))
ck("safe stop après ambiguïtés répétées", m.state == rsm.SAFE_STOP)

# ---------------------------------------------------------- arrêt demandé ---
m.reset()
m.enable()
m.feed(ev(rsm.EVENT_DEATH_DETECTED))
m.feed(ev(rsm.EVENT_STOP_REQUESTED))
ck("safe stop sur demande d'arrêt", m.state == rsm.SAFE_STOP)

# réinitialisation depuis le safe stop
m.reset()
ck("reset ramène à IDLE", m.state == rsm.IDLE)
ck("retry remis à zéro", m.retry_count == 0)
ck("dernière erreur effacée", m.last_error is None)

# ----------------------------------------------------------- config -------
cfg = rsm.RecoveryConfig(enabled=True, death_confirm_timeout_s=5.0)
m2 = rsm.RecoveryStateMachine(config=cfg)
ck("config personnalisée prise en compte", m2.config.death_confirm_timeout_s == 5.0)

try:
    rsm.RecoveryConfig(enabled=True, death_confirm_timeout_s=0.0)
    ck("config timeout invalide rejeté", False)
except ValueError:
    ck("config timeout invalide rejeté", True)

# -------------------------------------------------------------- bilan ------
ok_count = sum(1 for _, ok, _ in RESULTS if ok)
print("---- %d/%d PASS ----" % (ok_count, len(RESULTS)))
sys.exit(0 if ok_count == len(RESULTS) else 1)
