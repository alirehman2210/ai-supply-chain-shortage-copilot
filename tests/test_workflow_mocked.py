"""Integration tests: the real graph and the real database, with the AI stubbed.

MOCKED, not end to end. These drive the actual compiled LangGraph workflow
against the actual PostgreSQL data, but Claude and the embedding model are
replaced with stubs. That makes them fast, free and repeatable, and it lets
them force responses a real model would rarely produce — an invented quantity,
a malformed action, an outright failure.

What they prove: the wiring, the routing, the pause and resume, and that a bad
model response is contained.

What they cannot prove: that the real model and the real retrieval behave.
That needs `tests/test_end_to_end.py`, which makes real API calls.

Skipped automatically when LangGraph is not installed or Postgres is not up.
"""

import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def database_is_up() -> bool:
    if not HAS_LANGGRAPH:
        return False
    try:
        from src.tools.sql_tools import get_inventory

        return bool(get_inventory("BER"))
    except Exception:
        return False


DB_UP = database_is_up()
NEEDS = "needs LangGraph and a loaded PostgreSQL (docker compose up -d)"


# --- Stubs ------------------------------------------------------------------

POLICY_CHUNKS = [
    {
        "document_name": "substitution_policy.md",
        "document_type": "substitution_policy",
        "supplier_id": None,
        "sku": "CHK-250",
        "chunk_index": 0,
        "content": "CHK-250-B is approved for RCP-TERIYAKI and RCP-TUSCAN only.",
        "similarity": 0.91,
    }
]


class _Structured:
    def __init__(self, result):
        self._result = result

    def invoke(self, _prompt):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeLLM:
    """Stands in for ChatAnthropic. Returns whatever the test scripted."""

    def __init__(self, parsed, plan, narrative="A shortage was found."):
        self.parsed = parsed
        self.plan = plan
        self.narrative = narrative

    def with_structured_output(self, schema, **kwargs):
        # **kwargs so the stub keeps matching the real client's signature,
        # which takes method="json_schema".
        from src.nodes import ParsedQuestion

        return _Structured(self.parsed if schema is ParsedQuestion else self.plan)

    def invoke(self, _prompt):
        if isinstance(self.narrative, Exception):
            raise self.narrative
        return SimpleNamespace(content=self.narrative)


def plan_with(*options):
    from src.nodes import MitigationOption, MitigationPlan

    return MitigationPlan(options=[MitigationOption(**o) for o in options])


def berlin_question():
    from src.nodes import ParsedQuestion

    return ParsedQuestion(
        intent="mitigation",
        target_date="2026-09-29",
        target_fc="BER",
        target_sku="CHK-250",
    )


@unittest.skipUnless(DB_UP, NEEDS)
class WorkflowHarness(unittest.TestCase):
    """Runs the real graph with a scripted model."""

    counter = 0

    def run_workflow(self, parsed, plan, narrative="A shortage was found."):
        from src.graph import build_graph

        WorkflowHarness.counter += 1
        config = {
            "configurable": {"thread_id": f"mocked-{WorkflowHarness.counter}"}
        }
        app = build_graph()
        fake = FakeLLM(parsed, plan, narrative)

        with patch("src.nodes._llm", return_value=fake), patch(
            "src.nodes.vector_search", return_value=POLICY_CHUNKS
        ):
            state = app.invoke(
                {
                    "user_question": "Why is CHK-250 at risk in Berlin?",
                    "current_date": "2026-09-22",
                },
                config=config,
            )
        return app, config, state, fake

    def resume(self, app, config, fake, payload):
        from langgraph.types import Command

        with patch("src.nodes._llm", return_value=fake), patch(
            "src.nodes.vector_search", return_value=POLICY_CHUNKS
        ):
            return app.invoke(Command(resume=payload), config=config)


class ShortageWithValidOptions(WorkflowHarness):
    """Phase 7 scenario 1."""

    def setUp(self):
        self.app, self.config, self.state, self.fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Hamburg has spare stock.",
                }
            ),
        )

    def test_the_shortage_is_calculated_from_the_real_database(self):
        self.assertEqual(self.state["target_shortage"], 1800.0)

    def test_the_right_sku_site_and_date_are_used(self):
        self.assertEqual(self.state["target_sku"], "CHK-250")
        self.assertEqual(self.state["target_fc"], "BER")
        self.assertEqual(self.state["target_date"], "2026-09-29")

    def test_the_late_purchase_order_is_excluded(self):
        counted = {po["po_id"] for po in self.state["purchase_order_rows"]}
        self.assertNotIn("PO-882", counted)
        excluded = {po["po_id"] for po in self.state["excluded_purchase_orders"]}
        self.assertIn("PO-882", excluded)

    def test_the_affected_recipes_are_found(self):
        names = {r["recipe_name"] for r in self.state["impacted_recipes"]}
        self.assertEqual(
            names, {"Chicken Teriyaki", "Tuscan Chicken", "Creamy Chicken Pasta"}
        )

    def test_spare_stock_elsewhere_is_calculated(self):
        excess = {r["fc_id"]: float(r["excess_qty"]) for r in self.state["other_fc_rows"]}
        self.assertEqual(excess, {"HAM": 900.0, "MUC": 200.0})

    def test_the_proposal_was_validated(self):
        self.assertEqual(len(self.state["validated_options"]), 1)
        self.assertEqual(self.state["validated_options"][0]["approved_units"], 900.0)

    def test_the_workflow_paused_for_a_human(self):
        self.assertTrue(self.state.get("__interrupt__"))

    def test_nothing_was_decided_before_the_pause(self):
        self.assertIsNone(self.state.get("human_decision"))


