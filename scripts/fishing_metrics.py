#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_metrics.py — MESURE DES CAPTURES, PAR NIVEAUX DE PREUVE.

REGLE ABSOLUE : un clic, une animation ou une disparition de flotteur ne
sont PAS une capture. Chaque niveau de la chaine est compte separement, et
le tableau indique explicitement ce qui est observe, par quoi, et avec quelle
fiable.

    1. tentative de lancer   : le bot a DECIDE de lancer
    2. lancer envoye        : la touche a ete envoyee au client
    3. lancer confirme      : le client a confirme (le flotteur est apparu)
    4. touche detectee      : la touche de lancer a ete vues active
    5. clic envoye          : le clic droit a ete envoye
    6. interaction confirmee : le flotteur a disparu apres le clic
    7. poisson capture      :compte par l'ADDON (compteur de butin) — seul
                              canal independant, ecrit a la DECONNEXION du
                              client : ce niveau n'est donc PAS disponible en
                              temps reel.

Les niveaux 1 a 6 viennent du journal du bot (une ligne JSON par evenement).
Le niveau 7 vient des SavedVariables de l'addon, dont la derniere ecriture
date de la derniere deconnexion : l'analyseur indique donc la date de la
mesure pour ne pas faire croire a une donnee en direct.
"""

import json
import os
import time

#: Evenements du journal -> niveau de preuve.
LEVELS = [
    ("cast_attempted", "Tentative de lancer", ("CAST", "CAST_NOT_SENT")),
    ("cast_sent", "Lancer envoye au client", ("CAST",)),
    ("cast_unconfirmed", "Lancer non confirme (flotteur non vu)", ("CAST_UNCONFIRMED",)),
    ("bite_detected", "Morsure detectee", ("CATCH_DETECTED", "BITE_DIP")),
    ("click_sent", "Clic envoye", ("CLICK_BOBBER",)),
    ("click_failed", "Clic non envoye", ("CLICK_FAILED",)),
    ("interaction_confirmed", "Interaction confirmee (flotteur disparu)", ("VERIFY_OK",)),
    ("interaction_lost", "Interaction non confirmee", ("VERIFY_RETRY_FAILED",)),
    ("bite_timeout", "Morsure non survenue (timeout)", ("BITE_TIMEOUT",)),
]

#: Ce qui NE compte PAS comme une capture.
NOT_A_CAPTURE = ("click_sent", "interaction_confirmed", "cast_sent", "cast_attempted")


def read_log(path):
    """Lit le journal JSONL du bot (tolere les lignes corrompues)."""
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def read_addon_counters(path):
    """Compteurs de l'addon (SavedVariables). Retourne (donnees, meta).

    `meta.source` indique LE CANAL et la date de la derniere ecriture : les
    SavedVariables ne sont ecrits qu'a la deconnexion du client.
    """
    out = {"totals": {}, "meta": {"source": "addon SavedVariables",
                                  "available": False, "readAt": None,
                                  "lastWrite": None, "note": ""}}
    if not path or not os.path.isfile(path):
        out["meta"]["note"] = "fichier de SavedVariables absent"
        return out
    try:
        out["meta"]["lastWrite"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))
    except OSError:
        pass
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError as exc:
        out["meta"]["note"] = str(exc)
        return out
    totals = {}
    for key in ("catches", "targets", "lostCasts", "dups", "unknownKind",
                "castObserved", "castEvents", "castNameResolved", "combats",
                "deaths", "crafted", "possibleTargets"):
        m = _lua_number(txt, key)
        if m is not None:
            totals[key] = m
    for key in ("castLastUnit", "castLastArgs", "castLastName", "castLastState"):
        m = _lua_string(txt, key)
        if m is not None:
            totals[key] = m
    out["totals"] = totals
    out["meta"]["available"] = bool(totals)
    out["meta"]["readAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["meta"]["note"] = ("compteurs ecrits par l'addon a la deconnexion du "
                           "client : ce niveau n'est PAS temps reel")
    return out


def _lua_number(txt, key):
    import re
    m = re.search(r"\b%s\s*=\s*(\d+)" % re.escape(key), txt)
    return int(m.group(1)) if m else None


def _lua_string(txt, key):
    import re
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(key), txt)
    return m.group(1) if m else None


def analyse(log_path=None, savedvars_path=None, since=None):
    """Analyse les captures par niveau de preuve.

    :param log_path: journal JSONL du bot
    :param savedvars_path: fichier SavedVariables de l'addon
    :param since: timestamp ISO minimal (inclus) pour la fenetre d'analyse
    """
    rows = read_log(log_path)
    if since:
        rows = [r for r in rows if str(r.get("ts", "")) >= since]
    counts = {name: 0 for name, _lbl, _ev in LEVELS}
    for r in rows:
        ev = r.get("event")
        # Un evenement peut appartenir a PLUSIEURS niveaux (un CAST est a la
        # fois une tentative ET un lancer envoye) : les niveaux ne sont pas
        # exclusifs, ils sont imbriques.
        for name, _lbl, events in LEVELS:
            if ev in events:
                counts[name] += 1
    cycles = len({r.get("cycle") for r in rows if r.get("cycle")})
    addon = read_addon_counters(savedvars_path)
    totals = addon.get("totals", {})

    def ratio(a, b):
        return round(100.0 * a / b, 1) if b else None

    out = {
        "fenetre": {
            "since": since,
            "evenements": len(rows),
            "cycles": cycles,
        },
        "niveaux": {},
        "addon": addon,
    }
    for name, label, _ev in LEVELS:
        out["niveaux"][name] = {
            "libelle": label,
            "compteur": counts[name],
            "par_cycle": ratio(counts[name], cycles),
        }
    # Niveau 7 : capture reelle, source addon (ind independante)
    catches = totals.get("catches")
    out["niveaux"]["fish_captured"] = {
        "libelle": "Poisson reellement capture (compteur de l'addon)",
        "compteur": catches,
        "par_cycle": ratio(catches, cycles) if catches is not None else None,
        "source": "addon SavedVariables (ecrit a la deconnexion)",
        "temps_reel": False,
    }
    out["niveaux"]["cast_attempted"]["par_cycle"] = ratio(counts["cast_attempted"], cycles)
    out["niveaux"]["cast_sent"]["par_cycle"] = ratio(counts["cast_sent"], cycles)
    out["niveaux"]["click_sent"]["par_cycle"] = ratio(counts["click_sent"], cycles)
    out["niveaux"]["interaction_confirmed"]["par_cycle"] = ratio(counts["interaction_confirmed"], cycles)
    out["repartition"] = {
        "lancers_envoyes_sur_attemptes": ratio(counts["cast_sent"], counts["cast_attempted"]),
        "morsures_sur_lancers": ratio(counts["bite_detected"], counts["cast_sent"]),
        "clics_sur_morsures": ratio(counts["click_sent"], counts["bite_detected"]),
        "interactions_sur_clics": ratio(counts["interaction_confirmed"], counts["click_sent"]),
        "captures_sur_interactions": ratio(catches, counts["interaction_confirmed"])
        if catches is not None else None,
    }
    out["avertissement"] = (
        "Une interaction confirmee (le flotteur a disparu) n'est PAS une "
        "capture : seul le compteur de l'addon fait foi, et il n'est ecrit "
        "qu'a la deconnexion du client."
    )
    return out


def format_report(analysis):
    """Rendu texte lisible (pour le journal et le rapport de mission)."""
    lines = []
    lines.append("=== MESURE DES CAPTURES (par niveau de preuve) ===")
    w = analysis["fenetre"]
    lines.append("  fenetre : %s evenements, %s cycles%s"
                 % (w["evenements"], w["cycles"],
                    (" (depuis %s)" % w["since"]) if w.get("since") else ""))
    lines.append("  %-34s %8s %10s" % ("niveau", "compteur", "/cycle"))
    for name, data in analysis["niveaux"].items():
        c = data.get("compteur")
        pc = data.get("par_cycle")
        lines.append("  %-34s %8s %10s" % (data["libelle"],
                                            "-" if c is None else c,
                                            "-" if pc is None else ("%.1f%%" % pc)))
    lines.append("  --- enchainements ---")
    for k, v in analysis["repartition"].items():
        lines.append("  %-34s %s" % (k.replace("_", " "), "-" if v is None else ("%.1f%%" % v)))
    a = analysis.get("addon", {})
    if a.get("available"):
        lines.append("  --- addon (source independante, ecrite a la deconnexion) ---")
        lines.append("  dernier ecrit : %s | lu le %s" % (a.get("lastWrite"), a.get("readAt")))
        t = a.get("totals", {})
        for k in sorted(t):
            if isinstance(t[k], (int, float)):
                lines.append("    %-22s %s" % (k, t[k]))
        for k in ("castLastUnit", "castLastArgs", "castLastName", "castLastState"):
            if k in t:
                lines.append("    %-22s %s" % (k, t[k]))
    lines.append("  ATTENTION : %s" % analysis["avertissement"])
    return "\n".join(lines)
