"""Checks the hard-coded limits still match the policy documents.

The limits live in src/business_rules.py as configuration, and the documents in
data/rag_docs/ are what a planner reads. Those two can drift apart, and if they
do, the copilot would enforce a rule nobody has agreed to. These tests look for
each number in the document it claims to come from.

This is a text search, not a parser. It catches a changed number, which is the
realistic failure. It would not catch a rewritten sentence that keeps the same
figure.
"""

import unittest
from pathlib import Path

from src import business_rules

DOCS = Path(__file__).resolve().parents[1] / "data" / "rag_docs"


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


class TransferLimits(unittest.TestCase):
    def setUp(self):
        self.text = read("warehouse_transfer_policy.md")

    def test_single_transfer_cap_appears_in_the_transfer_policy(self):
        self.assertIn("1,000", self.text)
        self.assertEqual(business_rules.MAX_UNITS_PER_URGENT_TRANSFER, 1000)

    def test_transfer_lead_time_appears_in_the_transfer_policy(self):
        self.assertIn("24 hours", self.text)
        self.assertEqual(business_rules.TRANSFER_LEAD_TIME_HOURS, 24)

    def test_the_policy_still_requires_human_approval(self):
        self.assertIn("approval", self.text.lower())
        self.assertIn("may not execute it automatically", self.text)


class ExpediteLimits(unittest.TestCase):
    def setUp(self):
        self.text = read("supplier_nordfarm_contract.md")

    def test_weekly_allowance_appears_in_the_contract(self):
        self.assertIn("1,200", self.text)
        self.assertEqual(business_rules.EXPEDITE_MAX_UNITS_PER_WEEK, 1200)

    def test_notice_period_appears_in_the_contract(self):
        self.assertIn("48 hours", self.text)
        self.assertEqual(business_rules.EXPEDITE_MIN_NOTICE_HOURS, 48)

    def test_surcharge_appears_in_the_contract(self):
        self.assertIn("8%", self.text)
        self.assertEqual(business_rules.EXPEDITE_SURCHARGE_PERCENT, 8)


class SubstitutionRules(unittest.TestCase):
    def setUp(self):
        self.text = read("substitution_policy.md")
        self.rule = business_rules.substitution_rule_for("CHK-250")

    def test_there_is_a_rule_for_the_scenario_sku(self):
        self.assertIsNotNone(self.rule)
        self.assertEqual(self.rule.substitute_sku, "CHK-250-B")

    def test_the_approved_recipes_match_the_policy(self):
        self.assertEqual(
            self.rule.approved_recipes, frozenset({"RCP-TERIYAKI", "RCP-TUSCAN"})
        )
        self.assertIn("RCP-TERIYAKI", self.text)
        self.assertIn("RCP-TUSCAN", self.text)

    def test_the_pasta_recipe_is_excluded_in_both_places(self):
        self.assertNotIn("RCP-PASTA", self.rule.approved_recipes)
        self.assertIn("not approved", self.text)
        self.assertIn("RCP-PASTA", self.text)

    def test_the_policy_still_caps_substitution_at_stock_on_hand(self):
        self.assertIn("physical on-hand quantity", self.text)

    def test_the_rule_names_its_source_document(self):
        self.assertEqual(self.rule.source_document, "substitution_policy.md")
        self.assertTrue((DOCS / self.rule.source_document).exists())

    def test_an_sku_with_no_approved_substitute_returns_nothing(self):
        self.assertIsNone(business_rules.substitution_rule_for("SALM-250"))


class ApprovalAndOrdering(unittest.TestCase):
    def test_every_action_type_requires_human_approval(self):
        self.assertEqual(
            business_rules.ACTION_TYPES_REQUIRING_HUMAN_APPROVAL,
            frozenset({"transfer", "substitute", "expedite", "other"}),
        )

    def test_the_sop_order_puts_transfers_before_substitutions(self):
        order = business_rules.MITIGATION_PREFERENCE_ORDER
        self.assertLess(order.index("transfer"), order.index("substitute"))

    def test_the_sop_order_puts_substitutions_before_expedites(self):
        order = business_rules.MITIGATION_PREFERENCE_ORDER
        self.assertLess(order.index("substitute"), order.index("expedite"))

    def test_the_sop_document_still_lists_mitigation_in_that_order(self):
        text = read("shortage_response_sop.md").lower()
        transfers = text.index("transfers from other fulfillment centers")
        substitutions = text.index("substitutions")
        expedite = text.index("expedite options")
        self.assertLess(transfers, substitutions)
        self.assertLess(substitutions, expedite)

    def test_the_sop_requires_approval_before_execution(self):
        text = read("shortage_response_sop.md")
        self.assertIn("without the appropriate human approval", text)


if __name__ == "__main__":
    unittest.main()
