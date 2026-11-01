"""Tests that a document search cannot return the wrong document.

MOCKED, not end to end. The real `vector_search` runs, against the real
database, with the real SQL filter. Only the embedding call is replaced, with a
fixed vector — so these exercise the production filtering logic and cost
nothing.

Because the query vector is a stub, the *ranking* is meaningless here. What is
being checked is which documents are eligible to come back at all, which is
what the metadata filter decides.

Skipped when PostgreSQL is not running or the documents are not indexed.
"""

import importlib.util
import unittest
from unittest.mock import patch

HAS_DEPS = importlib.util.find_spec("psycopg") is not None

# A stand-in query vector. The dimension has to match the column.
STUB_VECTOR = [0.0] * 1536


def _ready() -> tuple[bool, str]:
    if not HAS_DEPS:
        return False, "needs psycopg (pip install -r requirements.txt)"
    try:
        from src.tools.rag_tools import count_indexed_chunks
    except ImportError as error:
        return False, f"missing dependency: {error.name}"
    try:
        if count_indexed_chunks() == 0:
            return False, "no documents indexed (python -m scripts.ingest_rag_docs)"
    except Exception as error:
        return False, str(error).splitlines()[0]
    return True, ""


READY, WHY_NOT = _ready()


@unittest.skipUnless(READY, WHY_NOT)
class SkuFiltering(unittest.TestCase):
    """A document only answers for the ingredients it covers."""

    def search(self, **kwargs):
        from src.tools import rag_tools

        # Patch the embedding call only. The SQL, the filter and the database
        # are all real.
        with patch.object(rag_tools, "embed_query", return_value=STUB_VECTOR):
            return rag_tools.vector_search("any query text", k=20, **kwargs)

    def docs(self, **kwargs) -> set[str]:
        return {chunk["document_name"] for chunk in self.search(**kwargs)}

    def test_the_chicken_contract_answers_for_its_primary_ingredient(self):
        self.assertIn(
            "supplier_nordfarm_contract.md",
            self.docs(document_type="supplier_contract", sku="CHK-250"),
        )

    def test_the_chicken_contract_also_answers_for_its_alternate_ingredient(self):
        """The reason the SKU list exists: the contract covers both."""
        self.assertIn(
            "supplier_nordfarm_contract.md",
            self.docs(document_type="supplier_contract", sku="CHK-250-B"),
        )

    def test_the_chicken_contract_does_not_answer_for_salmon(self):
        """The bug this fix closes.

        While the contract was stored as covering no ingredient, it matched
        every ingredient, so this search returned the chicken terms.
        """
        self.assertNotIn(
            "supplier_nordfarm_contract.md",
            self.docs(document_type="supplier_contract", sku="SALM-250"),
        )

    def test_a_salmon_contract_search_returns_nothing_rather_than_the_wrong_one(self):
        """There is no salmon contract in the synthetic data.

        Returning nothing is the correct answer. Returning another supplier's
        terms would be worse than an empty result, because the checker would
        then apply the wrong limits.
        """
        self.assertEqual(self.docs(document_type="supplier_contract", sku="SALM-250"), set())

    def test_the_wrong_contract_cannot_slip_through_without_a_supplier_filter(self):
        """The reachable path for the old bug.

        `get_supplier_for_sku()` returns None for an ingredient with no
        supplier row, and the search then runs with no supplier filter at all.
        The SKU filter has to be enough on its own.
        """
        for sku in ("SALM-250", "BEEF-250", "RICE-500", "UNKNOWN-SKU"):
            with self.subTest(sku=sku):
                self.assertNotIn(
                    "supplier_nordfarm_contract.md",
                    self.docs(document_type="supplier_contract", sku=sku),
                )

    def test_a_general_policy_answers_for_any_ingredient(self):
        """The transfer policy names no ingredient, so it applies to all."""
        for sku in ("CHK-250", "SALM-250", "BEEF-250"):
            with self.subTest(sku=sku):
                self.assertIn(
                    "warehouse_transfer_policy.md",
                    self.docs(document_type="transfer_policy", sku=sku),
                )

    def test_the_substitution_policy_answers_for_both_its_ingredients(self):
        for sku in ("CHK-250", "CHK-250-B"):
            with self.subTest(sku=sku):
                self.assertIn(
                    "substitution_policy.md",
                    self.docs(document_type="substitution_policy", sku=sku),
                )

    def test_the_substitution_policy_does_not_answer_for_an_unrelated_ingredient(self):
        self.assertNotIn(
            "substitution_policy.md",
            self.docs(document_type="substitution_policy", sku="SALM-250"),
        )

    def test_omitting_the_sku_filter_still_returns_the_document(self):
        """Not every caller knows the ingredient."""
        self.assertIn(
            "supplier_nordfarm_contract.md",
            self.docs(document_type="supplier_contract"),
        )


