"""Tests for the deterministic arithmetic.

These use small hand-built rows rather than the project's CSVs, so a failure
points at the formula rather than at the data.
"""

import unittest

from src import calculations


def demand(recipe_id: str, boxes: int, name: str | None = None) -> dict:
    return {
        "recipe_id": recipe_id,
        "recipe_name": name or recipe_id,
        "forecast_boxes": boxes,
    }


def requirement(recipe_id: str, sku: str, qty: float, name: str | None = None) -> dict:
    return {
        "recipe_id": recipe_id,
        "recipe_name": name or recipe_id,
        "sku": sku,
        "qty_per_box": qty,
    }


class RequiredBySku(unittest.TestCase):
    def test_multiplies_boxes_by_quantity_per_box(self):
        result = calculations.required_by_sku(
            [demand("R1", 100)], [requirement("R1", "SKU-A", 2)]
        )
        self.assertEqual(result, {"SKU-A": 200.0})

    def test_sums_the_same_sku_across_recipes(self):
        result = calculations.required_by_sku(
            [demand("R1", 100), demand("R2", 50)],
            [requirement("R1", "SKU-A", 1), requirement("R2", "SKU-A", 1)],
        )
        self.assertEqual(result, {"SKU-A": 150.0})

    def test_handles_fractional_quantity_per_box(self):
        result = calculations.required_by_sku(
            [demand("R1", 10)], [requirement("R1", "SKU-A", 0.5)]
        )
        self.assertEqual(result, {"SKU-A": 5.0})

    def test_ignores_recipes_with_no_demand(self):
        """A bill of materials for a recipe nobody ordered adds nothing."""
        result = calculations.required_by_sku(
            [demand("R1", 100)],
            [requirement("R1", "SKU-A", 1), requirement("R2", "SKU-A", 999)],
        )
        self.assertEqual(result, {"SKU-A": 100.0})

    def test_no_demand_means_no_requirement(self):
        self.assertEqual(calculations.required_by_sku([], []), {})


class ShortagesBySku(unittest.TestCase):
    def test_subtracts_on_hand_and_inbound(self):
        result = calculations.shortages_by_sku(
            {"SKU-A": 1000.0}, {"SKU-A": 600.0}, {"SKU-A": 100.0}
        )
        self.assertEqual(result, {"SKU-A": 300.0})

    def test_surplus_floors_at_zero_rather_than_going_negative(self):
        result = calculations.shortages_by_sku(
            {"SKU-A": 100.0}, {"SKU-A": 500.0}, {"SKU-A": 0.0}
        )
        self.assertEqual(result, {"SKU-A": 0.0})

    def test_missing_inventory_counts_as_zero_not_as_an_error(self):
        result = calculations.shortages_by_sku({"SKU-A": 100.0}, {}, {})
        self.assertEqual(result, {"SKU-A": 100.0})

    def test_a_surplus_of_one_sku_does_not_offset_another(self):
        result = calculations.shortages_by_sku(
            {"SKU-A": 100.0, "SKU-B": 100.0},
            {"SKU-A": 500.0, "SKU-B": 0.0},
            {},
        )
        self.assertEqual(result, {"SKU-A": 0.0, "SKU-B": 100.0})


class ImpactedRecipes(unittest.TestCase):
    def test_returns_only_recipes_using_the_sku(self):
        rows = calculations.impacted_recipes(
            [demand("R1", 10), demand("R2", 20)],
            [requirement("R1", "SKU-A", 1), requirement("R2", "SKU-B", 1)],
            "SKU-A",
        )
        self.assertEqual([r["recipe_id"] for r in rows], ["R1"])

    def test_largest_consumer_comes_first(self):
        rows = calculations.impacted_recipes(
            [demand("R1", 10), demand("R2", 500)],
            [requirement("R1", "SKU-A", 1), requirement("R2", "SKU-A", 1)],
            "SKU-A",
        )
        self.assertEqual([r["recipe_id"] for r in rows], ["R2", "R1"])

    def test_reports_the_units_each_recipe_needs(self):
        rows = calculations.impacted_recipes(
            [demand("R1", 10)], [requirement("R1", "SKU-A", 3)], "SKU-A"
        )
        self.assertEqual(rows[0]["sku_units_required"], 30.0)

    def test_unused_sku_returns_nothing(self):
        rows = calculations.impacted_recipes(
            [demand("R1", 10)], [requirement("R1", "SKU-A", 1)], "SKU-Z"
        )
        self.assertEqual(rows, [])


class InboundAndOnHand(unittest.TestCase):
    def test_inbound_sums_several_purchase_orders_for_one_sku(self):
        rows = [
            {"sku": "SKU-A", "quantity": 100},
            {"sku": "SKU-A", "quantity": 50},
            {"sku": "SKU-B", "quantity": 7},
        ]
        self.assertEqual(
            calculations.inbound_by_sku(rows), {"SKU-A": 150.0, "SKU-B": 7.0}
        )

    def test_no_purchase_orders_means_no_inbound(self):
        self.assertEqual(calculations.inbound_by_sku([]), {})

    def test_on_hand_reads_one_row_per_sku(self):
        rows = [
            {"sku": "SKU-A", "on_hand_qty": 10},
            {"sku": "SKU-B", "on_hand_qty": 0},
        ]
        self.assertEqual(
            calculations.on_hand_by_sku(rows), {"SKU-A": 10.0, "SKU-B": 0.0}
        )


if __name__ == "__main__":
    unittest.main()
