"""Builds the final summary, and fact-checks any model-written narrative.

Two jobs, in this order.

`build_summary()` assembles the summary from validated data only. Every number
in it is copied from a verified field — the shortage from the arithmetic, the
approved quantities from the validator, the decision from what the human
actually chose. No model is involved, so no number in it can be invented.

`fact_check()` takes a narrative written by Claude and checks the quantities in
it against that same validated data. Any number in the narrative that is not a
verified figure is reported. The caller decides what to do about it, and the
interactive app's answer is to show the deterministic summary and say the
narrative failed its check, rather than show a summary that might be wrong.

This is the last place a wrong number could reach a planner, which is why the
narrative is treated as untrusted text rather than as output.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from src import business_rules
from src.validation import REJECTED, ValidatedOption, ValidationReport


@dataclass
class FinalSummary:
    """The verified account of one investigation. No model output in here."""

    target_sku: str
    target_fc: str
    target_date: str
    required: float
    on_hand: float
    inbound: float
    projected_available: float
    shortage: float
    excluded_purchase_orders: list[dict[str, Any]] = field(default_factory=list)
    impacted_recipes: list[dict[str, Any]] = field(default_factory=list)
    other_fc_rows: list[dict[str, Any]] = field(default_factory=list)
    substitute_sku: str | None = None
    substitute_on_hand: float = 0.0
    options: list[dict[str, Any]] = field(default_factory=list)
    approved_units_total: float = 0.0
    residual_shortage: float = 0.0
    warnings: list[str] = field(default_factory=list)
    timing_checked: bool = True
    decision: str = "pending"
    decided_options: list[dict[str, Any]] = field(default_factory=list)
    decided_units_total: float = 0.0
    shortage_after_decision: float = 0.0
    note: str = ""

    def verified_numbers(self) -> set[float]:
        """Every quantity a truthful narrative is allowed to state.

        Used by fact_check(). Deliberately generous: it includes the figures
        themselves and the obvious arithmetic on them, because flagging a
        correct number as invented would make the check useless.
        """
        numbers: set[float] = {
            self.required,
            self.on_hand,
            self.inbound,
            self.projected_available,
            self.shortage,
            self.approved_units_total,
            self.residual_shortage,
            self.decided_units_total,
            self.shortage_after_decision,
            self.substitute_on_hand,
        }
        for recipe in self.impacted_recipes:
            numbers.add(float(recipe.get("sku_units_required", 0)))
            numbers.add(float(recipe.get("forecast_boxes", 0)))
        for row in self.other_fc_rows:
            numbers.add(float(row.get("on_hand_qty", 0)))
            numbers.add(float(row.get("required_qty", 0)))
            numbers.add(float(row.get("excess_qty", 0)))
        for option in self.options:
            numbers.add(float(option.get("approved_units", 0)))
            numbers.add(float(option.get("proposed_units", 0)))
        for po in self.excluded_purchase_orders:
            numbers.add(float(po.get("quantity", 0)))
        # Counts a narrative may reasonably mention.
        numbers.update(
            {
                float(len(self.impacted_recipes)),
                float(len(self.options)),
                float(len(self.other_fc_rows)),
                float(len(self.excluded_purchase_orders)),
            }
        )
        # The policy limits themselves. A narrative explaining *why* an option
        # was capped will quote the cap, and that is a correct statement of a
        # rule rather than an invented quantity.
        numbers.update(
            {
                float(business_rules.MAX_UNITS_PER_URGENT_TRANSFER),
                float(business_rules.EXPEDITE_MAX_UNITS_PER_WEEK),
                float(business_rules.TRANSFER_LEAD_TIME_HOURS),
                float(business_rules.EXPEDITE_MIN_NOTICE_HOURS),
                float(business_rules.EXPEDITE_SURCHARGE_PERCENT),
            }
        )
        return {n for n in numbers if n}


def message_text(content: object) -> str:
    """Pull the readable text out of a model reply.

    Current Claude models think before answering, and the reply comes back as a
    list of blocks rather than a plain string: thinking blocks (whose text is
    empty but which carry a long signature) followed by the text block. Using
    `.content` directly puts that whole structure into the summary, signature
    and all.

    Handles both shapes, so this keeps working if a reply is a plain string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(part for part in parts if part).strip()
    return str(content)


