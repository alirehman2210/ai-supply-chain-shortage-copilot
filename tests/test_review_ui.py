"""Tests the human review screens and the decision input.

Offline: no database, no model, no terminal. Input and output are passed in as
functions, so a whole review can be driven from a scripted list of answers.

What matters here is that a planner cannot be misled by the screens and that
unclear input is never guessed at.
"""

import unittest

from src import validation
from src.review_ui import (
    Decision,
    ask_for_decision,
    parse_selection,
    show_investigation,
    show_options,
)
from src.scenario import build_scenario
from src.summary import build_summary


def scripted(answers):
    """An input function that returns each answer in turn."""
    queue = list(answers)

    def ask(_prompt: str) -> str:
        return queue.pop(0) if queue else ""

    return ask


def collector():
    """An output function that records every line."""
    lines: list[str] = []
    return lines, lines.append


def berlin_summary(decision="pending", indexes=None):
    scenario = build_scenario("2026-09-29", "BER", "CHK-250")
    snapshot = scenario.constraint_snapshot()
    snapshot.hours_until_required = 7 * 24
    report = validation.validate_options(
        [
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
            {
                "action_type": "transfer",
                "action": "Transfer from a site with nothing spare",
                "estimated_units": 400,
                "source_fc": "NOWHERE",
            },
        ],
        snapshot,
    )
    return build_summary(scenario, report, decision, indexes or [])


class ParsingWhatThePlannerTyped(unittest.TestCase):
    def test_a_single_number(self):
        self.assertEqual(parse_selection("1", 3), Decision("approved", [0]))

    def test_comma_separated(self):
        self.assertEqual(parse_selection("1,3", 3), Decision("approved", [0, 2]))

    def test_space_separated(self):
        self.assertEqual(parse_selection("1 2", 3), Decision("approved", [0, 1]))

    def test_duplicates_are_collapsed(self):
        self.assertEqual(parse_selection("2,2,2", 3), Decision("approved", [1]))

    def test_out_of_order_is_sorted(self):
        self.assertEqual(parse_selection("3,1", 3), Decision("approved", [0, 2]))

    def test_all(self):
        self.assertEqual(parse_selection("all", 3), Decision("approved", [0, 1, 2]))

    def test_rejection_words(self):
        for word in ("none", "no", "n", "reject", "r"):
            with self.subTest(word=word):
                self.assertEqual(parse_selection(word, 3), Decision("rejected", []))

    def test_quit_words(self):
        for word in ("q", "quit", "cancel", "exit"):
            with self.subTest(word=word):
                self.assertEqual(parse_selection(word, 3), Decision("cancelled", []))

    def test_case_and_whitespace_are_forgiven(self):
        self.assertEqual(parse_selection("  ALL  ", 2), Decision("approved", [0, 1]))

    def test_nonsense_returns_none_rather_than_a_guess(self):
        for text in ("banana", "1a", "?", "--", "1;2"):
            with self.subTest(text=text):
                self.assertIsNone(parse_selection(text, 3))

    def test_a_number_out_of_range_returns_none(self):
        self.assertIsNone(parse_selection("4", 3))
        self.assertIsNone(parse_selection("0", 3))

    def test_a_partly_valid_list_is_rejected_entirely(self):
        """Approving 1 when the planner typed "1,9" would be a guess."""
        self.assertIsNone(parse_selection("1,9", 3))

    def test_empty_input_returns_none(self):
        self.assertIsNone(parse_selection("", 3))