class NoShortage(WorkflowHarness):
    """Phase 7 scenario 2: the cheap path."""

    def setUp(self):
        from src.nodes import ParsedQuestion

        self.app, self.config, self.state, _ = self.run_workflow(
            ParsedQuestion(
                intent="sku_investigation",
                target_date="2026-09-29",
                target_fc="MUC",
                target_sku="SALM-250",
            ),
            plan_with(),
        )

    def test_no_shortage_is_reported(self):
        self.assertEqual(self.state["target_shortage"], 0.0)

    def test_the_workflow_does_not_pause(self):
        self.assertFalse(self.state.get("__interrupt__"))

    def test_it_never_asked_for_mitigation_options(self):
        self.assertIsNone(self.state.get("validated_options"))

    def test_it_says_there_is_no_shortage(self):
        self.assertIn("No shortage", self.state["final_response"])


class SubstitutionExceedingStock(WorkflowHarness):
    """Phase 7 scenarios 3 and 4, through the whole workflow."""

    def setUp(self):
        self.app, self.config, self.state, self.fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "substitute",
                    "action": "Substitute CHK-250-B for the whole shortage",
                    "estimated_units": 1800,
                    "recipes": ["RCP-TERIYAKI", "RCP-TUSCAN", "RCP-PASTA"],
                    "rationale": "It is an approved alternate.",
                }
            ),
        )
        self.option = self.state["validated_options"][0]

    def test_the_quantity_is_capped_to_what_berlin_holds(self):
        self.assertEqual(self.option["proposed_units"], 1800.0)
        self.assertEqual(self.option["approved_units"], 600.0)

    def test_the_unapproved_recipe_is_named(self):
        self.assertIn("RCP-PASTA", " ".join(self.option["reasons"]))

    def test_the_remaining_shortage_is_reported(self):
        self.assertEqual(self.state["residual_shortage"], 1200.0)

    def test_the_planner_still_sees_what_was_proposed(self):
        self.assertEqual(self.option["verdict"], "capped")


class HumanApproval(WorkflowHarness):
    """Phase 7 scenarios 7 and 8: resume after a real pause."""

    def setUp(self):
        self.app, self.config, self.state, self.fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Spare stock.",
                }
            ),
        )

    def test_approving_records_the_decision_and_the_cover(self):
        final = self.resume(
            self.app,
            self.config,
            self.fake,
            {"decision": "approved", "approved_indexes": [0]},
        )
        summary = final["final_summary"]
        self.assertEqual(summary["decision"], "approved")
        self.assertEqual(summary["decided_units_total"], 900.0)
        self.assertEqual(summary["shortage_after_decision"], 900.0)

    def test_rejecting_leaves_the_shortage_standing(self):
        final = self.resume(
            self.app,
            self.config,
            self.fake,
            {"decision": "rejected", "approved_indexes": []},
        )
        summary = final["final_summary"]
        self.assertEqual(summary["decision"], "rejected")
        self.assertEqual(summary["decided_units_total"], 0.0)
        self.assertEqual(summary["shortage_after_decision"], 1800.0)

    def test_the_final_text_says_nothing_was_actioned(self):
        final = self.resume(
            self.app,
            self.config,
            self.fake,
            {"decision": "approved", "approved_indexes": [0]},
        )
        self.assertIn("No transfer, purchase order", final["final_response"])

    def test_the_facts_survive_the_pause(self):
        """Resuming must report the investigation it actually did."""
        final = self.resume(
            self.app,
            self.config,
            self.fake,
            {"decision": "approved", "approved_indexes": [0]},
        )
        self.assertEqual(final["final_summary"]["shortage"], 1800.0)
        self.assertEqual(final["final_summary"]["required"], 12400.0)


