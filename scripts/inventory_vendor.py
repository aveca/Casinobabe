# -*- coding: utf-8 -*-
"""
inventory_vendor.py — LOGIQUE PURE du module "Inventaire & vendeur".

Rôle : planifier, à partir d'un état de sacs et d'un profil de tournée,
 une sortie vendeur bornée (vendre l'excédent, réparer, réapprovisionner,
 revenir). AUCUN accès au jeu ici : les entrées sont des structures
 BagSnapshot déclarées (émetteur addon à venir, ou déclaration manuelle).
Ce qui manque (documenté, honnête) : le flux de données en jeu, la
 validation d'une tournée complète avant tout statut "available".

Toutes les fonctions sont déterministes et testables hors-ligne
 (tests/test_inventory_vendor.py).
"""
from __future__ import annotations

DEFAULT_KEEP_IDS = frozenset({"staff", "rod"})
DEFAULT_REPAIR_THRESHOLD = 0.35   # durabilité moyenne sous ce ratio -> réparer
DEFAULT_SLOT_TRIGGER = 4          # moins de N emplacements libres -> tournée


def free_slots(bags):
    """Nombre d'emplacements libres.

    bags: liste de sacs; chaque sac est une liste d'emplacements,
    chaque emplacement = None (libre) ou {"id": ..., "count": ...}.
    """
    free = 0
    for bag in bags:
        for slot in bag:
            if slot is None:
                free += 1
    return free


def bag_capacity(bags):
    """Capacité totale = nombre d'emplacements (libres ou occupés)."""
    return sum(len(bag) for bag in bags)


def used_ratio(bags):
    """Taux d'occupation [0..1]; 0 sacs -> 0.0."""
    total = bag_capacity(bags)
    if total <= 0:
        return 0.0
    occupied = sum(1 for bag in bags for slot in bag if slot is not None)
    return occupied / total


def sell_items(bags, price_table, keep_ids=DEFAULT_KEEP_IDS):
    """Liste de vente : tout ce qui a un prix et n'est pas protégé.

    Retourne (liste_vente, total_bronze) où chaque entrée est
    {"id", "count", "vendor_value"} ; stable et sérialisable.
    """
    sale = []
    total = 0
    for bag in bags:
        for slot in bag:
            if slot is None:
                continue
            item_id = slot["id"]
            if item_id in keep_ids:
                continue
            price = price_table.get(item_id)
            if not price:
                continue
            count = int(slot.get("count", 1))
            value = price * count
            total += value
            sale.append({"id": item_id, "count": count, "vendor_value": value})
    sale.sort(key=lambda row: row["id"])
    return sale, total


def restock_list(levels, have, price_table):
    """Liste de réappro.

    levels: {"hook": {"min": 20, "max": 60}, ...} — seuils par consommable.
    have:   {"hook": 12, ...} — possession actuelle.
    price_table: {"hook": 150, ...} (bronze unitaire).
    Retourne ([{"id","count","cost"}], total_bronze).
    """
    buys = []
    total = 0
    for item_id in sorted(levels):
        want_min = int(levels[item_id].get("min", 0))
        want_max = int(levels[item_id].get("max", want_min))
        current = int(have.get(item_id, 0))
        missing = want_min - current
        if missing <= 0:
            continue
        if want_max > want_min:
            missing = min(missing, want_max - current)  # remplir sans excéder
        price = int(price_table.get(item_id, 0))
        cost = missing * price
        total += cost
        buys.append({"id": item_id, "count": missing, "cost": cost})
    return buys, total


def durability_needs_repair(durability_by_slot, threshold=DEFAULT_REPAIR_THRESHOLD):
    """Moyenne de durabilité < seuil -> réparation nécessaire.

    durability_by_slot: {"0": 0.82, "3": 0.15, ...} ratios [0..1].
    Slots vides absents de la carte. Sans donnée -> False (ne répare pas pour rien).
    """
    if not durability_by_slot:
        return False
    ratios = [min(1.0, max(0.0, float(v))) for v in durability_by_slot.values()]
    mean = sum(ratios) / len(ratios) if ratios else 1.0
    return mean < float(threshold)


def plan_vendor_trip(bags, price_table, restock_levels, restock_have,
                     consumable_prices, travel_minutes, route_minutes=None,
                     keep_ids=DEFAULT_KEEP_IDS, repair_durability=None,
                     slot_trigger=DEFAULT_SLOT_TRIGGER,
                     repair_threshold=DEFAULT_REPAIR_THRESHOLD):
    """Plan de tournée vendeur BORNÉ (aucune exécution ici).

    Entrées : snapshot de sacs, tables de prix, seuils de réappro,
      temps estimés de trajet ; sorties : plan en étapes descriptives.
    Chaque étape décrit une action bornée + la preuve attendue.
    La tournée ne démarre que si elle est rentable OU nécessaire.
    """
    plan = {
        "triggered_by": [],
        "steps": [],
        "estimates": {},
        "bounded": True,       # chaque attente/action a un plafond
        "verifiable": False,   # False tant que le flux BagSnapshot n'existe pas en jeu
    }

    free = free_slots(bags)
    occupied_ratio = used_ratio(bags)
    if free < slot_trigger:
        plan["triggered_by"].append(
            {"reason": "few_free_slots", "free": free, "trigger": slot_trigger})
    elif occupied_ratio >= 0.8:
        plan["triggered_by"].append(
            {"reason": "bags_nearly_full", "ratio": round(occupied_ratio, 3)})

    sale, sale_value = sell_items(bags, price_table, keep_ids)
    buys, buys_cost = restock_list(restock_levels, restock_have, consumable_prices)
    repair_needed = durability_needs_repair(repair_durability, repair_threshold)
    if repair_needed:
        plan["triggered_by"].append({"reason": "durability_low"})

    if not plan["triggered_by"]:
        return plan  # rien à faire : honnêteté économique (pas de tournée gratuite)

    tml = float(travel_minutes or 0.0)
    leg_back = float(route_minutes if route_minutes is not None else travel_minutes or 0.0)
    net = sale_value - buys_cost
    minutes = tml + leg_back
    plan["estimates"] = {
        "sale_value_copper": sale_value,
        "restock_cost_copper": buys_cost,
        "net_copper": net,
        "travel_minutes_rounded": round(minutes, 1),
        "worth_per_minute_copper": (round(net / minutes, 1) if minutes > 0 else None),
    }

    if sale:
        plan["steps"].append({
            "action": "list_items_to_sell",
            "items": sale,
            "expected": "valeur vendue == total estimé (journalisé)",
            "bound": "liste finie, une transaction",
        })
    if repair_needed:
        plan["steps"].append({
            "action": "repair",
            "expected": "durabilité moyenne au-dessus du seuil avant de repartir",
            "bound": "un passage sur le PNJ réparateur",
        })
    if buys:
        plan["steps"].append({
            "action": "restock",
            "items": buys,
            "expected": "niveaux >= min définis au retour",
            "bound": "achats bornés par la liste calculée",
        })
    plan["steps"].append({
        "action": "return_to_route",
        "expected": "position de départ, sacs sous déclencheur",
        "bound": "timeout de trajet défini par le profil",
    })
    return plan
