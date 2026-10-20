#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""casino_engine — coeur metier du casino, porte depuis l'addon Lua.

ORIGINE ET PORTABILITE
======================
Ce module est une transcription FIDELE des fonctions casino reelles de
`runtime-addon/Casinobabe/Casinobabe.lua`. Aucune regle n'a ete inventee :

| Source Lua (ligne)              | Port Python                       |
|---------------------------------|-----------------------------------|
| `CB.GameRules` (L285-400)       | `GAME_RULES`                      |
| `GetGameRule` (L1907)           | `get_game_rule`                   |
| `ComputePayout` (L1914)         | `compute_payout`                  |
| `ValidateStake` (L1933)         | `validate_stake`                  |
| `ValidateRoll` (L1956)          | `validate_roll`                   |
| `ValidateRouletteColor` (L1966)| `validate_roulette_color`         |
| `ValidateDiceChoice` (L1977)    | `validate_dice_choice`            |
| `DEALER_STATES` (L2075)         | `STATES`                          |
| `DEALER_TRANSITIONS` (L2095)    | `TRANSITIONS`                     |
| `DEALER_ERRORS` (L2115)         | `ERRORS`                          |
| `DEALER_CORE_TIMEOUTS` (L4002)  | `CORE_TIMEOUTS`                   |
| `DealerValidateTransition` L2313| `validate_transition`              |
| `DealerGame` (L2864)            | `DealerSession.game`              |
| `DealerStake` (L2900)           | `DealerSession.stake`             |
| `DealerRecord` (L2939)          | `DealerSession.record_roll`       |
| `DealerResolve` (L2988)         | `DealerSession.resolve`           |
| `DealerCoreCheckTimeouts`       | `SessionStore.check_timeouts`     |
| `DealerCoreCheckIntegrity`      | `SessionStore.check_integrity`    |

CE QUI RESTE DANS L'ADDON (deliberement)
=========================================
`SendChatMessage`, `InitiateTrade`, `InviteUnit`, `IsInGroup` et la lecture des
messages systeme exigent le client WoW. Ils ne sont PAS simules ici : le moteur
les EXPOSE comme `actions en attente` (voir `DealerSession.pending_actions`) que
l'addon, seul legitimement capable de les executer, viendra recuperer. Aucune
fausse interaction n'est simulee.

AUCUNE REGLE INVENTEE
=====================
Si une entree necessaire n'est pas definie dans le Lua (montant de la mise
"house total" absent, annonce non definie...), le moteur REFUSE avec un code
d'erreur explicite au lieu d'inventer un comportement.

ANOMALIE REPEREE ET CONSIGNEE
==============================
`Casinobabe.lua` L3048 ecrit :
    elseif session.game == "lucky7" and (session.roll % 10) == 6 or (session.roll % 10) == 8 then
La precedence Lua fait que cela se lit
    (game == "lucky7" and roll%10 == 6) or (roll%10 == 8)
donc le near-miss "lucky7" se declenche sur n'importe quel jeu dont le lancer
finit par 8. C'est un bug probable du code existant. On ne le corrige pas
silencieusement (ce serait inventer une regle) : `near_miss` reproduit le
comportement EXACT, `near_miss_intended` expose la lecture evidente, et un test
documente l'ecart.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "GAME_RULES", "STATES", "TRANSITIONS", "ERRORS", "CORE_TIMEOUTS",
    "get_game_rule", "game_keys", "compute_payout", "validate_stake",
    "validate_roll", "validate_roulette_color", "validate_dice_choice",
    "validate_transition", "is_terminal", "allowed_transitions",
    "near_miss", "near_miss_intended", "Session", "SessionStore",
    "PERSIST_VERSION",
]

PERSIST_VERSION = 1


# ===========================================================================
# 1. Regles de jeu  (port de CB.GameRules, L285-400)
# ===========================================================================

def _resolve_normal(roll: float) -> int:
    if roll == 100:
        return 3          # jackpot x3
    if roll >= 59:
        return 2          # win x2
    return 0              # loss


def _resolve_high(roll: float) -> int:
    if roll == 100:
        return 4          # jackpot x4
    if roll >= 76:
        return 3          # win x3
    return 0


def _resolve_lucky7(roll: float) -> int:
    if (roll % 10) == 7:
        return 7          # ends in 7 = x7
    return 0


def _resolve_roulette(roll: float, color: Optional[str] = None) -> int:
    c = (color or "").lower()
    if c == "red":
        return 2 if (1 <= roll <= 45) else 0
    if c == "green":
        return 5 if (46 <= roll <= 55) else 0
    if c == "black":
        return 2 if (56 <= roll <= 100) else 0
    return 0


def _resolve_blackjack(player_total: float, house_total: float) -> int:
    # 1 = joueur gagne (x2), 2 = push (mise rendue), 0 = perte
    if player_total > 100:
        return 0          # bust joueur
    if house_total > 100:
        return 1          # bust maison
    if player_total > house_total:
        return 1
    if player_total == house_total:
        return 2          # push
    return 0


def _resolve_dice(choice: Optional[str], house_rolls: Optional[List[int]]) -> int:
    rolls = house_rolls or [0, 0]
    total = (rolls[0] or 0) + (rolls[1] or 0)
    c = (choice or "").lower()
    if c == "over":
        return 2 if (8 <= total <= 12) else 0
    if c == "under":
        return 2 if (2 <= total <= 6) else 0
    if c == "seven":
        return 4 if total == 7 else 0
    return 0


