"""Operational limits that gate what the copilot is allowed to recommend.

These are deliberately written as configuration rather than read out of the
policy documents at runtime.

The policy documents in data/rag_docs/ are retrieved and shown to the planner
so they can read the rule in its own words. But the *numbers* that decide
whether a recommendation is feasible are kept here, because a limit parsed out
of prose by a language model is a limit that can be misread, and a mitigation
plan that quietly exceeds a supplier's weekly cap is worse than no plan at all.

Each entry names the document it came from. When a policy changes, the document
and the constant have to be updated together, and the test suite checks that
the two still agree on the numbers.
"""

from dataclasses import dataclass, field

# --- Inter-fulfillment-center transfers -------------------------------------
# Source: data/rag_docs/warehouse_transfer_policy.md, "Capacity"
MAX_UNITS_PER_URGENT_TRANSFER = 1000

# Source: data/rag_docs/warehouse_transfer_policy.md, "Timing"
TRANSFER_LEAD_TIME_HOURS = 24

# --- Supplier expedite ------------------------------------------------------
# Source: data/rag_docs/supplier_nordfarm_contract.md, section 5
EXPEDITE_MAX_UNITS_PER_WEEK = 1200
EXPEDITE_MIN_NOTICE_HOURS = 48
EXPEDITE_SURCHARGE_PERCENT = 8


@dataclass(frozen=True)
class SubstitutionRule:
    """A pre-approved ingredient substitution and the recipes it covers.

    approved_recipes is a allow-list, not a deny-list. A recipe that is not
    named here is not approved, even if nothing explicitly forbids it.
    """

    primary_sku: str
    substitute_sku: str
    approved_recipes: frozenset[str] = field(default_factory=frozenset)
    source_document: str = ""


# Source: data/rag_docs/substitution_policy.md, "Approved uses"
# CHK-250-B is approved for Chicken Teriyaki and Tuscan Chicken only. It is
# explicitly not approved for Creamy Chicken Pasta, because the alternate SKU's
# preparation spec has not been validated for that recipe's sauce process.
SUBSTITUTION_RULES: dict[str, SubstitutionRule] = {
    "CHK-250": SubstitutionRule(
        primary_sku="CHK-250",
        substitute_sku="CHK-250-B",
        approved_recipes=frozenset({"RCP-TERIYAKI", "RCP-TUSCAN"}),
        source_document="substitution_policy.md",
    )
}

# --- Human approval ---------------------------------------------------------
# Source: data/rag_docs/shortage_response_sop.md, closing paragraph, and
# data/rag_docs/warehouse_transfer_policy.md, "Approval".
#
# Every action type the copilot can propose moves physical stock, spends money,
# or commits the business to a supplier. None of them may be executed by the
# system, so this is a list of action types rather than a rule with exceptions.
ACTION_TYPES_REQUIRING_HUMAN_APPROVAL = frozenset(
    {"transfer", "substitute", "expedite", "other"}
)

# Source: data/rag_docs/shortage_response_sop.md, numbered list.
# The SOP orders mitigation: verify data, confirm POs, then transfers, then
# substitutions, then expedites. Transfers come before substitutions because
# they move the ingredient the recipe was specified with; substitutions change
# what the customer receives. Expedites come last because they cost money and
# depend on the supplier agreeing.
MITIGATION_PREFERENCE_ORDER = ("transfer", "substitute", "expedite", "other")


def substitution_rule_for(sku: str) -> SubstitutionRule | None:
    """The approved substitution for a SKU, or None if there is not one."""
    return SUBSTITUTION_RULES.get(sku)
