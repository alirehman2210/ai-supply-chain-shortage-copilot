"""Tests the document header parser.

Offline: no database, no model, no network.

This parser decides which ingredients a policy document applies to. Getting it
wrong is not a cosmetic problem: a contract indexed as applying to no
particular ingredient used to match every ingredient, so a question about
salmon could be answered with the chicken supplier's terms.
"""

import unittest
from pathlib import Path

from src.document_metadata import DEFAULT_DOCUMENT_TYPE, parse_metadata

DOCS = Path(__file__).resolve().parents[1] / "data" / "rag_docs"


class TheRealDocuments(unittest.TestCase):
    """Every document in the project must parse to the right metadata."""

    def meta(self, name: str):
        return parse_metadata((DOCS / name).read_text(encoding="utf-8"))

    def test_the_supplier_contract_carries_both_of_its_ingredients(self):
        """The bug this parser was rewritten for.

        The header says "Relevant SKUs", which the old parser did not
        recognise, so the contract was stored as covering nothing.
        """
        meta = self.meta("supplier_nordfarm_contract.md")
        self.assertEqual(meta.document_type, "supplier_contract")
        self.assertEqual(meta.supplier_id, "SUP-NORD")
        self.assertEqual(meta.skus, ["CHK-250", "CHK-250-B"])
        self.assertFalse(meta.is_general)

    def test_the_substitution_policy_carries_primary_and_alternate(self):
        meta = self.meta("substitution_policy.md")
        self.assertEqual(meta.document_type, "substitution_policy")
        self.assertEqual(meta.skus, ["CHK-250", "CHK-250-B"])

    def test_the_ingredient_specification_carries_one_ingredient(self):
        meta = self.meta("chicken_specification.md")
        self.assertEqual(meta.document_type, "ingredient_specification")
        self.assertEqual(meta.skus, ["CHK-250"])
        self.assertEqual(meta.primary_sku, "CHK-250")

    def test_the_transfer_policy_is_general(self):
        meta = self.meta("warehouse_transfer_policy.md")
        self.assertEqual(meta.document_type, "transfer_policy")
        self.assertEqual(meta.skus, [])
        self.assertTrue(meta.is_general)

    def test_the_shortage_sop_is_general(self):
        meta = self.meta("shortage_response_sop.md")
        self.assertEqual(meta.document_type, "shortage_sop")
        self.assertTrue(meta.is_general)

    def test_only_the_contract_names_a_supplier(self):
        """A supplier id on the wrong document would misdirect a search."""
        with_supplier = {
            path.name
            for path in DOCS.glob("*.md")
            if parse_metadata(path.read_text(encoding="utf-8")).supplier_id
        }
        self.assertEqual(with_supplier, {"supplier_nordfarm_contract.md"})

    def test_every_document_has_a_recognised_type(self):
        for path in sorted(DOCS.glob("*.md")):
            with self.subTest(doc=path.name):
                meta = parse_metadata(path.read_text(encoding="utf-8"))
                self.assertNotEqual(meta.document_type, DEFAULT_DOCUMENT_TYPE)

    def test_no_document_is_left_with_a_stray_marker_in_its_metadata(self):
        for path in sorted(DOCS.glob("*.md")):
            meta = parse_metadata(path.read_text(encoding="utf-8"))
            for sku in meta.skus:
                with self.subTest(doc=path.name, sku=sku):
                    self.assertNotIn("*", sku)
                    self.assertNotIn("`", sku)
                    self.assertEqual(sku, sku.strip())


