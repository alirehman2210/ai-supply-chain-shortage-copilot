"""Tests the final summary and the fact-check that guards it.

Offline: no database, no model, no network.

The summary is the last thing a planner reads, so the thing being tested here
is that its numbers come from validated data and that a model-written narrative
cannot smuggle a different number past it.
"""

import unittest

from src import validation
from src.scenario import build_scenario
from src.summary import build_summary, fact_check, render_summary
from src.validation import ConstraintSnapshot

DATE, FC, SKU = "2026-09-29", "BER", "CHK-250"

OPTIONS = [
    {
        "action_type": "transfer",
        "action": "Transfer from Hamburg",
        "estimated_units": 900,
        "source_fc": "HAM",
    },
    {
        "action_type": "substitute",
        "action": "Substitute the alternate SKU",
        "estimated_units": 1800,
    },
]


def scenario_and_report(hours=7 * 24, options=None):
    scenario = build_scenario(DATE, FC, SKU)
    snapshot = scenario.constraint_snapshot()
    snapshot.hours_until_required = hours
    report = validation.validate_options(
        OPTIONS if options is None else options, snapshot
    )
    return scenario, report


class SummaryNumbersComeFromValidatedData(unittest.TestCase):
    def setUp(self):
        self.scenario, self.report = scenario_and_report()
        self.summary = build_summary(self.scenario, self.report)

    def test_the_shortage_matches_the_arithmetic(self):
        self.assertEqual(self.summary.shortage, 1800.0)

    def test_availability_is_on_hand_plus_inbound(self):
        self.assertEqual(
            self.summary.projected_available,
            self.summary.on_hand + self.summary.inbound,
        )
        self.assertEqual(self.summary.projected_available, 10600.0)

    def test_the_substitution_is_recorded_at_its_capped_quantity(self):
        substitute = next(
            o for o in self.summary.options if o["action_type"] == "substitute"
        )
        self.assertEqual(substitute["proposed_units"], 1800.0)
        self.assertEqual(substitute["approved_units"], 600.0)

    def test_the_excluded_purchase_order_is_carried_into_the_summary(self):
        ids = {po["po_id"] for po in self.summary.excluded_purchase_orders}
        self.assertIn("PO-882", ids)

    def test_decision_starts_as_pending(self):
        self.assertEqual(self.summary.decision, "pending")
        self.assertEqual(self.summary.decided_units_total, 0.0)


class ApprovalArithmetic(unittest.TestCase):
    def setUp(self):
        self.scenario, self.report = scenario_and_report()

    def test_approving_both_counts_only_feasible_units(self):
        summary = build_summary(self.scenario, self.report, "approved", [0, 1])
        # 900 from Hamburg plus 600 of substitute, not the 1,800 proposed.
        self.assertEqual(summary.decided_units_total, 1500.0)
        self.assertEqual(summary.shortage_after_decision, 300.0)

    def test_approving_one_option_only(self):
        summary = build_summary(self.scenario, self.report, "approved", [0])
        self.assertEqual(summary.decided_units_total, 900.0)
        self.assertEqual(summary.shortage_after_decision, 900.0)

    def test_rejecting_leaves_the_shortage_untouched(self):
        summary = build_summary(self.scenario, self.report, "rejected", [])
        self.assertEqual(summary.decided_units_total, 0.0)
        self.assertEqual(summary.shortage_after_decision, 1800.0)

    def test_approving_a_rejected_option_adds_no_units(self):
        """Approval is a decision, not a way to create stock."""
        _, report = scenario_and_report(
            options=[
                {
                    "action_type": "transfer",
                    "action": "Transfer from a site with nothing spare",
                    "estimated_units": 500,
                    "source_fc": "MUC",
                }
            ],
        )
        scenario = build_scenario(DATE, FC, SKU)
        snapshot = scenario.constraint_snapshot()
        snapshot.other_fc_excess = {"MUC": 0.0}
        snapshot.hours_until_required = 7 * 24
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Transfer from Munich",
                    "estimated_units": 500,
                    "source_fc": "MUC",
                }
            ],
            snapshot,
        )
        summary = build_summary(scenario, report, "approved", [0])
        self.assertEqual(summary.decided_units_total, 0.0)
        self.assertEqual(summary.shortage_after_decision, 1800.0)

    def test_an_out_of_range_index_is_ignored_rather_than_crashing(self):
        summary = build_summary(self.scenario, self.report, "approved", [0, 99, -5])
        self.assertEqual(summary.decided_units_total, 900.0)