@dataclass(frozen=True)
class GameRule:
    key: str
    name: str
    roll_min: int
    roll_max: int
    stake_min: int
    stake_max: int
    description: str
    resolve: Callable[..., int]
    colors: Tuple[str, ...] = ()
    choices: Tuple[str, ...] = ()
    single_roll: bool = True       # resolution automatique (DealerRecord L2966)

    @property
    def needs_extra(self) -> bool:
        return bool(self.colors or self.choices)


GAME_RULES: Dict[str, GameRule] = {
    "normal": GameRule(
        key="normal", name="Normal", roll_min=1, roll_max=100,
        stake_min=1, stake_max=1000, description="Roll 1-100. 59-99 = x2, 100 = x3 jackpot.",
        resolve=_resolve_normal, single_roll=True),
    "high": GameRule(
        key="high", name="High Risk", roll_min=1, roll_max=100,
        stake_min=1, stake_max=1000,
        description="Roll 1-100. 76-99 = x3, 100 = x4 jackpot. (1-75 lose)",
        resolve=_resolve_high, single_roll=True),
    "lucky7": GameRule(
        key="lucky7", name="Lucky 7", roll_min=1, roll_max=100,
        stake_min=1, stake_max=1000,
        description="Roll 1-100. Roll ending in 7 (7,17,27...97) = x7!",
        resolve=_resolve_lucky7, single_roll=True),
    "roulette": GameRule(
        key="roulette", name="Roulette", roll_min=1, roll_max=100,
        stake_min=1, stake_max=1000,
        description="Pick color, then /roll 1-100. Red 1-45 = x2, Green 46-55 = x5, Black 56-100 = x2.",
        resolve=_resolve_roulette, colors=("red", "green", "black"), single_roll=True),
    "blackjack": GameRule(
        key="blackjack", name="Blackjack", roll_min=1, roll_max=100,
        stake_min=1, stake_max=1000,
        description="Get closest to 100 without busting. Hit to draw, Stand to stop. Beat the house.",
        resolve=_resolve_blackjack, single_roll=False),
    "dice": GameRule(
        key="dice", name="Dice", roll_min=2, roll_max=12,
        stake_min=1, stake_max=1000,
        description="House rolls 2d6. Over (8-12) = x2, Under (2-6) = x2, exactly 7 = x4.",
        resolve=_resolve_dice, choices=("over", "under", "seven"), single_roll=False),
}


def game_keys() -> List[str]:
    return sorted(GAME_RULES)


def get_game_rule(key: Optional[str]) -> Optional[GameRule]:
    """Port de GetGameRule (L1907) : la cle est normalisee en minuscules."""
    if not key:
        return None
    return GAME_RULES.get(str(key).lower())


# ===========================================================================
# 2. Paiement et validations  (port L1913-1985)
# ===========================================================================

def compute_payout(game_key: str, stake: float, *args: Any) -> Tuple[Optional[float], Optional[str]]:
    """Port de ComputePayout (L1914), avec un CORRECTIF ARGUMENTAIRE.

    Convention du code source : le retour est le gain NET.
      * multiplicateur 0            -> perte, on retourne -stake
      * blackjack mult 1            -> gain, on retourne +stake
      * blackjack mult 2            -> push, on retourne 0 (mise rendue)
      * sinon                       -> net = stake * (mult - 1)

    BUG CORRIGE DANS L'ADDON (Casinobabe.lua L3027-3031)
    ---------------------------------------------------
    Le source appelle :
        ComputePayout(game, stake,
            game=="roulette" and gameData.color or ... or session.roll)
    soit, pour la roulette, UN SEUL argument : la couleur. Or le resolver est
    `resolve(roll, color)`. L'appel revient donc avec
        resolve("red")  ->  roll = "red", color = nil
                        ->  c = ""  ->  aucune branche  ->  return 0
    Consequence dans l'addon actuel : **une victoire roulette est toujours
    payee -stake** (perte), alors que `session.multiplier` vaut bien 2 et que
    `session.result` vaut "WIN". Le joueur est annonce gagnant et paye une
    perte.

    Ici le paiement recoit EXACTEMENT les memes arguments que le calcul du
    multiplicateur, fait trois lignes plus haut dans `resolve()`. Ce n'est pas
    une regle inventee : c'est la coherence interne que le code s'etait deja
    donnee, et que le bug d'appel violait.

    `compute_payout_lua_faithful()` reproduit le comportement d'origine pour
    que l'ecart soit demontrable et non declaratif.

    Contrat UNIFORME : toujours un couple (valeur, erreur).
    """
    rule = get_game_rule(game_key)
    if rule is None:
        return None, "ERR_INVALID_GAME"
    mult = rule.resolve(*args)
    if mult is None:
        return None, "ERR_RESOLVE_FAILED"
    if mult == 0:
        return -stake, None
    if game_key == "blackjack":
        if mult == 1:
            return stake, None      # blackjack gagnant = x2 (net +stake)
        if mult == 2:
            return 0, None          # push = mise rendue (net 0)
    return stake * (mult - 1), None