@unittest.skipUnless(READY, WHY_NOT)
class AContractWithNoIngredientsListed(unittest.TestCase):
    """A scoped document missing its SKU header must answer for nothing.

    This is the fail-open case. A supplier contract whose header the parser did
    not read gets stored with an empty ingredient list. If an empty list meant
    "applies to everything", that contract would answer for every ingredient
    and hand back one supplier's terms for another supplier's goods — the same
    bug as before, arriving through a different door.

    The row is inserted for the test and removed afterwards.
    """

    TEMP_DOC = "__test_contract_with_no_skus.md"

    def setUp(self):
        import psycopg

        from src.config import DATABASE_URL

        self.addCleanup(self._remove)
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO document_chunks
                        (document_name, document_type, supplier_id, sku, skus,
                         chunk_index, content, embedding)
                    VALUES (%s, 'supplier_contract', 'SUP-FISH', NULL, '{}', 0,
                            'Fish supply terms. SKU header deliberately absent.',
                            array_fill(0.02::real, ARRAY[1536])::vector)
                    """,
                    (self.TEMP_DOC,),
                )
            conn.commit()

    def _remove(self):
        import psycopg

        from src.config import DATABASE_URL

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM document_chunks WHERE document_name = %s",
                    (self.TEMP_DOC,),
                )
            conn.commit()

    def docs(self, **kwargs) -> set[str]:
        from src.tools import rag_tools

        with patch.object(rag_tools, "embed_query", return_value=STUB_VECTOR):
            chunks = rag_tools.vector_search("any query text", k=20, **kwargs)
        return {chunk["document_name"] for chunk in chunks}

    def test_it_does_not_answer_for_an_unrelated_ingredient(self):
        self.assertNotIn(
            self.TEMP_DOC, self.docs(document_type="supplier_contract", sku="CHK-250")
        )

    def test_it_does_not_answer_for_any_ingredient_at_all(self):
        for sku in ("CHK-250", "CHK-250-B", "SALM-250", "BEEF-250", "RICE-500"):
            with self.subTest(sku=sku):
                self.assertNotIn(
                    self.TEMP_DOC,
                    self.docs(document_type="supplier_contract", sku=sku),
                )

    def test_it_cannot_sneak_in_when_no_supplier_filter_is_applied(self):
        """The reachable path: an ingredient with no supplier row."""
        self.assertNotIn(
            self.TEMP_DOC,
            self.docs(document_type="supplier_contract", sku="UNKNOWN-SKU"),
        )

    def test_the_correct_contract_is_still_returned_alongside_it(self):
        """Failing closed must not take the good document down with it."""
        self.assertIn(
            "supplier_nordfarm_contract.md",
            self.docs(document_type="supplier_contract", sku="CHK-250"),
        )

    def test_it_is_still_visible_when_no_sku_filter_is_given(self):
        """It is not hidden from the index, only from ingredient questions."""
        self.assertIn(self.TEMP_DOC, self.docs(document_type="supplier_contract"))

    def test_a_general_policy_with_no_skus_is_unaffected(self):
        """The fallback is denied to scoped types only."""
        self.assertIn(
            "warehouse_transfer_policy.md",
            self.docs(document_type="transfer_policy", sku="SALM-250"),
        )


@unittest.skipUnless(READY, WHY_NOT)
class SupplierFiltering(unittest.TestCase):
    def search(self, **kwargs):
        from src.tools import rag_tools

        with patch.object(rag_tools, "embed_query", return_value=STUB_VECTOR):
            return rag_tools.vector_search("any query text", k=20, **kwargs)

    def test_filtering_by_one_supplier_excludes_every_other(self):
        for chunk in self.search(document_type="supplier_contract", supplier_id="SUP-NORD"):
            with self.subTest(doc=chunk["document_name"]):
                self.assertEqual(chunk["supplier_id"], "SUP-NORD")

    def test_another_suppliers_search_cannot_return_nordfarm(self):
        for supplier in ("SUP-FISH", "SUP-MEAT", "SUP-GRAIN"):
            with self.subTest(supplier=supplier):
                docs = {
                    chunk["document_name"]
                    for chunk in self.search(
                        document_type="supplier_contract", supplier_id=supplier
                    )
                }
                self.assertNotIn("supplier_nordfarm_contract.md", docs)

    def test_supplier_and_sku_filters_combine(self):
        """Both have to agree, so neither can rescue a wrong match."""
        docs = {
            chunk["document_name"]
            for chunk in self.search(
                document_type="supplier_contract",
                supplier_id="SUP-NORD",
                sku="SALM-250",
            )
        }
        self.assertEqual(docs, set())


@unittest.skipUnless(READY, WHY_NOT)
class IndexedMetadata(unittest.TestCase):
    """What is actually stored, after indexing or backfilling."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row

        from src.config import DATABASE_URL

        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document_name, document_type, supplier_id, sku, skus,
                           count(*) AS chunks
                    FROM document_chunks
                    GROUP BY 1,2,3,4,5
                    """
                )
                cls.rows = {row["document_name"]: row for row in cur.fetchall()}

    def test_the_contract_is_stored_with_both_ingredients(self):
        row = self.rows["supplier_nordfarm_contract.md"]
        self.assertEqual(row["skus"], ["CHK-250", "CHK-250-B"])
        self.assertEqual(row["supplier_id"], "SUP-NORD")

    def test_the_substitution_policy_is_stored_with_both_ingredients(self):
        self.assertEqual(
            self.rows["substitution_policy.md"]["skus"], ["CHK-250", "CHK-250-B"]
        )

    def test_general_policies_are_stored_with_an_empty_list(self):
        for name in ("warehouse_transfer_policy.md", "shortage_response_sop.md"):
            with self.subTest(doc=name):
                self.assertEqual(self.rows[name]["skus"], [])

    def test_no_document_is_left_with_null_skus(self):
        """NULL was the state that caused the bug. Nothing should carry it."""
        for name, row in self.rows.items():
            with self.subTest(doc=name):
                self.assertIsNotNone(row["skus"])

    def test_the_stored_metadata_matches_what_the_parser_reads_now(self):
        """Catches a database left behind after the documents changed."""
        from pathlib import Path

        from src.document_metadata import parse_metadata

        docs = Path(__file__).resolve().parents[1] / "data" / "rag_docs"
        for path in sorted(docs.glob("*.md")):
            with self.subTest(doc=path.name):
                expected = parse_metadata(path.read_text(encoding="utf-8"))
                row = self.rows.get(path.name)
                self.assertIsNotNone(row, f"{path.name} is not indexed")
                self.assertEqual(row["document_type"], expected.document_type)
                self.assertEqual(row["supplier_id"], expected.supplier_id)
                self.assertEqual(row["skus"], expected.skus)

    def test_there_are_no_duplicate_chunks(self):
        import psycopg

        from src.config import DATABASE_URL

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT document_name, chunk_index
                        FROM document_chunks
                        GROUP BY 1,2 HAVING count(*) > 1
                    ) duplicates
                    """
                )
                self.assertEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