class RenderedText(unittest.TestCase):
    def setUp(self):
        self.scenario, self.report = scenario_and_report()

    def test_it_states_the_shortage(self):
        text = render_summary(build_summary(self.scenario, self.report))
        self.assertIn("1,800", text)

    def test_it_shows_the_capped_quantity_and_the_original_request(self):
        text = render_summary(build_summary(self.scenario, self.report))
        self.assertIn("600", text)
        self.assertIn("1,800", text)

    def test_it_says_nothing_was_actioned(self):
        for decision, indexes in (
            ("pending", []),
            ("approved", [0]),
            ("rejected", []),
            ("cancelled", []),
        ):
            with self.subTest(decision=decision):
                text = render_summary(
                    build_summary(self.scenario, self.report, decision, indexes)
                )
                self.assertIn("No transfer, purchase order", text)

    def test_a_cancelled_review_does_not_read_as_an_approval(self):
        text = render_summary(
            build_summary(self.scenario, self.report, "cancelled", [])
        )
        self.assertIn("stopped without deciding", text)
        self.assertNotIn("The planner approved:", text)

    def test_approving_nothing_does_not_read_as_an_approval(self):
        text = render_summary(
            build_summary(self.scenario, self.report, "approved", [])
        )
        self.assertIn("approved nothing", text)

    def test_a_no_shortage_scenario_says_so_plainly(self):
        scenario = build_scenario(DATE, "MUC", "SALM-250")
        report = validation.validate_options([], scenario.constraint_snapshot())
        text = render_summary(build_summary(scenario, report))
        self.assertIn("No shortage", text)

    def test_an_unchecked_timing_window_is_stated(self):
        scenario, report = scenario_and_report(hours=None)
        text = render_summary(build_summary(scenario, report))
        self.assertIn("timing was not checked", text.lower())


class ModelReplyExtraction(unittest.TestCase):
    """Current Claude models reply with blocks, not a string.

    A thinking block carries a long base64 signature. Using `.content`
    directly put that whole structure into the planner's summary, which is
    what this guards against.
    """

    def setUp(self):
        from src.summary import message_text

        self.extract = message_text

    def test_a_plain_string_passes_through(self):
        self.assertEqual(self.extract("Berlin is short."), "Berlin is short.")

    def test_a_thinking_block_is_dropped_and_its_signature_never_appears(self):
        content = [
            {
                "type": "thinking",
                "thinking": "",
                "signature": "CAQStw4KEAgTGAI4AUIIdGhpbmtpbmcSDCfxypektOajR1za",
            },
            {"type": "text", "text": "Berlin is short 1,800 units."},
        ]
        result = self.extract(content)
        self.assertEqual(result, "Berlin is short 1,800 units.")
        self.assertNotIn("signature", result)
        self.assertNotIn("CAQStw4", result)

    def test_several_text_blocks_are_joined(self):
        content = [
            {"type": "text", "text": "First."},
            {"type": "text", "text": "Second."},
        ]
        self.assertEqual(self.extract(content), "First.\nSecond.")

    def test_no_text_blocks_gives_an_empty_string(self):
        self.assertEqual(self.extract([{"type": "thinking", "thinking": ""}]), "")

    def test_an_empty_reply_is_handled(self):
        self.assertEqual(self.extract([]), "")

    def test_a_block_list_does_not_leak_dict_syntax(self):
        """The old bug produced text containing "{'type':" and "signature"."""
        content = [
            {"type": "thinking", "thinking": "", "signature": "abc123"},
            {"type": "text", "text": "Clean."},
        ]
        result = self.extract(content)
        self.assertNotIn("{'type'", result)
        self.assertNotIn("'signature'", result)


class FactCheck(unittest.TestCase):
    def setUp(self):
        self.scenario, self.report = scenario_and_report()
        self.summary = build_summary(self.scenario, self.report, "approved", [0])

    def test_a_truthful_narrative_passes(self):
        text = (
            "Berlin is short 1,800 units of CHK-250. Hamburg can send 900 units, "
            "and 600 units of the approved alternative are on hand."
        )
        self.assertTrue(fact_check(text, self.summary).passed)

    def test_an_invented_quantity_fails(self):
        result = fact_check("We can recover 5,400 units this week.", self.summary)
        self.assertFalse(result.passed)
        self.assertIn(5400.0, result.unverified_numbers)

    def test_the_proposed_figure_is_allowed_because_the_summary_reports_it(self):
        """The summary shows 1,800 proposed next to 600 approved."""
        self.assertTrue(
            fact_check("The AI proposed 1,800 units.", self.summary).passed
        )

    def test_a_policy_limit_is_allowed(self):
        """Explaining a cap means quoting it."""
        text = "A single urgent transfer is limited to 1,000 units."
        self.assertTrue(fact_check(text, self.summary).passed)

    def test_claiming_an_order_was_placed_fails(self):
        result = fact_check("I have placed the order.", self.summary)
        self.assertFalse(result.passed)
        self.assertTrue(any("action was taken" in n for n in result.notes))

    def test_claiming_the_supplier_was_contacted_fails(self):
        result = fact_check("The supplier has been contacted.", self.summary)
        self.assertFalse(result.passed)

    def test_claiming_inventory_changed_fails(self):
        result = fact_check("Inventory has been updated.", self.summary)
        self.assertFalse(result.passed)

    def test_small_numbers_and_dates_do_not_trip_the_check(self):
        text = "On 2026-09-29 there are 3 recipes affected, with an 8% surcharge."
        self.assertTrue(fact_check(text, self.summary).passed)

    def test_an_empty_narrative_passes(self):
        self.assertTrue(fact_check("", self.summary).passed)

    def test_a_narrative_with_no_numbers_passes(self):
        text = "Berlin is short of chicken and some stock can be moved."
        self.assertTrue(fact_check(text, self.summary).passed)


if __name__ == "__main__":
    unittest.main()
