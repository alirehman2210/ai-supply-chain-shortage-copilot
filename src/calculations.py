"""Deterministic supply-chain arithmetic.

Every number the copilot reports comes from a function in this file. None of
them call an LLM, and none of them read the database. They take rows in and
return numbers out, which means the graph and the offline scenario runner can
share exactly the same math instead of each having its own copy of it.

Row dictionaries use the column names from sql/schema.sql, because the SQL
tools and the CSV loader both produce rows in that shape.
"""

from collections import defaultdict
from typing import Any


def required_by_sku(
    demand_rows: list[dict[str, Any]],
    recipe_requirement_rows: list[dict[str, Any]],
) -> dict[str, float]:
    """How many units of each SKU the forecast demand consumes.

    forecast_boxes for a recipe x qty_per_box for each ingredient in it,
    summed across every recipe that uses the ingredient.
    """
    boxes_by_recipe = {r["recipe_id"]: float(r["forecast_boxes"]) for r in demand_rows}
    totals: dict[str, float] = defaultdict(float)
    for row in recipe_requirement_rows:
        recipe_id = row["recipe_id"]
        if recipe_id not in boxes_by_recipe:
            # A recipe we hold a bill of materials for but have no demand for.
            continue
        totals[row["sku"]] += boxes_by_recipe[recipe_id] * float(row["qty_per_box"])
    return dict(totals)


def shortages_by_sku(
    required: dict[str, float],
    on_hand: dict[str, float],
    inbound: dict[str, float],
) -> dict[str, float]:
    """required - on_hand - confirmed_inbound, floored at zero.

    Floored because a surplus of one ingredient does not offset a shortage of
    another, so a negative number here would be misleading rather than useful.
    """
    return {
        sku: max(qty - on_hand.get(sku, 0.0) - inbound.get(sku, 0.0), 0.0)
        for sku, qty in required.items()
    }


def impacted_recipes(
    demand_rows: list[dict[str, Any]],
    recipe_requirement_rows: list[dict[str, Any]],
    sku: str,
) -> list[dict[str, Any]]:
    """Which demanded recipes consume this SKU, and how much each one needs.

    Sorted by the largest consumer first, so the biggest contributor to the
    shortage is the first thing a planner reads.
    """
    demand_by_recipe = {r["recipe_id"]: r for r in demand_rows}
    rows = []
    for requirement in recipe_requirement_rows:
        if requirement["sku"] != sku:
            continue
        demand = demand_by_recipe.get(requirement["recipe_id"])
        if demand is None:
            continue
        boxes = float(demand["forecast_boxes"])
        rows.append(
            {
                "recipe_id": requirement["recipe_id"],
                "recipe_name": requirement["recipe_name"],
                "forecast_boxes": int(boxes),
                "sku_units_required": boxes * float(requirement["qty_per_box"]),
            }
        )
    return sorted(rows, key=lambda r: r["sku_units_required"], reverse=True)


def inbound_by_sku(purchase_order_rows: list[dict[str, Any]]) -> dict[str, float]:
    """Total confirmed inbound quantity per SKU.

    The caller is responsible for only passing in POs that are CONFIRMED and
    arrive by the date in question. That filter lives in the SQL query and in
    the CSV loader, not here.
    """
    totals: dict[str, float] = defaultdict(float)
    for row in purchase_order_rows:
        totals[row["sku"]] += float(row["quantity"])
    return dict(totals)


def on_hand_by_sku(inventory_rows: list[dict[str, Any]]) -> dict[str, float]:
    """On-hand quantity per SKU for a single fulfillment center."""
    return {row["sku"]: float(row["on_hand_qty"]) for row in inventory_rows}
