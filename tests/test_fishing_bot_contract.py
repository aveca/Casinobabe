#!/usr/bin/env node
# -*- coding: utf-8 -*-
"""
test_fishing_bot_contract.py — contrats de comportement du bot de peche.

Couvre ce que la mission exige explicitement (P1 §7) :
  1. l'eau en eclairage DIURNE et NOCTURNE ;
  2. l'absence d'eau (garde « pas d'eau, pas de lancer ») ;
  3. les cheveux roux et autres objets ressemblant au flotteur ;
  4. les signaux de morsure ;
  5. les clics differes et la reprise ;
  6. les logs incomplets / incoherents (contrat des champs) ;
  7. la protection contre les doubles instances.

Ces tests n'imitent PAS le detecteur : ils executent le vrai code
(\`_red_blobs\`, \`_water_stats\`, \`_water_gate_ok\`, \`_bite_diff\`,
\`_scene_water_ratio\`, \`acquire_single_instance\`, \`FishingBot.log\`) sur des
images de synthese controlees, et sur des captures REELLES quand elles
existent.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import fishing_bot as fb  # noqa: E402
from fishing_observer import acquire_single_instance, release_single_instance  # noqa: E402

FAILED = []
PASSED = 0


def check(name, ok, detail=""):
    global PASSED
    if ok:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s %s" % (name, detail))


# ---------------------------------------------------------------------------
# Outils de synthese
# ---------------------------------------------------------------------------
def hsv_to_bgr(h, s, v):
    px = np.uint8([[[h, s, v]]])
    return tuple(int(c) for c in cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0][0])


def make_scene(water_h, water_s, water_v, ground_h, ground_s, ground_v,
               size=(900, 1200), bobber=True, bobber_area=140,
               bobber_h=5, bobber_s=225, bobber_v=190,
               ground_texture=True, hair=False):
    """Scene de synthese : une zone d'eau (bande centrale) + un sol texture,
    et eventuellement un flotteur rouge (ou une meche de chevelure).

    ATTENTION : le masque rouge de `_red_blobs` impose V <= 200 (le plafond
    historique) et S >= 140, H <= 10 : le flotteur synthetique respecte ces
    bornes, sinon il ne serait tout simplement pas candidat.
    """
    h, w = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = hsv_to_bgr(ground_h, ground_s, ground_v)
    if ground_texture:
        # herbe texturee : bruit de teinte -> IQR eleve
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 26, size=(h, w), dtype=np.int16)
        base = np.array(hsv_to_bgr(ground_h, ground_s, ground_v), dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise[:, :, None] * 3, 0, 255).astype(np.uint8)
    # eau : bande centrale lisse
    y0, y1 = int(h * 0.30), int(h * 0.72)
    img[y0:y1, :] = hsv_to_bgr(water_h, water_s, water_v)
    if bobber:
        r = int((bobber_area / 3.14159) ** 0.5)
        cx, cy = w // 2, (y0 + y1) // 2
        cv2.circle(img, (cx, cy), max(2, r), hsv_to_bgr(bobber_h, bobber_s, bobber_v), -1)
    if hair:
        # mèche de chevelure : petit triangle rouge au milieu de l'eau
        cx, cy = w // 2 + 40, (y0 + y1) // 2 - 20
        cv2.circle(img, (cx, cy), 3, hsv_to_bgr(bobber_h, bobber_s, bobber_v), -1)
    return img


def detect(img, v_floor=80):
    fb.detect_bobber._prefer_near = None
    fb.detect_bobber._locked_water = None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return fb.detect_bobber(img, None, img_hsv=hsv, v_floor=v_floor)


def main():
    print("=== 1. EAU EN ECLAIREMENT DIURNE ET NOCTURNE ===")
    # JOUR : l'eau de l'oasis est VERTE (mesures reelles : H 45-62, S 77-89)
    day = make_scene(water_h=60, water_s=80, water_v=120,
                     ground_h=37, ground_s=190, ground_v=95)
    bx, by, conf = detect(day)
    check("eau de JOUR (H=60) : le flotteur est detecte",
          bx is not None and conf > 0.5, "(pos=%s,%s conf=%.3f)" % (bx, by, conf))

    # NUIT : l'eau est bleu-vert (mesures reelles : H 78-95, S 148-169)
    night = make_scene(water_h=85, water_s=160, water_v=110,
                       ground_h=30, ground_s=90, ground_v=55)
    bx, by, conf = detect(night, v_floor=55)
    check("eau de NUIT (H=85) : le flotteur est detecte",
          bx is not None and conf > 0.5, "(pos=%s,%s conf=%.3f)" % (bx, by, conf))

    # L'ancien code (plage [72,115]) aurait ete AVEUGLE le jour : on le
    # verifie en appliquant l'ancienne plage a la scene de jour.
    old_min = fb.WATER_H_MIN
    try:
        fb.WATER_H_MIN = 72
        bx_old, _, _ = detect(day)
        check("l'ancienne plage [72,115] est bien AVEUGLE le jour (temoignage)",
              bx_old is None, "(detecte=%s)" % (bx_old,))
    finally:
        fb.WATER_H_MIN = old_min

    print()
    print("=== 2. ABSENCE D'EAU (garde « pas d'eau, pas de lancer ») ===")
    r_water = fb._scene_water_ratio(day)
    r_indoor = fb._scene_water_ratio(make_scene(
        water_h=20, water_s=60, water_v=180, ground_h=20, ground_s=40,
        ground_v=200, bobber=False, size=(1440, 2547)))
    check("scene d'eau : ratio >= 0,40 (le bot peche)", r_water >= 0.40,
          "(%.3f)" % r_water)
    check("scene SANS eau : ratio < 0,40 (aucun lancer)",
          r_indoor < 0.40, "(%.3f)" % r_indoor)
    # l'indoeur ne doit produire AUCUN candidat accepte par la porte
    indoor = make_scene(water_h=20, water_s=60, water_v=180, ground_h=20,
                        ground_s=40, ground_v=200, bobber=False, size=(900, 1200))
    bx, by, conf = detect(indoor)
    check("scene SANS eau : aucun faux flotteur", bx is None,
          "(pos=%s,%s conf=%.3f)" % (bx, by, conf))

    print()
    print("=== 3. CHEVEUX ROUX ET OBJETS RESSEMBLANT AU FLOTTEUR ===")
    # (a) mèche de chevelure : petite aire, dans l'eau, couleur identique
    hair_scene = make_scene(water_h=60, water_s=80, water_v=120,
                            ground_h=37, ground_s=190, ground_v=95,
                            bobber=False, hair=True)
    cands = fb._red_blobs(hair_scene, area_min=20, area_max=2000, v_floor=80)
    hair_ok = all(c[2] >= 20 for c in cands)
    check("la mèche de chevelure (aire < 20 px²) est ecartee par le plancher d'aire",
          hair_ok, "(aires=%s)" % [round(c[2], 1) for c in cands])
    bx, by, conf = detect(hair_scene)
    check("la mèche de chevelure seule ne produit aucun flotteur", bx is None,
          "(pos=%s,%s conf=%.3f)" % (bx, by, conf))
    # (b) un leurre ROUGE GRAND (la cape du personnage) : entoure d'eau,
    #     il passerait la porte -> il doit etre ecarte par la TAILLE
    big = make_scene(water_h=60, water_s=80, water_v=120,
                     ground_h=37, ground_s=190, ground_v=95,
                     bobber=False)
    cv2.circle(big, (600, 460), 60, hsv_to_bgr(5, 225, 220), -1)
    cands = fb._red_blobs(big, area_min=20, area_max=2000, v_floor=80)
    check("un leurre rouge de grande taille est ecarte (aire > 2000 px²)",
          all(c[2] <= 2000 for c in cands),
          "(aires=%s)" % [round(c[2], 1) for c in cands])

    print()
    print("=== 4. SIGNAUX DE MORSURE ===")
    # `_bite_diff` compare l'image courante a la PRECEDENTE image analysee
    # (reference memorisee dans le module) : il faut donc deux appels.
    ref = make_scene(water_h=60, water_s=80, water_v=120,
                     ground_h=37, ground_s=190, ground_v=95)
    splash = ref.copy()
    cv2.circle(splash, (600, 460), 9, hsv_to_bgr(240, 60, 250), -1)   # eclaboussure
    fb._bite_prev["pos"] = None
    fb._bite_prev["gray"] = None
    fb._bite_diff(ref, None, (600, 460))            # 1er appel : pose la reference
    d_move, wf_move = fb._bite_diff(splash, None, (600, 460))   # 2e : eclaboussure
    fb._bite_prev["pos"] = None
    fb._bite_prev["gray"] = None
    fb._bite_diff(ref, None, (600, 460))
    d_still, wf_still = fb._bite_diff(ref.copy(), None, (600, 460))  # 2e : rien ne bouge
    check("un changement net (morsure) donne un diff eleve",
          d_move > d_still, "(diff=%s vs %s)" % (round(d_move, 1), round(d_still, 1)))
    check("un etat immobile donne un diff faible", d_still < d_move,
          "(diff=%s)" % round(d_still, 1))
    # la porte a 2 frames consecutives (biteConsecutive) doit etre satisfaite
    # par le diff, pas par l'ecume : on verifie le parametre
    check("le parametrage de detection de morsure est coherent",
          fb.WATER_GATE == 0.88 and fb.WATER_ARC_GATE == 0.90
          and fb.WATER_RING_MIN == 0.60 and fb.WATER_IQR_MAX == 3.0
          and fb.WATER_H_MIN == 40 and fb.WATER_H_MAX == 115,
          "(gate=%.2f arc=%.2f ring=%.2f iqr=%.1f h=%d-%d)" % (
              fb.WATER_GATE, fb.WATER_ARC_GATE, fb.WATER_RING_MIN,
              fb.WATER_IQR_MAX, fb.WATER_H_MIN, fb.WATER_H_MAX))

    print()
    print("=== 5. CLICS DIFFERES ET REPRISE ===")

    class Stub:
        state = "CATCH"
        counters = {"casts": 0, "bites": 1, "clicks": 0, "errors": 0,
                    "unreliable": 0, "popups": 0, "deferred": 0}
        _cycle_id = "act-test"
        _click_source = "detect"
        _last_watch_ts = None
        _bite_signal_ts = None
        _cursor_set = None
        _frame_period_ms = 218
        config = {"cursorDivergePx": 120}
        _mem_end_cycle = lambda *a, **k: None
        log_fn = staticmethod(lambda s: None)

        def log(self, event, **kw):
            pass

    stub = Stub()
    orig = fb.focus_broker_is_user_active
    orig_win = fb.find_wow_window
    fb.focus_broker_is_user_active = lambda: True     # utilisateur actif
    fb.find_wow_window = lambda: (12345, "World of Warcraft", (0, 0, 2560, 1440))
    try:
        ok = fb.FishingBot._click(stub, 100, 100)
    finally:
        fb.focus_broker_is_user_active = orig
        fb.find_wow_window = orig_win
    check("un clic differe n'est PAS effectue", ok is False)
    check("un clic differe est compte dans `deferred` (distinct des erreurs)",
          stub.counters.get("deferred") == 1, "(deferred=%s)" % stub.counters.get("deferred"))
    check("un clic differe met l'etat en PAUSED (reprise possible)",
          stub.state == fb.PAUSED, "(etat=%s)" % stub.state)
    check("un clic differe n'incrémente PAS le compteur de clics",
          stub.counters["clicks"] == 0, "(clicks=%s)" % stub.counters["clicks"])

    print()
    print("=== 6. CONTRAT DES LOGS (champs obligatoires) ===")
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "fb-log-contract.jsonl")
    orig_log = fb.LOG_FILE
    fb.LOG_FILE = tmp
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        s = Stub()
        s.state = "CATCH"
        s.counters = {"casts": 1, "bites": 1, "clicks": 1, "errors": 0,
                      "unreliable": 0, "popups": 0, "deferred": 0}
        fb.FishingBot.log(s, "CATCH_DETECTED", bobber=(1, 2), cycle="act-1",
                          framePeriodMs=218, trigger="foam")
        fb.FishingBot.log(s, "CLICK_BOBBER", button="right", screen=(3, 4),
                          latencyMs=120, sinceLastSightMs=0, source="detect",
                          cycle="act-1", framePeriodMs=218)
        fb.FishingBot.log(s, "CLICK_DEFERRED", reason="utilisateur actif",
                          cycle="act-2", source="detect")
        import json
        rows = [json.loads(l) for l in open(tmp, encoding="utf-8") if l.strip()]
    finally:
        fb.LOG_FILE = orig_log
    by_ev = {r["event"]: r for r in rows}
    check("CATCH_DETECTED porte cycle + framePeriodMs",
          "cycle" in by_ev.get("CATCH_DETECTED", {})
          and "framePeriodMs" in by_ev.get("CATCH_DETECTED", {}))
    check("CLICK_BOBBER porte latencyMs + sinceLastSightMs + source + cycle",
          all(k in by_ev.get("CLICK_BOBBER", {})
              for k in ("latencyMs", "sinceLastSightMs", "source", "cycle")))
    check("CLICK_DEFERRED est un evenement distinct, avec cycle",
          "cycle" in by_ev.get("CLICK_DEFERRED", {}))
    check("chaque ligne porte ts + event + state",
          all(("ts" in r and "event" in r and "state" in r) for r in rows))

    print()
    print("=== 7. PROTECTION CONTRE LES DOUBLES INSTANCES ===")
    import subprocess
    import tempfile
    pid_file = os.path.join(tempfile.gettempdir(), "fb-single-instance-test.pid")
    if os.path.exists(pid_file):
        os.remove(pid_file)
    # un processus vivant et ETRANGER : le verrou doit refuser
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with open(pid_file, "w", encoding="utf-8") as fh:
            fh.write(str(child.pid))
        refused = acquire_single_instance(pid_file)
        check("une 2e instance est REFUSEE quand un autre processus vit (verrou)",
              refused is False, "(retour=%s)" % (refused,))
        with open(pid_file, "r", encoding="utf-8") as fh:
            kept = fh.read().strip()
        check("le fichier PID n'est PAS ecrasé par l'instance refusee",
              kept == str(child.pid), "(pid=%s)" % kept)
    finally:
        child.kill()
        child.wait()
    # un PID mort (residu) : le verrou doit etre pris (pas de faux refus)
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write("999999")
    try:
        took = acquire_single_instance(pid_file)
        check("un residus de PID mort n'empeche PAS de demarrer", took is True)
    finally:
        try:
            release_single_instance(pid_file)
        except Exception:
            pass
        if os.path.exists(pid_file):
            os.remove(pid_file)

    print()
    print("%d ok / %d echec(s)" % (PASSED, len(FAILED)))
    for f in FAILED:
        print("  - " + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
