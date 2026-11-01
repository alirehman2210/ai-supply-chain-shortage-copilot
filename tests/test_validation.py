"""Tests for the constraint validator.

The validator exists because a model once proposed substituting the whole
shortage with an alternate SKU that Berlin only held 600 units of. The first
test class here is that exact case.
"""

import unittest

from src import business_rules, validation
from src.validation import CAPPED, OK, REJECTED, ConstraintSnapshot


def berlin_snapshot(**overrides) -> ConstraintSnapshot:
    """The headline scenario: 1,800 units short of CHK-250 in Berlin."""
    defaults = dict(
        target_sku="CHK-250",
        target_fc="BER",
        shortage=1800.0,
        other_fc_excess={"HAM": 900.0, "MUC": 200.0},
        substitute_on_hand=600.0,
        impacted_recipes=[
            {
                "recipe_id": "RCP-TERIYAKI",
                "recipe_name": "Chicken Teriyaki",
                "sku_units_required": 6200.0,
            },
            {
                "recipe_id": "RCP-TUSCAN",
                "recipe_name": "Tuscan Chicken",
                "sku_units_required": 4000.0,
            },
            {
                "recipe_id": "RCP-PASTA",
                "recipe_name": "Creamy Chicken Pasta",
                "sku_units_required": 2200.0,
            },
        ],
    )
    defaults.update(overrides)
    return ConstraintSnapshot(**defaults)


class SubstitutionCannotExceedStockOnHand(unittest.TestCase):
    """The bug this validator was written for."""

    def setUp(self):
        self.option = {
            "action_type": "substitute",
            "action": "Substitute CHK-250-B for the whole shortage",
            "estimated_units": 1800,
        }

    def test_proposal_for_the_whole_shortage_is_capped_to_stock_on_hand(self):
        report = validation.validate_options([self.option], berlin_snapshot())
        self.assertEqual(report.options[0].approved_units, 600.0)

    def test_the_original_proposal_is_still_reported(self):
        """A planner needs to see that the copilot wanted 1,800."""
        report = validation.validate_options([self.option], berlin_snapshot())
        self.assertEqual(report.options[0].proposed_units, 1800.0)
        self.assertEqual(report.options[0].verdict, CAPPED)

    def test_the_reason_names_the_binding_rule_and_its_source(self):
        report = validation.validate_options([self.option], berlin_snapshot())
        reasons = " ".join(report.options[0].reasons)
        self.assertIn("600", reasons)
        self.assertIn("substitution_policy.md", reasons)

    def test_the_uncovered_shortage_is_reported_not_hidden(self):
        report = validation.validate_options([self.option], berlin_snapshot())
        self.assertEqual(report.residual_shortage, 1200.0)
        self.assertFalse(report.fully_covered)

    def test_rejected_outright_when_no_substitute_is_in_stock(self):
        report = validation.validate_options(
            [self.option], berlin_snapshot(substitute_on_hand=0.0)
        )
        self.assertEqual(report.options[0].verdict, REJECTED)
        self.assertEqual(report.options[0].approved_units, 0.0)

    def test_two_substitutions_cannot_both_claim_the_same_stock(self):
        report = validation.validate_options(
            [dict(self.option), dict(self.option)], berlin_snapshot()
        )
        approved = sum(o.approved_units for o in report.options)
        self.assertEqual(approved, 600.0)


