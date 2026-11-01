"""Real end-to-end tests. These make paid API calls.

Nothing here is mocked: real Claude, real embeddings, real pgvector retrieval,
real PostgreSQL. This is the only test file that can prove the model and the
retrieval actually work, and it is the only one that costs money.

They are opt-in, so `python -m unittest discover tests` never spends anything
by accident. To run them:

    RUN_E2E=1 python -m unittest tests.test_end_to_end -v

Requires a loaded database, indexed documents, and both API keys in .env.
One full pass makes roughly 10-14 Claude calls and a similar number of small
embedding calls.
"""

import importlib.util
import os
import unittest

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
OPTED_IN = os.getenv("RUN_E2E", "").strip() == "1"


def _ready() -> tuple[bool, str]:
    if not OPTED_IN:
        return False, "set RUN_E2E=1 to run tests that make real paid API calls"
    if not HAS_LANGGRAPH:
        return False, "needs LangGraph (pip install -r requirements.txt)"
    try:
        from src.config import missing_api_keys
        from src.tools.rag_tools import count_indexed_chunks
        from src.tools.sql_tools import get_inventory
    except ImportError as error:
        return False, f"missing dependency: {error.name}"

    missing = missing_api_keys()
    if missing:
        return False, f"missing credentials in .env: {', '.join(missing)}"
    try:
        if not get_inventory("BER"):
            return False, "database is empty (python -m scripts.load_structured_data)"
    except Exception as error:
        return False, str(error).splitlines()[0]
    if count_indexed_chunks() == 0:
        return False, "no documents indexed (python -m scripts.ingest_rag_docs)"
    return True, ""


READY, WHY_NOT = _ready()


@unittest.skipUnless(READY, WHY_NOT)
class RealQuestionParsing(unittest.TestCase):
    """Claude turns a planner's sentence into a date, a site and a SKU."""

    def parse(self, question: str, current_date: str = "2026-09-22"):
        from src.nodes import understand_question

        return understand_question(
            {"user_question": question, "current_date": current_date}
        )

    def test_it_identifies_the_site_ingredient_and_date(self):
        parsed = self.parse(
            "Why is CHK-250 at risk in Berlin on 2026-09-29 and what can we do?"
        )
        self.assertEqual(parsed["target_fc"], "BER")
        self.assertEqual(parsed["target_sku"], "CHK-250")
        self.assertEqual(parsed["target_date"], "2026-09-29")

    def test_it_resolves_a_city_name_to_a_site_code(self):
        parsed = self.parse("Is Hamburg short of chicken on 2026-09-29?")
        self.assertEqual(parsed["target_fc"], "HAM")

    def test_it_resolves_a_relative_date(self):
        """2026-09-22 is a Tuesday, so next Tuesday is 2026-09-29."""
        parsed = self.parse(
            "Are we short of CHK-250 in Berlin next Tuesday?",
            current_date="2026-09-22",
        )
        self.assertEqual(parsed["target_date"], "2026-09-29")


@unittest.skipUnless(READY, WHY_NOT)
class RealRetrieval(unittest.TestCase):
    """The three golden questions that depend on pgvector retrieval."""

    def search(self, query: str, **kwargs):
        from src.tools.rag_tools import vector_search

        return vector_search(query, **kwargs)

    def test_gq03_substitution_policy_is_found_for_the_pasta_recipe(self):
        chunks = self.search(
            "Can CHK-250-B be substituted into Creamy Chicken Pasta?",
            document_type="substitution_policy",
            sku="CHK-250",
            k=3,
        )
        self.assertTrue(chunks, "retrieval returned nothing")
        joined = " ".join(c["content"] for c in chunks)
        self.assertIn("substitution_policy.md", {c["document_name"] for c in chunks})
        self.assertIn("not approved", joined)
        self.assertIn("RCP-PASTA", joined)

    def test_gq04_supplier_contract_gives_the_weekly_expedite_allowance(self):
        chunks = self.search(
            "What is the maximum additional quantity through the expedited process "
            "in a calendar week?",
            document_type="supplier_contract",
            supplier_id="SUP-NORD",
            k=3,
        )
        self.assertTrue(chunks, "retrieval returned nothing")
        joined = " ".join(c["content"] for c in chunks)
        self.assertIn(
            "supplier_nordfarm_contract.md", {c["document_name"] for c in chunks}
        )
        self.assertIn("1,200", joined)

    def test_gq06_transfer_policy_requires_human_approval(self):
        chunks = self.search(
            "Does an inter-fulfillment-center transfer require human approval?",
            document_type="transfer_policy",
            k=3,
        )
        self.assertTrue(chunks, "retrieval returned nothing")
        joined = " ".join(c["content"] for c in chunks).lower()
        self.assertIn(
            "warehouse_transfer_policy.md", {c["document_name"] for c in chunks}
        )
        self.assertIn("approval", joined)

    def test_a_contract_search_cannot_return_another_suppliers_terms(self):
        chunks = self.search(
            "expedited supply limits", document_type="supplier_contract",
            supplier_id="SUP-FISH", k=3,
        )
        for chunk in chunks:
            self.assertNotEqual(chunk["supplier_id"], "SUP-NORD")

    def test_semantic_search_finds_the_clause_without_the_exact_words(self):
        """'emergency chicken delivery' should reach the expedite clause."""
        chunks = self.search(
            "emergency chicken delivery at short notice",
            document_type="supplier_contract",
            k=3,
        )
        joined = " ".join(c["content"] for c in chunks).lower()
        self.assertIn("expedited", joined)


