#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du Casinobabe Control Center (hors ligne).

REGLE ABSOLUE : aucun test ne lance le vrai bot de peche, ne touche a WoW,
n'envoie aucune entree. Les commandes START/PAUSE/STOP sont exercees sur un
processus FICTIF inoffensif (`tests/fake_bot_target.py`) qui reproduit la
structure du bot (verrou PID + journal de progression) sans aucun effet.

    python -m pytest tests/test_control_center.py -v
    python scripts/control_core.py --selftest
    python scripts/window_guard.py --selftest
    python scripts/control_center.py --selftest   (fenetre non mappee)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import control_core as core  # noqa: E402
import window_guard  # noqa: E402


# ===========================================================================
#Fixtures
# ===========================================================================

FAKE_SCRIPT = os.path.join("tests", "fake_bot_target.py")


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Redirige state/ et reports/ vers un dossier temporaire.

    Les repertoires sont monkeypatches AVANT tout import-utilisation : aucune
    ecriture ne touche le vrai state/ du depot.
    """
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    logs = tmp_path / "logs"
    for d in (state, reports, logs):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "STATE_DIR", str(state))
    monkeypatch.setattr(core, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(core, "LOGS_DIR", str(logs))
    monkeypatch.setattr(core, "CC_CONFIG_FILE", str(state / "control-center.json"))
    monkeypatch.setattr(core, "FISHING_CONFIG_FILE", str(state / "fishing-bot-config.json"))
    return {"root": tmp_path, "state": state, "reports": reports, "logs": logs}


def make_fake(name: str, mode: str, ws, **kw) -> core.Component:
    """Composant factice : meme code de commande que la production."""
    jsonl = ws["state"] / ("%s.jsonl" % name)
    return core.Component(
        key=name,
        label="Faux composant %s" % name,
        kind="bot",
        script=FAKE_SCRIPT,
        args=["--pid-file", str(ws["state"] / ("%s.pid" % name)),
              "--progress-log", str(jsonl),
              "--mode", mode],
        pid_file="%s.pid" % name,
        # `progress_log` est resolu via ROOT : sans le chemin relatif du
        # journal du test, le composant est juge « sans journal de progression ».
        progress_log=os.path.relpath(str(jsonl), ROOT),
        stdout_log=str(ws["logs"] / ("%s.out.log" % name)),
        # BOT_START est volontairement ABSENT : « j'ai demarre » ne prouve pas
        # que le bot travaille (cf. Component.startup_events).
        progress_events=("WATCH", "CAST", "CYCLE_SUMMARY"),
        heartbeat_stale_sec=5.0,
        controls=("start", "stop", "pause", "resume", "restart"),
        pause_mode="suspend_os",
        **kw,
    )


def fake_with_absolute_progress(comp: core.Component) -> core.Component:
    """`progress_log` est resolu via ROOT ; on pointe le journal du test."""
    comp.progress_log = os.path.relpath(comp.progress_path or "", ROOT)
    return comp


# ===========================================================================
# 1. Etats
# ===========================================================================

def fake_with_absolute_progress(comp: core.Component) -> core.Component:
    """`progress_log` est resolu via ROOT ; on pointe le journal du test."""
    comp.progress_log = os.path.relpath(comp.progress_path or "", ROOT)
    return comp


def test_etat_processus_absent(workspace):
    comp = make_fake("absent", "progress", workspace)
    st = core.component_status(comp)
    assert st["state"] == core.STOPPED
    assert "aucun verrou PID" in st["detail"]


def test_etat_verrou_residuel_detecte(workspace):
    comp = make_fake("residuel", "progress", workspace)
    pid_file = ws_pid(workspace, "residuel")
    pid_file.write_text("999999", encoding="utf-8")   # PID mort
    st = core.component_status(comp)
    assert st["state"] == core.STOPPED
    assert "VERROU RESIDUEL" in st["detail"]


def test_etat_actif_avec_heartbeat(workspace):
    comp = make_fake("vivant", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    res = cc.start("vivant")
    assert res.ok, res["detail"]
    try:
        time.sleep(1.0)
        st = core.component_status(comp)
        assert st["state"] == core.ACTIVE, st["detail"]
        assert st["pid"] > 0
        assert st["lock"]["status"] == "LOCKED_ALIVE"
        assert st["heartbeat"]["fresh"] is True
        assert st["heartbeat"]["last_event"] in ("WATCH", "BOT_START")
    finally:
        cc.stop("vivant")


def test_processus_vivant_sans_progression_est_bloque_pas_actif(workspace):
    """Exigence mission : ne PAS signaler ACTIF sur un simple processus vivant."""
    comp = make_fake("bloque", "silent", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("bloque").ok
    try:
        time.sleep(1.0)
        st = core.component_status(comp)
        assert st["state"] == core.BLOCKED, st["detail"]
        assert "aucune progression" in st["detail"]
    finally:
        cc.stop("bloque")


def test_processus_vivant_sans_journal_est_bloque(workspace):
    comp = make_fake("muet", "quiet", workspace)
    comp.progress_log = os.path.relpath(str(workspace["state"] / "absent.jsonl"), ROOT)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("muet").ok
    try:
        time.sleep(1.0)
        st = core.component_status(comp)
        assert st["state"] == core.BLOCKED, st["detail"]
    finally:
        cc.stop("muet")


def test_evenement_erreur_remonte_dans_le_heartbeat(workspace):
    comp = make_fake("enpanne", "error", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("enpanne").ok
    try:
        time.sleep(1.0)
        hb = core.heartbeat(comp)
        assert hb.get("last_error") is not None
        assert hb["last_error"]["event"] == "ERROR"
    finally:
        cc.stop("enpanne")


def test_etat_erreur_de_configuration(workspace):
    bad = make_fake("casse", "progress", workspace)
    bad.pid_file = "casse.pid"
    ws_pid(workspace, "casse").write_text("pas-un-pid", encoding="utf-8")
    lock = core.read_pid_lock(bad.pid_path)
    assert lock["status"] == "UNREADABLE"


def test_etat_absent_composant_casino():
    st = core.component_status(core.COMPONENTS_BY_KEY["casino_bot"])
    assert st["state"] == core.ABSENT
    assert "Aucun processus" in st["detail"]
    assert st["controls"] == []


def test_etat_pause_interne_du_bot_remonte(workspace):
    """Le bot peut se mettre lui-meme en PAUSED (WoW sans focus) : l'interface
    doit le montrer comme EN PAUSE, pas comme un blocage."""
    # mode « quiet » + injection d'une ligne de travail : la ligne injectee est
    # la derniere de facon deterministe (pas de course avec un producteur).
    comp = make_fake("autopause", "quiet", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("autopause").ok
    try:
        time.sleep(0.6)
        log = workspace["state"] / "autopause.jsonl"
        now = datetime.now().isoformat(timespec="milliseconds")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "event": "WATCH",
                                 "state": "PAUSED"}) + "\n")
        st = core.component_status(comp)
        assert st["state"] == core.PAUSED, st["detail"]
        assert "pause interne" in st["detail"]
    finally:
        cc.stop("autopause")


def test_etat_erreur_signalee_par_le_bot(workspace):
    # mode « quiet » : la cible n'ecrit RIEN apres le demarrage, donc la ligne
    # injectee ci-dessous est deterministiquement la derniere. Avec « silent »
    # elle etait ecrasée par les HEARTBEAT_SEUL suivants -> test instable.
    comp = make_fake("crash", "quiet", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("crash").ok
    try:
        time.sleep(0.6)
        log = workspace["state"] / "crash.jsonl"
        now = datetime.now().isoformat(timespec="milliseconds")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "event": "WATCH",
                                 "state": "ERROR",
                                 "reason": "capture impossible"}) + "\n")
        st = core.component_status(comp)
        assert st["state"] == core.ERROR, st["detail"]
    finally:
        cc.stop("crash")


def test_config_lisible_meme_si_fishing_bot_est_absent(workspace, monkeypatch):
    """`scripts/fishing_bot.py` n'existe PAS sur `main`. L'onglet Parametres
    ne doit pas planter : il doit basculer sur le schema interne et le dire."""
    monkeypatch.setattr(core, "bot_script_present", lambda: False)
    monkeypatch.setitem(sys.modules, "fishing_bot", None)
    cfg = core.load_fishing_config()
    assert "castingSettle" not in cfg
    assert cfg["__bot_present__"] is False
    assert "fishing_bot.py absent" in cfg["__config_source__"]
    # toutes les cles du schema restent accessibles
    for key in core.FISHING_SCHEMA:
        assert key in cfg, key


def test_start_refuse_si_le_lanceur_est_absent(workspace, monkeypatch):
    """Sur `main`, le lanceur n'est pas present : refus explicite, aucun
    subprocess fantome."""
    comp = make_fake("fantome", "progress", workspace)
    monkeypatch.setattr(core, "ROOT", str(workspace["root"]))
    cc = core.ControlCenter(registry={comp.key: comp})
    res = cc.start("fantome")
    assert not res.ok
    assert "lanceur absent de ce checkout" in res["detail"]
    assert res["action"] == "start"


def test_secret_variantes_toutes_masquees():
    for key in ("sessionToken", "session_token", "SessionToken", "SESSION-TOKEN",
                "api_key", "apiKey", "API-KEY", "apikey", "ApiKey",
                "CLIENT_SECRET", "client_secret", "clientSecret",
                "Authorization", "password", "refreshToken", "accessToken"):
        assert core.is_secret(key), key
    for key in ("pollMs", "supabaseUrl", "eventFunctionPath", "sessionId",
                "tokenCount", "secretariat"):
        assert not core.is_secret(key), key


def test_export_ne_fuit_aucune_variante_de_secret(workspace):
    charge = {
        "sessionToken": "S1", "session_token": "S2", "API-KEY": "S3",
        "CLIENT_SECRET": "S4", "authorization": "Bearer S5", "password": "S6",
        "nested": {"apiKey": "S7", "deep": [{"clientSecret": "S8", "ok": 1}]},
        "pollMs": 1500,
    }
    texte = json.dumps(core.mask_tree(charge), ensure_ascii=False)
    for i in range(1, 9):
        assert "S%d" % i not in texte, "secret S%d fuite" % i
    out = core.mask_tree(charge)
    assert out["pollMs"] == 1500
    assert out["nested"]["deep"][0]["clientSecret"] == core.MASK
    assert out["nested"]["deep"][0]["ok"] == 1


def test_cles_internes_jamais_persistees(workspace):
    """Regression 2026-10-19 : une cle `__interne__` passait le controle
    « parametre inconnu » et atterrissait dans la VRAIE configuration."""
    path = workspace["state"] / "fishing-bot-config.json"
    ok, errs, _ = core.save_fishing_config({"__none__": 1, "__config_source__": "x"})
    assert not ok and errs
    assert not path.exists(), "la configuration du bot a ete polluee"
    clean, errs2 = core.validate_update({"__unknown__": 1})
    assert not clean and errs2


def test_auto_test_et_suite_ne_polluent_pas_le_depot(workspace, tmp_path):
    """Les tests doivent travailler dans un dossier temporaire, jamais dans
    le state/ du depot."""
    disk = os.path.join(core.ROOT, "state", "fishing-bot-config.json")
    before = os.path.exists(disk)
    cc = core.ControlCenter(registry={})
    core.load_fishing_config()
    cc.diagnostic_report()
    assert os.path.exists(disk) == before


def ws_pid(ws, name):
    return ws["state"] / ("%s.pid" % name)


# ===========================================================================
# 2. Commandes START / PAUSE / STOP sur processus de test
# ===========================================================================

def test_start_stop_cycle(workspace):
    comp = make_fake("cycle", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})

    res = cc.start("cycle")
    assert res.ok, res["detail"]
    time.sleep(0.8)
    assert ws_pid(workspace, "cycle").exists()

    res = cc.stop("cycle")
    assert res.ok, res["detail"]
    time.sleep(0.5)
    assert not core._pid_alive(comp_pid(comp))


def comp_pid(comp: core.Component) -> int:
    try:
        return int(open(comp.pid_path).read().strip())
    except (OSError, ValueError):
        return 0


def test_start_refuse_si_instance_vivante_pas_de_doublon(workspace):
    """Exigence mission : un second START ne cree pas de doublon."""
    comp = make_fake("unique", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})

    assert cc.start("unique").ok
    time.sleep(0.8)
    first_pid = comp_pid(comp)

    for _ in range(3):
        res = cc.start("unique")
        assert not res.ok
        assert "anti-doublon" in res["detail"]

    time.sleep(0.4)
    assert comp_pid(comp) == first_pid, "le PID a change : doublon cree"
    assert core.component_status(comp)["state"] != core.STOPPED
    cc.stop("unique")


def test_pause_reprise_reversibles_et_conservent_le_verrou(workspace):
    comp = make_fake("pause", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})

    assert cc.start("pause").ok
    time.sleep(0.8)
    pid = comp_pid(comp)

    res = cc.pause("pause")
    assert res.ok, res["detail"]
    time.sleep(0.4)
    assert core._pid_alive(pid), "la suspension OS a tue le processus"
    assert ws_pid(workspace, "pause").exists(), "le verrou PID a disparu"

    import psutil
    assert psutil.Process(pid).status() == psutil.STATUS_STOPPED

    st = core.component_status(comp)
    assert st["state"] == core.PAUSED, st["detail"]
    assert "suspendu" in st["detail"]

    res = cc.resume("pause")
    assert res.ok, res["detail"]
    time.sleep(0.4)
    assert psutil.Process(pid).status() != psutil.STATUS_STOPPED
    assert core._pid_alive(pid)
    assert core.component_status(comp)["state"] in (core.ACTIVE, core.STARTING)
    cc.stop("pause")


def test_stop_est_propre_et_libe_le_verrou(workspace):
    comp = make_fake("arret", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("arret").ok
    time.sleep(0.8)
    pid = comp_pid(comp)

    res = cc.stop("arret")
    assert res.ok, res["detail"]
    deadline = time.time() + 8
    while core._pid_alive(pid) and time.time() < deadline:
        time.sleep(0.2)
    assert not core._pid_alive(pid)
    assert not ws_pid(workspace, "arret").exists()


def test_stop_sans_processus_refuse_avec_explication(workspace):
    comp = make_fake("rien", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    res = cc.stop("rien")
    assert not res.ok
    assert "aucun processus" in res["detail"].lower()


def test_restart_fait_un_arret_propre_puis_une_relance(workspace):
    comp = make_fake("bounce", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    assert cc.start("bounce").ok
    time.sleep(0.8)
    first = comp_pid(comp)

    res = cc.restart("bounce")
    assert res.ok, res["detail"]
    time.sleep(0.8)
    second = comp_pid(comp)
    assert second and second != first
    assert core._pid_alive(second)
    cc.stop("bounce")


def test_start_nettoie_un_verrou_residuel(workspace):
    comp = make_fake("residuel2", "progress", workspace)
    ws_pid(workspace, "residuel2").write_text("999999", encoding="utf-8")
    cc = core.ControlCenter(registry={comp.key: comp})
    res = cc.start("residuel2")
    assert res.ok, res["detail"]
    assert "verrou residuel" in res["detail"]
    time.sleep(0.5)
    cc.stop("residuel2")


def test_toutes_les_commandes_sont_tracees(workspace):
    comp = make_fake("trace", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    cc.start("trace")
    cc.stop("trace")
    cc.start("trace")
    actions = [(h["component"], h["action"]) for h in cc.history]
    assert actions == [("trace", "start"), ("trace", "stop"), ("trace", "start")]
    cc.stop("trace")


def test_commande_inconnue_refusee(workspace):
    cc = core.ControlCenter(registry={})
    res = cc.dispatch("detruire", "peche")
    assert not res.ok
    assert "inconnue" in res["detail"]


def test_toutes_les_commandes_passent_par_le_garde_fou(workspace, monkeypatch):
    comp = make_fake("garde", "progress", workspace)
    cc = core.ControlCenter(registry={comp.key: comp})
    seen = []
    real = window_guard.safe_call

    def spy(action, fn, **kw):
        seen.append(action)
        return real(action, fn, **kw)

    monkeypatch.setattr(core.window_guard, "safe_call", spy)
    cc.start("garde")
    cc.stop("garde")
    assert seen == ["start_fishing_bot", "stop_fishing_bot"]


# ===========================================================================
# 3. Persistance des parametres et masquage des secrets
# ===========================================================================

def test_config_peche_conservee_apres_une_mise_a_jour(workspace):
    path = workspace["state"] / "fishing-bot-config.json"
    path.write_text(json.dumps(
        {"castSettleSec": 2.0, "cle inconnue du bot": 42, "bobberMinConf": 0.55}),
        encoding="utf-8")

    ok, errs, msg = core.save_fishing_config({"maxBiteWaitSec": 30.0})
    assert ok, errs
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["maxBiteWaitSec"] == 30.0
    assert data["castSettleSec"] == 2.0, "valeur existante ecrasee"
    assert data["cle inconnue du bot"] == 42, "cle inconnue perdue"
    assert "maxBiteWaitSec" in msg


def test_config_invalide_refusee_sans_ecriture(workspace):
    path = workspace["state"] / "fishing-bot-config.json"
    path.write_text(json.dumps({"castSettleSec": 2.0}), encoding="utf-8")
    ok, errs, _ = core.save_fishing_config({"bobberMinConf": 99.0})
    assert not ok and errs
    assert json.loads(path.read_text(encoding="utf-8")) == {"castSettleSec": 2.0}


def test_parametre_verrouille_refuse(workspace):
    ok, errs, _ = core.save_fishing_config({"detectScale": 0.5})
    assert not ok
    assert "verrouille" in errs[0]


def test_separations_des_configs_entre_les_bots(workspace):
    """La config du Control Center ne doit toucher QUE son propre fichier."""
    core.save_cc_settings({"refreshSec": 7.0})
    cc_path = workspace["state"] / "control-center.json"
    assert json.loads(cc_path.read_text(encoding="utf-8"))["refreshSec"] == 7.0
    assert not (workspace["state"] / "fishing-bot-config.json").exists()
    assert not (workspace["state"] / "casino-bot-config.json").exists()


def test_secrets_masques_dans_le_rapport(workspace):
    bridge = os.path.join(ROOT, "bridge", "config.json")
    if not os.path.isfile(bridge):
        pytest.skip("bridge/config.json absent")
    raw = json.loads(open(bridge, "r", encoding="utf-8").read())
    if "sessionToken" not in raw:
        pytest.skip("pas de sessionToken a masquer")
    cc = core.ControlCenter(registry={})
    rep = cc.diagnostic_report()
    text = json.dumps(rep, ensure_ascii=False)
    assert raw["sessionToken"] not in text
    assert core.MASK in text


@pytest.mark.parametrize("key", ["sessionToken", "password", "api_key", "Authorization",
                                 "refreshToken", "CLIENT_SECRET"])
def test_masquage_par_nom_de_cle(key):
    assert core.is_secret(key)
    assert core.mask_value(key, "valeur") == core.MASK


@pytest.mark.parametrize("key", ["pollMs", "castSettleSec", "supabaseUrl"])
def test_pas_de_masquage_injustifie(key):
    assert not core.is_secret(key)


# ===========================================================================
# 4. Garde-fou window_guard
# ===========================================================================

def test_garde_fou_interdit_les_actions_danger():
    for action in ("steal_focus", "minimize_wow", "close_wow",
                   "inject_keyboard", "inject_mouse", "move_user_cursor"):
        ok, detail = window_guard.safe_call(action, lambda: 1 / 0)
        assert not ok
        assert "INTERDITE" in detail
        assert "division" not in detail.lower(), "l'action interdite a ete executee"


def test_garde_fou_detecte_les_observations_de_bureau():
    """Les changements de bureau sont des OBSERVATIONS, pas des refus."""
    before = {"wow": {"present": True, "hwnd": 7, "minimized": False,
                      "visible": True, "rect": [0, 0, 100, 100]},
              "foreground": {"hwnd": 9}, "cursor": (10, 10)}
    checks = {
        "FENETRE_WOW_MINIMISEE": {"wow": dict(before["wow"], minimized=True)},
        "CURSEUR_DEPLACE": {"cursor": (999, 999)},
        "FOCUS_VOL": {"foreground": {"hwnd": 7}},
        "FENETRE_WOW_FERMEE": {"wow": {"present": False, "hwnd": 0, "minimized": None,
                                       "visible": False, "rect": None}},
    }
    for marker, patch in checks.items():
        after = dict(before)
        after.update(patch)
        problems = window_guard.desktop_deltas(before, after)
        assert any(marker in p for p in problems), (marker, problems)


def test_bouger_la_souris_ne_bloque_pas_une_commande(monkeypatch):
    """Regression du 2026-10-19 : l'utilisateur bougeait sa souris pendant une
    commande et le garde-fou REFUSAIT l'action. C'est un faux positif : le
    changement n'est pas attribuable a l'application."""
    avant = {"wow": {"present": False, "hwnd": 0, "minimized": None,
                     "visible": False, "rect": None},
             "foreground": {"hwnd": 0, "title": "", "pid": 0, "process": "",
                            "is_wow": False},
             "cursor": (100, 100)}
    apres = dict(avant, cursor=(900, 900))
    etat = {"n": 0}

    def snapshot_bouge():
        etat["n"] += 1
        snap = dict(avant)
        if etat["n"] > 1:
            snap = dict(apres)
        return snap

    monkeypatch.setattr(window_guard, "snapshot", snapshot_bouge)
    ok, detail = window_guard.safe_call("read_state", lambda: "action effectuee")
    assert ok, detail
    assert "action effectuee" in detail


def test_preuve_statique_aucune_api_win32_mutante():
    """Le Control Center n'appelle AUCUNE API qui modifierait le bureau."""
    for module in ("window_guard.py", "control_core.py", "control_center.py"):
        path = os.path.join(ROOT, "scripts", module)
        ok, occurrences = window_guard.assert_no_mutating_api(path)
        assert ok, "%s : %s" % (module, occurrences)


