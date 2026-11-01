"""The terminal screens a planner sees, and the decision they make.

Kept separate from the workflow so the wording can be tested without a
terminal and without running the graph. Every function takes its input and
output as arguments, so a test can drive a whole review by passing scripted
answers.

The language here is deliberately plain. The reader is a planner deciding
whether to move stock, not a developer reading field names.
"""

from dataclasses import dataclass
from typing import Any, Callable

from src.summary import FinalSummary, render_summary
from src.validation import CAPPED, OK, REJECTED

WIDTH = 74
RULE = "-" * WIDTH

#: Questions offered at startup. The first is the documented scenario.
SAMPLE_QUESTIONS = [
    "Why is CHK-250 at risk in Berlin on 2026-09-29 and what can we do?",
    "Are we short of any chicken in Berlin next Tuesday?",
    "Is Hamburg short of anything on 2026-09-29?",
    "Do we have enough salmon in Munich on 2026-09-29?",
]

VERDICT_LABEL = {
    OK: "CAN DO",
    CAPPED: "PARTLY",
    REJECTED: "CANNOT",
}

VERDICT_EXPLANATION = {
    OK: "possible as proposed",
    CAPPED: "possible, but for fewer units than proposed",
    REJECTED: "not possible",
}


@dataclass
class Decision:
    """What the planner chose. A record, not an instruction to act."""

    decision: str  # "approved" | "rejected" | "cancelled"
    approved_indexes: list[int]

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "approved_indexes": list(self.approved_indexes),
        }


def heading(text: str, out: Callable[[str], None]) -> None:
    out("")
    out(RULE)
    out(text)
    out(RULE)


def choose_question(
    ask: Callable[[str], str], out: Callable[[str], None]
) -> str | None:
    """Offer the sample questions, or let the planner type their own."""
    heading("SUPPLY CHAIN EXCEPTION COPILOT", out)
    out("")
    out("Ask about a possible shortage. Pick a question or type your own.")
    out("")
    for index, question in enumerate(SAMPLE_QUESTIONS, start=1):
        out(f"  {index}. {question}")
    out("")
    out("  q. Quit")
    out("")

    answer = ask("Choose 1-4, type a question, or q to quit: ").strip()
    if answer.lower() in {"q", "quit", "exit"}:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(SAMPLE_QUESTIONS):
        return SAMPLE_QUESTIONS[int(answer) - 1]
    if not answer:
        return SAMPLE_QUESTIONS[0]
    return answer


def show_progress(step: str, out: Callable[[str], None]) -> None:
    """One line per workflow step, so the investigation is visible."""
    out(f"  ... {step}")


def show_investigation(summary: FinalSummary, out: Callable[[str], None]) -> None:
    """The facts, before any recommendation is discussed."""
    heading(
        f"WHAT I FOUND — {summary.target_sku} at {summary.target_fc} "
        f"on {summary.target_date}",
        out,
    )
    out("")

    if summary.shortage <= 0:
        out(
            f"  There is no shortage. Demand needs "
            f"{summary.required:,.0f} units and {summary.projected_available:,.0f} "
            f"will be available."
        )
        return

    out(f"  Demand needs          {summary.required:>9,.0f} units")
    out(f"  In the building       {summary.on_hand:>9,.0f} units")
    out(f"  Arriving in time      {summary.inbound:>9,.0f} units")
    out(f"  {'-' * 32}")
    out(f"  Short by              {summary.shortage:>9,.0f} units")

    for po in summary.excluded_purchase_orders:
        out("")
        out(
            f"  Note: {po.get('po_id')} brings "
            f"{float(po.get('quantity', 0)):,.0f} units but arrives "
            f"{po.get('expected_delivery')}, after it is needed. Not counted."
        )

    out("")
    out("  Recipes that need this ingredient:")
    for recipe in summary.impacted_recipes:
        out(
            f"    {recipe['recipe_name']:<24}"
            f"{recipe['sku_units_required']:>9,.0f} units"
        )

    if summary.other_fc_rows:
        out("")
        out("  Spare stock at the other sites, after their own production:")
        for row in summary.other_fc_rows:
            out(
                f"    {row['fc_id']}  holds {float(row['on_hand_qty']):>7,.0f}, "
                f"needs {float(row['required_qty']):>7,.0f}, "
                f"spare {float(row['excess_qty']):>7,.0f}"
            )

    if summary.substitute_sku:
        out("")
        out(
            f"  Approved alternative {summary.substitute_sku}: "
            f"{summary.substitute_on_hand:,.0f} units in {summary.target_fc}"
        )