class LabelVariants(unittest.TestCase):
    """The headers are hand-written, so the parser has to tolerate variation."""

    def skus(self, text: str) -> list[str]:
        return parse_metadata(text).skus

    def test_plain_sku(self):
        self.assertEqual(self.skus("**SKU:** CHK-250"), ["CHK-250"])

    def test_plural_skus(self):
        self.assertEqual(self.skus("**SKUs:** CHK-250"), ["CHK-250"])

    def test_relevant_skus(self):
        self.assertEqual(
            self.skus("**Relevant SKUs:** CHK-250, CHK-250-B"),
            ["CHK-250", "CHK-250-B"],
        )

    def test_primary_and_alternate_are_both_collected(self):
        self.assertEqual(
            self.skus("**Primary SKU:** A-1\n**Alternate SKU:** A-2"), ["A-1", "A-2"]
        )

    def test_the_word_and_separates(self):
        self.assertEqual(self.skus("**SKUs:** A-1 and A-2"), ["A-1", "A-2"])

    def test_semicolons_and_slashes_separate(self):
        self.assertEqual(self.skus("**SKUs:** A-1; A-2"), ["A-1", "A-2"])
        self.assertEqual(self.skus("**SKUs:** A-1/A-2"), ["A-1", "A-2"])

    def test_extra_whitespace_is_tolerated(self):
        self.assertEqual(
            self.skus("**  Relevant SKUs :  ** A-1 ,   A-2"), ["A-1", "A-2"]
        )

    def test_labels_are_case_insensitive(self):
        self.assertEqual(self.skus("**relevant skus:** A-1"), ["A-1"])

    def test_duplicates_are_collapsed_but_order_is_kept(self):
        text = "**Primary SKU:** A-1\n**Relevant SKUs:** A-1, A-2"
        self.assertEqual(self.skus(text), ["A-1", "A-2"])

    def test_the_first_named_ingredient_is_the_primary(self):
        meta = parse_metadata("**Relevant SKUs:** A-1, A-2")
        self.assertEqual(meta.primary_sku, "A-1")

    def test_no_sku_line_means_general(self):
        meta = parse_metadata("**Document type:** transfer_policy")
        self.assertEqual(meta.skus, [])
        self.assertIsNone(meta.primary_sku)
        self.assertTrue(meta.is_general)

    def test_an_empty_sku_line_does_not_produce_a_blank_entry(self):
        self.assertEqual(self.skus("**SKU:** \n**Document type:** x"), [])

    def test_a_document_with_no_header_at_all_gets_safe_defaults(self):
        meta = parse_metadata("Just some prose with no metadata.")
        self.assertEqual(meta.document_type, DEFAULT_DOCUMENT_TYPE)
        self.assertIsNone(meta.supplier_id)
        self.assertEqual(meta.skus, [])

    def test_sku_codes_containing_digits_and_hyphens_survive_intact(self):
        self.assertEqual(self.skus("**SKU:** CHK-250-B"), ["CHK-250-B"])

    def test_a_trailing_full_stop_is_removed(self):
        self.assertEqual(self.skus("**SKUs:** A-1, A-2."), ["A-1", "A-2"])

    def test_prose_mentioning_skus_later_is_not_picked_up_as_metadata(self):
        """Only the bold header lines count, not the body text."""
        text = "**SKU:** A-1\n\nThis clause also mentions B-2 and C-3 in passing."
        self.assertEqual(self.skus(text), ["A-1"])


class IngredientScopedTypes(unittest.TestCase):
    """Which document types must name the ingredients they cover.

    A scoped type with no ingredients listed is treated as answering for
    nothing rather than for everything, so a missed SKU header cannot make one
    supplier's contract apply to every ingredient.
    """

    def setUp(self):
        from src.document_metadata import (
            INGREDIENT_SCOPED_DOCUMENT_TYPES,
            is_ingredient_scoped,
        )

        self.scoped = INGREDIENT_SCOPED_DOCUMENT_TYPES
        self.is_scoped = is_ingredient_scoped

    def test_a_supplier_contract_is_scoped(self):
        self.assertTrue(self.is_scoped("supplier_contract"))

    def test_a_substitution_policy_is_scoped(self):
        self.assertTrue(self.is_scoped("substitution_policy"))

    def test_an_ingredient_specification_is_scoped(self):
        self.assertTrue(self.is_scoped("ingredient_specification"))

    def test_a_transfer_policy_is_general(self):
        self.assertFalse(self.is_scoped("transfer_policy"))

    def test_a_shortage_procedure_is_general(self):
        self.assertFalse(self.is_scoped("shortage_sop"))

    def test_an_unrecognised_type_is_treated_as_general(self):
        """A general document is the safe default for retrieval breadth.

        A type nobody has classified should not silently become a
        supplier-specific document, and it carries no supplier terms to leak.
        """
        self.assertFalse(self.is_scoped("something_new"))
        self.assertFalse(self.is_scoped(DEFAULT_DOCUMENT_TYPE))

    def test_every_scoped_document_in_the_project_names_its_ingredients(self):
        """The condition that would reopen the fail-open path."""
        for path in sorted(DOCS.glob("*.md")):
            meta = parse_metadata(path.read_text(encoding="utf-8"))
            if self.is_scoped(meta.document_type):
                with self.subTest(doc=path.name, type=meta.document_type):
                    self.assertTrue(
                        meta.skus,
                        f"{path.name} is {meta.document_type}, which must list "
                        f"the ingredients it covers, but lists none",
                    )

    def test_the_supplier_contract_is_among_them(self):
        """Guards against the scoped list quietly losing an entry."""
        meta = parse_metadata(
            (DOCS / "supplier_nordfarm_contract.md").read_text(encoding="utf-8")
        )
        self.assertIn(meta.document_type, self.scoped)


class SupplierField(unittest.TestCase):
    def test_a_supplier_is_read(self):
        self.assertEqual(
            parse_metadata("**Supplier:** SUP-NORD").supplier_id, "SUP-NORD"
        )

    def test_no_supplier_line_means_none(self):
        self.assertIsNone(parse_metadata("**Document type:** x").supplier_id)

    def test_the_supplier_is_not_confused_with_a_sku(self):
        meta = parse_metadata("**Supplier:** SUP-NORD\n**Relevant SKUs:** CHK-250")
        self.assertEqual(meta.supplier_id, "SUP-NORD")
        self.assertEqual(meta.skus, ["CHK-250"])


if __name__ == "__main__":
    unittest.main()