def build_summary(
    scenario: Any,
    report: ValidationReport,
    decision: str = "pending",
    approved_option_indexes: list[int] | None = None,
) -> FinalSummary:
    """Assemble the verified summary.

    `scenario` is a ShortageScenario (offline) or any object exposing the same
    fields. `report` is the validator's output. `approved_option_indexes` are
    positions in `report.options` the human approved.
    """
    options = [
        {
            "action_type": option.action_type,
            "description": option.description,
            "proposed_units": option.proposed_units,
            "approved_units": option.approved_units,
            "verdict": option.verdict,
            "reasons": list(option.reasons),
            "source_fc": option.source_fc,
            "requires_human_approval": option.requires_human_approval,
        }
        for option in report.options
    ]

    chosen_indexes = approved_option_indexes or []
    decided = [
        options[i] for i in chosen_indexes if 0 <= i < len(options)
    ]
    # Only feasible units count. Approving a rejected option cannot conjure
    # stock, and the summary must not imply that it did.
    decided_units = sum(
        o["approved_units"] for o in decided if o["verdict"] != REJECTED
    )

    return FinalSummary(
        target_sku=scenario.target_sku,
        target_fc=scenario.target_fc,
        target_date=scenario.target_date,
        required=float(scenario.required),
        on_hand=float(scenario.on_hand),
        inbound=float(scenario.inbound),
        projected_available=float(scenario.on_hand) + float(scenario.inbound),
        shortage=float(scenario.shortage),
        excluded_purchase_orders=list(
            getattr(scenario, "excluded_purchase_orders", [])
        ),
        impacted_recipes=list(scenario.impacted_recipes),
        other_fc_rows=list(scenario.other_fc_rows),
        substitute_sku=getattr(scenario, "substitute_sku", None),
        substitute_on_hand=float(getattr(scenario, "substitute_on_hand", 0.0)),
        options=options,
        approved_units_total=report.approved_units_total,
        residual_shortage=report.residual_shortage,
        warnings=list(report.warnings),
        timing_checked=report.timing_checked,
        decision=decision,
        decided_options=decided,
        decided_units_total=decided_units,
        shortage_after_decision=max(float(scenario.shortage) - decided_units, 0.0),
    )


@dataclass
class ScenarioView:
    """The scenario fields build_summary() needs, read out of graph state.

    The offline runner passes a ShortageScenario straight in. The graph keeps
    the same facts spread across its state dictionary, so this gathers them
    into the same shape rather than giving build_summary() two code paths.
    """

    target_sku: str
    target_fc: str
    target_date: str
    required: float
    on_hand: float
    inbound: float
    shortage: float
    impacted_recipes: list[dict[str, Any]]
    other_fc_rows: list[dict[str, Any]]
    excluded_purchase_orders: list[dict[str, Any]]
    substitute_sku: str | None
    substitute_on_hand: float


def scenario_view_from_state(state: dict[str, Any]) -> ScenarioView:
    sku = state.get("target_sku") or ""
    rule = business_rules.substitution_rule_for(sku)
    return ScenarioView(
        target_sku=sku,
        target_fc=state.get("target_fc") or "",
        target_date=state.get("target_date") or "",
        required=float((state.get("required_by_sku") or {}).get(sku, 0.0)),
        on_hand=float((state.get("on_hand_by_sku") or {}).get(sku, 0.0)),
        inbound=float((state.get("inbound_by_sku") or {}).get(sku, 0.0)),
        shortage=float(state.get("target_shortage") or 0.0),
        impacted_recipes=list(state.get("impacted_recipes") or []),
        other_fc_rows=list(state.get("other_fc_rows") or []),
        excluded_purchase_orders=list(state.get("excluded_purchase_orders") or []),
        substitute_sku=rule.substitute_sku if rule else None,
        substitute_on_hand=float(state.get("substitute_on_hand") or 0.0),
    )


def report_from_state(state: dict[str, Any]) -> ValidationReport:
    """Rebuild a ValidationReport from the dicts stored in graph state.

    LangGraph state holds plain dictionaries so it can be checkpointed, so the
    validator's dataclasses are flattened on the way in and restored here.
    """
    options = [
        ValidatedOption(
            action_type=o.get("action_type", "other"),
            description=o.get("description", ""),
            proposed_units=float(o.get("proposed_units", 0.0)),
            approved_units=float(o.get("approved_units", 0.0)),
            verdict=o.get("verdict", REJECTED),
            reasons=list(o.get("reasons", [])),
            source_fc=o.get("source_fc"),
            requires_human_approval=bool(o.get("requires_human_approval", True)),
        )
        for o in state.get("validated_options") or []
    ]
    return ValidationReport(
        options=options,
        shortage=float(state.get("target_shortage") or 0.0),
        approved_units_total=sum(o.approved_units for o in options),
        residual_shortage=float(state.get("residual_shortage") or 0.0),
        warnings=list(state.get("validation_warnings") or []),
        timing_checked=bool(state.get("timing_checked", True)),
    )


