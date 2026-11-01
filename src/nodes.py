"""One function per step in the workflow.

Each node is tagged with the kind of work it does, because the whole point of
splitting the workflow up this way is that you can tell at a glance which steps
depend on a language model and which ones do not:

    LLM     a model call
    SQL     a database read
    RULES   deterministic Python: arithmetic or constraint checking
    RAG     vector search over the policy documents
    HUMAN   stops and waits for a person
"""

from datetime import date
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from src import business_rules, calculations, validation
from src import summary as summary_mod
from src.summary import message_text
from src.config import CLAUDE_MODEL, anthropic_default_headers, require_api_key
from src.state import SupplyChainState
from src.tools.rag_tools import vector_search
from src.tools.sql_tools import (
    get_all_purchase_orders,
    get_confirmed_purchase_orders,
    get_demand,
    get_inventory,
    get_on_hand,
    get_other_fc_excess,
    get_recipe_requirements,
    get_supplier_for_sku,
)


class ParsedQuestion(BaseModel):
    intent: Literal["shortage_overview", "sku_investigation", "mitigation"]
    target_date: str = Field(description="ISO date YYYY-MM-DD")
    target_fc: str = Field(description="BER, HAM, or MUC")
    target_sku: str | None = None


def _structured(schema):
    """Ask the model for a result matching a schema.

    method="json_schema" rather than the library default. The default uses
    forced tool calling, which current Claude models reject — it warns that it
    "will raise an OutputParserException if tool calls are not generated".
    Constrained output is the supported way to get a typed result back, and it
    works with thinking enabled.
    """
    return _llm().with_structured_output(schema, method="json_schema")


def hours_between(current_date: str | None, target_date: str | None) -> float | None:
    """Hours from today until the stock is needed, or None if unknown.

    The synthetic data holds dates without times, so this is whole days turned
    into hours. Returning None rather than a guess matters: the validator
    reports an unchecked timing window instead of assuming there is enough
    time.
    """
    if not current_date or not target_date:
        return None
    try:
        start = date.fromisoformat(str(current_date))
        end = date.fromisoformat(str(target_date))
    except ValueError:
        return None
    return (end - start).days * 24.0


def _llm() -> ChatAnthropic:
    """The model client.

    No temperature is set. Current Claude models reject the sampling
    parameters, and the model calls here get their consistency from
    structured output schemas instead.

    The key is read from the environment by the client. Checking it here
    first turns a provider authentication error into setup instructions.
    """
    require_api_key("ANTHROPIC_API_KEY")
    # default_headers is empty unless ANTHROPIC_WORKSPACE_ID is set, which is
    # only needed for an organisation-level key.
    return ChatAnthropic(
        model=CLAUDE_MODEL, default_headers=anthropic_default_headers()
    )


# --- NODE 1 — LLM -----------------------------------------------------------
def understand_question(state: SupplyChainState) -> dict:
    """Turn a typed question into the fields the rest of the graph needs.

    This is the only place a model is allowed to decide what to look up. It
    picks a date, a site and a SKU, and everything downstream is then a lookup
    or a calculation against those three values.
    """
    parser = _structured(ParsedQuestion)
    prompt = f"""
    You are parsing a supply-chain planning question about synthetic data.
    Current date: {state.get('current_date', '2026-09-22')}

    Fulfillment-center codes:
    - Berlin = BER
    - Hamburg = HAM
    - Munich = MUC

    Resolve relative dates against the current date above. The synthetic data
    set only holds a forecast for 2026-09-29.

    User question: {state['user_question']}
    """
    result = parser.invoke(prompt)
    return result.model_dump()


# --- NODE 2 — SQL -----------------------------------------------------------
def get_demand_node(state: SupplyChainState) -> dict:
    return {"demand_rows": get_demand(state["target_date"], state["target_fc"])}


