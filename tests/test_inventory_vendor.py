# -*- coding: utf-8 -*-
"""
test_inventory_vendor.py — tests hors-ligne du planificateur inventaire &
vendeur (scripts/inventory_vendor.py). Logique pure : sacs factices,
tables de prix factices ; aucun jeu, aucun clic, aucun I/O.
Mode script : exit 0 si tout passe, 1 sinon.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import inventory_vendor as iv  # noqa: E402

RESULTS = []


def ck(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# --------------------------------------------------------- math des sacs --
BAGS = [
    [None, None, {"id": "rod", "count": 1}],          # 2 libres
    [{"id": "fish_raw_bass", "count": 3}, None],      # 1 libre
    [],                                               # 0 libre (petit sac)
]

ck("sacs: free_slots", iv.free_slots(BAGS) == 3, "attendu 3")
ck("sacs: bag_capacity", iv.bag_capacity(BAGS) == 5, "attendu 5")
ck("sacs: used_ratio", abs(iv.used_ratio(BAGS) - 0.4) < 1e-9, "attendu 0.4")
ck("sacs: used_ratio vide", iv.used_ratio([]) == 0.0)
ck("sacs: free_slots vide", iv.free_slots([[], []]) == 0)

# ------------------------------------------------------------- vente ------
PRICE = {"fish_raw_bass": 100, "lure_fragile": 20}
KEEP = frozenset({"rod"})

sale, total = iv.sell_items(BAGS, PRICE, keep_ids=KEEP)
ck("vente: seule l'item a prix non protege est vendue",
   sale == [{"id": "fish_raw_bass", "count": 3, "vendor_value": 300}],
   str(sale))
ck("vente: total bronze", total == 300, "attendu 300")

sale2, total2 = iv.sell_items(
    [[{"id": "a", "count": 2}, {"id": "unknown_x"}]], {"a": 10}, keep_ids=frozenset())
ck("vente: item sans prix ignore", sale2 == [{"id": "a", "count": 2, "vendor_value": 20}]
   and total2 == 20, str(sale2))

sale3, total3 = iv.sell_items(
    [[{"id": "b", "count": 1}, {"id": "a", "count": 1}]],
    {"a": 5, "b": 7}, keep_ids=frozenset())
ck("vente: liste triee par id (determinisme)",
   [row["id"] for row in sale3] == ["a", "b"] and total3 == 12, str(sale3))

# --------------------------------------------------------- reappro ------
LEVELS = {"hook": {"min": 20, "max": 60}, "lure": {"min": 5}}
HAVE = {"hook": 12, "lure": 7}
PRICES = {"hook": 150, "lure": 30}

buys, cost = iv.restock_list(LEVELS, HAVE, PRICES)
ck("reappro: hook complete jusqu'au besoin (min, sans excéder max)",
   buys == [{"id": "hook", "count": 8, "cost": 1200}], str(buys))
ck("reappro: item au-dessus du min ignoree + total",
   cost == 1200, "attendu 1200")

buys2, cost2 = iv.restock_list({"bait": {"min": 10}}, {}, {"bait": 5})
ck("reappro: possession vide -> achat du min", buys2 == [
   {"id": "bait", "count": 10, "cost": 50}] and cost2 == 50, str(buys2))

# ------------------------------------------------------- durabilite ------
ck("repar: sans donnee -> pas de reparation", iv.durability_needs_repair({}) is False)
ck("repar: moyenne au-dessus du seuil -> False",
   iv.durability_needs_repair({"0": 0.5, "1": 0.5}) is False)
ck("repar: moyenne sous le seuil -> True",
   iv.durability_needs_repair({"0": 0.30, "1": 0.31}) is True)
ck("repar: ratios bornes (clamp)",
   iv.durability_needs_repair({"0": 1.2, "1": 0.0, "2": 0.0}) is True,
   "1.2 clamp a 1.0 -> moyenne 0.333 < 0.35")
ck("repar: clamp vers le haut n'augmente pas la moyenne",
   iv.durability_needs_repair({"0": 1.2, "1": 0.2}) is False,
   "moyenne 0.6 >= 0.35")

# ----------------------------------------------------- plan de tournée ---
PRICE_TABLE = {"fish_raw_bass": 100}
CONSUM = {"hook": 150}

plan_none = iv.plan_vendor_trip(
    [[None, None, None], [None, None, None]],   # sacs largement vides
    PRICE_TABLE, LEVELS, {"hook": 50, "lure": 9}, CONSUM,
    travel_minutes=6)
ck("plan: pas de declencheur -> aucune etape",
   plan_none["triggered_by"] == [] and plan_none["steps"] == [],
   str(plan_none["triggered_by"]))

plan_slots = iv.plan_vendor_trip(
    [[{"id": "fish_raw_bass", "count": 5}, {"id": "fish_raw_bass", "count": 5},
      {"id": "fish_raw_bass", "count": 5}], [{"id": "fish_raw_bass", "count": 5}, None]],
    PRICE_TABLE, LEVELS, {"hook": 50, "lure": 9}, CONSUM,
    travel_minutes=6, route_minutes=6)
trig = [t["reason"] for t in plan_slots["triggered_by"]]
ck("plan: declencheur sacs pleins", "few_free_slots" in trig, str(trig))
actions = [s["action"] for s in plan_slots["steps"]]
ck("plan: vente + retour presents", "list_items_to_sell" in actions
   and "return_to_route" in actions, str(actions))
ck("plan: estime net = vente - reappro",
   plan_slots["estimates"]["sale_value_copper"] == 2000
   and plan_slots["estimates"]["net_copper"] == plan_slots["estimates"]["sale_value_copper"]
   - plan_slots["estimates"]["restock_cost_copper"], str(plan_slots["estimates"]))
ck("plan: rendement/minute calcule",
   abs(plan_slots["estimates"]["worth_per_minute_copper"] - 2000 / 12) < 0.1,
   str(plan_slots["estimates"]["worth_per_minute_copper"]))
ck("plan: borne mais non verifiable en jeu (honnetete)",
   plan_slots["bounded"] is True and plan_slots["verifiable"] is False)

plan_repair = iv.plan_vendor_trip(
    [[None], [None]], {}, LEVELS, {"hook": 50}, {},
    travel_minutes=5, repair_durability={"0": 0.2, "1": 0.22})
actions_r = [s["action"] for s in plan_repair["steps"]]
ck("plan: durabilite basse -> etape reparation sans vente",
   "repair" in actions_r and "list_items_to_sell" not in actions_r, str(actions_r))

plan_restock = iv.plan_vendor_trip(
    [[{"id": "fish_raw_bass", "count": 9}, None]], PRICE_TABLE,
    {"hook": {"min": 20}}, {"hook": 0}, {"hook": 150}, travel_minutes=8)
actions_s = [s["action"] for s in plan_restock["steps"]]
ck("plan: reappro step avec liste bornee",
   "restock" in actions_s, str(actions_s))

ck("plan: temps de trajet nul -> rendement None (pas de division/zero)",
   iv.plan_vendor_trip(
       [[{"id": "fish_raw_bass", "count": 9}, None]], PRICE_TABLE, {}, {}, {},
       travel_minutes=0)["estimates"]["worth_per_minute_copper"] is None)

# ------------------------------------------------------------- bilan -----
ok_count = sum(1 for _, ok, _ in RESULTS if ok)
print("---- %d/%d PASS ----" % (ok_count, len(RESULTS)))
sys.exit(0 if ok_count == len(RESULTS) else 1)
