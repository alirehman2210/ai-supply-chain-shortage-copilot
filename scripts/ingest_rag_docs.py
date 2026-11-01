from pathlib import Path
import psycopg
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import DATABASE_URL, OPENAI_EMBEDDING_MODEL, redact, require_api_key
from src.document_metadata import parse_metadata

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "rag_docs"


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def main():
    # Fail early with setup instructions rather than a provider error
    # part-way through indexing.
    require_api_key("OPENAI_API_KEY")
    embedder = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL)
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=120)

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_chunks")

            for path in sorted(DOCS.glob("*.md")):
                text = path.read_text(encoding="utf-8")
                meta = parse_metadata(text)
                chunks = splitter.split_text(text)
                vectors = embedder.embed_documents(chunks)

                for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                    cur.execute(
                        """
                        INSERT INTO document_chunks
                        (document_name, document_type, supplier_id, sku, skus,
                         chunk_index, content, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                        """,
                        (
                            path.name,
                            meta.document_type,
                            meta.supplier_id,
                            meta.primary_sku,
                            meta.skus,
                            idx,
                            chunk,
                            vector_literal(vector),
                        ),
                    )
            conn.commit()
    print("RAG documents chunked, embedded, and stored in pgvector.")


if __name__ == "__main__":
    main()