def compute_payout_lua_faithful(game_key: str, stake: float,
                                *args: Any) -> Tuple[Optional[float], Optional[str]]:
    """Reproduit l'appel fautif de l'addon pour la roulette.

    Utile uniquement pour prouver l'ecart. Ne JAMAIS utiliser en production.
    """
    if game_key == "roulette" and args:
        # Le Lua ne passe que la couleur ; on respecte ce defaut.
        rule = get_game_rule("roulette")
        mult = rule.resolve(args[0]) if rule else 0
        if mult == 0:
            return -stake, None
        return stake * (mult - 1), None
    return compute_payout(game_key, stake, *args)


def validate_stake(game_key: Optional[str], amount: Any) -> Tuple[bool, Optional[str]]:
    """Port de ValidateStake (L1933). gameKey peut etre None (mise armee avant
    le choix du jeu) : on valide alors contre l'union des bornes."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return False, "ERR_BAD_STAKE"
    lo: Optional[float] = None
    hi: Optional[float] = None
    if game_key:
        rule = get_game_rule(game_key)
        if rule is None:
            return False, "ERR_INVALID_GAME"
        lo, hi = rule.stake_min, rule.stake_max
    else:
        for r in GAME_RULES.values():
            lo = r.stake_min if lo is None else min(lo, r.stake_min)
            hi = r.stake_max if hi is None else max(hi, r.stake_max)
        if lo is None:
            return False, "ERR_INVALID_GAME"
    if amount < lo or amount > hi:
        return False, "ERR_BAD_STAKE"
    return True, None


def validate_roll(game_key: str, roll: Any) -> Tuple[bool, Optional[str]]:
    """Port de ValidateRoll (L1956)."""
    rule = get_game_rule(game_key)
    if rule is None:
        return False, "ERR_INVALID_GAME"
    if not isinstance(roll, (int, float)) or isinstance(roll, bool):
        return False, "ERR_ROLL_OUT_OF_RANGE"
    if roll < rule.roll_min or roll > rule.roll_max:
        return False, "ERR_ROLL_OUT_OF_RANGE"
    return True, None


def validate_roulette_color(color: Any) -> bool:
    """Port de ValidateRouletteColor (L1966)."""
    return (color or "").lower() in GAME_RULES["roulette"].colors


def validate_dice_choice(choice: Any) -> bool:
    """Port de ValidateDiceChoice (L1977)."""
    return (choice or "").lower() in GAME_RULES["dice"].choices


# ===========================================================================
# 3. Machine a etats du dealer  (port L2075-2130)
# ===========================================================================

STATES: Dict[str, str] = {
    "NEW": "NEW",
    "CONTACTED": "CONTACTED",
    "INVITED": "INVITED",
    "TRADE_PENDING": "TRADE_PENDING",
    "TRADE_VERIFIED": "TRADE_VERIFIED",
    "STAKE_CONFIRMED": "STAKE_CONFIRMED",
    "GROUPED": "GROUPED",
    "GAME_SELECTED": "GAME_SELECTED",
    "ROLLING": "ROLLING",
    "RESOLVED": "RESOLVED",
    "PAYOUT_PENDING": "PAYOUT_PENDING",
    "PAID": "PAID",
    "CLOSED": "CLOSED",
    "ERROR": "ERROR",
    "RECOVERY_REQUIRED": "RECOVERY_REQUIRED",
    "CANCELLED": "CANCELLED",
}

#: Port de DEALER_TRANSITIONS (L2095). CLOSED et CANCELLED sont terminaux.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    STATES["NEW"]: (STATES["CONTACTED"], STATES["CANCELLED"]),
    STATES["CONTACTED"]: (STATES["INVITED"], STATES["GAME_SELECTED"], STATES["CANCELLED"]),
    STATES["INVITED"]: (STATES["TRADE_PENDING"], STATES["CANCELLED"]),
    STATES["TRADE_PENDING"]: (STATES["TRADE_VERIFIED"], STATES["CANCELLED"], STATES["ERROR"]),
    STATES["TRADE_VERIFIED"]: (STATES["STAKE_CONFIRMED"], STATES["CANCELLED"]),
    STATES["STAKE_CONFIRMED"]: (STATES["GROUPED"], STATES["GAME_SELECTED"], STATES["CANCELLED"]),
    STATES["GROUPED"]: (STATES["GAME_SELECTED"], STATES["CANCELLED"]),
    STATES["GAME_SELECTED"]: (STATES["TRADE_PENDING"], STATES["ROLLING"], STATES["CANCELLED"]),
    STATES["ROLLING"]: (STATES["RESOLVED"], STATES["CANCELLED"]),
    STATES["RESOLVED"]: (STATES["PAYOUT_PENDING"], STATES["CLOSED"], STATES["CANCELLED"]),
    STATES["PAYOUT_PENDING"]: (STATES["PAID"], STATES["CANCELLED"], STATES["ERROR"]),
    STATES["PAID"]: (STATES["CLOSED"], STATES["CANCELLED"]),
    STATES["CLOSED"]: (),
    STATES["ERROR"]: (STATES["RECOVERY_REQUIRED"], STATES["CANCELLED"]),
    STATES["RECOVERY_REQUIRED"]: (STATES["CANCELLED"],),
    STATES["CANCELLED"]: (),
}

#: Port de DEALER_ERRORS (L2115).
ERRORS: Dict[str, str] = {
    "ERR_NO_PLAYER": "ERR_NO_PLAYER",
    "ERR_UNKNOWN_SESSION": "ERR_UNKNOWN_SESSION",
    "ERR_DUPLICATE_SESSION": "ERR_DUPLICATE_SESSION",
    "ERR_BAD_STAKE": "ERR_BAD_STAKE",
    "ERR_WRONG_TRADE_TARGET": "ERR_WRONG_TRADE_TARGET",
    "ERR_TRADE_MISMATCH": "ERR_TRADE_MISMATCH",
    "ERR_TRADE_CANCELLED": "ERR_TRADE_CANCELLED",
    "ERR_TRADE_TIMEOUT": "ERR_TRADE_TIMEOUT",
    "ERR_NOT_GROUPED": "ERR_NOT_GROUPED",
    "ERR_INVALID_GAME": "ERR_INVALID_GAME",
    "ERR_ROLL_OUT_OF_RANGE": "ERR_ROLL_OUT_OF_RANGE",
    "ERR_DUPLICATE_ROLL": "ERR_DUPLICATE_ROLL",
    "ERR_INVALID_STATE": "ERR_INVALID_STATE",
    "ERR_PAYOUT_TOO_EARLY": "ERR_PAYOUT_TOO_EARLY",
    "ERR_RESOLVE_FAILED": "ERR_RESOLVE_FAILED",
}

#: Port de DEALER_CORE_TIMEOUTS (L4002). RECOVERY_REQUIRED est volontairement
#: absent : il demande un choix humain apres rechargement (cf. KNOWN_LIMITATIONS).
CORE_TIMEOUTS: Dict[str, float] = {
    STATES["CONTACTED"]: 300,
    STATES["INVITED"]: 180,
    STATES["TRADE_PENDING"]: 180,
    STATES["TRADE_VERIFIED"]: 180,
    STATES["STAKE_CONFIRMED"]: 600,
    STATES["GROUPED"]: 600,
    STATES["GAME_SELECTED"]: 600,
    STATES["ROLLING"]: 600,
    STATES["RESOLVED"]: 600,
    STATES["PAYOUT_PENDING"]: 300,
    STATES["ERROR"]: 60,
}

#: Etats considers comme terminaux par DealerCoreActiveSession (L3910).
_TERMINAL = (STATES["CLOSED"], STATES["CANCELLED"])


def is_terminal(state: str) -> bool:
    return state in _TERMINAL


def allowed_transitions(state: str) -> Tuple[str, ...]:
    return TRANSITIONS.get(state, ())


def validate_transition(session_state: str, new_state: str) -> Tuple[bool, Optional[str]]:
    """Port de DealerValidateTransition (L2313)."""
    allowed = TRANSITIONS.get(session_state)
    if allowed is None:
        return False, "ERR_INVALID_STATE"
    if new_state not in allowed:
        return False, "ERR_INVALID_STATE"
    return True, None


# ===========================================================================
# 4. Near-miss  (port L3041-3051, anomalie de precedence consignee)
# ===========================================================================

def near_miss(game: Optional[str], roll: Optional[float],
              result: Optional[str]) -> bool:
    """Reproduction EXACTE du Lua L3042-3051, anomalie de precedence comprise.

    Le code source est une chaine `if / elseif` :

        if     game == "normal"  and roll == 58 then ...
        elseif game == "high"    and roll == 75 then ...
        elseif game == "lucky7"  and roll % 10 == 6 or roll % 10 == 8 then ...

    En Lua comme en Python, `and` lie plus fort que `or`, ET l'echelle est
    associative a gauche. Le troisieme membre se lit donc :
        (game == "lucky7" and roll % 10 == 6) or (roll % 10 == 8)
    Le second terme N'EST PAS conditionne au jeu : dans le code d'origine,
    un lancer finissant par 8 declenche un near-miss dans TOUS les jeux
    (normal, high, roulette...). C'est très probablement un bug, mais on ne
    le corrige pas : corriger serait inventer une regle.

    Conséquence à connaître : cette fonction est volontairement plus
    permissive que `near_miss_intended`, qui expose la lecture évidente.
    """
    if result != "LOSS" or roll is None:
        return False
    r = int(roll)
    if game == "normal" and r == 58:
        return True
    if game == "high" and r == 75:
        return True
    if (game == "lucky7" and (r % 10) == 6) or (r % 10) == 8:
        return True
    return False


def near_miss_intended(game: Optional[str], roll: Optional[float],
                       result: Optional[str]) -> bool:
    """Lecture evidentement voulue : le cas « finir par 8 » ne concerne que
    lucky7. Sert uniquement a MESURER l'ecart avec le comportement reel."""
    if result != "LOSS" or roll is None:
        return False
    r = int(roll)
    if game == "normal":
        return r == 58
    if game == "high":
        return r == 75
    if game == "lucky7":
        return (r % 10) in (6, 8)
    return False


