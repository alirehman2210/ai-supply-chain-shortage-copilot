"""Wires the nodes together into the workflow.

The order matters and is fixed. Facts are gathered before anything is
calculated, the shortage is calculated before the expensive investigation
starts, the model's proposals are validated before a person sees them, and the
graph stops at human_review whatever the proposals say.
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.nodes import (
    calculate_downstream_impact,
    calculate_required_inventory,
    calculate_shortage,
    check_inventory,
    check_other_fcs,
    check_purchase_orders,
    generate_final_response,
    generate_mitigation_options,
    get_demand_node,
    get_recipe_requirements_node,
    human_review,
    search_substitution_policy,
    search_supplier_contract,
    search_transfer_policy,
    shortage_router,
    understand_question,
    validate_mitigation_options,
)
from src.state import SupplyChainState


def build_graph():
    graph = StateGraph(SupplyChainState)

    graph.add_node("understand_question", understand_question)
    graph.add_node("get_demand", get_demand_node)
    graph.add_node("get_recipe_requirements", get_recipe_requirements_node)
    graph.add_node("calculate_required_inventory", calculate_required_inventory)
    graph.add_node("check_inventory", check_inventory)
    graph.add_node("check_purchase_orders", check_purchase_orders)
    graph.add_node("calculate_shortage", calculate_shortage)
    graph.add_node("calculate_downstream_impact", calculate_downstream_impact)
    graph.add_node("check_other_fcs", check_other_fcs)
    graph.add_node("search_supplier_contract", search_supplier_contract)
    graph.add_node("search_substitution_policy", search_substitution_policy)
    graph.add_node("search_transfer_policy", search_transfer_policy)
    graph.add_node("generate_mitigation_options", generate_mitigation_options)
    graph.add_node("validate_mitigation_options", validate_mitigation_options)
    graph.add_node("human_review", human_review)
    graph.add_node("generate_final_response", generate_final_response)

    # Establish the problem before investigating it.
    graph.add_edge(START, "understand_question")
    graph.add_edge("understand_question", "get_demand")
    graph.add_edge("get_demand", "get_recipe_requirements")
    graph.add_edge("get_recipe_requirements", "calculate_required_inventory")
    graph.add_edge("calculate_required_inventory", "check_inventory")
    graph.add_edge("check_inventory", "check_purchase_orders")
    graph.add_edge("check_purchase_orders", "calculate_shortage")

    # No shortage means no investigation, no retrieval and no model calls.
    graph.add_conditional_edges(
        "calculate_shortage",
        shortage_router,
        {
            "investigate": "calculate_downstream_impact",
            "no_shortage": "generate_final_response",
        },
    )

    graph.add_edge("calculate_downstream_impact", "check_other_fcs")
    graph.add_edge("check_other_fcs", "search_supplier_contract")
    graph.add_edge("search_supplier_contract", "search_substitution_policy")
    graph.add_edge("search_substitution_policy", "search_transfer_policy")
    graph.add_edge("search_transfer_policy", "generate_mitigation_options")

    # The model proposes, then deterministic rules check it, and only then
    # does a person see it. There is no edge that skips the validator.
    graph.add_edge("generate_mitigation_options", "validate_mitigation_options")
    graph.add_edge("validate_mitigation_options", "human_review")
    graph.add_edge("human_review", "generate_final_response")
    graph.add_edge("generate_final_response", END)

    return graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    app = build_graph()
    config = {"configurable": {"thread_id": "demo-1"}}
    initial_state = {
        "user_question": (
            "Why is CHK-250 at risk in Berlin on 2026-09-29 and what can we do?"
        ),
        "current_date": "2026-09-22",
    }

    result = app.invoke(initial_state, config=config)
    print(result)
    print(
        "\nThe graph has paused at human_review. Nothing has been actioned. "
        "Resume it with Command(resume={...})."
    )