def show_options(summary: FinalSummary, out: Callable[[str], None]) -> None:
    """The options, with what the checks did to each one."""
    heading("WHAT COULD BE DONE", out)
    out("")
    out("  Each option below was proposed by the AI, then checked against the")
    out("  stock we actually hold and the rules in our policies.")
    out("")

    for index, option in enumerate(summary.options, start=1):
        label = VERDICT_LABEL.get(option["verdict"], option["verdict"].upper())
        out(f"  [{index}] {label}  {option['description']}")

        if option["verdict"] == REJECTED:
            out(
                f"       Asked for {option['proposed_units']:,.0f} units. "
                f"Not possible."
            )
        elif option["verdict"] == CAPPED:
            out(
                f"       Asked for {option['proposed_units']:,.0f} units. "
                f"{option['approved_units']:,.0f} units possible."
            )
        else:
            out(f"       {option['approved_units']:,.0f} units.")

        for reason in option["reasons"]:
            out(f"       Why: {reason}")
        out("")

    out(RULE)
    out(f"  Short by                      {summary.shortage:>9,.0f} units")
    out(f"  Everything possible adds up to {summary.approved_units_total:>8,.0f} units")
    out(f"  Would still be short          {summary.residual_shortage:>9,.0f} units")

    if not summary.timing_checked:
        out("")
        out("  Delivery timing was not checked on this run.")

    if summary.warnings:
        out("")
        out("  Worth knowing:")
        for warning in summary.warnings:
            out(f"    - {warning}")


def parse_selection(answer: str, option_count: int) -> Decision | None:
    """Turn what the planner typed into a decision.

    Accepts "1", "1,3", "1 3", "all", "none"/"reject", "q"/"cancel".
    Returns None when the input makes no sense, so the caller can re-ask
    instead of guessing at an intention.
    """
    text = answer.strip().lower()

    if text in {"q", "quit", "cancel", "exit"}:
        return Decision("cancelled", [])
    if text in {"n", "no", "none", "reject", "r"}:
        return Decision("rejected", [])
    if text == "all":
        return Decision("approved", list(range(option_count)))
    if not text:
        return None

    raw = [piece for piece in text.replace(",", " ").split() if piece]
    if not all(piece.isdigit() for piece in raw):
        return None

    picked = sorted({int(piece) for piece in raw})
    if any(number < 1 or number > option_count for number in picked):
        return None

    return Decision("approved", [number - 1 for number in picked])


def ask_for_decision(
    summary: FinalSummary,
    ask: Callable[[str], str],
    out: Callable[[str], None],
    max_attempts: int = 3,
) -> Decision:
    """Ask the planner what to do. Nothing happens without this answer."""
    heading("YOUR DECISION", out)
    out("")
    out("  Nothing has been actioned. Approving an option records your decision")
    out("  in this prototype — it does not move stock, place an order, or")
    out("  contact a supplier.")
    out("")
    out("  Type the numbers you approve (e.g. 1 or 1,2), or:")
    out("    all     approve everything possible")
    out("    none    reject all of them")
    out("    q       stop without deciding")
    out("")

    feasible = [
        index + 1
        for index, option in enumerate(summary.options)
        if option["verdict"] != REJECTED
    ]
    if feasible:
        out(f"  Options that are actually possible: {', '.join(map(str, feasible))}")
        out("")

    for attempt in range(max_attempts):
        decision = parse_selection(
            ask("Your decision: "), len(summary.options)
        )
        if decision is not None:
            rejected_picks = [
                index + 1
                for index in decision.approved_indexes
                if summary.options[index]["verdict"] == REJECTED
            ]
            if rejected_picks:
                # Allowed, but it must not look like it achieved something.
                out("")
                out(
                    f"  Note: option(s) {', '.join(map(str, rejected_picks))} are not "
                    f"possible, so they add no units. Recorded as approved anyway."
                )
            return decision

        remaining = max_attempts - attempt - 1
        if remaining:
            out(f"  Sorry, I did not understand that. {remaining} tries left.")

    out("  No clear decision given. Recording this as 'no decision'.")
    return Decision("cancelled", [])


def show_final(summary: FinalSummary, out: Callable[[str], None]) -> None:
    """The verified summary. Every number here came from checked data."""
    heading("FINAL SUMMARY", out)
    out("")
    for line in render_summary(summary).splitlines():
        out(f"  {line}" if line else "")


def show_preflight(problems: list[str], out: Callable[[str], None]) -> None:
    """Explain what is missing before anything is attempted."""
    heading("SETUP NEEDED", out)
    out("")
    for problem in problems:
        out(f"  - {problem}")
    out("")
    out("  You can still see the checking logic with no setup at all:")
    out("")
    out("      python3 -m scripts.run_scenario_offline")
    out("")
