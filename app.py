"""The interactive prototype. One command, start to finish.

    python3 app.py              run the full workflow (needs Postgres + keys)
    python3 app.py --demo       run the checking logic with no setup at all

The full run asks Claude to understand your question and to propose options,
retrieves the policy documents from pgvector, validates every proposal against
the synthetic inventory and the policy limits, then stops and waits for you to
approve or reject. The demo run does everything except the two model calls and
the retrieval, using a fixed set of proposals instead, so the review experience
can be seen without any setup.

Nothing in either mode moves stock, places an order, or contacts a supplier.
Approval is recorded as a decision and nothing more.
"""

import argparse
import sys

from src import review_ui
from src.config import missing_api_keys


def preflight() -> list[str]:
    """What is missing before a full run can work.

    Checked up front so the answer is a short list of things to fix, rather
    than a traceback part-way through an investigation.
    """
    problems: list[str] = []

    for name in missing_api_keys():
        problems.append(
            f"{name} is not set. Copy .env.example to .env and add your key. "
            f"(.env is gitignored and will not be committed.)"
        )

    try:
        from src.tools.rag_tools import count_indexed_chunks
        from src.tools.sql_tools import get_inventory

        try:
            if not get_inventory("BER"):
                problems.append(
                    "The database is reachable but empty. Run: "
                    "python -m scripts.load_structured_data"
                )
        except Exception as error:  # noqa: BLE001 — message is already safe
            problems.append(str(error).splitlines()[0])
            problems.append(
                "Start it with: docker compose up -d   then apply sql/schema.sql"
            )
            return problems

        if count_indexed_chunks() == 0:
            problems.append(
                "No policy documents are indexed yet. Run: "
                "python -m scripts.ingest_rag_docs"
            )
    except ImportError as error:
        problems.append(
            f"A dependency is missing ({error.name}). Run: "
            f"pip install -r requirements.txt"
        )

    return problems


def run_demo() -> int:
    """The review experience, driven by the deterministic path only.

    Uses the same screens and the same validator as a full run. The only
    difference is where the proposals come from: a fixed list instead of
    Claude, so this works with no database, no keys and no dependencies.
    """
    from scripts.run_scenario_offline import PROPOSED_OPTIONS
    from src import validation
    from src.scenario import build_scenario
    from src.summary import build_summary

    out = print
    review_ui.heading("DEMO MODE — no database, no API keys, no model calls", out)
    out("")
    out("  The investigation and the checks below are real. The proposals are a")
    out("  fixed list standing in for the ones Claude writes in a full run.")

    question = review_ui.choose_question(input, out)
    if question is None:
        out("\nStopped. Nothing was actioned.")
        return 0

    out("")
    for step in (
        "reading the question",
        "fetching the demand forecast",
        "working out what the recipes need",
        "checking stock on hand",
        "checking incoming deliveries",
        "calculating the shortage",
        "looking for spare stock elsewhere",
        "checking each proposal against stock and policy",
    ):
        review_ui.show_progress(step, out)

    scenario = build_scenario("2026-09-29", "BER", "CHK-250")
    snapshot = scenario.constraint_snapshot()
    # A full run derives this from today's date; fixed here so the demo is
    # reproducible rather than changing with the calendar.
    snapshot.hours_until_required = 7 * 24
    report = validation.validate_options(PROPOSED_OPTIONS, snapshot)

    summary = build_summary(scenario, report)
    review_ui.show_investigation(summary, out)
    review_ui.show_options(summary, out)

    decision = review_ui.ask_for_decision(summary, input, out)
    final = build_summary(
        scenario, report, decision.decision, decision.approved_indexes
    )
    review_ui.show_final(final, out)
    return 0


def run_full() -> int:
    """The real workflow: Claude, pgvector retrieval, and the human pause."""
    from langgraph.types import Command

    from src import summary as summary_mod
    from src.graph import build_graph

    out = print

    problems = preflight()
    if problems:
        review_ui.show_preflight(problems, out)
        return 1

    question = review_ui.choose_question(input, out)
    if question is None:
        out("\nStopped. Nothing was actioned.")
        return 0

    app = build_graph()
    config = {"configurable": {"thread_id": "interactive-review"}}

    out("")
    review_ui.show_progress("asking Claude to read the question", out)
    review_ui.show_progress("gathering demand, stock and deliveries", out)
    review_ui.show_progress("retrieving the supplier contract and policies", out)
    review_ui.show_progress("asking Claude for options", out)
    review_ui.show_progress("checking every option against stock and policy", out)

    state = app.invoke(
        {"user_question": question, "current_date": "2026-09-22"}, config=config
    )

    # No shortage: the graph routed straight past the investigation.
    if not state.get("__interrupt__"):
        out("")
        out(state.get("final_response", "No shortage was found."))
        return 0

    snapshot = app.get_state(config).values
    summary = summary_mod.build_summary(
        summary_mod.scenario_view_from_state(snapshot),
        summary_mod.report_from_state(snapshot),
    )

    review_ui.show_investigation(summary, out)
    review_ui.show_options(summary, out)
    decision = review_ui.ask_for_decision(summary, input, out)

    # Hand the decision back in. LangGraph resumes the paused workflow from its
    # checkpoint, so the facts it reports are the ones it investigated.
    final_state = app.invoke(Command(resume=decision.as_payload()), config=config)

    review_ui.heading("FINAL SUMMARY", out)
    out("")
    for line in str(final_state.get("final_response", "")).splitlines():
        out(f"  {line}" if line else "")

    if final_state.get("narrative_passed_fact_check") is False:
        notes = final_state.get("fact_check_notes") or []
        if notes:
            out("")
            out("  Why the written summary was withheld:")
            for note in notes:
                out(f"    - {note}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Supply Chain Exception Copilot",
        epilog="Nothing here places orders or moves stock.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run without a database, API keys or dependencies",
    )
    args = parser.parse_args()

    try:
        return run_demo() if args.demo else run_full()
    except KeyboardInterrupt:
        print("\n\nStopped. Nothing was actioned.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
