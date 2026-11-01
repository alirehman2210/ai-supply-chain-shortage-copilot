"""Checks the OpenAI key works, with exactly one tiny embedding request.

    python -m scripts.verify_openai_key

It embeds the single word "test" — about one token, which costs roughly
$0.00000002. It writes nothing to the database and prints no part of the key.

What it reports:
  - whether the key is configured at all (no call made if not)
  - whether the key is accepted by the embeddings endpoint
  - which model answered, and how many dimensions it returned
  - whether that dimension matches the `vector(...)` column in sql/schema.sql,
    because a mismatch fails later at insert time with a confusing error

Exit code 0 means the key works and the dimension matches.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = "test"


def schema_dimension() -> int | None:
    """The dimension the embedding column expects, read from the schema."""
    match = re.search(
        r"embedding\s+vector\((\d+)\)",
        (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"),
    )
    return int(match.group(1)) if match else None


def main() -> int:
    from src.config import (
        OPENAI_EMBEDDING_MODEL,
        MissingCredential,
        redact,
        require_api_key,
    )

    print("OpenAI key check")
    print("-" * 60)
    print(f"  Model configured : {OPENAI_EMBEDDING_MODEL}")
    print(f"  Read from        : OPENAI_API_KEY (via .env)")

    try:
        require_api_key("OPENAI_API_KEY")
    except MissingCredential as error:
        # No request is made, so nothing is spent.
        print("\n  NOT CONFIGURED — no API call was made.\n")
        print("  " + str(error).replace("\n", "\n  "))
        return 1

    print("  Key              : configured (value not shown)")

    expected = schema_dimension()
    print(f"  Schema expects   : {expected} dimensions")
    print(f"\n  Sending one embedding request for the word {PROBE!r}...")

    try:
        from langchain_openai import OpenAIEmbeddings

        vector = OpenAIEmbeddings(model=OPENAI_EMBEDDING_MODEL).embed_query(PROBE)
    except ImportError as error:
        print(f"\n  FAILED — dependency missing ({error.name}).")
        print("  Run: pip install -r requirements.txt")
        return 1
    except Exception as error:
        # redact() strips anything key-shaped out of the provider's message.
        message = redact(error)
        print("\n  FAILED — the embeddings endpoint rejected the request.")
        print(f"  Provider said: {message}")
        print()
        if "401" in message or "invalid_api_key" in message.lower():
            print("  That usually means the key is wrong, revoked, or from a")
            print("  different account.")
        elif "403" in message or "permission" in message.lower():
            print("  That usually means the key is restricted and lacks write")
            print("  access to model capabilities, which /v1/embeddings needs.")
        elif "429" in message or "quota" in message.lower():
            print("  That usually means no credit or a rate limit, not a bad key.")
        return 1

    print("  OK — the key was accepted.")
    print(f"  Returned         : {len(vector)} dimensions")

    if expected is not None and len(vector) != expected:
        print(
            f"\n  MISMATCH — the schema column is vector({expected}) but this "
            f"model returns {len(vector)}."
        )
        print("  Indexing would fail. Either set OPENAI_EMBEDDING_MODEL back to a")
        print(f"  {expected}-dimension model, or change sql/schema.sql to match.")
        return 1

    print("\n  PASS — key works and the dimension matches the schema.")
    print("  Nothing was written to the database.")
    print("\n  Next: python -m scripts.ingest_rag_docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
