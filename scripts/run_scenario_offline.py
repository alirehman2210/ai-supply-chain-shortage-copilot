"""Runs the deterministic half of the workflow with no database and no API keys.

The full graph needs PostgreSQL, pgvector and two API keys. This script reads
the same synthetic CSVs, runs the same arithmetic and the same constraint
validator the graph uses, and prints what a planner would be shown.

What it does not do is call a model. So instead of asking Claude for mitigation
options, it feeds the validator a fixed set of proposals — including one that
is deliberately wrong — to show what the validator does with each kind.

    python3 -m scripts.run_scenario_offline
"""

from src import validation
from src.scenario import build_scenario

TARGET_DATE = "2026-09-29"
TARGET_FC = "BER"
TARGET_SKU = "CHK-250"

# Stand-ins for what the model proposes at this step in the real graph. The
# substitution figure is the interesting one: it asks to cover the whole
# shortage with the alternate SKU, which reads perfectly sensibly and is not
# possible, because Berlin only holds 600 units of it.
PROPOSED_OPTIONS = [
    {
        "action_type": "transfer",
        "action": "Transfer CHK-250 from Hamburg to Berlin",
        "estimated_units": 900,
        "source_fc": "HAM",
        "rationale": "Hamburg has spare stock after covering its own forecast.",
    },
    {
        "action_type": "transfer",
        "action": "Transfer CHK-250 from Munich to Berlin",
        "estimated_units": 500,
        "source_fc": "MUC",
        "rationale": "Munich also holds spare stock.",
    },
    {
        "action_type": "substitute",
        "action": "Substitute CHK-250-B for the full remaining shortage",
        "estimated_units": 1800,
        "recipes": ["RCP-TERIYAKI", "RCP-TUSCAN", "RCP-PASTA"],
        "rationale": "CHK-250-B is an approved alternate for this ingredient.",
    },
    {
        "action_type": "expedite",
        "action": "Request an expedited delivery from the supplier",
        "estimated_units": 1500,
        "rationale": "The contract allows expedited supply for material shortages.",
    },
]

RULE = "-" * 72


def money(value: float) -> str:
    return f"{value:,.0f}"


def main() -> None:
    scenario = build_scenario(TARGET_DATE, TARGET_FC, TARGET_SKU)

    print(RULE)
    print(f"SHORTAGE PROJECTION — {TARGET_SKU} at {TARGET_FC} on {TARGET_DATE}")
    print(RULE)
    print(f"Required by forecast demand   {money(scenario.required):>10}")
    print(f"On hand                       {money(scenario.on_hand):>10}")
    print(f"Confirmed inbound by the date {money(scenario.inbound):>10}")
    print(f"Projected available           {money(scenario.projected_available):>10}")
    print(f"Projected shortage            {money(scenario.shortage):>10}")

    if scenario.excluded_purchase_orders:
        print("\nPurchase orders left out of the projection:")
        for po in scenario.excluded_purchase_orders:
            print(
                f"  {po['po_id']}  {money(float(po['quantity']))} units  "
                f"arrives {po['expected_delivery']}  ({po['status']})"
                f"  — after the date the stock is needed"
            )

    print("\nRecipes consuming this SKU:")
    for recipe in scenario.impacted_recipes:
        print(
            f"  {recipe['recipe_name']:<22} {recipe['forecast_boxes']:>6} boxes  "
            f"{money(recipe['sku_units_required']):>7} units"
        )

    print("\nSpare stock at the other sites (after their own forecast):")
    for row in scenario.other_fc_rows:
        print(
            f"  {row['fc_id']}  holds {money(row['on_hand_qty']):>6}  "
            f"needs {money(row['required_qty']):>6}  "
            f"spare {money(row['excess_qty']):>6}"
        )
    print(
        f"\nSubstitute {scenario.substitute_sku} on hand at {TARGET_FC}: "
        f"{money(scenario.substitute_on_hand)}"
    )

    print()
    print(RULE)
    print("PROPOSED OPTIONS, BEFORE VALIDATION")
    print(RULE)
    for option in PROPOSED_OPTIONS:
        print(f"  {money(option['estimated_units']):>6} units  {option['action']}")

    report = validation.validate_options(PROPOSED_OPTIONS, scenario.constraint_snapshot())

    print()
    print(RULE)
    print("AFTER VALIDATION")
    print(RULE)
    for option in report.options:
        print(f"\n[{option.verdict.upper()}] {option.description}")
        print(
            f"  proposed {money(option.proposed_units)} units  ->  "
            f"approved {money(option.approved_units)} units"
        )
        for reason in option.reasons:
            print(f"  - {reason}")
        print(f"  requires human approval: {option.requires_human_approval}")

    print()
    print(RULE)
    print("WHAT THE PLANNER IS ASKED TO DECIDE")
    print(RULE)
    print(f"Projected shortage                {money(report.shortage):>10}")
    print(f"Covered by feasible actions       {money(report.approved_units_total):>10}")
    print(f"Still uncovered                   {money(report.residual_shortage):>10}")
    if report.warnings:
        print("\nFlagged for the planner:")
        for warning in report.warnings:
            print(f"  - {warning}")
    print(
        "\nNothing above has been actioned. In the full graph this is the point "
        "where the workflow stops and waits for a person."
    )


if __name__ == "__main__":
    main()
