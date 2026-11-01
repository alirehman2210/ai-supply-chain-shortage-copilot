"""Runs the six golden questions and reports which ones were answered.

    python3 -m scripts.run_golden_questions

Three of them are answered from the structured data and need nothing installed.
Three need pgvector retrieval, which means a loaded database, indexed documents
and an embedding key; those are reported as SKIPPED when the setup is not
there, rather than silently passing.

Each retrieval answer reports the document the evidence came from, so you can
see whether the right policy was found and not just whether the answer looked
right.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def load_golden() -> dict:
    with (ROOT / "evals" / "golden_questions.json").open(encoding="utf-8") as handle:
        return {item["id"]: item for item in json.load(handle)}


# --- The three answerable from structured data ------------------------------


def structured_answers() -> dict[str, tuple[str, str, str]]:
    """Returns {id: (status, answer, evidence)}."""
    from src.scenario import build_scenario

    scenario = build_scenario("2026-09-29", "BER", "CHK-250")
    results = {}

    results["GQ-01"] = (
        PASS if scenario.shortage == 1800.0 else FAIL,
        f"{scenario.shortage:,.0f} units",
        "demand_forecast, recipe_ingredients, inventory, purchase_orders",
    )

    names = [r["recipe_name"] for r in scenario.impacted_recipes]
    expected = {"Chicken Teriyaki", "Tuscan Chicken", "Creamy Chicken Pasta"}
    results["GQ-02"] = (
        PASS if set(names) == expected else FAIL,
        ", ".join(names),
        "demand_forecast, recipe_ingredients, recipes",
    )

    excess = {r["fc_id"]: float(r["excess_qty"]) for r in scenario.other_fc_rows}
    results["GQ-05"] = (
        PASS if excess.get("HAM") == 900.0 else FAIL,
        f"{excess.get('HAM', 0):,.0f} units",
        "inventory, demand_forecast, recipe_ingredients",
    )
    return results


# --- The three that need retrieval ------------------------------------------


def retrieval_answers() -> dict[str, tuple[str, str, str]]:
    """Returns {id: (status, answer, evidence)}.

    Each check asks whether the retrieved text actually supports the expected
    answer, and reports which document it came from.
    """
    from src.tools.rag_tools import vector_search

    results = {}

    chunks = vector_search(
        "Can CHK-250-B be substituted into Creamy Chicken Pasta?",
        document_type="substitution_policy",
        sku="CHK-250",
        k=3,
    )
    text = " ".join(c["content"] for c in chunks)
    docs = ", ".join(sorted({c["document_name"] for c in chunks}))
    found = "not approved" in text and "RCP-PASTA" in text
    results["GQ-03"] = (
        PASS if found else FAIL,
        "No — not approved for RCP-PASTA" if found else "evidence not retrieved",
        docs or "nothing retrieved",
    )

    chunks = vector_search(
        "maximum additional quantity through the expedited process per calendar week",
        document_type="supplier_contract",
        supplier_id="SUP-NORD",
        k=3,
    )
    text = " ".join(c["content"] for c in chunks)
    docs = ", ".join(sorted({c["document_name"] for c in chunks}))
    found = "1,200" in text
    results["GQ-04"] = (
        PASS if found else FAIL,
        "1,200 units" if found else "evidence not retrieved",
        docs or "nothing retrieved",
    )

    chunks = vector_search(
        "Does an inter-fulfillment-center transfer require approval before dispatch?",
        document_type="transfer_policy",
        k=3,
    )
    text = " ".join(c["content"] for c in chunks).lower()
    docs = ", ".join(sorted({c["document_name"] for c in chunks}))
    found = "approval" in text
    results["GQ-06"] = (
        PASS if found else FAIL,
        "Yes" if found else "evidence not retrieved",
        docs or "nothing retrieved",
    )
    return results


def retrieval_blocked() -> str | None:
    """Why retrieval cannot run, or None if it can."""
    try:
        from src.config import missing_api_keys
        from src.tools.rag_tools import count_indexed_chunks
    except ImportError as error:
        return f"dependency missing ({error.name}); pip install -r requirements.txt"

    missing = missing_api_keys()
    if missing:
        return f"{', '.join(missing)} not configured in .env"
    try:
        if count_indexed_chunks() == 0:
            return "no documents indexed; run python -m scripts.ingest_rag_docs"
    except Exception as error:
        return str(error).splitlines()[0]
    return None


def main() -> int:
    golden = load_golden()
    results = structured_answers()

    blocked = retrieval_blocked()
    if blocked is None:
        results.update(retrieval_answers())

    print("GOLDEN QUESTIONS")
    print("=" * 76)

    failures = 0
    for question_id in sorted(golden):
        item = golden[question_id]
        if question_id in results:
            status, answer, evidence = results[question_id]
        else:
            status, answer, evidence = SKIP, "not run", blocked or "unavailable"

        if status == FAIL:
            failures += 1

        print(f"\n[{status}] {question_id}  {item['question']}")
        print(f"       expected: {item['expected_answer']}")
        print(f"       got     : {answer}")
        print(f"       evidence: {evidence}")

    answered = sum(1 for r in results.values() if r[0] == PASS)
    skipped = len(golden) - len(results)

    print()
    print("=" * 76)
    print(f"{answered} of {len(golden)} passed, {failures} failed, {skipped} skipped")
    if skipped:
        print(f"Skipped because: {blocked}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
