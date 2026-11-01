"""Reads the metadata header out of a policy document.

Each document in data/rag_docs/ starts with a few bold lines naming what kind
of document it is, which supplier it belongs to, and which ingredients it
covers:

    **Document type:** supplier_contract
    **Supplier:** SUP-NORD
    **Relevant SKUs:** CHK-250, CHK-250-B

That metadata is what lets a search for one supplier's contract avoid returning
another supplier's terms, so it has to be read correctly. A document that
covers two ingredients has to come back with both of them: the earlier version
of this parser only looked for a label spelled exactly "SKU", so
"Relevant SKUs" was missed entirely and the contract was indexed as applying to
no particular ingredient — which, with the old retrieval filter, meant it
applied to every ingredient.

No third-party imports here, so the parser can be tested without a database or
an embedding provider.
"""

import re
from dataclasses import dataclass, field

#: Labels that name an ingredient. All of them are read, and a document that
#: uses several (the substitution policy names a primary and an alternate) is
#: recorded as covering all of them.
SKU_LABELS = (
    "SKU",
    "SKUs",
    "Primary SKU",
    "Primary SKUs",
    "Alternate SKU",
    "Alternate SKUs",
    "Relevant SKU",
    "Relevant SKUs",
    "Applies to SKU",
    "Applies to SKUs",
)

DEFAULT_DOCUMENT_TYPE = "general"

#: Document types that are about specific ingredients by their nature. A
#: supplier contract covers the ingredients that supplier provides; a
#: substitution policy covers the ingredient being substituted.
#:
#: These must never be treated as applying to every ingredient. If one is
#: stored with no ingredients listed — because its header was written in a way
#: the parser did not read, which has happened — the safe reading is "this
#: document answers for nothing" rather than "this document answers for
#: everything". Otherwise one supplier's terms can be returned for another
#: supplier's ingredient.
#:
#: Anything not listed here is a general document: a transfer policy or a
#: shortage procedure applies whatever the ingredient is, so an empty list is
#: correct for those and means exactly that.
INGREDIENT_SCOPED_DOCUMENT_TYPES = (
    "supplier_contract",
    "substitution_policy",
    "ingredient_specification",
)


def is_ingredient_scoped(document_type: str) -> bool:
    """Whether this kind of document must name the ingredients it covers."""
    return document_type in INGREDIENT_SCOPED_DOCUMENT_TYPES


@dataclass
class DocumentMetadata:
    """What a document's header says about itself."""

    document_type: str = DEFAULT_DOCUMENT_TYPE
    supplier_id: str | None = None
    #: Every ingredient the document covers. Empty means it is general and
    #: applies whatever ingredient is being investigated — a transfer policy,
    #: for instance.
    skus: list[str] = field(default_factory=list)

    @property
    def primary_sku(self) -> str | None:
        """The first ingredient named, or None for a general document.

        Kept for display and for the single-value `sku` column. Filtering uses
        the full list, because a contract covering two ingredients must match
        a question about either one.
        """
        return self.skus[0] if self.skus else None

    @property
    def is_general(self) -> bool:
        return not self.skus


def _field(text: str, label: str) -> str | None:
    """Read one `**Label:** value` line, if it is there.

    The spacing is matched with `[ \\t]*` rather than `\\s*` on purpose. `\\s`
    includes newlines, so a label with nothing after it — `**SKU:**` followed
    by a line break — would run past the end of its own line and capture the
    next one as its value.
    """
    match = re.search(
        rf"\*\*[ \t]*{re.escape(label)}[ \t]*:[ \t]*\*\*[ \t]*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _split_skus(value: str) -> list[str]:
    """Split a value like 'CHK-250, CHK-250-B' into separate codes.

    Handles commas, semicolons, slashes and the word 'and', and strips the
    stray markdown and trailing whitespace these headers pick up.
    """
    parts = re.split(r"[,;/]| and ", value, flags=re.IGNORECASE)
    skus = []
    for part in parts:
        cleaned = part.strip().strip("`*_ ").rstrip(".")
        if cleaned:
            skus.append(cleaned)
    return skus


def parse_metadata(text: str) -> DocumentMetadata:
    """Read the header. Unknown or missing fields fall back to safe defaults.

    SKUs are de-duplicated but keep the order they appear in, so the first one
    named stays the primary.
    """
    skus: list[str] = []
    for label in SKU_LABELS:
        value = _field(text, label)
        if not value:
            continue
        for sku in _split_skus(value):
            if sku not in skus:
                skus.append(sku)

    return DocumentMetadata(
        document_type=_field(text, "Document type") or DEFAULT_DOCUMENT_TYPE,
        supplier_id=_field(text, "Supplier"),
        skus=skus,
    )
