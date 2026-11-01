"""Vector search over the policy documents stored in pgvector.

Retrieval is written close to the database rather than behind a high-level
retriever abstraction, so the embedding step and the distance query are both
visible.

The embedding call needs OPENAI_API_KEY. It is read from the environment and
never logged: errors from the provider are passed through `redact()` before
they are raised, because a client error can echo request details.
"""

from typing import Any, Optional

import psycopg
from langchain_openai import OpenAIEmbeddings
from psycopg.rows import dict_row

from src.document_metadata import INGREDIENT_SCOPED_DOCUMENT_TYPES
from src.config import (
    DATABASE_URL,
    OPENAI_EMBEDDING_MODEL,
    redact,
    require_api_key,
)


class RetrievalFailed(RuntimeError):
    """Retrieval could not complete. Message is credential-safe."""


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def embed_query(text: str) -> list[float]:
    """Turn one string into a vector.

    Raises MissingCredential with setup instructions if the key is absent,
    rather than a provider error that is harder to act on.
    """
    require_api_key("OPENAI_API_KEY")
    try:
        return OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL).embed_query(text)
    except Exception as error:
        raise RetrievalFailed(
            f"Embedding call failed: {redact(error)}"
        ) from None


def vector_search(
    query: str,
    document_type: Optional[str] = None,
    supplier_id: Optional[str] = None,
    sku: Optional[str] = None,
    k: int = 4,
) -> list[dict[str, Any]]:
    """Find the k chunks closest in meaning to the query.

    The metadata filters narrow the search before the distance comparison, so a
    question about one supplier's contract cannot return another supplier's
    terms.

    `sku` matches a document that lists that ingredient among the ones it
    covers. A general document that lists none at all also matches, because a
    transfer policy applies whatever the ingredient is — but an
    ingredient-scoped document (a supplier contract, a substitution policy)
    that lists none matches nothing, so a missed SKU header cannot turn one
    supplier's terms into an answer for every ingredient.
    """
    vector = _vector_literal(embed_query(query))

    clauses = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if document_type:
        clauses.append("document_type = %s")
        params.append(document_type)
    if supplier_id:
        clauses.append("supplier_id = %s")
        params.append(supplier_id)
    if sku:
        # Match when the document names this ingredient, or when it names none
        # at all AND is a general document — a transfer policy applies whatever
        # the ingredient is.
        #
        # The empty-list fallback is deliberately denied to ingredient-scoped
        # types. A supplier contract stored with no ingredients listed, because
        # its header was written in a way the parser did not read, would
        # otherwise match every ingredient and hand back one supplier's terms
        # for another supplier's ingredient. Failing closed there means such a
        # document answers for nothing, which is noticeable, rather than for
        # everything, which is not.
        clauses.append(
            "(%s = ANY(skus) OR ((skus IS NULL OR cardinality(skus) = 0) "
            "AND NOT (document_type = ANY(%s))))"
        )
        params.append(sku)
        params.append(list(INGREDIENT_SCOPED_DOCUMENT_TYPES))

    sql = f"""
        SELECT document_name, document_type, supplier_id, sku, skus, chunk_index, content,
               1 - (embedding <=> %s::vector) AS similarity
        FROM document_chunks
        WHERE {" AND ".join(clauses)}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """

    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple([vector] + params + [vector, k]))
                return list(cur.fetchall())
    except psycopg.OperationalError as error:
        raise RetrievalFailed(
            f"Could not reach PostgreSQL for retrieval. Is it running? "
            f"Try: docker compose up -d\nDriver said: {redact(error)}"
        ) from None


def count_indexed_chunks() -> int:
    """How many document chunks are indexed.

    Used as a pre-flight check: zero means the documents have not been
    ingested yet, which is a much clearer thing to report than empty
    retrieval results later on.
    """
    try:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM document_chunks")
                return int(cur.fetchone()["n"])
    except psycopg.Error:
        return 0
