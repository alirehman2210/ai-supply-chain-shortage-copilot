from typing import Any, Optional, TypedDict


class SupplyChainState(TypedDict, total=False):
    # Original request
    user_question: str
    current_date: str

    # Parsed intent / scope
    intent: str
    target_date: str
    target_fc: str
    target_sku: Optional[str]

    # Raw structured facts
    demand_rows: list[dict[str, Any]]
    recipe_requirement_rows: list[dict[str, Any]]
    inventory_rows: list[dict[str, Any]]
    purchase_order_rows: list[dict[str, Any]]
    other_fc_rows: list[dict[str, Any]]
    substitute_on_hand: float

    # Deterministic calculations
    required_by_sku: dict[str, float]
    on_hand_by_sku: dict[str, float]
    inbound_by_sku: dict[str, float]
    shortages_by_sku: dict[str, float]
    target_shortage: float
    impacted_recipes: list[dict[str, Any]]

    # Retrieved RAG context — keep raw chunks, not prompts
    supplier_contract_chunks: list[dict[str, Any]]
    substitution_policy_chunks: list[dict[str, Any]]
    transfer_policy_chunks: list[dict[str, Any]]

    # What the model proposed
    mitigation_options: list[dict[str, Any]]

    # What the deterministic validator allowed, and what is left over
    validated_options: list[dict[str, Any]]
    residual_shortage: float
    validation_warnings: list[str]

    # Decision and output
    human_decision: dict[str, Any]
    final_response: str
