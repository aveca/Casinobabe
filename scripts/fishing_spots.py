#!/usr/bin/env node
"""
fishing_spots.py — modèle de données des bancs de poissons (Fishing V2).

Ce module est la MÉMOIRE PERSISTANTE et la CARTE du bot : il enregistre ce
que le bot a observé (où sont les bancs, combien de temps ils ont pris, quels
mobs rôdent autour, quelles erreurs sont survenues) et fournit les agrégats
utilisés par le planificateur de rondes.

PRINCIPES
---------
* Independence : la detection (scripts/fishing_bot.py) ignore ce module ; le
  planificateur (scripts/fishing_routes.py) ignore egalement la detection.
  Les deux ne partagent QUE ce modele.
* Persistance : un seul fichier JSON versionne (state/fishing-spots.json),
  avec migrations, sauvegardes et ecriture atomique. Il doit survivre aux
  redemarrages et a la corruption partielle.
* Prudence : une observation incertaine n'est PAS transformee en banc
  confirme. Un banc exige un score de confiance ET au moins deux observations
  distinctes (une seule detection n'est pas une preuve).
* Honestete : tout ce qui n'est pas mesure est absent du modele (pas de
  coordonnee inventee, pas de duree estimee presente comme mesuree).

REFERENTIELS DE POSITION
------------------------
Trois referentiels distincts, jamais melanges :

``screen``
    coordonnees ecran (fenetre du client, origine en haut a gauche) ;
    valables uniquement pour l'image ou elles ont ete capturees.
``minimap``
    coordonnees RELATIVES dans le rond de la minicarte (centre = 0,0,
    nord = -y, unite = pixel de minicarte). La minicarte de WoW Classic est
    verrouillee au nord : ce referentiel est STABLE dans le temps, contrairement
    aux coordonnees ecran. C'est la base de la carte.
``world``
    reserve : si le client expose des coordonnees monde (addon, AddOn
    "GetPlayerPosition"), elles sont stockees telles quelles. Tant qu'elles
    n'existent pas, ce champ vaut None et la navigation reste RELATIVE.

Les coordonnees monde ne sont PAS accessibles aujourd'hui (le module addon
n'expose pas la position du joueur) : la carte est donc en referentiel
``minimap``, ce qui est suffisant pour raisonner sur les distances et les
directions, avec les limites documentees (voir ``LIMITS_REFERENTIEL``).
"""

import json
import math
import os
import shutil
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOTS_FILE = os.path.join(ROOT, "state", "fishing-spots.json")
BACKUP_DIR = os.path.join(ROOT, "state", "fishing-spots-backups")

SCHEMA_VERSION = 2

#: Ages de confirmation : un candidat doit etre vu deux fois (a des instants
#: differents) avant d'etre promu « banc » (confirmed). En dessous, il reste
#: « candidat » (candidate) et ne pese pas dans la carte.
CONFIRM_MIN_OBS = 2

#: Confiance minimale pour qu'un candidat soit promu banc.
CONFIRM_MIN_SCORE = 0.62

#: Score de confiance en deca sous lequel le candidat est rejete.
SCORE_MIN = 0.45

#: Duree maximale de validite d'une observation de banc (le banc peut
#: disparaitre / etre vide). Au-dela, le banc est marque « probablement vide ».
STALE_AFTER_S = 6 * 3600.0

#: Les limites connues du referentiel employe, ecrites dans le fichier de
#: carte pour que la carte soit auto-descriptive.
LIMITS_REFERENTIEL = {
    "referentiel": "minimap",
    "unite": "pixel de minicarte (rayon utile ~ 45 px)",
    "origine": "centre de la minicarte = position du joueur ; nord = -y",
    "limites": [
        "la minicarte est verrouillee au nord : le referentiel ne depend pas "
        "de l'orientation de la camera (avantage) mais il ne donne pas la "
        "position monde absolue",
        "la resolution de la minicarte borne la precision : 1 px ~ 1 unite de "
        "carte, l'echelle monde reelle n'est pas connue",
        "les positions observees hors minicarte (banc dans le chat) ne sont "
        "pas localisables : elles ne sont PAS stockees",
    ],
}


