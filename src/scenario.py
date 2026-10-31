"""Reads the synthetic CSVs and builds the same rows the SQL tools return.

The graph gets its facts from PostgreSQL. That means running it needs Docker, a
database, an Anthropic key and an OpenAI key. This module reads the same
synthetic data straight out of data/structured/ and hands back rows in exactly
the shape src/tools/sql_tools.py produces, so the deterministic half of the
workflow — the arithmetic and the constraint validation — can be run and tested
with nothing installed.

It is a second way into the same calculations, not a second copy of them. Both
paths call src/calculations.py and src/validation.py.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src import calculations
from src.business_rules import substitution_rule_for
from src.validation import ConstraintSnapshot

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "structured"


def _read(name: str, data_dir: Path) -> list[dict[str, str]]:
    with (data_dir / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@dataclass
class Facts:
    """Every synthetic table, as lists of row dictionaries."""

    demand_forecast: list[dict[str, str]]
    recipes: list[dict[str, str]]
    recipe_ingredients: list[dict[str, str]]
    ingredients: list[dict[str, str]]
    inventory: list[dict[str, str]]
    purchase_orders: list[dict[str, str]]


def load_facts(data_dir: Path = DATA_DIR) -> Facts:
    return Facts(
        demand_forecast=_read("demand_forecast.csv", data_dir),
        recipes=_read("recipes.csv", data_dir),
        recipe_ingredients=_read("recipe_ingredients.csv", data_dir),
        ingredients=_read("ingredients.csv", data_dir),
        inventory=_read("inventory.csv", data_dir),
        purchase_orders=_read("purchase_orders.csv", data_dir),
    )


def demand_rows(facts: Facts, target_date: str, fc_id: str) -> list[dict[str, Any]]:
    """Same shape as sql_tools.get_demand()."""
    recipe_names = {r["recipe_id"]: r["recipe_name"] for r in facts.recipes}
    rows = [
        {
            "forecast_date": r["forecast_date"],
            "fc_id": r["fc_id"],
            "recipe_id": r["recipe_id"],
            "recipe_name": recipe_names.get(r["recipe_id"], r["recipe_id"]),
            "forecast_boxes": int(r["forecast_boxes"]),
        }
        for r in facts.demand_forecast
        if r["forecast_date"] == target_date and r["fc_id"] == fc_id
    ]
    return sorted(rows, key=lambda r: r["recipe_id"])


def recipe_requirement_rows(facts: Facts, recipe_ids: list[str]) -> list[dict[str, Any]]:
    """Same shape as sql_tools.get_recipe_requirements()."""
    recipe_names = {r["recipe_id"]: r["recipe_name"] for r in facts.recipes}
    ingredient_names = {r["sku"]: r["ingredient_name"] for r in facts.ingredients}
    wanted = set(recipe_ids)
    rows = [
        {
            "recipe_id": r["recipe_id"],
            "recipe_name": recipe_names.get(r["recipe_id"], r["recipe_id"]),
            "sku": r["sku"],
            "ingredient_name": ingredient_names.get(r["sku"], r["sku"]),
            "qty_per_box": float(r["qty_per_box"]),
        }
        for r in facts.recipe_ingredients
        if r["recipe_id"] in wanted
    ]
    return sorted(rows, key=lambda r: (r["recipe_id"], r["sku"]))


def inventory_rows(facts: Facts, fc_id: str) -> list[dict[str, Any]]:
    """Same shape as sql_tools.get_inventory()."""
    ingredient_names = {r["sku"]: r["ingredient_name"] for r in facts.ingredients}
    rows = [
        {
            "fc_id": r["fc_id"],
            "sku": r["sku"],
            "ingredient_name": ingredient_names.get(r["sku"], r["sku"]),
            "on_hand_qty": int(r["on_hand_qty"]),
        }
        for r in facts.inventory
        if r["fc_id"] == fc_id
    ]
    return sorted(rows, key=lambda r: r["sku"])


def confirmed_purchase_order_rows(
    facts: Facts, fc_id: str, target_date: str
) -> list[dict[str, Any]]:
    """Same shape as sql_tools.get_confirmed_purchase_orders().

    Mirrors the SQL filter: CONFIRMED only, and arriving on or before the date
    the stock is needed. A PO that lands the day after is real but useless, so
    it is left out of the projection rather than counted and footnoted.
    """
    rows = [
        {
            "po_id": r["po_id"],
            "supplier_id": r["supplier_id"],
            "fc_id": r["fc_id"],
            "sku": r["sku"],
            "quantity": int(r["quantity"]),
            "expected_delivery": r["expected_delivery"],
            "status": r["status"],
        }
        for r in facts.purchase_orders
        if r["fc_id"] == fc_id
        and r["status"] == "CONFIRMED"
        and r["expected_delivery"] <= target_date
    ]
    return sorted(rows, key=lambda r: r["expected_delivery"])


def other_fc_excess_rows(
    facts: Facts, sku: str, target_fc: str, target_date: str
) -> list[dict[str, Any]]:
    """Same shape as sql_tools.get_other_fc_excess().

    A site's excess is its on-hand quantity minus its own forecast requirement
    for the same date. Like the SQL version, this ignores that site's inbound
    POs, so the figure is a floor rather than an estimate.
    """
    rows = []
    for inventory_row in facts.inventory:
        if inventory_row["sku"] != sku or inventory_row["fc_id"] == target_fc:
            continue
        fc_id = inventory_row["fc_id"]
        fc_demand = demand_rows(facts, target_date, fc_id)
        requirements = recipe_requirement_rows(
            facts, [r["recipe_id"] for r in fc_demand]
        )
        required = calculations.required_by_sku(fc_demand, requirements).get(sku, 0.0)
        on_hand = float(inventory_row["on_hand_qty"])
        rows.append(
            {
                "fc_id": fc_id,
                "sku": sku,
                "on_hand_qty": on_hand,
                "required_qty": required,
                "excess_qty": max(on_hand - required, 0.0),
            }
        )
    return sorted(rows, key=lambda r: r["excess_qty"], reverse=True)


@dataclass
class ShortageScenario:
    """Everything the deterministic half of the workflow establishes."""

    target_date: str
    target_fc: str
    target_sku: str
    required: float
    on_hand: float
    inbound: float
    shortage: float
    demand_rows: list[dict[str, Any]]
    purchase_order_rows: list[dict[str, Any]]
    excluded_purchase_orders: list[dict[str, Any]]
    impacted_recipes: list[dict[str, Any]]
    other_fc_rows: list[dict[str, Any]]
    substitute_sku: str | None
    substitute_on_hand: float

    @property
    def projected_available(self) -> float:
        return self.on_hand + self.inbound

    def constraint_snapshot(self) -> ConstraintSnapshot:
        return ConstraintSnapshot(
            target_sku=self.target_sku,
            target_fc=self.target_fc,
            shortage=self.shortage,
            other_fc_excess={
                r["fc_id"]: float(r["excess_qty"]) for r in self.other_fc_rows
            },
            substitute_on_hand=self.substitute_on_hand,
            impacted_recipes=self.impacted_recipes,
        )


def build_scenario(
    target_date: str,
    target_fc: str,
    target_sku: str,
    facts: Facts | None = None,
) -> ShortageScenario:
    """Run the deterministic path for one SKU at one site on one date."""
    facts = facts or load_facts()

    demand = demand_rows(facts, target_date, target_fc)
    requirements = recipe_requirement_rows(facts, [r["recipe_id"] for r in demand])
    inventory = inventory_rows(facts, target_fc)
    confirmed_pos = confirmed_purchase_order_rows(facts, target_fc, target_date)

    required = calculations.required_by_sku(demand, requirements).get(target_sku, 0.0)
    on_hand = calculations.on_hand_by_sku(inventory).get(target_sku, 0.0)
    inbound = calculations.inbound_by_sku(confirmed_pos).get(target_sku, 0.0)
    shortage = calculations.shortages_by_sku(
        {target_sku: required}, {target_sku: on_hand}, {target_sku: inbound}
    )[target_sku]

    # POs for this SKU that exist but do not help, so the report can say why.
    excluded = [
        r
        for r in facts.purchase_orders
        if r["fc_id"] == target_fc
        and r["sku"] == target_sku
        and not (r["status"] == "CONFIRMED" and r["expected_delivery"] <= target_date)
    ]

    rule = substitution_rule_for(target_sku)
    substitute_sku = rule.substitute_sku if rule else None
    substitute_on_hand = (
        calculations.on_hand_by_sku(inventory).get(substitute_sku, 0.0)
        if substitute_sku
        else 0.0
    )

    return ShortageScenario(
        target_date=target_date,
        target_fc=target_fc,
        target_sku=target_sku,
        required=required,
        on_hand=on_hand,
        inbound=inbound,
        shortage=shortage,
        demand_rows=demand,
        purchase_order_rows=confirmed_pos,
        excluded_purchase_orders=excluded,
        impacted_recipes=calculations.impacted_recipes(demand, requirements, target_sku),
        other_fc_rows=other_fc_excess_rows(facts, target_sku, target_fc, target_date),
        substitute_sku=substitute_sku,
        substitute_on_hand=substitute_on_hand,
    )
