"""Tests the headline scenario against the actual CSV data.

These are the numbers the README quotes. If someone edits the synthetic data,
these tests fail and the documentation stops being true, which is the point.
"""

import json
import unittest
from pathlib import Path

from src.scenario import build_scenario, load_facts

ROOT = Path(__file__).resolve().parents[1]

DATE = "2026-09-29"
FC = "BER"
SKU = "CHK-250"


class BerlinChickenShortage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = build_scenario(DATE, FC, SKU)

    def test_required_quantity(self):
        self.assertEqual(self.scenario.required, 12400.0)

    def test_on_hand_quantity(self):
        self.assertEqual(self.scenario.on_hand, 8900.0)

    def test_confirmed_inbound_before_the_required_date(self):
        self.assertEqual(self.scenario.inbound, 1700.0)

    def test_projected_available(self):
        self.assertEqual(self.scenario.projected_available, 10600.0)

    def test_projected_shortage(self):
        self.assertEqual(self.scenario.shortage, 1800.0)

    def test_a_later_purchase_order_is_excluded_and_explained(self):
        """PO-882 is confirmed but lands on the 30th, a day too late."""
        excluded = {po["po_id"] for po in self.scenario.excluded_purchase_orders}
        self.assertIn("PO-882", excluded)
        included = {po["po_id"] for po in self.scenario.purchase_order_rows}
        self.assertEqual(included, {"PO-881", "PO-901"})

    def test_counting_the_late_po_would_change_the_answer(self):
        """Shows the filter is doing real work, not just tidying."""
        late = sum(
            float(po["quantity"])
            for po in self.scenario.excluded_purchase_orders
            if po["sku"] == SKU
        )
        self.assertEqual(late, 1400.0)
        self.assertNotEqual(self.scenario.shortage, 1800.0 - late)

    def test_three_recipes_consume_the_sku(self):
        names = [r["recipe_name"] for r in self.scenario.impacted_recipes]
        self.assertEqual(
            names, ["Chicken Teriyaki", "Tuscan Chicken", "Creamy Chicken Pasta"]
        )

    def test_recipe_contributions_add_up_to_the_requirement(self):
        total = sum(r["sku_units_required"] for r in self.scenario.impacted_recipes)
        self.assertEqual(total, self.scenario.required)

    def test_hamburg_spare_stock(self):
        excess = {r["fc_id"]: r["excess_qty"] for r in self.scenario.other_fc_rows}
        self.assertEqual(excess["HAM"], 900.0)

    def test_munich_spare_stock(self):
        excess = {r["fc_id"]: r["excess_qty"] for r in self.scenario.other_fc_rows}
        self.assertEqual(excess["MUC"], 200.0)

    def test_the_short_site_is_not_listed_as_a_source(self):
        self.assertNotIn(FC, {r["fc_id"] for r in self.scenario.other_fc_rows})

    def test_substitute_stock_is_far_below_the_shortage(self):
        """The fact that makes the substitution story happen."""
        self.assertEqual(self.scenario.substitute_sku, "CHK-250-B")
        self.assertEqual(self.scenario.substitute_on_hand, 600.0)
        self.assertLess(self.scenario.substitute_on_hand, self.scenario.shortage)

    def test_transfers_and_substitution_alone_cannot_close_the_shortage(self):
        """Why the scenario needs the supplier: internal stock is not enough.

        1,100 units spare across the other two sites, plus 600 units of the
        substitute, is 1,700 against a shortage of 1,800.
        """
        spare = sum(r["excess_qty"] for r in self.scenario.other_fc_rows)
        self.assertEqual(spare, 1100.0)
        internal = spare + self.scenario.substitute_on_hand
        self.assertEqual(internal, 1700.0)
        self.assertLess(internal, self.scenario.shortage)


class SitesWithNoShortage(unittest.TestCase):
    def test_hamburg_has_no_chicken_shortage(self):
        scenario = build_scenario(DATE, "HAM", SKU)
        self.assertEqual(scenario.required, 2400.0)
        self.assertEqual(scenario.shortage, 0.0)

    def test_munich_has_no_chicken_shortage(self):
        scenario = build_scenario(DATE, "MUC", SKU)
        self.assertEqual(scenario.shortage, 0.0)

    def test_an_sku_nobody_is_short_of(self):
        scenario = build_scenario(DATE, FC, "RICE-500")
        self.assertEqual(scenario.shortage, 0.0)


class UnknownScope(unittest.TestCase):
    def test_a_date_with_no_forecast_produces_no_demand(self):
        scenario = build_scenario("2026-01-01", FC, SKU)
        self.assertEqual(scenario.required, 0.0)
        self.assertEqual(scenario.shortage, 0.0)

    def test_an_unknown_sku_produces_no_requirement(self):
        scenario = build_scenario(DATE, FC, "NOPE-000")
        self.assertEqual(scenario.required, 0.0)
        self.assertEqual(scenario.shortage, 0.0)


class GoldenQuestions(unittest.TestCase):
    """The golden questions that can be answered from the structured data.

    The remaining questions in evals/golden_questions.json depend on vector
    retrieval, so they need the database and are not asserted here.
    """

    @classmethod
    def setUpClass(cls):
        with (ROOT / "evals" / "golden_questions.json").open() as handle:
            cls.golden = {q["id"]: q for q in json.load(handle)}
        cls.scenario = build_scenario(DATE, FC, SKU)

    def test_gq01_shortage_quantity(self):
        expected = float(self.golden["GQ-01"]["expected_answer"])
        self.assertEqual(self.scenario.shortage, expected)

    def test_gq02_recipes_consuming_the_sku(self):
        expected = set(self.golden["GQ-02"]["expected_answer"])
        actual = {r["recipe_name"] for r in self.scenario.impacted_recipes}
        self.assertEqual(actual, expected)

    def test_gq05_hamburg_excess(self):
        expected = float(
            self.golden["GQ-05"]["expected_answer"].split()[0]
        )
        excess = {r["fc_id"]: r["excess_qty"] for r in self.scenario.other_fc_rows}
        self.assertEqual(excess["HAM"], expected)


class FactLoading(unittest.TestCase):
    def test_every_csv_loads_and_is_not_empty(self):
        facts = load_facts()
        for name in (
            "demand_forecast",
            "recipes",
            "recipe_ingredients",
            "ingredients",
            "inventory",
            "purchase_orders",
        ):
            with self.subTest(table=name):
                self.assertTrue(getattr(facts, name))

    def test_every_recipe_ingredient_points_at_a_real_sku(self):
        facts = load_facts()
        skus = {r["sku"] for r in facts.ingredients}
        for row in facts.recipe_ingredients:
            with self.subTest(sku=row["sku"]):
                self.assertIn(row["sku"], skus)

    def test_every_inventory_row_points_at_a_real_sku(self):
        facts = load_facts()
        skus = {r["sku"] for r in facts.ingredients}
        for row in facts.inventory:
            with self.subTest(sku=row["sku"]):
                self.assertIn(row["sku"], skus)


if __name__ == "__main__":
    unittest.main()