class SubstitutionRecipeApproval(unittest.TestCase):
    def test_unapproved_recipe_is_excluded_and_flagged(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "substitute",
                    "action": "Substitute into all three recipes",
                    "estimated_units": 600,
                    "recipes": ["RCP-TERIYAKI", "RCP-TUSCAN", "RCP-PASTA"],
                }
            ],
            berlin_snapshot(),
        )
        self.assertIn("RCP-PASTA", " ".join(report.options[0].reasons))
        self.assertEqual(report.options[0].verdict, CAPPED)

    def test_rejected_when_only_unapproved_recipes_are_named(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "substitute",
                    "action": "Substitute into the pasta recipe",
                    "estimated_units": 300,
                    "recipes": ["RCP-PASTA"],
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)
        self.assertEqual(report.options[0].approved_units, 0.0)

    def test_approved_recipes_alone_pass_cleanly(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "substitute",
                    "action": "Substitute into teriyaki only",
                    "estimated_units": 500,
                    "recipes": ["RCP-TERIYAKI"],
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, OK)
        self.assertEqual(report.options[0].approved_units, 500.0)

    def test_cannot_exceed_the_demand_the_substitute_is_approved_for(self):
        """Only 300 units of approved demand exists, so 600 in stock is moot."""
        snapshot = berlin_snapshot(
            impacted_recipes=[
                {
                    "recipe_id": "RCP-TERIYAKI",
                    "recipe_name": "Chicken Teriyaki",
                    "sku_units_required": 300.0,
                },
                {
                    "recipe_id": "RCP-PASTA",
                    "recipe_name": "Creamy Chicken Pasta",
                    "sku_units_required": 5000.0,
                },
            ]
        )
        report = validation.validate_options(
            [
                {
                    "action_type": "substitute",
                    "action": "Substitute the lot",
                    "estimated_units": 600,
                }
            ],
            snapshot,
        )
        self.assertEqual(report.options[0].approved_units, 300.0)


class Transfers(unittest.TestCase):
    def test_transfer_within_spare_stock_is_approved_as_proposed(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, OK)
        self.assertEqual(report.options[0].approved_units, 900.0)

    def test_transfer_is_capped_to_the_sending_sites_spare_stock(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Munich",
                    "estimated_units": 500,
                    "source_fc": "MUC",
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].approved_units, 200.0)
        self.assertEqual(report.options[0].verdict, CAPPED)

    def test_transfer_is_capped_to_the_single_transfer_limit(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move a lot of stock from Hamburg",
                    "estimated_units": 5000,
                    "source_fc": "HAM",
                }
            ],
            berlin_snapshot(other_fc_excess={"HAM": 4000.0}),
        )
        self.assertEqual(
            report.options[0].approved_units,
            float(business_rules.MAX_UNITS_PER_URGENT_TRANSFER),
        )

    def test_transfer_from_a_site_with_nothing_spare_is_rejected(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Munich",
                    "estimated_units": 100,
                    "source_fc": "MUC",
                }
            ],
            berlin_snapshot(other_fc_excess={"MUC": 0.0}),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_transfer_with_no_named_source_is_rejected(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from somewhere",
                    "estimated_units": 100,
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_transfer_from_an_unknown_site_is_rejected(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Vienna",
                    "estimated_units": 100,
                    "source_fc": "VIE",
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_a_site_cannot_transfer_to_itself(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Berlin to Berlin",
                    "estimated_units": 100,
                    "source_fc": "BER",
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_two_transfers_cannot_both_claim_the_same_spare_stock(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "First claim on Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                },
                {
                    "action_type": "transfer",
                    "action": "Second claim on Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                },
            ],
            berlin_snapshot(),
        )
        self.assertEqual(sum(o.approved_units for o in report.options), 900.0)


class Expedites(unittest.TestCase):
    def test_expedite_is_capped_to_the_weekly_contract_allowance(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 5000,
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(
            report.options[0].approved_units,
            float(business_rules.EXPEDITE_MAX_UNITS_PER_WEEK),
        )

    def test_the_surcharge_and_notice_period_are_surfaced(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 100,
                }
            ],
            berlin_snapshot(),
        )
        reasons = " ".join(report.options[0].reasons)
        self.assertIn("8%", reasons)
        self.assertIn("48", reasons)

    def test_already_committed_units_reduce_what_is_left(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 1200,
                }
            ],
            berlin_snapshot(expedite_units_committed_this_week=1000.0),
        )
        self.assertEqual(report.options[0].approved_units, 200.0)

    def test_rejected_when_the_weekly_allowance_is_spent(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 500,
                }
            ],
            berlin_snapshot(expedite_units_committed_this_week=1200.0),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_two_expedites_share_one_weekly_allowance(self):
        option = {
            "action_type": "expedite",
            "action": "Expedite from the supplier",
            "estimated_units": 1000,
        }
        report = validation.validate_options(
            [dict(option), dict(option)], berlin_snapshot()
        )
        self.assertEqual(
            sum(o.approved_units for o in report.options),
            float(business_rules.EXPEDITE_MAX_UNITS_PER_WEEK),
        )