# --- NODE 3 — SQL -----------------------------------------------------------
def get_recipe_requirements_node(state: SupplyChainState) -> dict:
    recipe_ids = [r["recipe_id"] for r in state["demand_rows"]]
    return {"recipe_requirement_rows": get_recipe_requirements(recipe_ids)}


# --- NODE 4 — RULES ---------------------------------------------------------
def calculate_required_inventory(state: SupplyChainState) -> dict:
    return {
        "required_by_sku": calculations.required_by_sku(
            state["demand_rows"], state["recipe_requirement_rows"]
        )
    }


# --- NODE 5 — SQL -----------------------------------------------------------
def check_inventory(state: SupplyChainState) -> dict:
    rows = get_inventory(state["target_fc"])
    return {
        "inventory_rows": rows,
        "on_hand_by_sku": calculations.on_hand_by_sku(rows),
    }


# --- NODE 6 — SQL -----------------------------------------------------------
def check_purchase_orders(state: SupplyChainState) -> dict:
    """Only POs that are confirmed and land by the date the stock is needed.

    The filter is in the SQL query. A PO arriving the next morning does not
    help a production run tonight, so it is left out of the projection rather
    than counted and explained away.
    """
    rows = get_confirmed_purchase_orders(state["target_fc"], state["target_date"])
    return {
        "purchase_order_rows": rows,
        "inbound_by_sku": calculations.inbound_by_sku(rows),
    }


# --- NODE 7 — RULES --------------------------------------------------------
def calculate_shortage(state: SupplyChainState) -> dict:
    shortages = calculations.shortages_by_sku(
        state["required_by_sku"], state["on_hand_by_sku"], state["inbound_by_sku"]
    )
    target_sku = state.get("target_sku")
    if target_sku:
        target_shortage = shortages.get(target_sku, 0.0)
    else:
        target_shortage = max(shortages.values(), default=0.0)
    return {"shortages_by_sku": shortages, "target_shortage": target_shortage}


# --- ROUTER ----------------------------------------------------------------
def shortage_router(state: SupplyChainState) -> Literal["investigate", "no_shortage"]:
    """Decides whether there is anything to investigate.

    A plain comparison rather than a model call. If the projection says there
    is enough stock, there is nothing for the expensive half of the graph to do.
    """
    return "investigate" if state.get("target_shortage", 0) > 0 else "no_shortage"


# --- NODE 8 — RULES --------------------------------------------------------
def calculate_downstream_impact(state: SupplyChainState) -> dict:
    sku = state.get("target_sku")
    if not sku:
        # An overview question: look at whatever is worst first.
        sku = max(state["shortages_by_sku"], key=state["shortages_by_sku"].get)
    return {
        "target_sku": sku,
        "impacted_recipes": calculations.impacted_recipes(
            state["demand_rows"], state["recipe_requirement_rows"], sku
        ),
    }


# --- NODE 9 — SQL ----------------------------------------------------------
def check_other_fcs(state: SupplyChainState) -> dict:
    """Spare stock at the other sites, and the substitute's stock here.

    A site's excess is what it holds minus its own forecast for the same date,
    so the copilot never proposes solving one site's shortage by creating
    another one.
    """
    sku = state["target_sku"]
    fc = state["target_fc"]
    rows = get_other_fc_excess(sku, fc, state["target_date"])
    rule = business_rules.substitution_rule_for(sku)
    substitute_on_hand = get_on_hand(fc, rule.substitute_sku) if rule else 0.0

    # POs that exist for this SKU but do not count toward availability, so the
    # summary can say which ones were left out and why.
    counted = {r["po_id"] for r in state.get("purchase_order_rows", [])}
    excluded = [
        po
        for po in get_all_purchase_orders(fc, sku)
        if po["po_id"] not in counted
    ]

    return {
        "other_fc_rows": rows,
        "substitute_on_hand": substitute_on_hand,
        "excluded_purchase_orders": excluded,
    }