# ---------------------------------------------------------------- utilitaires
def _now():
    return time.time()


def _uid(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def _atomic_write(path, data):
    """Ecriture atomique : tmp + os.replace (pas de fichier tronque)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _backup(path):
    """Sauvegarde horodatee avant ecriture (rotation : 5 dernieres)."""
    if not os.path.isfile(path):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "spots-%s.json" % time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, name)
    try:
        shutil.copy2(path, dst)
    except OSError:
        return None
    try:
        keeps = sorted(os.listdir(BACKUP_DIR))[-5:]
        for k in keeps:
            if k != name:
                os.remove(os.path.join(BACKUP_DIR, k))
    except OSError:
        pass
    return dst


def _empty_db():
    return {
        "schema": SCHEMA_VERSION,
        "createdAt": _now(),
        "updatedAt": _now(),
        "referenceName": "minimap",   # referentiel de position utilise
        "reference": dict(LIMITS_REFERENTIEL),   # descripteur + limites
        "spots": {},          # spot_id -> enregistrement
        "segments": {},       # "a>b" -> segment mesure
        "history": [],        # journal des modifications (bornde 200)
        "errors": [],         # incidents localises (detection/deplacement)
    }


# ------------------------------------------------------------- migrations
def migrate(db):
    """Fait evoluer la base vers SCHEMA_VERSION (idempotent)."""
    if not isinstance(db, dict):
        return _empty_db()
    ver = db.get("schema", 0)
    if ver < 1:
        db.setdefault("spots", {})
        db.setdefault("segments", {})
        db.setdefault("history", [])
        db.setdefault("errors", [])
    # Toujours : le nom du referentiel doit exister, sinon la deduplication et
    # la carte ne savent pas ou lire les positions (bug trouve par les tests
    # V2 : une base migree depuis la v0 n'avait pas ce champ).
    if not db.get("referenceName"):
        db["referenceName"] = "minimap"
    if ver < 2:
        # v2 : referentiel explicite + raison de confirmation par observation
        db.setdefault("reference", dict(LIMITS_REFERENTIEL))
        for s in (db.get("spots") or {}).values():
            s.setdefault("confirmReasons", [])
            s.setdefault("detectorVersion", None)
    db["schema"] = SCHEMA_VERSION
    return db


def load(path=None):
    """Charge la base (migrations + sauvegarde si le fichier est corrompu)."""
    path = path or SPOTS_FILE
    if not os.path.isfile(path):
        return _empty_db()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        _backup(path)
        return _empty_db()
    return migrate(data)


def save(db, path=None):
    path = path or SPOTS_FILE
    _backup(path)
    db["updatedAt"] = _now()
    _atomic_write(path, db)
    return db


# ------------------------------------------------------------------- modele
def new_spot(zone, subsection=None, kind=None, reference="minimap"):
    return {
        "id": _uid("spot"),
        "zone": zone,
        "subsection": subsection,
        "kind": kind,                 # "cible" | "appat" | None (inconnu)
        "coords": None,               # {reference: [x, y], "confidence": f}
        "reference": reference,
        "firstSeenAt": _now(),
        "lastSeenAt": _now(),
        "lastConfirmedAt": None,
        "score": 0.0,                 # confiance du dernier score
        "confirmScore": 0.0,          # confiance cumulee (moyenne mobile)
        "observations": 0,
        "confirmations": 0,
        "confirmReasons": [],
        "visits": 0,
        "results": {"catch": 0, "empty": 0, "error": 0},
        "accessTimes": [],            # durees d'acces mesurees (s)
        "fishTimes": [],              # durees de peche mesurees (s)
        "revisitAfterSec": None,      # delai mesure avant reapparition
        "mobs": [],                   # dangers observes a proximite
        "errors": {"locate": 0, "move": 0, "detect": 0},
        "lastError": None,
        "detectorVersion": None,
        "history": [],                # journal local du banc
    }


def _distance(a, b):
    if not a or not b:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def nearest_spot(db, coords, max_dist=25.0, zone=None):
    """Banc existant le plus proche d'une position (deduplication).

    La deduplication utilise le referentiel de la carte (minimap) : deux
    observations a moins de `max_dist` unites de la meme zone sont le MEME
    banc. Sans cela, chaque capture creerait un banc et la carte exploserait.
    """
    best = None
    best_d = None
    for s in (db.get("spots") or {}).values():
        if zone and s.get("zone") != zone:
            continue
        c = (s.get("coords") or {}).get(db.get("referenceName", "minimap"))
        if not c:
            continue
        d = _distance(coords, c)
        if d is None or d > max_dist:
            continue
        if best_d is None or d < best_d:
            best, best_d = s, d
    return (best, best_d) if best else (None, None)


def observe_spot(db, zone, coords=None, screen=None, score=0.0, kind=None,
                 reference="minimap", reasons=None, detector_version=None,
                 now=None):
    """Enregistre une observation de banc ; cree ou met a jour l'enregistrement.

    Retourne (spot, statut) ou statut est :
      ``new``      : banc cree (premiere observation) ;
      ``candidate``: observation.mergee mais NON promu (pas assez de confirmations) ;
      ``confirmed``: le banc est confirme (2+ observations, score >= seuil) ;
      ``rejected`` : observation rejetee (score trop bas) -> rien n'est stocke.
    """
    now = now or _now()
    if score < SCORE_MIN:
        db.setdefault("history", []).append(
            {"ts": now, "event": "spot_rejected", "zone": zone, "score": round(score, 3)})
        return (None, "rejected")

    spot, dist = nearest_spot(db, coords, zone=zone) if coords else (None, None)
    if spot is None:
        spot = new_spot(zone, kind=kind, reference=reference)
        spot["score"] = float(score)
        spot["confirmScore"] = float(score)
        spot["observations"] = 1
        spot["detectorVersion"] = detector_version
        if coords:
            spot["coords"] = {reference: [float(coords[0]), float(coords[1])],
                              "confidence": float(score)}
        db.setdefault("spots", {})[spot["id"]] = spot
        db.setdefault("history", []).append(
            {"ts": now, "event": "spot_created", "spot": spot["id"], "zone": zone})
        return (spot, "new")

    # mise a jour
    spot["observations"] += 1
    spot["lastSeenAt"] = now
    spot["score"] = float(score)
    # moyenne mobile de la confiance (fenetre 8 observations)
    hist = spot.setdefault("_scoreHist", [])
    hist.append(float(score))
    del hist[:-8]
    spot["confirmScore"] = sum(hist) / len(hist)
    spot.pop("_scoreHist", None)
    if coords:
        ref = reference
        prev = (spot.get("coords") or {}).get(ref)
        if prev:
            # moyenne de position (lissage) : la detection est bruitee
            k = min(0.5, 1.0 / max(1, spot["observations"]))
            merged = [prev[0] + (coords[0] - prev[0]) * k,
                      prev[1] + (coords[1] - prev[1]) * k]
            spot["coords"][ref] = merged
            spot["coords"]["confidence"] = spot["confirmScore"]
            if dist is not None:
                spot["coords"]["lastJitter"] = round(dist, 2)
        else:
            spot["coords"] = {ref: [float(coords[0]), float(coords[1])],
                              "confidence": float(score)}
    if kind and not spot.get("kind"):
        spot["kind"] = kind
    if detector_version:
        spot["detectorVersion"] = detector_version
    for r in (reasons or []):
        if r not in spot.setdefault("confirmReasons", []):
            spot["confirmReasons"].append(r)

    if spot["confirmScore"] >= CONFIRM_MIN_SCORE and \
            spot["observations"] >= CONFIRM_MIN_OBS:
        spot["lastConfirmedAt"] = now
        spot["confirmations"] += 1
        spot.setdefault("history", []).append(
            {"ts": now, "event": "confirmed", "score": round(spot["confirmScore"], 3)})
        status = "confirmed"
    else:
        status = "candidate"

    db.setdefault("history", []).append(
        {"ts": now, "event": "spot_observed", "spot": spot["id"],
         "status": status, "score": round(spot["confirmScore"], 3)})
    return (spot, status)


def record_result(db, spot_id, result, access_sec=None, fish_sec=None,
                  mob=None, error=None, now=None):
    """Enregistre le resultat reel d'une visite (poisson / vide / erreur)."""
    now = now or _now()
    spot = (db.get("spots") or {}).get(spot_id)
    if not spot:
        return None
    spot["visits"] = int(spot.get("visits", 0)) + 1
    res = spot.setdefault("results", {"catch": 0, "empty": 0, "error": 0})
    if result in res:
        res[result] += 1
    if access_sec is not None and access_sec >= 0:
        spot.setdefault("accessTimes", []).append(round(float(access_sec), 1))
        del spot["accessTimes"][:-20]
    if fish_sec is not None and fish_sec >= 0:
        spot.setdefault("fishTimes", []).append(round(float(fish_sec), 1))
        del spot["fishTimes"][:-20]
    if mob:
        m = str(mob)
        if m not in spot.setdefault("mobs", []):
            spot["mobs"].append(m)
    if error:
        kind = str(error.get("kind", "detect"))
        spot.setdefault("errors", {"locate": 0, "move": 0, "detect": 0})
        if kind in spot["errors"]:
            spot["errors"][kind] += 1
        spot["lastError"] = {"kind": kind, "at": now,
                             "note": str(error.get("note", ""))[:200]}
    # delai de reapparition mesure (banc reobserve apres une visite vide)
    if result == "empty" and spot.get("lastConfirmedAt"):
        spot["revisitAfterSec"] = round(now - spot["lastConfirmedAt"], 1)
    spot.setdefault("history", []).append(
        {"ts": now, "event": "visit", "result": result})
    return spot


def reliability(spot):
    """Fiabilite d'un banc dans [0,1] : proportion de visites utiles."""
    res = spot.get("results") or {}
    total = sum(res.values())
    if not total:
        return 0.0
    return res.get("catch", 0) / float(total)


def expected_catch_rate(spot):
    """Taux de prises attendu (loi de Bernoulli lissée) d'apres l'historique."""
    res = spot.get("results") or {}
    total = sum(res.values())
    if total < 3:
        return 0.5          # prior neutre : pas d'historique
    return min(0.95, max(0.05, res.get("catch", 0) / float(total)))


def availability(spot, now=None):
    """Probabilite que le banc soit encore peuple (fenetre de validite)."""
    now = now or _now()
    last = spot.get("lastConfirmedAt") or spot.get("lastSeenAt") or 0
    age = now - last
    if age <= STALE_AFTER_S:
        return 1.0
    return max(0.05, 1.0 - (age - STALE_AFTER_S) / float(STALE_AFTER_S))


def mean(values):
    values = [v for v in (values or []) if v is not None]
    return (sum(values) / float(len(values))) if values else None


def confirmed_spots(db, zone=None):
    """Bancs confirmes (>= CONFIRM_MIN_OBS observations, score suffisant)."""
    out = []
    for s in (db.get("spots") or {}).values():
        if zone and s.get("zone") != zone:
            continue
        if s.get("observations", 0) < CONFIRM_MIN_OBS:
            continue
        if s.get("confirmScore", 0.0) < CONFIRM_MIN_SCORE:
            continue
        out.append(s)
    out.sort(key=lambda s: -(s.get("confirmScore", 0.0)))
    return out


def all_spots(db, zone=None):
    out = list((db.get("spots") or {}).values())
    if zone:
        out = [s for s in out if s.get("zone") == zone]
    return out


def spot_map(db, zone=None, reference=None):
    """Carte serialisable (pour export / pour la demonstration web)."""
    reference = reference or db.get("referenceName", "minimap")
    items = []
    for s in all_spots(db, zone):
        c = (s.get("coords") or {}).get(reference)
        items.append({
            "id": s["id"],
            "zone": s.get("zone"),
            "subsection": s.get("subsection"),
            "kind": s.get("kind"),
            "x": c[0] if c else None,
            "y": c[1] if c else None,
            "score": round(s.get("confirmScore", 0.0), 3),
            "status": "confirmed" if s.get("observations", 0) >= CONFIRM_MIN_OBS else "candidate",
            "visits": s.get("visits", 0),
            "catches": (s.get("results") or {}).get("catch", 0),
            "reliability": round(reliability(s), 3),
            "expectedCatchRate": round(expected_catch_rate(s), 3),
            "availability": round(availability(s), 3),
            "mobs": s.get("mobs", []),
            "lastSeenAt": s.get("lastSeenAt"),
            "accessTimes": s.get("accessTimes", []),
            "fishTimes": s.get("fishTimes", []),
            "revisitAfterSec": s.get("revisitAfterSec"),
        })
    return {
        "schema": db.get("schema"),
        "reference": reference,
        "limits": db.get("reference"),
        "updatedAt": db.get("updatedAt"),
        "spots": items,
        "segments": db.get("segments", {}),
    }

def record_segment(db, a_id, b_id, duration_sec=None, distance=None,
                   success=True, obstacle=None, now=None):
    """Enregistre (ou met a jour) un segment mesure entre deux points."""
    now = now or _now()
    key = "%s>%s" % (a_id, b_id)
    segs = db.setdefault("segments", {})
    seg = segs.setdefault(key, {
        "a": a_id, "b": b_id, "runs": 0, "success": 0, "failure": 0,
        "durations": [], "distance": None, "obstacles": [], "lastUsedAt": None,
    })
    seg["runs"] += 1
    if success:
        seg["success"] += 1
    else:
        seg["failure"] += 1
    if duration_sec is not None and duration_sec >= 0:
        seg["durations"].append(round(float(duration_sec), 1))
        del seg["durations"][:-10]
    if distance is not None:
        seg["distance"] = round(float(distance), 2)
    if obstacle:
        o = str(obstacle)
        if o not in seg["obstacles"]:
            seg["obstacles"].append(o)
    seg["lastUsedAt"] = now
    return seg


def segment_stats(db, a_id, b_id):
    seg = (db.get("segments") or {}).get("%s>%s" % (a_id, b_id))
    if not seg:
        return None
    runs = max(1, seg.get("runs", 0))
    return {
        "runs": seg.get("runs", 0),
        "successRate": seg.get("success", 0) / float(runs),
        "meanDuration": mean(seg.get("durations")),
        "distance": seg.get("distance"),
        "obstacles": seg.get("obstacles", []),
    }


def record_error(db, kind, note, spot_id=None, now=None):
    """Incident localise (detection / deplacement / localisation)."""
    now = now or _now()
    db.setdefault("errors", []).append(
        {"ts": now, "kind": kind, "note": str(note)[:200], "spot": spot_id})
    del db["errors"][:-200]
    if spot_id:
        spot = (db.get("spots") or {}).get(spot_id)
        if spot:
            spot.setdefault("errors", {"locate": 0, "move": 0, "detect": 0})
            if kind in spot["errors"]:
                spot["errors"][kind] += 1
            spot["lastError"] = {"kind": kind, "at": now, "note": str(note)[:200]}
    return True
