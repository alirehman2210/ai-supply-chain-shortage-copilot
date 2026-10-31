from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from langchain_openai import OpenAIEmbeddings

from src.config import DATABASE_URL, OPENAI_EMBEDDING_MODEL


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def vector_search(
    query: str,
    document_type: Optional[str] = None,
    supplier_id: Optional[str] = None,
    sku: Optional[str] = None,
    k: int = 4,
) -> list[dict[str, Any]]:
    """Semantic search over pgvector.

    This is intentionally implemented close to the database instead of hiding
    retrieval behind a high-level retriever. It makes embeddings + vector search
    easier to learn.
    """
    embedder = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)
    q = _vector_literal(embedder.embed_query(query))

    clauses = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if document_type:
        clauses.append("document_type = %s")
        params.append(document_type)
    if supplier_id:
        clauses.append("supplier_id = %s")
        params.append(supplier_id)
    if sku:
        clauses.append("(sku = %s OR sku IS NULL)")
        params.append(sku)

    where_sql = " AND ".join(clauses)
    sql = f"""
        SELECT document_name, document_type, supplier_id, sku, chunk_index, content,
               1 - (embedding <=> %s::vector) AS similarity
        FROM document_chunks
        WHERE {where_sql}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    final_params = [q] + params + [q, k]

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(final_params))
            return list(cur.fetchall())