class TheDecisionPrompt(unittest.TestCase):
    def test_a_valid_answer_is_returned(self):
        lines, out = collector()
        decision = ask_for_decision(berlin_summary(), scripted(["1"]), out)
        self.assertEqual(decision, Decision("approved", [0]))

    def test_it_re_asks_after_unclear_input(self):
        lines, out = collector()
        decision = ask_for_decision(
            berlin_summary(), scripted(["banana", "1"]), out
        )
        self.assertEqual(decision, Decision("approved", [0]))
        self.assertTrue(any("did not understand" in line for line in lines))

    def test_it_gives_up_rather_than_looping_forever(self):
        lines, out = collector()
        decision = ask_for_decision(
            berlin_summary(), scripted(["x", "y", "z"]), out
        )
        self.assertEqual(decision.decision, "cancelled")

    def test_it_says_nothing_has_been_actioned_before_asking(self):
        lines, out = collector()
        ask_for_decision(berlin_summary(), scripted(["1"]), out)
        text = "\n".join(lines)
        self.assertIn("Nothing has been actioned", text)
        self.assertIn("does not move stock", text)

    def test_it_lists_which_options_are_actually_possible(self):
        lines, out = collector()
        ask_for_decision(berlin_summary(), scripted(["1"]), out)
        self.assertTrue(
            any("actually possible" in line for line in lines),
            "the planner should be told which options can be done",
        )

    def test_approving_an_impossible_option_is_flagged_to_the_planner(self):
        """Approving a rejected option must not look like it worked.

        The validator reorders options into the SOP's preferred order, so the
        rejected one is found by its verdict rather than by a fixed position.
        """
        summary = berlin_summary()
        rejected_position = next(
            index
            for index, option in enumerate(summary.options)
            if option["verdict"] == "rejected"
        )
        lines, out = collector()
        decision = ask_for_decision(
            summary, scripted([str(rejected_position + 1)]), out
        )
        self.assertEqual(decision, Decision("approved", [rejected_position]))
        self.assertTrue(any("add no units" in line for line in lines))


class TheInvestigationScreen(unittest.TestCase):
    def setUp(self):
        self.lines, self.out = collector()
        show_investigation(berlin_summary(), self.out)
        self.text = "\n".join(self.lines)

    def test_it_shows_the_shortage(self):
        self.assertIn("1,800", self.text)

    def test_it_explains_the_purchase_order_that_was_not_counted(self):
        self.assertIn("PO-882", self.text)
        self.assertIn("after it is needed", self.text)

    def test_it_names_the_affected_recipes(self):
        for name in ("Chicken Teriyaki", "Tuscan Chicken", "Creamy Chicken Pasta"):
            with self.subTest(recipe=name):
                self.assertIn(name, self.text)

    def test_it_shows_spare_stock_net_of_each_sites_own_need(self):
        self.assertIn("HAM", self.text)
        self.assertIn("spare", self.text)

    def test_a_no_shortage_scenario_says_so(self):
        lines, out = collector()
        scenario = build_scenario("2026-09-29", "MUC", "SALM-250")
        report = validation.validate_options([], scenario.constraint_snapshot())
        show_investigation(build_summary(scenario, report), out)
        self.assertIn("no shortage", "\n".join(lines).lower())


class TheOptionsScreen(unittest.TestCase):
    def setUp(self):
        self.lines, self.out = collector()
        show_options(berlin_summary(), self.out)
        self.text = "\n".join(self.lines)

    def test_a_capped_option_shows_both_numbers(self):
        self.assertIn("Asked for 1,800 units", self.text)
        self.assertIn("600 units possible", self.text)

    def test_a_rejected_option_says_it_is_not_possible(self):
        self.assertIn("CANNOT", self.text)
        self.assertIn("Not possible", self.text)

    def test_every_cap_carries_a_reason(self):
        self.assertIn("Why:", self.text)

    def test_reasons_cite_a_source_document(self):
        self.assertIn(".md", self.text)

    def test_it_states_the_remaining_shortage(self):
        self.assertIn("still be short", self.text.lower())

    def test_it_avoids_developer_jargon(self):
        for jargon in ("action_type", "estimated_units", "approved_units", "verdict"):
            with self.subTest(jargon=jargon):
                self.assertNotIn(jargon, self.text)


if __name__ == "__main__":
    unittest.main()