# ===========================================================================
# 5. Session  (port des fonctions Dealer* L2352-3096)
# ===========================================================================

@dataclass
class Session:
    """Une session de jeu. Les champs reprennent 1 pour 1 la table `session`
    construite par DealerCreateSession (L2361)."""
    player: str
    session_id: str = ""
    state: str = STATES["NEW"]
    state_time: float = 0.0
    created_at: float = 0.0
    round_counter: int = 0
    stake: float = 0.0
    game: Optional[str] = None
    game_data: Dict[str, Any] = field(default_factory=dict)
    trade: Dict[str, Any] = field(default_factory=lambda: {
        "armed": False, "expected_amount": 0, "target": None,
        "verified": False, "completed": False, "sub": None, "is_payout": False,
    })
    roll: Optional[float] = None
    roll_time: Optional[float] = None
    result: Optional[str] = None
    multiplier: Optional[int] = None
    payout: float = 0.0
    payout_completed: bool = False
    error: Optional[str] = None
    recovery_required: bool = False
    last_transition: Optional[str] = None
    last_reason: Optional[str] = None
    audit: List[str] = field(default_factory=list)
    #: Actions que le moteur NE PEUT PAS executer seul (SendChatMessage,
    #: InitiateTrade, InviteUnit...). L'addon les recoit et les joue.
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)

    # -- transition ---------------------------------------------------
    def set_state(self, new_state: str, reason: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Port de DealerSetState (L2334) sans la partie UI."""
        ok, err = validate_transition(self.state, new_state)
        if not ok:
            self.audit.append("STATE_ERROR invalid %s -> %s (%s)"
                              % (self.state, new_state, err))
            return False, err
        old = self.state
        self.state = new_state
        self.state_time = time.time()
        self.last_transition = "%s -> %s" % (old, new_state)
        if reason:
            self.last_reason = reason
        self.audit.append("STATE %s -> %s%s"
                          % (old, new_state, (" (%s)" % reason) if reason else ""))
        return True, None

    def log(self, event: str, detail: str = "") -> None:
        self.audit.append("%s %s" % (event, detail) if detail else event)
        if len(self.audit) > 200:          # DEALER_MAX_LOG = 200 (L2072)
            del self.audit[:-200]

    def queue_action(self, kind: str, **payload: Any) -> Dict[str, Any]:
        """Enregistre une action reservee au client WoW. Ne l'execute pas."""
        action = {"kind": kind, "at": time.time()}
        action.update(payload)
        self.pending_actions.append(action)
        return action

    def drain_actions(self) -> List[Dict[str, Any]]:
        out, self.pending_actions = self.pending_actions, []
        return out

    def age(self) -> float:
        return max(0.0, time.time() - (self.state_time or self.created_at or time.time()))

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("pending_actions", None)
        d["pending_action_count"] = len(self.pending_actions)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ===========================================================================
# 6. Store de sessions  (port des fonctions DealerCore* L3909-4223)
# ===========================================================================

class SessionStore:
    """DealerCore* : une seule session active a la fois (L3909)."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.sessions: Dict[str, Session] = {}
        self.counter = 0
        self.log: List[str] = []
        self._lock = threading.RLock()

    # -- acces ---------------------------------------------------------
    def active(self) -> Optional[Session]:
        """Port de DealerCoreActiveSession (L3910)."""
        with self._lock:
            for s in self.sessions.values():
                if s.state not in _TERMINAL:
                    return s
        return None

    def get(self, player: str) -> Optional[Session]:
        with self._lock:
            return self.sessions.get(player)

    def owns(self, player: Optional[str]) -> Tuple[Optional[Session], Optional[str]]:
        """Port de DealerCoreOwnsSession (L3957)."""
        s = self.active()
        if s and player is not None and s.player == player:
            return s, None
        if s:
            return None, ERRORS["ERR_DUPLICATE_SESSION"]
        return None, ERRORS["ERR_UNKNOWN_SESSION"]

    def next_session_id(self) -> str:
        self.counter += 1
        return "S%04d-%d" % (self.counter, int(time.time()))

    # -- cycle de vie --------------------------------------------------
    def create(self, player: Optional[str]) -> Tuple[Optional[Session], Optional[str]]:
        """Port de DealerCoreCreateSession (L3946)."""
        with self._lock:
            if not self.enabled:
                return None, "Dealer mode disabled"
            if not player:
                return None, ERRORS["ERR_NO_PLAYER"]
            if self.active() is not None:
                return None, ERRORS["ERR_DUPLICATE_SESSION"]
            existing = self.sessions.get(player)
            if existing is not None and existing.state not in _TERMINAL:
                return None, ERRORS["ERR_DUPLICATE_SESSION"]
            now = time.time()
            s = Session(player=player, session_id=self.next_session_id(),
                        created_at=now, state_time=now)
            self.sessions[player] = s
            ok, err = s.set_state(STATES["CONTACTED"], "Player whispered JOIN")
            if not ok:
                return None, err
            s.log("SESSION", s.session_id)
            return s, None

    def game(self, player: str, game_key: str, extra: Optional[str] = None
             ) -> Tuple[bool, Optional[str]]:
        """Port de DealerGame (L2864)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state not in (STATES["CONTACTED"], STATES["STAKE_CONFIRMED"],
                            STATES["GROUPED"], STATES["GAME_SELECTED"]):
            return False, ERRORS["ERR_INVALID_STATE"]
        rule = get_game_rule(game_key)
        if rule is None:
            return False, ERRORS["ERR_INVALID_GAME"]
        s.game = rule.key
        s.game_data = {}
        if rule.key == "roulette" and extra is not None:
            color = str(extra).lower()
            if not validate_roulette_color(color):
                return False, "Invalid color for roulette"
            s.game_data["color"] = color
        elif rule.key == "dice" and extra is not None:
            choice = str(extra).lower()
            if not validate_dice_choice(choice):
                return False, "Invalid choice for dice"
            s.game_data["choice"] = choice
        elif rule.key == "dice":
            # Le Lua utilise defaut "over"/{1,1} au moment du RESOLVE (L3007).
            s.game_data["choice"] = "over"
            s.game_data["houseRolls"] = [1, 1]
        elif rule.key == "roulette":
            s.game_data["color"] = "red"          # defaut Lua L3003
        s.round_counter += 1
        ok, err = s.set_state(STATES["GAME_SELECTED"],
                              "Game: %s%s" % (rule.name, (" " + str(extra)) if extra else ""))
        if not ok:
            return False, err
        s.log("GAME_SELECTED", "%s%s" % (rule.name, (" " + str(extra)) if extra else ""))
        s.queue_action("REACTION", event="GAME_SELECTED",
                       player=player, game=s.game, extra=extra)
        return True, None

    def stake(self, player: str, amount: Any) -> Tuple[bool, Optional[str]]:
        """Port de DealerStake (L2900), SANS InitiateTrade."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state not in (STATES["GAME_SELECTED"], STATES["STAKE_CONFIRMED"],
                            STATES["TRADE_PENDING"], STATES["TRADE_VERIFIED"],
                            STATES["INVITED"]):
            return False, ERRORS["ERR_INVALID_STATE"]
        if isinstance(amount, str):
            digits = ""
            for ch in amount:
                if ch.isdigit() or ch == ".":
                    digits += ch
                elif digits:
                    break
            try:
                amount = float(digits)
            except ValueError:
                return False, ERRORS["ERR_BAD_STAKE"]
        ok, err = validate_stake(s.game, amount)
        if not ok:
            return False, err
        s.stake = amount
        s.trade.update({"armed": True, "expected_amount": amount, "target": player,
                        "verified": False, "completed": False, "is_payout": False})
        ok, err = s.set_state(STATES["TRADE_PENDING"], "Stake armed: %sg" % amount)
        if not ok:
            return False, err
        s.log("STAKE_ARMED", "%sg" % amount)
        # InitiateTrade n'est jouable que par le client WoW : on le signale.
        s.queue_action("INITIATE_TRADE", player=player, amount=amount, is_payout=False)
        s.queue_action("REACTION", event="TRADE_OPEN", player=player,
                       amount=amount, is_payout=False)
        return True, None

    def record_roll(self, player: str, roll: Any) -> Tuple[bool, Optional[str]]:
        """Port de DealerRecord (L2939), resolution automatique comprise."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state not in (STATES["ROLLING"], STATES["GAME_SELECTED"]):
            return False, ERRORS["ERR_INVALID_STATE"]
        try:
            roll = float(roll)
        except (TypeError, ValueError):
            return False, "Invalid roll value"
        if roll != int(roll):
            return False, ERRORS["ERR_ROLL_OUT_OF_RANGE"]
        roll = int(roll)
        ok, err = validate_roll(s.game, roll)
        if not ok:
            return False, err
        if s.roll is not None:
            return False, ERRORS["ERR_DUPLICATE_ROLL"]
        s.roll = roll
        s.roll_time = time.time()
        ok, err = s.set_state(STATES["ROLLING"], "Roll recorded: %d" % roll)
        if not ok:
            return False, err
        s.log("ROLL", "%d game=%s" % (roll, s.game))
        s.queue_action("REACTION", event="ROLL_RECEIVED",
                       player=player, roll=roll, game=s.game)

        # AUTO FLOW (L2966) : les jeux a tirage unique se resolvent seuls.
        rule = get_game_rule(s.game)
        if rule is not None and rule.single_roll:
            ok, err = self.resolve(player)
            if ok:
                s.log("AUTO_RESOLVE", "result=%s mult=x%s payout=%sg"
                      % (s.result, s.multiplier, s.payout))
                s.queue_action("ROUND_RESULT", player=player, result=s.result,
                               roll=roll, payout=s.payout, mult=s.multiplier)
                if s.result == "WIN":
                    ok2, err2 = self.arm_payout(player)
                    if ok2:
                        s.log("AUTO_PAYOUT", "armed %sg" % s.payout)
                    else:
                        s.log("AUTO_PAYOUT_FAILED", str(err2))
            else:
                s.log("AUTO_RESOLVE_FAILED", str(err))
        return True, None

    def resolve(self, player: str) -> Tuple[bool, Optional[str]]:
        """Port de DealerResolve (L2988)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state not in (STATES["ROLLING"], STATES["GAME_SELECTED"]):
            return False, ERRORS["ERR_INVALID_STATE"]
        if s.roll is None:
            return False, "No roll recorded"
        if not s.game:
            return False, ERRORS["ERR_INVALID_GAME"]
        if not s.stake or s.stake <= 0:
            return False, ERRORS["ERR_BAD_STAKE"]
        rule = get_game_rule(s.game)
        if rule is None:
            return False, ERRORS["ERR_INVALID_GAME"]

        if s.game == "roulette":
            color = s.game_data.get("color", "red")
            mult = rule.resolve(s.roll, color)
            detail = "color=%s roll=%d" % (color, s.roll)
            # Mêmes arguments que le multiplicateur ci-dessus : c'est le
            # correctif du bug d'appel de l'addon (voir compute_payout).
            payout_args = (s.roll, color)
        elif s.game == "dice":
            choice = s.game_data.get("choice", "over")
            house = s.game_data.get("houseRolls", [1, 1])
            mult = rule.resolve(choice, house)
            detail = "choice=%s house=%d" % (choice, sum(house))
            payout_args = (choice, house)
        elif s.game == "blackjack":
            house_total = s.game_data.get("houseTotal", 0)
            if not house_total:
                return False, "House total not set"
            mult = rule.resolve(s.roll, house_total)
            detail = "player=%d house=%d" % (s.roll, house_total)
            payout_args = (s.roll, house_total)
        else:
            mult = rule.resolve(s.roll)
            detail = "roll=%d" % s.roll
            payout_args = (s.roll,)

        s.multiplier = mult
        s.result = "WIN" if (mult and mult > 0) else "LOSS"
        if mult == 2 and s.game == "blackjack":
            s.result = "PUSH"

        payout, perr = compute_payout(s.game, s.stake, *payout_args)
        if perr:
            return False, perr
        s.payout = payout or 0
        ok, err = s.set_state(STATES["RESOLVED"],
                              "Result: %s (x%s)" % (s.result, mult or 0))
        if not ok:
            return False, err
        s.log("RESOLVED", "%s -> %s payout=%sg" % (detail, s.result, s.payout))

        event = "LOSS"
        if s.result == "PUSH":
            event = "LOSS"
        elif mult is not None and mult >= 7:
            event = "JACKPOT"
        elif mult is not None and mult >= 4:
            event = "BIG_WIN"
        elif s.result == "WIN":
            event = "WIN"
        elif near_miss(s.game, s.roll, s.result):
            event = "NEAR_MISS"
        s.queue_action("REACTION", event=event, player=player, roll=s.roll,
                       game=s.game, payout=s.payout, mult=mult)
        return True, None

    def confirm_trade(self, player: str, player_money: int, target_money: int
                      ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Port de DealerOnTradeAccept (L3733), SANS les appels WoW.

        `GetPlayerTradeMoney()` / `GetTargetTradeMoney()` exigent le client :
        les montants en CUIVRE sont donc fournis en parametre par l'addon. Le
        moteur ne les invente jamais.

        Retourne (ok, code_verification, message) :
          * "VERIFIED"  -> TRADE_VERIFIED (mise) ou PAID (paiement)
          * "MISMATCH"  -> ERROR
        """
        s, err = self.owns(player)
        if s is None:
            return False, "ERR_UNKNOWN_SESSION", str(err)
        if not s.trade.get("armed") or s.trade.get("target") != player:
            return False, "NOT_ARMED", "trade non armee ou mauvais destinataire"
        s.trade["sub"] = "VALIDATING"
        expected = s.trade.get("expected_amount", 0) * 10000   # copper
        if s.trade.get("is_payout"):
            # NOUS payons : on verifie ce que NOUS mettons.
            verified = (player_money == expected)
            s.log("PAYOUT_VERIFIED" if verified else "PAYOUT_MISMATCH",
                  "attendu %scu obtenu %scu" % (expected, player_money))
        else:
            # Le JOUEUR paye la mise : on verifie ce que LUI met.
            verified = (target_money == expected)
            s.log("STAKE_VERIFIED" if verified else "STAKE_MISMATCH",
                  "attendu %scu obtenu %scu" % (expected, target_money))
        s.trade["verified"] = verified
        if verified:
            s.trade["sub"] = "ACCEPTED"
            target_state = (STATES["PAID"] if s.trade.get("is_payout")
                            else STATES["TRADE_VERIFIED"])
            ok, err = s.set_state(target_state,
                                  "Payout verified" if s.trade.get("is_payout")
                                  else "Stake verified")
            if not ok:
                return False, "ERR_INVALID_STATE", str(err)
            s.queue_action("REACTION", event="TRADE_VERIFIED", player=player,
                           is_payout=bool(s.trade.get("is_payout")),
                           amount=s.trade.get("expected_amount"))
            return True, "VERIFIED", target_state
        s.trade["sub"] = "MISMATCH"
        s.error = ERRORS["ERR_TRADE_MISMATCH"]
        s.set_state(STATES["ERROR"], "Trade mismatch")
        s.queue_action("REACTION", event="TRADE_MISMATCH", player=player)
        return False, "MISMATCH", ERRORS["ERR_TRADE_MISMATCH"]

    def mark_stake_confirmed(self, player: str) -> Tuple[bool, Optional[str]]:
        """TRADE_VERIFIED -> STAKE_CONFIRMED.

        Dans le code source cette transition est declenchee par l'evenement de
        groupe WoW (`DealerCoreOnGroupUpdate`). Le moteur expose donc une
        commande explicite plutot que de pretendre connaitre le groupe.
        """
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state != STATES["TRADE_VERIFIED"]:
            return False, ERRORS["ERR_INVALID_STATE"]
        ok, err = s.set_state(STATES["STAKE_CONFIRMED"], "stake confirmed")
        if not ok:
            return False, err
        s.log("STAKE_CONFIRMED", player)
        return True, None

    def mark_grouped(self, player: str) -> Tuple[bool, Optional[str]]:
        """STAKE_CONFIRMED -> GROUPED (idem : evenement de groupe WoW)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state != STATES["STAKE_CONFIRMED"]:
            return False, ERRORS["ERR_INVALID_STATE"]
        ok, err = s.set_state(STATES["GROUPED"], "grouped")
        if not ok:
            return False, err
        s.log("GROUPED", player)
        return True, None

    def set_house(self, player: str, house_total: int) -> Tuple[bool, Optional[str]]:
        """Total de la maison pour le blackjack. Non defini dans le Lua comme
        commande : expose parce que DealerResolve l'exige (L3015)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.game != "blackjack":
            return False, ERRORS["ERR_INVALID_GAME"]
        s.game_data["houseTotal"] = int(house_total)
        s.log("HOUSE_TOTAL", str(house_total))
        return True, None

    def set_house_rolls(self, player: str, die1: int, die2: int) -> Tuple[bool, Optional[str]]:
        """Des de la maison pour le jeu dice."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.game != "dice":
            return False, ERRORS["ERR_INVALID_GAME"]
        s.game_data["houseRolls"] = [int(die1), int(die2)]
        s.log("HOUSE_ROLLS", "%d+%d" % (die1, die2))
        return True, None

    def arm_payout(self, player: str) -> Tuple[bool, Optional[str]]:
        """Arme le paiement (DealerPayout L3099, partie logique)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state != STATES["RESOLVED"]:
            return False, ERRORS["ERR_PAYOUT_TOO_EARLY"]
        s.trade.update({"armed": True, "expected_amount": s.payout,
                        "target": player, "verified": False, "completed": False,
                        "is_payout": True})
        ok, err = s.set_state(STATES["PAYOUT_PENDING"], "Payout armed: %sg" % s.payout)
        if not ok:
            return False, err
        s.log("PAYOUT_ARMED", "%sg" % s.payout)
        s.queue_action("INITIATE_TRADE", player=player, amount=s.payout, is_payout=True)
        return True, None

    def mark_paid(self, player: str) -> Tuple[bool, Optional[str]]:
        s, err = self.owns(player)
        if s is None:
            return False, err
        if s.state != STATES["PAYOUT_PENDING"]:
            return False, ERRORS["ERR_INVALID_STATE"]
        s.trade["completed"] = True
        s.payout_completed = True
        ok, err = s.set_state(STATES["PAID"], "Payout completed")
        if not ok:
            return False, err
        s.log("PAID", "%sg" % s.payout)
        return True, None

    def close(self, player: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Port de DealerCoreCloseSession (L3965)."""
        s, err = self.owns(player)
        if s is None:
            return False, err
        ok, err = s.set_state(STATES["CLOSED"], "session closed")
        if not ok:
            return False, err
        s.log("CLOSED", s.session_id)
        self.sessions.pop(s.player, None)
        return True, None

    def recover(self, player: Optional[str] = None,
                reason: str = "recovered to IDLE") -> Tuple[bool, Optional[str]]:
        """Port de DealerCoreRecoverToIdle (L3973)."""
        with self._lock:
            s = self.get(player) if player else self.active()
            if s is None:
                return True, None
            s.trade.update({"armed": False, "sub": None, "verified": False})
            s.log("CORE_RECOVER", reason)
            if s.state not in _TERMINAL:
                s.set_state(STATES["CANCELLED"], reason)
            self.sessions.pop(s.player, None)
            return True, None

    # -- surveillance --------------------------------------------------
    def check_timeouts(self, now: Optional[float] = None) -> List[str]:
        """Port de DealerCoreCheckTimeouts : signale les sessions trop longues.
        NE recover PAS d'elle-meme : la decision reste humaine (L4000)."""
        now = now if now is not None else time.time()
        flagged: List[str] = []
        with self._lock:
            for s in self.sessions.values():
                if s.state in _TERMINAL:
                    continue
                limit = CORE_TIMEOUTS.get(s.state)
                if limit is None:
                    continue
                if (now - (s.state_time or s.created_at or now)) > limit:
                    flagged.append("%s en %s depuis %ds (limite %ds)" % (
                        s.player, s.state, int(now - s.state_time), limit))
                    s.error = ERRORS["ERR_TRADE_TIMEOUT"]
                    s.log("TIMEOUT", "%s > %ds" % (s.state, limit))
        return flagged

    def check_integrity(self) -> List[str]:
        """Port de DealerCoreCheckIntegrity (L4213) : etat inconnu = Recuperation."""
        problems: List[str] = []
        with self._lock:
            for player, s in list(self.sessions.items()):
                if s.state not in STATES.values() or s.state not in TRANSITIONS:
                    problems.append("%s : etat inconnu %r" % (player, s.state))
                    s.log("IMPOSSIBLE_STATE", "unknown state %s" % s.state)
                    self.recover(player, "impossible state")
        return problems

    # -- persistance ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": PERSIST_VERSION,
                "saved_at": time.time(),
                "counter": self.counter,
                "enabled": self.enabled,
                "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
            }

    def load_dict(self, data: Dict[str, Any]) -> int:
        """Reprise apres arret/redemarrage. Retourne le nombre de sessions
        restaurees. Ne restaure QUE les sessions non terminales."""
        if not isinstance(data, dict):
            return 0
        restored = 0
        with self._lock:
            self.counter = int(data.get("counter", 0) or 0)
            self.enabled = bool(data.get("enabled", True))
            for player, blob in (data.get("sessions") or {}).items():
                try:
                    s = Session.from_dict(blob)
                except Exception:
                    continue
                if s.state in _TERMINAL:
                    continue
                if s.state not in TRANSITIONS:
                    # Etat illisible apres redemarrage : on ne devine pas,
                    # on marque la reprise necessaire.
                    s.state = STATES["RECOVERY_REQUIRED"]
                    s.recovery_required = True
                s.log("RESTORED", "reprise apres redemarrage")
                self.sessions[player] = s
                restored += 1
        return restored