# --- NODE 10 — RAG ---------------------------------------------------------
def search_supplier_contract(state: SupplyChainState) -> dict:
    """Retrieve the contract for the supplier that actually owns this SKU."""
    sku = state["target_sku"]
    supplier_id = get_supplier_for_sku(sku)
    chunks = vector_search(
        query=f"expedited supply lead time capacity shortage rules for {sku}",
        document_type="supplier_contract",
        supplier_id=supplier_id,
        sku=sku,
        k=3,
    )
    return {"supplier_contract_chunks": chunks}


# --- NODE 11 — RAG ---------------------------------------------------------
def search_substitution_policy(state: SupplyChainState) -> dict:
    recipe_names = ", ".join(r["recipe_name"] for r in state["impacted_recipes"])
    chunks = vector_search(
        query=f"approved substitute for {state['target_sku']} in recipes {recipe_names}",
        document_type="substitution_policy",
        sku=state["target_sku"],
        k=3,
    )
    return {"substitution_policy_chunks": chunks}


# --- NODE 12 — RAG ---------------------------------------------------------
def search_transfer_policy(state: SupplyChainState) -> dict:
    chunks = vector_search(
        query=(
            "inter fulfillment center transfer limits approval timing chilled "
            f"ingredient {state['target_sku']}"
        ),
        document_type="transfer_policy",
        k=3,
    )
    return {"transfer_policy_chunks": chunks}


# --- NODE 13 — LLM ---------------------------------------------------------
class MitigationOption(BaseModel):
    """One proposed action.

    action_type and the quantity fields are what make the proposal checkable.
    Free text alone would read fine and could not be validated against the
    inventory, so the model is required to say which kind of action it means
    and, for a transfer, which site it wants to take the stock from.
    """

    action_type: Literal["transfer", "substitute", "expedite", "other"]
    action: str = Field(description="One sentence a planner can read")
    estimated_units: int = Field(description="Units this action would recover")
    source_fc: str | None = Field(
        default=None, description="For a transfer: the sending site's code"
    )
    recipes: list[str] = Field(
        default_factory=list,
        description="For a substitution: the recipe IDs it would apply to",
    )
    rationale: str


class MitigationPlan(BaseModel):
    options: list[MitigationOption]


def generate_mitigation_options(state: SupplyChainState) -> dict:
    """Ask the model for options. It proposes; it does not decide.

    The quantities that come back are treated as proposals, not facts. The next
    node checks every one of them against the inventory and the policy limits.
    """
    planner = _structured(MitigationPlan)
    prompt = f"""
    You are a supply-chain planning assistant working only with the synthetic
    evidence below. Do not invent quantities, contract terms, or policies.

    Target SKU: {state['target_sku']}
    Site with the shortage: {state['target_fc']}
    Projected shortage: {state['target_shortage']} units
    Recipes consuming this SKU: {state['impacted_recipes']}
    Spare stock at other sites: {state['other_fc_rows']}
    Substitute units on hand here: {state.get('substitute_on_hand', 0)}

    Supplier contract: {state['supplier_contract_chunks']}
    Substitution policy: {state['substitution_policy_chunks']}
    Transfer policy: {state['transfer_policy_chunks']}

    Propose practical mitigation options. For each one give the action type, the
    units it would recover, and for a transfer the site the stock would come
    from. Every option will be checked against the inventory and the policy
    limits before a planner sees it, so quantities you cannot support from the
    evidence above will be reduced or rejected.
    """
    plan = planner.invoke(prompt)
    return {"mitigation_options": [o.model_dump() for o in plan.options]}