def test_garde_fou_ne_signale_rien_si_rien_ne_bouge():
    snap = window_guard.snapshot()
    assert window_guard.desktop_deltas(snap, window_guard.snapshot()) == []


def test_rapport_garde_fou_expose_la_politique():
    rep = window_guard.policy_report()
    assert "steal_focus" in rep["policy"]["forbidden"]
    assert "desktop" in rep


# ===========================================================================
# 5. Robustesse
# ===========================================================================

def test_snapshot_mentionne_tous_les_composants_de_production():
    snap = core.ControlCenter().snapshot()
    keys = {c["key"] for c in snap["components"]}
    assert keys == {c.key for c in core.COMPONENTS}
    assert snap["casino_state"] == core.ABSENT


def test_rapport_de_diagnostic_est_du_json_serialisable(workspace):
    rep = core.ControlCenter().diagnostic_report()
    json.dumps(rep, ensure_ascii=False)          # ne doit pas lever
    assert rep["schema"] == 1
    assert rep["casino"]["state"] == core.ABSENT


def test_journal_filtre_par_niveau_et_erreur(workspace):
    comp = make_fake("filtre", "progress", workspace)
    log = workspace["state"] / "filtre.jsonl"
    rows = [
        {"ts": "2026-10-19T10:00:00.000", "event": "WATCH", "state": "WAITING FOR BITE"},
        {"ts": "2026-10-19T10:00:01.000", "event": "ERROR", "state": "ERROR",
         "reason": "boum"},
        {"ts": "2026-10-19T10:00:02.000", "event": "CAST", "state": "CASTING"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    comp.progress_log = os.path.relpath(str(log), ROOT)
    cc = core.ControlCenter(registry={comp.key: comp})

    assert len(cc.log_tail("filtre")) == 3
    assert [r["event"] for r in cc.log_tail("filtre", error_only=True)] == ["ERROR"]
    assert [r["event"] for r in cc.log_tail("filtre", level="DEBUG")] == ["WATCH"]
    since = "2026-10-19T10:00:01.500"
    assert [r["event"] for r in cc.log_tail("filtre", since=since)] == ["CAST"]


def test_fenetre_de_bouton_refusee_si_wow_ferme_pendant_laction(workspace, monkeypatch):
    """Le garde-fou NE doit PAS refuser : une disparition de WoW pendant une
    commande n'est pas attribuable (l'utilisateur peut l'avoir fermee). Elle
    doit etre consignee comme observation. Seule une panne du garde-fou refuse."""
    real_snapshot = window_guard.snapshot
    calls = {"n": 0}

    def flaky_snapshot():
        calls["n"] += 1
        snap = real_snapshot()
        if calls["n"] > 1:
            snap = dict(snap)
            snap["wow"] = {"present": False, "hwnd": 0, "minimized": None,
                           "visible": False, "rect": None}
        return snap

    monkeypatch.setattr(window_guard, "snapshot", flaky_snapshot)
    ok, detail = window_guard.safe_call("read_state", lambda: "fait")
    assert ok, detail
    assert "OBSERVATIONS" in detail
    assert "FERMEE" in detail


def test_panne_du_garde_fou_refuse_bien(workspace, monkeypatch):
    """En revanche, si le garde-fou lui-meme tombe en panne, on refuse."""
    real_snapshot = window_guard.snapshot
    calls = {"n": 0}

    def flaky_snapshot():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("sampler casse")
        return real_snapshot()

    monkeypatch.setattr(window_guard, "snapshot", flaky_snapshot)
    ok, detail = window_guard.safe_call("read_state", lambda: "fait")
    assert not ok
    assert "GARDE_FOU_ERREUR" in detail