class BadModelOutput(WorkflowHarness):
    """Phase 7 scenario 9: an unsupported or invented recommendation."""

    def test_an_unknown_action_type_is_credited_nothing(self):
        _, _, state, _ = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "other",
                    "action": "Rewrite the production plan",
                    "estimated_units": 1800,
                    "rationale": "It would solve it.",
                }
            ),
        )
        self.assertEqual(state["validated_options"][0]["approved_units"], 0.0)
        self.assertEqual(state["residual_shortage"], 1800.0)

    def test_a_transfer_from_a_site_that_cannot_supply_is_rejected(self):
        _, _, state, _ = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer 5,000 units from Munich",
                    "estimated_units": 5000,
                    "source_fc": "MUC",
                    "rationale": "Munich looks well stocked.",
                }
            ),
        )
        self.assertEqual(state["validated_options"][0]["approved_units"], 200.0)

    def test_an_invented_quantity_in_the_narrative_is_withheld(self):
        app, config, _, fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Spare stock.",
                }
            ),
            narrative="Good news: we have recovered all 7,777 units needed.",
        )
        final = self.resume(
            app, config, fake, {"decision": "approved", "approved_indexes": [0]}
        )
        self.assertFalse(final["narrative_passed_fact_check"])
        self.assertNotIn("7,777", final["final_response"])
        self.assertIn("withheld", final["final_response"])
        self.assertEqual(final["final_summary"]["shortage"], 1800.0)

    def test_a_narrative_claiming_action_was_taken_is_withheld(self):
        app, config, _, fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Spare stock.",
                }
            ),
            narrative="I have placed the order with the supplier.",
        )
        final = self.resume(
            app, config, fake, {"decision": "approved", "approved_indexes": [0]}
        )
        self.assertFalse(final["narrative_passed_fact_check"])
        self.assertNotIn("placed the order", final["final_response"])

    def test_a_truthful_narrative_is_kept(self):
        app, config, _, fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Spare stock.",
                }
            ),
            narrative="Berlin is short 1,800 units and Hamburg can send 900.",
        )
        final = self.resume(
            app, config, fake, {"decision": "approved", "approved_indexes": [0]}
        )
        self.assertTrue(final["narrative_passed_fact_check"])
        self.assertIn("Hamburg can send 900", final["final_response"])


class ModelAndRetrievalFailures(WorkflowHarness):
    """Phase 7 scenario 10: the AI or retrieval is unavailable."""

    def test_a_failing_narrative_call_still_produces_a_verified_summary(self):
        app, config, _, fake = self.run_workflow(
            berlin_question(),
            plan_with(
                {
                    "action_type": "transfer",
                    "action": "Transfer from Hamburg",
                    "estimated_units": 900,
                    "source_fc": "HAM",
                    "rationale": "Spare stock.",
                }
            ),
            narrative=RuntimeError("API unavailable"),
        )
        final = self.resume(
            app, config, fake, {"decision": "approved", "approved_indexes": [0]}
        )
        self.assertFalse(final["narrative_passed_fact_check"])
        # The numbers still arrive, because they never needed the model.
        self.assertIn("1,800", final["final_response"])
        self.assertEqual(final["final_summary"]["shortage"], 1800.0)

    def test_a_failing_retrieval_call_surfaces_rather_than_being_swallowed(self):
        from src.graph import build_graph
        from src.tools.rag_tools import RetrievalFailed

        WorkflowHarness.counter += 1
        config = {"configurable": {"thread_id": f"mocked-fail-{self.counter}"}}
        app = build_graph()
        fake = FakeLLM(berlin_question(), plan_with())

        with patch("src.nodes._llm", return_value=fake), patch(
            "src.nodes.vector_search",
            side_effect=RetrievalFailed("embedding provider unreachable"),
        ):
            with self.assertRaises(RetrievalFailed):
                app.invoke(
                    {
                        "user_question": "Why is CHK-250 at risk in Berlin?",
                        "current_date": "2026-09-22",
                    },
                    config=config,
                )

    def test_a_retrieval_failure_message_carries_no_credentials(self):
        from src.config import redact
        from src.tools.rag_tools import RetrievalFailed

        error = RetrievalFailed(
            redact("auth failed for sk-ant-api03-" + "A" * 40)
        )
        self.assertNotIn("sk-ant", str(error))


@unittest.skipUnless(DB_UP, NEEDS)
class MissingPolicyDocuments(unittest.TestCase):
    """Phase 7 scenario 6: the contract is missing or is the wrong one."""

    def test_an_empty_retrieval_does_not_invent_a_rule(self):
        """With no contract retrieved, the limits still come from config.

        The validator does not read its limits out of retrieved text, so losing
        the document changes what the planner can read, not what is enforced.
        """
        from src import validation
        from src.scenario import build_scenario

        scenario = build_scenario("2026-09-29", "BER", "CHK-250")
        snapshot = scenario.constraint_snapshot()
        snapshot.hours_until_required = 7 * 24
        report = validation.validate_options(
            [
                {
                    "action_type": "expedite",
                    "action": "Expedite 5,000 units",
                    "estimated_units": 5000,
                }
            ],
            snapshot,
        )
        self.assertEqual(report.options[0].approved_units, 1200.0)

    def test_the_supplier_lookup_targets_the_sku_owner(self):
        """A contract search must not be hard-coded to one supplier."""
        from src.tools.sql_tools import get_supplier_for_sku

        self.assertEqual(get_supplier_for_sku("CHK-250"), "SUP-NORD")
        self.assertEqual(get_supplier_for_sku("SALM-250"), "SUP-FISH")
        self.assertIsNone(get_supplier_for_sku("NOT-A-SKU"))


if __name__ == "__main__":
    unittest.main()