class UnknownAndMalformedActions(unittest.TestCase):
    def test_an_action_type_with_no_rule_is_credited_zero_units(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "other",
                    "action": "Rewrite the production plan",
                    "estimated_units": 1800,
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].approved_units, 0.0)
        self.assertEqual(report.options[0].verdict, REJECTED)
        self.assertEqual(report.residual_shortage, 1800.0)

    def test_an_unrecognised_action_type_does_not_slip_through(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "teleport_the_chicken",
                    "action": "Teleport it",
                    "estimated_units": 1800,
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].approved_units, 0.0)
        self.assertEqual(report.residual_shortage, 1800.0)

    def test_a_missing_action_type_is_treated_as_unrecognised(self):
        report = validation.validate_options(
            [{"action": "Do something", "estimated_units": 500}], berlin_snapshot()
        )
        self.assertEqual(report.options[0].approved_units, 0.0)

    def test_zero_and_negative_quantities_are_rejected(self):
        for quantity in (0, -500):
            with self.subTest(quantity=quantity):
                report = validation.validate_options(
                    [
                        {
                            "action_type": "transfer",
                            "action": "Move stock",
                            "estimated_units": quantity,
                            "source_fc": "HAM",
                        }
                    ],
                    berlin_snapshot(),
                )
                self.assertEqual(report.options[0].verdict, REJECTED)

    def test_a_missing_quantity_is_rejected_rather_than_assumed(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock",
                    "source_fc": "HAM",
                }
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.options[0].verdict, REJECTED)

    def test_no_options_leaves_the_whole_shortage_open(self):
        report = validation.validate_options([], berlin_snapshot())
        self.assertEqual(report.residual_shortage, 1800.0)
        self.assertEqual(report.approved_units_total, 0.0)


class DeliveryTiming(unittest.TestCase):
    """An action that cannot arrive in time is not a mitigation.

    Stock that exists but lands after the production run is worth nothing, so
    the window matters as much as the quantity.
    """

    def transfer(self, hours):
        return validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                }
            ],
            berlin_snapshot(hours_until_required=hours),
        ).options[0]

    def expedite(self, hours):
        return validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 1000,
                }
            ],
            berlin_snapshot(hours_until_required=hours),
        ).options[0]

    def test_transfer_with_a_week_of_notice_is_fine(self):
        self.assertEqual(self.transfer(7 * 24).approved_units, 900.0)

    def test_transfer_with_exactly_the_lead_time_is_allowed(self):
        option = self.transfer(business_rules.TRANSFER_LEAD_TIME_HOURS)
        self.assertEqual(option.approved_units, 900.0)

    def test_transfer_with_too_little_notice_is_rejected(self):
        option = self.transfer(business_rules.TRANSFER_LEAD_TIME_HOURS - 1)
        self.assertEqual(option.verdict, REJECTED)
        self.assertEqual(option.approved_units, 0.0)
        self.assertIn("in time", " ".join(option.reasons))

    def test_same_day_transfer_is_rejected(self):
        self.assertEqual(self.transfer(0).verdict, REJECTED)

    def test_expedite_with_48_hours_is_allowed(self):
        option = self.expedite(business_rules.EXPEDITE_MIN_NOTICE_HOURS)
        self.assertEqual(option.approved_units, 1000.0)

    def test_expedite_with_less_than_48_hours_is_rejected(self):
        option = self.expedite(business_rules.EXPEDITE_MIN_NOTICE_HOURS - 1)
        self.assertEqual(option.verdict, REJECTED)
        self.assertIn("48", " ".join(option.reasons))

    def test_a_transfer_can_still_work_when_an_expedite_cannot(self):
        """At 24 hours' notice, moving stock beats ordering more."""
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                },
                {
                    "action_type": "expedite",
                    "action": "Expedite from the supplier",
                    "estimated_units": 1200,
                },
            ],
            berlin_snapshot(hours_until_required=24),
        )
        by_type = {o.action_type: o for o in report.options}
        self.assertEqual(by_type["transfer"].approved_units, 900.0)
        self.assertEqual(by_type["expedite"].verdict, REJECTED)

    def test_unknown_window_does_not_silently_pass_as_feasible(self):
        """An unchecked rule is reported, not assumed satisfied."""
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Move stock from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                }
            ],
            berlin_snapshot(hours_until_required=None),
        )
        self.assertFalse(report.timing_checked)
        self.assertTrue(any("timing" in w.lower() for w in report.warnings))
        self.assertIn("not checked", " ".join(report.options[0].reasons).lower())

    def test_an_unchecked_window_does_not_make_a_clean_option_look_capped(self):
        """A note about an unverified rule is not a reduction in quantity."""
        option = self.transfer(None)
        self.assertEqual(option.approved_units, option.proposed_units)
        self.assertEqual(option.verdict, OK)

    def test_a_known_window_marks_the_report_as_checked(self):
        report = validation.validate_options(
            [], berlin_snapshot(hours_until_required=48)
        )
        self.assertTrue(report.timing_checked)