@unittest.skipUnless(READY, WHY_NOT)
class RealWorkflow(unittest.TestCase):
    """The whole thing: Claude, retrieval, validation, pause, resume."""

    @classmethod
    def setUpClass(cls):
        from src.graph import build_graph

        cls.app = build_graph()
        cls.config = {"configurable": {"thread_id": "e2e-real-1"}}
        cls.state = cls.app.invoke(
            {
                "user_question": (
                    "Why is CHK-250 at risk in Berlin on 2026-09-29 and what "
                    "can we do about it?"
                ),
                "current_date": "2026-09-22",
            },
            config=cls.config,
        )

    def test_the_shortage_is_1800_units(self):
        self.assertEqual(self.state["target_shortage"], 1800.0)

    def test_the_late_purchase_order_was_not_counted(self):
        counted = {po["po_id"] for po in self.state["purchase_order_rows"]}
        self.assertNotIn("PO-882", counted)

    def test_the_affected_recipes_were_identified(self):
        names = {r["recipe_name"] for r in self.state["impacted_recipes"]}
        self.assertEqual(
            names, {"Chicken Teriyaki", "Tuscan Chicken", "Creamy Chicken Pasta"}
        )

    def test_spare_stock_at_the_other_sites_is_correct(self):
        excess = {
            r["fc_id"]: float(r["excess_qty"]) for r in self.state["other_fc_rows"]
        }
        self.assertEqual(excess, {"HAM": 900.0, "MUC": 200.0})

    def test_the_policy_documents_were_retrieved(self):
        for key in (
            "supplier_contract_chunks",
            "substitution_policy_chunks",
            "transfer_policy_chunks",
        ):
            with self.subTest(key=key):
                self.assertTrue(self.state.get(key), f"{key} came back empty")

    def test_claude_proposed_at_least_one_option(self):
        self.assertTrue(self.state.get("mitigation_options"))

    def test_every_proposal_was_validated(self):
        self.assertEqual(
            len(self.state["validated_options"]),
            len(self.state["mitigation_options"]),
        )

    def test_no_approved_quantity_exceeds_what_was_proposed(self):
        for option in self.state["validated_options"]:
            with self.subTest(option=option["description"][:40]):
                self.assertLessEqual(
                    option["approved_units"], option["proposed_units"]
                )

    def test_no_substitution_exceeds_the_stock_on_hand(self):
        """The original bug, against whatever Claude actually proposed."""
        on_hand = float(self.state.get("substitute_on_hand", 0.0))
        total = sum(
            o["approved_units"]
            for o in self.state["validated_options"]
            if o["action_type"] == "substitute"
        )
        self.assertLessEqual(total, on_hand)

    def test_no_transfer_exceeds_the_sending_sites_spare_stock(self):
        excess = {
            r["fc_id"]: float(r["excess_qty"]) for r in self.state["other_fc_rows"]
        }
        used: dict[str, float] = {}
        for option in self.state["validated_options"]:
            if option["action_type"] == "transfer" and option["source_fc"]:
                used[option["source_fc"]] = (
                    used.get(option["source_fc"], 0.0) + option["approved_units"]
                )
        for fc_id, amount in used.items():
            with self.subTest(fc=fc_id):
                self.assertLessEqual(amount, excess.get(fc_id, 0.0))

    def test_every_option_requires_human_approval(self):
        for option in self.state["validated_options"]:
            with self.subTest(option=option["description"][:40]):
                self.assertTrue(option["requires_human_approval"])

    def test_the_workflow_paused_for_a_human(self):
        self.assertTrue(self.state.get("__interrupt__"))

    def test_resuming_with_an_approval_produces_a_verified_summary(self):
        from langgraph.types import Command

        config = {"configurable": {"thread_id": "e2e-real-approve"}}
        app = self.app
        app.invoke(
            {
                "user_question": (
                    "Why is CHK-250 at risk in Berlin on 2026-09-29?"
                ),
                "current_date": "2026-09-22",
            },
            config=config,
        )
        final = app.invoke(
            Command(resume={"decision": "approved", "approved_indexes": [0]}),
            config=config,
        )
        self.assertEqual(final["final_summary"]["shortage"], 1800.0)
        self.assertIn("1,800", final["final_response"])
        self.assertIn("No transfer, purchase order", final["final_response"])

    def test_the_narrative_was_fact_checked(self):
        from langgraph.types import Command

        config = {"configurable": {"thread_id": "e2e-real-check"}}
        self.app.invoke(
            {
                "user_question": "Why is CHK-250 at risk in Berlin on 2026-09-29?",
                "current_date": "2026-09-22",
            },
            config=config,
        )
        final = self.app.invoke(
            Command(resume={"decision": "rejected", "approved_indexes": []}),
            config=config,
        )
        # Either it passed and was shown, or it failed and was withheld. What
        # must never happen is an unchecked narrative reaching the planner.
        self.assertIn("narrative_passed_fact_check", final)
        if not final["narrative_passed_fact_check"]:
            self.assertIn("withheld", final["final_response"])


if __name__ == "__main__":
    unittest.main()
