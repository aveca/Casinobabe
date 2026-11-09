#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fishing_controls.py — CARTOGRAPHIE STRUCTUREE DES COMMANDES DISPONIBLES.

Ce module ne suppose rien : il LIT les raccourcis reels du client WoW
(`WTF/Account/<compte>/bindings-cache.wtf`) et en deduit ce qui est
reellement pilotable. Les raccourcis ne sont jamais modifies.

Pourquoi c'est important : les raccourcis par defaut de WoW Classic sont
 decevants. Sur la machine de developpement, `W` est lie a une barre
d'actions (pas a l'avance), `UP` n'est lie a rien, et l'avance/recul ne
sont PAS disponibles au clavier : seul le strafe droit l'est, et le
deplacement passe par l'auto-run (`Z` = TOGGLEAUTORUN) suivi d'un clic
droit sur le sol.

Le module distingue aussi deux orientations que le jeu melange :
  * l'ORIENTATION DE LA CAMERA : c'est la souris (le deplacement souris
    fait tourner la camera) ;
  * l'ORIENTATION DU PERSONNAGE : c'est la camera (le personnage avance
    dans la direction regardee quand il se deplace).
Aucun binding ne pilote la camera : elle passe par la souris, que le bot
pilote deja (SetCursorPos).

API
---
    controls = load_controls()
    controls.bindings          -> {touche: action}
    controls.forward()         -> touche d'avance ou None
    controls.auto_run()        -> touche d'auto-run ou None
    controls.cast_key_bound()  -> True/False (la touche de lancer est-elle
                                  reellement liee ?)
    controls.report()          -> dict imprimable (la carte complete)