def render_summary(summary: FinalSummary) -> str:
    """Format the verified summary as plain text.

    Written in ordinary English rather than field names, because the reader is
    a planner, not a developer.
    """
    lines: list[str] = []
    add = lines.append

    add(
        f"{summary.target_sku} at {summary.target_fc} for {summary.target_date}"
    )
    add("")

    if summary.shortage <= 0:
        add(
            f"No shortage. Demand needs {summary.required:,.0f} units and "
            f"{summary.projected_available:,.0f} will be available."
        )
        return "\n".join(lines)

    add("The shortage")
    add(f"  Demand needs            {summary.required:>9,.0f} units")
    add(f"  In the building         {summary.on_hand:>9,.0f} units")
    add(f"  Arriving in time        {summary.inbound:>9,.0f} units")
    add(f"  So available            {summary.projected_available:>9,.0f} units")
    add(f"  Short by                {summary.shortage:>9,.0f} units")

    for po in summary.excluded_purchase_orders:
        add(
            f"  Not counted: {po.get('po_id')} of {float(po.get('quantity', 0)):,.0f} "
            f"units, arriving {po.get('expected_delivery')} — after it is needed"
        )

    add("")
    add("What it affects")
    for recipe in summary.impacted_recipes:
        add(
            f"  {recipe['recipe_name']:<24}{recipe['sku_units_required']:>9,.0f} units"
        )

    add("")
    add("Options, after checking against stock and policy")
    for option in summary.options:
        label = {"ok": "allowed", "capped": "reduced", "rejected": "not possible"}.get(
            option["verdict"], option["verdict"]
        )
        add(f"  [{label}] {option['description']}")
        if option["verdict"] == "capped":
            add(
                f"      asked for {option['proposed_units']:,.0f} units, "
                f"{option['approved_units']:,.0f} possible"
            )
        elif option["verdict"] == REJECTED:
            add(f"      asked for {option['proposed_units']:,.0f} units, none possible")
        else:
            add(f"      {option['approved_units']:,.0f} units")
        for reason in option["reasons"]:
            add(f"      - {reason}")

    add("")
    add("Where that leaves the shortage")
    add(f"  Short by                {summary.shortage:>9,.0f} units")
    add(f"  All feasible options    {summary.approved_units_total:>9,.0f} units")
    add(f"  Still short             {summary.residual_shortage:>9,.0f} units")

    if not summary.timing_checked:
        add("  Delivery timing was not checked for this run.")

    if summary.warnings:
        add("")
        add("Worth knowing")
        for warning in summary.warnings:
            add(f"  - {warning}")

    add("")
    add("Decision")
    if summary.decision == "pending":
        add("  Not yet reviewed. Nothing has been actioned.")
    elif summary.decision == "rejected":
        add("  The planner rejected the recommendations.")
        add(f"  The shortage of {summary.shortage:,.0f} units stands.")
    elif summary.decision == "cancelled":
        add("  The planner stopped without deciding.")
        add(f"  The shortage of {summary.shortage:,.0f} units is still open.")
    elif not summary.decided_options:
        add("  The planner approved nothing.")
        add(f"  The shortage of {summary.shortage:,.0f} units stands.")
    else:
        add("  The planner approved:")
        for option in summary.decided_options:
            add(
                f"    {option['approved_units']:,.0f} units — {option['description']}"
            )
        add(f"  Approved cover          {summary.decided_units_total:>9,.0f} units")
        add(f"  Remaining shortage      {summary.shortage_after_decision:>9,.0f} units")

    add("")
    add(
        "  Recorded as a decision in this prototype only. No transfer, purchase "
        "order, or supplier contact has been made."
    )
    if summary.note:
        add(f"  Note: {summary.note}")

    return "\n".join(lines)


# Numbers written as 1800, 1,800, or 1800.0.
#
# The lookarounds keep digits that are part of an identifier out of the scan.
# Without them "CHK-250" reads as the quantity 250 and "PO-882" as 882, and a
# perfectly truthful sentence naming the SKU fails its own fact check.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9-])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![A-Za-z0-9-])"
    r"|(?<![A-Za-z0-9-])\d+(?:\.\d+)?(?![A-Za-z0-9-])"
)

# Small numbers appear as list positions, dates, percentages and clause
# references. Checking them produces noise rather than signal, so the check
# applies to quantities, which is where a damaging invention would be.
_IGNORE_BELOW = 100.0


@dataclass
class FactCheckResult:
    passed: bool
    unverified_numbers: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def fact_check(narrative: str, summary: FinalSummary) -> FactCheckResult:
    """Check every quantity in a model-written narrative against verified data.

    Returns passed=False if the narrative states a quantity that is not a
    verified figure. The caller is expected to withhold the narrative in that
    case and show the deterministic summary instead.

    Known limits of this check: it compares numbers, not claims. A narrative
    that uses only correct numbers but describes an option as approved when it
    was rejected would pass. That is why the wording about what was approved
    comes from `render_summary()` rather than from the model.
    """
    allowed = summary.verified_numbers()
    notes: list[str] = []
    unverified: list[float] = []
    claim_violations = 0

    for raw in _NUMBER.findall(narrative):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if value < _IGNORE_BELOW:
            continue
        if value in allowed:
            continue
        # A year or a date fragment is not a quantity claim.
        if 1900 <= value <= 2100 and float(value).is_integer():
            continue
        unverified.append(value)
        notes.append(
            f"The narrative states {value:,.0f}, which is not a verified figure."
        )

    forbidden_claims = [
        ("order has been placed", "claims an order was placed"),
        ("i have placed", "claims an action was taken"),
        ("inventory has been updated", "claims inventory was changed"),
        ("supplier has been contacted", "claims the supplier was contacted"),
        ("transfer has been executed", "claims a transfer was executed"),
    ]
    lowered = narrative.lower()
    for phrase, description in forbidden_claims:
        if phrase in lowered:
            claim_violations += 1
            notes.append(f"The narrative {description}, which never happens here.")

    return FactCheckResult(
        passed=not unverified and claim_violations == 0,
        unverified_numbers=unverified,
        notes=notes,
    )
