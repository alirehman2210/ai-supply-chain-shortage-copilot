"""Repairs the metadata on already-indexed documents. Makes no API calls.

    python -m scripts.backfill_document_metadata

Use this when the metadata parser changes but the documents themselves have
not. It re-reads each document's header and updates the metadata columns on the
rows that are already stored, leaving the embeddings untouched.

That matters for two reasons. The embeddings are what cost money to produce, and
they do not depend on the header at all — so re-indexing from scratch would pay
to compute vectors that are already correct. And an UPDATE cannot create
duplicate chunks, because it adds no rows.

Safe to run repeatedly: it sets each row to whatever the document says now, so
running it twice leaves the same result as running it once.

If a document has been edited, or a new one added, the chunk text itself has
changed and this is the wrong tool — run `scripts.ingest_rag_docs` instead,
which re-chunks and re-embeds everything.
"""

import sys
from pathlib import Path

import psycopg

from src.config import DATABASE_URL, redact
from src.document_metadata import parse_metadata

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "rag_docs"


def main() -> int:
    documents = sorted(DOCS.glob("*.md"))
    if not documents:
        print(f"No documents found in {DOCS}")
        return 1

    print("Backfilling document metadata (no embeddings recomputed)")
    print("-" * 70)

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM document_chunks")
                total = cur.fetchone()[0]
                if total == 0:
                    print("\nNothing is indexed yet, so there is no metadata to")
                    print("repair. Run: python -m scripts.ingest_rag_docs")
                    return 1

                changed_rows = 0
                for path in documents:
                    meta = parse_metadata(path.read_text(encoding="utf-8"))

                    # Only touch rows whose metadata actually differs, so the
                    # output says what changed rather than just what was seen.
                    cur.execute(
                        """
                        UPDATE document_chunks
                        SET document_type = %s,
                            supplier_id   = %s,
                            sku           = %s,
                            skus          = %s
                        WHERE document_name = %s
                          AND (document_type IS DISTINCT FROM %s
                               OR supplier_id IS DISTINCT FROM %s
                               OR sku         IS DISTINCT FROM %s
                               OR skus        IS DISTINCT FROM %s)
                        """,
                        (
                            meta.document_type,
                            meta.supplier_id,
                            meta.primary_sku,
                            meta.skus,
                            path.name,
                            meta.document_type,
                            meta.supplier_id,
                            meta.primary_sku,
                            meta.skus,
                        ),
                    )
                    updated = cur.rowcount
                    changed_rows += updated

                    cur.execute(
                        "SELECT count(*) FROM document_chunks WHERE document_name = %s",
                        (path.name,),
                    )
                    stored = cur.fetchone()[0]

                    state = f"{updated} of {stored} chunk(s) updated" if updated else "already correct"
                    skus = ", ".join(meta.skus) if meta.skus else "(general, applies to any)"
                    print(f"  {path.name}")
                    print(f"     type     : {meta.document_type}")
                    print(f"     supplier : {meta.supplier_id or '(none)'}")
                    print(f"     skus     : {skus}")
                    print(f"     {state}")

            conn.commit()
    except psycopg.OperationalError as error:
        print(f"\nCould not reach PostgreSQL. Is it running? docker compose up -d")
        print(f"Driver said: {redact(error)}")
        return 1
    except psycopg.errors.UndefinedColumn:
        print("\nThe document_chunks table has no 'skus' column yet.")
        print("Apply the schema first:")
        print("  psql $DATABASE_URL -f sql/schema.sql")
        return 1

    print("-" * 70)
    print(f"{changed_rows} row(s) updated. Embeddings untouched, no API calls made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