# --- NODE 14 — RULES -------------------------------------------------------
def validate_mitigation_options(state: SupplyChainState) -> dict:
    """Check each proposed option against the facts and the operational limits.

    This is the node that stops a plausible-sounding recommendation from
    reaching a planner as if it were feasible. It caps quantities to what the
    inventory and the policies actually allow, rejects actions that are not
    possible, and works out how much of the shortage is still open afterwards.
    """
    snapshot = validation.ConstraintSnapshot(
        target_sku=state["target_sku"],
        target_fc=state["target_fc"],
        shortage=float(state["target_shortage"]),
        other_fc_excess={
            r["fc_id"]: float(r["excess_qty"]) for r in state.get("other_fc_rows", [])
        },
        substitute_on_hand=float(state.get("substitute_on_hand", 0.0)),
        impacted_recipes=state.get("impacted_recipes", []),
        hours_until_required=hours_between(
            state.get("current_date"), state.get("target_date")
        ),
    )
    report = validation.validate_options(state["mitigation_options"], snapshot)
    return {
        "validated_options": [vars(o) for o in report.options],
        "residual_shortage": report.residual_shortage,
        "validation_warnings": report.warnings,
    }


# --- NODE 15 — HUMAN -------------------------------------------------------
def human_review(state: SupplyChainState) -> dict:
    """Stop here. A person decides.

    The graph pauses with a real LangGraph interrupt, so nothing past this
    point runs until someone resumes it with a decision. The planner is shown
    the validated quantities, not the model's original proposals, along with
    anything the validator rejected or cut back.
    """
    decision = interrupt(
        {
            "message": "Review the validated mitigation options. No action has been taken.",
            "target_sku": state["target_sku"],
            "shortage": state["target_shortage"],
            "residual_shortage_if_all_approved": state.get("residual_shortage"),
            "options": state.get("validated_options", []),
            "warnings": state.get("validation_warnings", []),
            "allowed_responses": ["approve", "reject", "edit"],
        }
    )
    return {"human_decision": decision}


# --- NODE 16 — LLM ---------------------------------------------------------
def generate_final_response(state: SupplyChainState) -> dict:
    """Build the summary from validated data, then fact-check the narrative.

    The figures come from `src/summary.py`, which copies them from verified
    fields. A model is then asked for a short covering paragraph, and every
    quantity in it is checked against those same figures. If the check fails
    the paragraph is dropped and the verified summary is shown on its own, so
    an invented number cannot reach a planner inside readable prose.
    """
    scenario = summary_mod.scenario_view_from_state(state)
    report = summary_mod.report_from_state(state)
    decision_payload = state.get("human_decision") or {}

    final = summary_mod.build_summary(
        scenario,
        report,
        decision=str(decision_payload.get("decision", "pending")),
        approved_option_indexes=list(decision_payload.get("approved_indexes", [])),
    )
    verified_text = summary_mod.render_summary(final)

    if final.shortage <= 0:
        return {
            "final_response": verified_text,
            "final_summary": vars(final),
            "narrative_passed_fact_check": True,
            "fact_check_notes": [],
        }

    # Ask for prose, not for numbers. The numbers are already settled.
    try:
        reply = _llm().invoke(
            "You are writing two or three sentences for a supply-chain planner, "
            "summarising a shortage investigation that is already complete.\n\n"
            "Rules: use only the figures in the report below. Do not introduce any "
            "number that is not in it. Do not say any action has been taken, "
            "ordered, executed, or confirmed — nothing has. Plain English, no "
            "jargon.\n\n"
            f"Report:\n{verified_text}"
        )
        narrative = message_text(reply.content)
    except Exception as error:  # noqa: BLE001 — any failure falls back safely
        return {
            "final_response": verified_text,
            "final_summary": vars(final),
            "narrative_passed_fact_check": False,
            "fact_check_notes": [f"The narrative could not be generated: {error}"],
        }

    check = summary_mod.fact_check(narrative, final)
    if check.passed:
        return {
            "final_response": f"{narrative}\n\n{verified_text}",
            "final_summary": vars(final),
            "narrative_passed_fact_check": True,
            "fact_check_notes": [],
        }

    return {
        "final_response": (
            f"{verified_text}\n\n"
            "A written summary was generated and withheld: it did not match the "
            "verified figures. The numbers above come straight from the checked "
            "data."
        ),
        "final_summary": vars(final),
        "narrative_passed_fact_check": False,
        "fact_check_notes": check.notes,
    }
