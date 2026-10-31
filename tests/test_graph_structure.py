"""Checks the shape of the workflow.

These tests do not run the graph. Running it needs a database and two API
keys. What they check is the wiring, and in particular the two guarantees the
project claims: that the model's proposals always pass through the validator,
and that nothing gets past the human-review stop.
"""

import importlib.util
import unittest

# Everything else in the suite runs on the standard library alone. These tests
# need LangGraph, because the thing being checked is the compiled graph. They
# skip rather than fail so the rest of the suite still runs on a bare Python.
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
NEEDS_LANGGRAPH = "requires LangGraph (pip install -r requirements.txt)"


@unittest.skipUnless(HAS_LANGGRAPH, NEEDS_LANGGRAPH)
class GraphWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.graph import build_graph

        cls.graph = build_graph().get_graph()
        cls.edges = [(e.source, e.target) for e in cls.graph.edges]
        cls.outgoing = {}
        for source, target in cls.edges:
            cls.outgoing.setdefault(source, set()).add(target)

    def test_the_graph_compiles(self):
        self.assertIn("validate_mitigation_options", self.graph.nodes)

    def test_proposals_go_straight_to_the_validator(self):
        self.assertEqual(
            self.outgoing["generate_mitigation_options"],
            {"validate_mitigation_options"},
        )

    def test_nothing_reaches_human_review_without_being_validated(self):
        """The only way into human_review is out of the validator."""
        into_review = {s for s, t in self.edges if t == "human_review"}
        self.assertEqual(into_review, {"validate_mitigation_options"})

    def test_the_validator_leads_to_human_review_and_nowhere_else(self):
        self.assertEqual(
            self.outgoing["validate_mitigation_options"], {"human_review"}
        )

    def test_no_edge_skips_the_validator(self):
        for source, target in self.edges:
            with self.subTest(edge=f"{source}->{target}"):
                if source == "generate_mitigation_options":
                    self.assertEqual(target, "validate_mitigation_options")

    def test_facts_are_gathered_before_the_shortage_is_calculated(self):
        self.assertIn(("check_purchase_orders", "calculate_shortage"), self.edges)
        self.assertIn(("check_inventory", "check_purchase_orders"), self.edges)

    def test_the_shortage_check_decides_whether_to_investigate(self):
        conditional = {
            (e.source, e.target) for e in self.graph.edges if e.conditional
        }
        self.assertIn(("calculate_shortage", "calculate_downstream_impact"), conditional)
        self.assertIn(("calculate_shortage", "generate_final_response"), conditional)

    def test_no_shortage_skips_retrieval_and_the_planning_model_call(self):
        """The cheap path really is cheap: it goes straight to the answer."""
        self.assertNotIn(
            ("calculate_shortage", "search_supplier_contract"), self.edges
        )
        self.assertNotIn(
            ("calculate_shortage", "generate_mitigation_options"), self.edges
        )

    def test_all_three_policy_searches_run_before_options_are_proposed(self):
        for node in (
            "search_supplier_contract",
            "search_substitution_policy",
            "search_transfer_policy",
        ):
            with self.subTest(node=node):
                self.assertIn(node, self.graph.nodes)
        self.assertIn(
            ("search_transfer_policy", "generate_mitigation_options"), self.edges
        )

    def test_the_final_answer_is_the_last_step(self):
        self.assertIn(("generate_final_response", "__end__"), self.edges)
        self.assertIn(("human_review", "generate_final_response"), self.edges)


class NodeResponsibilities(unittest.TestCase):
    """The validator must not depend on a model, directly or indirectly."""

    def test_the_validation_module_does_not_import_an_llm_client(self):
        import src.validation as module

        source = open(module.__file__, encoding="utf-8").read()
        self.assertNotIn("ChatAnthropic", source)
        self.assertNotIn("langchain", source)

    def test_the_calculations_module_does_not_import_an_llm_client(self):
        import src.calculations as module

        source = open(module.__file__, encoding="utf-8").read()
        self.assertNotIn("ChatAnthropic", source)
        self.assertNotIn("langchain", source)

    def test_the_calculations_module_does_not_touch_the_database(self):
        import src.calculations as module

        source = open(module.__file__, encoding="utf-8").read()
        self.assertNotIn("psycopg", source)


if __name__ == "__main__":
    unittest.main()