class ReportLevelBehaviour(unittest.TestCase):
    def test_every_option_requires_human_approval(self):
        options = [
            {
                "action_type": "transfer",
                "action": "t",
                "estimated_units": 100,
                "source_fc": "HAM",
            },
            {"action_type": "substitute", "action": "s", "estimated_units": 100},
            {"action_type": "expedite", "action": "e", "estimated_units": 100},
            {"action_type": "other", "action": "o", "estimated_units": 100},
        ]
        report = validation.validate_options(options, berlin_snapshot())
        self.assertEqual(len(report.options), 4)
        for option in report.options:
            with self.subTest(action_type=option.action_type):
                self.assertTrue(option.requires_human_approval)

    def test_over_coverage_is_flagged(self):
        report = validation.validate_options(
            [
                {
                    "action_type": "transfer",
                    "action": "Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                },
                {
                    "action_type": "transfer",
                    "action": "Munich",
                    "estimated_units": 200,
                    "source_fc": "MUC",
                },
                {"action_type": "substitute", "action": "sub", "estimated_units": 600},
                {"action_type": "expedite", "action": "exp", "estimated_units": 1200},
            ],
            berlin_snapshot(),
        )
        self.assertEqual(report.approved_units_total, 2900.0)
        self.assertEqual(report.residual_shortage, 0.0)
        self.assertTrue(any("over-procure" in w for w in report.warnings))

    def test_an_uncovered_shortage_asks_for_escalation(self):
        report = validation.validate_options([], berlin_snapshot())
        self.assertTrue(any("escalat" in w.lower() for w in report.warnings))

    def test_options_are_checked_in_the_sop_preferred_order(self):
        """A transfer gets first claim on stock, whatever order the model used."""
        report = validation.validate_options(
            [
                {"action_type": "expedite", "action": "e", "estimated_units": 100},
                {"action_type": "substitute", "action": "s", "estimated_units": 100},
                {
                    "action_type": "transfer",
                    "action": "t",
                    "estimated_units": 100,
                    "source_fc": "HAM",
                },
            ],
            berlin_snapshot(),
        )
        self.assertEqual(
            [o.action_type for o in report.options],
            ["transfer", "substitute", "expedite"],
        )

    def test_the_validator_never_increases_a_proposed_quantity(self):
        options = [
            {
                "action_type": "transfer",
                "action": "t",
                "estimated_units": 10,
                "source_fc": "HAM",
            },
            {"action_type": "substitute", "action": "s", "estimated_units": 10},
            {"action_type": "expedite", "action": "e", "estimated_units": 10},
        ]
        report = validation.validate_options(options, berlin_snapshot())
        for option in report.options:
            with self.subTest(action_type=option.action_type):
                self.assertLessEqual(option.approved_units, option.proposed_units)


if __name__ == "__main__":
    unittest.main()