"""

import os
import re
import time

#: Racines WoW inspectees (lecture seule).
WOW_ROOTS = [
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
]

#: Actions du jeu qui nous interessent.
#:   - deplacement : le personnage avance/recul/strafe
#:   - auto-run : le mode « courir vers un point »
ACTION_MOVEMENT = {
    "FORWARD": "avance",
    "BACKWARD": "recul",
    "STRAFELEFT": "strafe gauche",
    "STRAFERIGHT": "strafe droite",
    "TURNLEFT": "rotation gauche (camera)",
    "TURNRIGHT": "rotation droite (camera)",
    "PITCHUP": "inclinaison camera haut",
    "PITCHDOWN": "inclinaison camera bas",
    "TOGGLEAUTORUN": "auto-run (marche vers le point clique)",
    "TOGGLEWORLDMAP": "carte du monde",
    "TOGGLEUI": "interface (panneaux)",
    "CAMERAZOOMIN": "zoom camera avant",
    "CAMERAZOOMOUT": "zoom camera arriere",
}

#: Actions de barre d'actions (le bot ne les pilote PAS : elles sont
#: lancees par le client, pas par une touche de deplacement).
ACTION_BAR = re.compile(r"^(ACTIONBUTTON|MULTIACTIONBAR|BONUSACTIONBUTTON)\d+$")

#: L'action reellement utilisee par le bot pour lancer (la touche 2 est
#: liee a ACTIONBUTTON10 sur cette installation).
CAST_ACTION = "ACTIONBUTTON10"

#: Touches reservees par le bot lui-meme (hors bindings du jeu).
BOT_RESERVED = {
    "F9": "ARRET D'URGENCE (lu par le bot via GetAsyncKeyState, pas par le jeu)",
}


def _wow_account_dirs():
    """Dossiers de comptes WoW trouves, du plus recemment modifie au plus ancien."""
    found = []
    for root in WOW_ROOTS:
        if not os.path.isdir(root):
            continue
        for flavor in ("_anniversary_", "_classic_", "_classic_era_", "_retail_"):
            base = os.path.join(root, flavor, "WTF", "Account")
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                d = os.path.join(base, name)
                if os.path.isdir(d):
                    found.append(d)
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found


def parse_bindings_file(path):
    """Parse un `bindings-cache.wtf` : {touche: action}.

    Le format est une ligne par binding : `bind <touche> <action>`.
    Les touches modifiees (Shift-/Ctrl-) sont conservees telles quelles.
    """
    bindings = {}
    if not path or not os.path.isfile(path):
        return bindings
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("bind "):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            key = parts[1].upper()
            action = parts[2].upper()
            if action == "NONE":
                continue          # touche explicitement liberee
            bindings[key] = action
    return bindings


class Controls(object):
    """Cartographie des commandes disponibles sur la machine.

    Les raccourcis sont lus pour TOUS les comptes WoW trouves : ils
    different d'un compte a l'autre (sur la machine de developpement, la
    touche de lancer est `2` sur un compte et `X` sur un autre). On retient
    donc la commmun (les actions liees sur TOUS les comptes) comme
    reference sure, et on expose le detail par compte.
    """

    def __init__(self, per_account=None, errors=None):
        self.per_account = per_account or {}
        self.errors = errors or []

    # ------------------------------------------------------------- lecture
    @classmethod
    def load(cls, root=None):
        per_account = {}
        errors = []
        for d in _wow_account_dirs():
            f = os.path.join(d, "bindings-cache.wtf")
            if not os.path.isfile(f):
                continue
            try:
                b = parse_bindings_file(f)
            except OSError as exc:
                errors.append("%s: %s" % (os.path.basename(d), exc))
                continue
            if b:
                per_account[os.path.basename(d)] = b
        return cls(per_account, errors)

    # ---------------------------------------------------------- agregats
    def _accounts(self):
        return list(self.per_account.keys())

    def common(self):
        """Actions liees sur TOUS les comptes (intersection)."""
        accounts = self._accounts()
        if not accounts:
            return {}
        sets = []
        for a in accounts:
            sets.append({v for v in self.per_account[a].values()})
        common = set.intersection(*sets) if sets else set()
        return common

    def key_for(self, action, common_only=True):
        """Touche liee a une action du jeu.

        `common_only=True` : uniquement si l'action est liee sur TOUS les
        comptes (choix prudent : on ne suppose pas que le personnage joue
        avec le meme compte que celui qu'on a lu en premier).
        """
        accounts = self._accounts()
        if not accounts:
            return None
        keys = []
        for a in accounts:
            found = None
            for k, v in self.per_account[a].items():
                if v == action:
                    found = k
                    break
            if found is None:
                if common_only:
                    return None
                continue
            keys.append(found)
        if not keys:
            return None
        if common_only and len(set(keys)) > 1:
            # touche differente selon le compte : on ne renvoie rien
            return None
        return keys[0]

    def keys_for(self, action):
        """Touche liee a une action, pour chaque compte {compte: touche}."""
        out = {}
        for a, binds in self.per_account.items():
            for k, v in binds.items():
                if v == action:
                    out[a] = k
                    break
        return out

    # ------------------------------------------------- compte en jeu
    def active_account(self):
        """Compte probablement en jeu (le plus recent ecrit d'addon).

        Le client n'ecrit ses SavedVariables qu'a la deconnexion : le fichier
        ecrit le plus recemment est donc celui du dernier personnage joue.
        C'est le seul indice fiable sans passer par l'addon.
        """
        best = None
        best_ts = 0.0
        for d in _wow_account_dirs():
            for rel in (os.path.join("SavedVariables", "Casinobabe.lua"),
                        "bindings-cache.wtf"):
                p = os.path.join(d, rel)
                try:
                    ts = os.path.getmtime(p)
                except OSError:
                    continue
                if ts > best_ts:
                    best_ts = ts
                    best = os.path.basename(d)
        return best

    def cast_key_for(self, account):
        """Touche de lancer du compte donne (celle liee a ACTIONBUTTON10)."""
        binds = self.per_account.get(account) or {}
        for k, v in binds.items():
            if v == CAST_ACTION:
                return k
        return None

    def forward(self):
        return self.key_for("FORWARD")

    def backward(self):
        return self.key_for("BACKWARD")

    def strafe_left(self):
        return self.key_for("STRAFELEFT")

    def strafe_right(self):
        return self.key_for("STRAFERIGHT")

    def auto_run(self):
        return self.key_for("TOGGLEAUTORUN")

    def cast_keys(self):
        """Touches liees a l'action de lancer, par compte."""
        return self.keys_for(CAST_ACTION)

    def cast_key_bound(self):
        """Une touche de lancer existe-t-elle sur TOUS les comptes ?

        Retourne None si les comptes DISAGREENT (le cas observe : `2`,
        `X` et `PADDRIGHT` selon le compte) : on ne peut alors pas deduire une
        touche unique, il faut la connaitre par compte.
        """
        keys = self.cast_keys()
        if not keys or len(keys) < len(self._accounts()):
            return None
        values = set(keys.values())
        if len(values) > 1:
            return None
        return list(values)[0]

    def configured_cast_ok(self, cast_key):
        """La touche de lancer CONFIGUREE est-elle liee sur le compte joue ?

        Retourne (ok, details). C'est le controle a faire au demarrage : sur
        l'installation de developpement, la config porte `castKey: "2"`, qui
        n'est liee que sur UN des trois comptes ; sur le compte reellement
        joue, la touche de lancer est une autre (mesure : 0 capture avec la
        mauvaise touche).
        """
        key = (cast_key or "").upper()
        per = self.keys_for(CAST_ACTION)
        account = self.active_account()
        ok_accounts = [a for a, k in per.items() if k == key]
        detected = self.cast_key_for(account) if account else None
        return (bool(ok_accounts) and len(ok_accounts) == len(self._accounts()),
                {"configured": key, "boundOn": sorted(ok_accounts),
                 "boundElsewhere": {a: k for a, k in per.items() if k != key},
                 "activeAccount": account,
                 "detectedForActive": detected,
                 "configuredWorksOnActive": bool(account and detected == key)})

    def resolve_cast_key(self, configured=None, account=None):
        """Touche de lancer a utiliser, resolue depuis le compte en jeu.

        Priorite :
          1. la touche CONFIGUREE, si elle est bien liee sur le compte joue ;
          2. sinon, la touche liee a l'action de peche sur le compte joue ;
          3. sinon, la touche configuree (au cas ou l'action ne soit pas
             identifiee) — le bot n'invente jamais de raccourci.
        Retourne (touche, diagnostic).
        """
        account = account or self.active_account()
        detected = self.cast_key_for(account) if account else None
        configured = (configured or "").upper() or None
        diag = {"activeAccount": account, "detectedForActive": detected,
                "configured": configured}
        # 2026-10-21 (P0.35) : la touche CONFIGUREE est prioritaire.
        # L'auto-detection matche des ACTIONBUTTON (le cache du compte
        # porte 'bind X ACTIONBUTTON10') : c'est un faux positif qui
        # remplacait la vraie touche et empechait toute peche
        # (CAST_UNCONFIRMED en boucle).  Le cache du PERSONNAGE ne
        # contient aucun bind de peche (la peche passe par une macro) :
        # seule la touche configuree par le joueur est donc fiable.
        if configured:
            if detected and configured != detected:
                diag["reason"] = ("touche configuree %s conservee (la touche "
                                  "auto-detectee %s vient d'un ACTIONBUTTON : "
                                  "faux positif de detection)"
                                  % (configured, detected))
            else:
                diag["reason"] = "touche configuree utilisee"
            return configured, diag
        diag["reason"] = "aucune touche de lancer resolue"
        return detected, diag

    def action_bar_keys(self):
        """Touches liees a des boutons de barre d'actions, par compte."""
        out = {}
        for a, binds in self.per_account.items():
            out[a] = sorted((k, v) for k, v in binds.items() if ACTION_BAR.match(v))
        return out

    def movement_available(self):
        """Deplacements pilotables au CLAVIER ? (et donc utilisables ou non)."""
        return {
            "forward": self.forward(),
            "backward": self.backward(),
            "strafe_left": self.strafe_left(),
            "strafe_right": self.strafe_right(),
        }

    # -------------------------------------------------------------- rapport
    def report(self):
        """Carte complete, prete a etre journalisee ou documentee."""
        moves = self.movement_available()
        return {
            "readAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "comptes": self._accounts(),
            "erreurs_lecture": self.errors,
            "clavier": {
                "deplacement_par_compte": {
                    "avant": self.keys_for("FORWARD"),
                    "arriere": self.keys_for("BACKWARD"),
                    "strafe_gauche": self.keys_for("STRAFELEFT"),
                    "strafe_droite": self.keys_for("STRAFERIGHT"),
                },
                "deplacement_commun": {k: (v or "NON LIE")
                                       for k, v in self.movement_available().items()},
                "auto_run_par_compte": self.keys_for("TOGGLEAUTORUN"),
                "auto_run_commun": self.auto_run() or "NON LIE / DIFFERE PAR COMPTE",
                "cast_par_compte": self.cast_keys(),
                "cast_commun": self.cast_key_bound() or "NON LIE / DIFFERE PAR COMPTE",
                "barres_actions_par_compte": self.action_bar_keys(),
            },
            "camera": {
                "source": "SOURIS (deplacement souris) - aucun binding ne la pilote",
                "zoom": {"avant": self.key_for("CAMERAZOOMIN") or "NON LIE",
                         "arriere": self.key_for("CAMERAZOOMOUT") or "NON LIE"},
                "carte": self.key_for("TOGGLEWORLDMAP") or "NON LIE",
            },
            "orientation": {
                "camera": "orientation de la vue, pilotee par la souris",
                "personnage": "suit la camera lors d'un deplacement ; c'est la "
                              "direction reelle de marche",
            },
            "cycle_de_peche": {
                "touche_lancer": self.cast_keys(),
                "clic_flotteur": "clic droit sur le flotteur (souris, via WM_*)",
                "popup_bouton": "clic GAUCHE sur le popup (les boutons WoW ne "
                                "reagissent qu'au clic gauche)",
                "appat": "touche liee a une barre d'actions",
            },
            "deplacement_reel": {
                "mecanisme": "auto-run (TOGGLEAUTORUN) + clic droit sur le sol",
                "raison": "le client ignore les touches de direction injectees "
                          "(raw input) : mesure = 0,00 px de deplacement",
                "touche_auto_run": self.auto_run() or "NON LIE",
            },
            "arret_et_reprise": {
                "arret_urgence": "F9 (lu par le bot, touche du systeme)",
                "libere_touches": "WM_KEYUP systematique apres chaque envoi",
                "perte_focus": "etat PAUSED, timers geles, reexamen a la reprise",
                "activite_humaine": "suspension des entrees (jamais de vol de souris)",
                "blocage": "detection par odometre + recuperations bornees",
                "mort": "detection de vie (is_ingame_frame) + reprise",
                "popup": "confirmation du popup avant de poursuivre",
            },
            "reservees_par_le_bot": BOT_RESERVED,
        }


def load_controls(root=None):
    return Controls.load(root)


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(load_controls().report(), indent=2, ensure_ascii=False))
