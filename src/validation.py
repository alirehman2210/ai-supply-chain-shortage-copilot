"""Checks each proposed mitigation option against the facts and the rules.

The language model proposes mitigation options. This module decides how many
units of each option are actually feasible, and it is the only thing that gets
to make that call. It uses the inventory figures the SQL tools returned and the
limits in business_rules.py. It never calls a model.

Three outcomes per option:

  ok        the proposed quantity is feasible as written
  capped    the action is allowed but the quantity is not; it is reduced to
            the largest feasible number and the binding limit is named
  rejected  the action is not possible at all, and it is credited zero units

A rejected or capped option is never silently dropped or quietly rewritten. It
stays in the report with the reason attached, because a planner needs to see
that the copilot wanted to move 1,800 units and could only justify 600.

Nothing here approves anything for execution. Every option comes back marked
as requiring human approval.
"""

from dataclasses import dataclass, field
from typing import Any

from src import business_rules

OK = "ok"
CAPPED = "capped"
REJECTED = "rejected"


@dataclass
class ConstraintSnapshot:
    """The deterministic facts an option is checked against.

    Built from SQL rows in the graph, and from CSV rows in the offline runner.
    Everything in here is a measured or calculated number, not a model output.
    """

    target_sku: str
    target_fc: str
    shortage: float
    #: fc_id -> units that site can release after covering its own forecast
    other_fc_excess: dict[str, float] = field(default_factory=dict)
    #: on-hand units of the approved substitute SKU at the target site
    substitute_on_hand: float = 0.0
    #: rows from calculations.impacted_recipes()
    impacted_recipes: list[dict[str, Any]] = field(default_factory=list)
    #: expedited units already requested from the supplier this calendar week
    expedite_units_committed_this_week: float = 0.0
    #: Hours between now and the date the stock is needed. An action that
    #: cannot physically arrive in that window is not a mitigation, however
    #: much stock exists. None means the window is unknown, in which case the
    #: timing check is skipped and the option is flagged as unchecked rather
    #: than quietly treated as feasible.
    hours_until_required: float | None = None


@dataclass
class ValidatedOption:
    action_type: str
    description: str
    proposed_units: float
    approved_units: float
    verdict: str
    reasons: list[str] = field(default_factory=list)
    source_fc: str | None = None
    requires_human_approval: bool = True


@dataclass
class ValidationReport:
    options: list[ValidatedOption]
    shortage: float
    approved_units_total: float
    residual_shortage: float
    warnings: list[str] = field(default_factory=list)
    timing_checked: bool = True

    @property
    def fully_covered(self) -> bool:
        return self.residual_shortage <= 0

    @property
    def feasible_options(self) -> list["ValidatedOption"]:
        """Options that contribute units. What a planner can actually pick."""
        return [o for o in self.options if o.approved_units > 0]

    @property
    def rejected_options(self) -> list[ValidatedOption]:
        return [o for o in self.options if o.verdict == REJECTED]

    @property
    def capped_options(self) -> list[ValidatedOption]:
        return [o for o in self.options if o.verdict == CAPPED]


def _check_timing(
    snapshot: ConstraintSnapshot, required_hours: int, what: str, source: str
) -> tuple[bool, str | None]:
    """Can this action arrive before the stock is needed?

    Returns (feasible, note). An unknown window is feasible-but-noted, because
    guessing that there is enough time is exactly the kind of invented fact
    this module exists to prevent.
    """
    available = snapshot.hours_until_required
    if available is None:
        return True, (
            f"Timing not checked: the window until the required date is unknown. "
            f"{what} needs {required_hours} hours ({source})."
        )
    if available < required_hours:
        return False, (
            f"Cannot arrive in time. {what} needs {required_hours} hours and only "
            f"{available:.0f} remain before the stock is required ({source})."
        )
    return True, None


def _approved_substitution_demand(snapshot: ConstraintSnapshot) -> float:
    """Units of demand a substitution is actually allowed to cover.

    A substitute approved for two of the three recipes that consume the SKU can
    only displace the demand belonging to those two recipes.
    """
    rule = business_rules.substitution_rule_for(snapshot.target_sku)
    if rule is None:
        return 0.0
    return sum(
        float(r.get("sku_units_required", 0.0))
        for r in snapshot.impacted_recipes
        if r.get("recipe_id") in rule.approved_recipes
    )


def _validate_transfer(
    option: dict[str, Any],
    snapshot: ConstraintSnapshot,
    remaining_excess: dict[str, float],
) -> ValidatedOption:
    proposed = float(option.get("estimated_units", 0) or 0)
    source_fc = option.get("source_fc")
    reasons: list[str] = []

    if not source_fc:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=["No sending fulfillment center was named, so the transfer cannot be checked."],
        )

    if source_fc == snapshot.target_fc:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[f"{source_fc} is the site with the shortage; it cannot transfer to itself."],
            source_fc=source_fc,
        )

    if source_fc not in snapshot.other_fc_excess:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[f"{source_fc} does not hold {snapshot.target_sku}, or was not measured."],
            source_fc=source_fc,
        )

    in_time, timing_note = _check_timing(
        snapshot,
        business_rules.TRANSFER_LEAD_TIME_HOURS,
        "An inter-site transfer",
        "warehouse_transfer_policy.md, timing",
    )
    if not in_time:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[timing_note or "Cannot arrive in time."],
            source_fc=source_fc,
        )
    if timing_note:
        reasons.append(timing_note)

    available = remaining_excess.get(source_fc, 0.0)
    approved = proposed

    if available <= 0:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[
                f"{source_fc} has no units left to release after covering its own forecast "
                f"for the same date (warehouse_transfer_policy.md, transfer eligibility)."
            ],
            source_fc=source_fc,
        )

    if approved > available:
        reasons.append(
            f"Reduced to {available:.0f} units. {source_fc} only has that much spare after "
            f"covering its own forecast for the same date "
            f"(warehouse_transfer_policy.md, transfer eligibility)."
        )
        approved = available

    cap = business_rules.MAX_UNITS_PER_URGENT_TRANSFER
    if approved > cap:
        reasons.append(
            f"Reduced to {cap} units. A single urgent transfer of a temperature-controlled "
            f"ingredient may not exceed {cap} units (warehouse_transfer_policy.md, capacity)."
        )
        approved = float(cap)

    if proposed <= 0:
        return ValidatedOption(
            action_type="transfer",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=["Proposed quantity is zero or negative."],
            source_fc=source_fc,
        )

    remaining_excess[source_fc] = available - approved
    return ValidatedOption(
        action_type="transfer",
        description=option.get("action", ""),
        proposed_units=proposed,
        approved_units=approved,
        verdict=CAPPED if approved < proposed else OK,
        reasons=reasons,
        source_fc=source_fc,
    )


def _validate_substitution(
    option: dict[str, Any],
    snapshot: ConstraintSnapshot,
    remaining_substitute: dict[str, float],
) -> ValidatedOption:
    proposed = float(option.get("estimated_units", 0) or 0)
    reasons: list[str] = []
    rule = business_rules.substitution_rule_for(snapshot.target_sku)
    # True when part of the option's scope was disallowed even though its
    # quantity survived, e.g. one of the named recipes is not approved.
    scope_narrowed = False

    if rule is None:
        return ValidatedOption(
            action_type="substitute",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[
                f"No pre-approved substitute exists for {snapshot.target_sku} "
                f"(substitution_policy.md)."
            ],
        )

    named_recipes = option.get("recipes") or []
    if named_recipes:
        unapproved = [r for r in named_recipes if r not in rule.approved_recipes]
        approved_named = [r for r in named_recipes if r in rule.approved_recipes]
        if not approved_named:
            return ValidatedOption(
                action_type="substitute",
                description=option.get("action", ""),
                proposed_units=proposed,
                approved_units=0.0,
                verdict=REJECTED,
                reasons=[
                    f"{rule.substitute_sku} is not approved for "
                    f"{', '.join(unapproved)} ({rule.source_document})."
                ],
            )
        if unapproved:
            scope_narrowed = True
            reasons.append(
                f"{rule.substitute_sku} is not approved for {', '.join(unapproved)}; "
                f"only the approved recipes are counted ({rule.source_document})."
            )

    if proposed <= 0:
        return ValidatedOption(
            action_type="substitute",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=["Proposed quantity is zero or negative."],
        )

    approved = proposed

    on_hand_left = remaining_substitute.get(rule.substitute_sku, 0.0)
    if on_hand_left <= 0:
        return ValidatedOption(
            action_type="substitute",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[
                f"{snapshot.target_fc} has no {rule.substitute_sku} on hand. A site may only "
                f"use alternate inventory up to its physical on-hand quantity "
                f"({rule.source_document}, quantity constraint)."
            ],
        )

    if approved > on_hand_left:
        reasons.append(
            f"Reduced to {on_hand_left:.0f} units. {snapshot.target_fc} only holds that many "
            f"{rule.substitute_sku}, and a site may only substitute up to its physical "
            f"on-hand quantity ({rule.source_document}, quantity constraint)."
        )
        approved = on_hand_left

    allowed_demand = _approved_substitution_demand(snapshot)
    if allowed_demand and approved > allowed_demand:
        reasons.append(
            f"Reduced to {allowed_demand:.0f} units. That is all the demand "
            f"{rule.substitute_sku} is approved to cover ({rule.source_document})."
        )
        approved = allowed_demand

    remaining_substitute[rule.substitute_sku] = on_hand_left - approved
    return ValidatedOption(
        action_type="substitute",
        description=option.get("action", ""),
        proposed_units=proposed,
        approved_units=approved,
        verdict=CAPPED if (approved < proposed or scope_narrowed) else OK,
        reasons=reasons,
    )


def _validate_expedite(
    option: dict[str, Any],
    snapshot: ConstraintSnapshot,
    expedite_budget: dict[str, float],
) -> ValidatedOption:
    proposed = float(option.get("estimated_units", 0) or 0)
    reasons: list[str] = []

    if proposed <= 0:
        return ValidatedOption(
            action_type="expedite",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=["Proposed quantity is zero or negative."],
        )

    in_time, timing_note = _check_timing(
        snapshot,
        business_rules.EXPEDITE_MIN_NOTICE_HOURS,
        "An expedited supplier delivery",
        "supplier_nordfarm_contract.md, section 5",
    )
    if not in_time:
        return ValidatedOption(
            action_type="expedite",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[timing_note or "Cannot arrive in time."],
        )
    if timing_note:
        reasons.append(timing_note)

    remaining = expedite_budget["remaining"]
    if remaining <= 0:
        return ValidatedOption(
            action_type="expedite",
            description=option.get("action", ""),
            proposed_units=proposed,
            approved_units=0.0,
            verdict=REJECTED,
            reasons=[
                f"The supplier's expedited allowance of "
                f"{business_rules.EXPEDITE_MAX_UNITS_PER_WEEK} units for this calendar week "
                f"is already committed (supplier_nordfarm_contract.md, section 5)."
            ],
        )

    approved = proposed
    if approved > remaining:
        reasons.append(
            f"Reduced to {remaining:.0f} units. The supplier may provide at most "
            f"{business_rules.EXPEDITE_MAX_UNITS_PER_WEEK} expedited units per calendar week "
            f"(supplier_nordfarm_contract.md, section 5)."
        )
        approved = remaining

    reasons.append(
        f"Carries a {business_rules.EXPEDITE_SURCHARGE_PERCENT}% unit-price surcharge and needs "
        f"at least {business_rules.EXPEDITE_MIN_NOTICE_HOURS} hours notice. Not committed until "
        f"the supplier confirms in writing (supplier_nordfarm_contract.md, section 5)."
    )

    expedite_budget["remaining"] = remaining - approved
    return ValidatedOption(
        action_type="expedite",
        description=option.get("action", ""),
        proposed_units=proposed,
        approved_units=approved,
        verdict=CAPPED if approved < proposed else OK,
        reasons=reasons,
    )


def validate_options(
    proposed_options: list[dict[str, Any]],
    snapshot: ConstraintSnapshot,
) -> ValidationReport:
    """Check every proposed option and work out what shortage is left.

    Options are checked in the order the SOP prefers, not the order the model
    produced them, so that a transfer gets first claim on a site's spare stock
    rather than whichever option the model happened to list first.
    """
    remaining_excess = dict(snapshot.other_fc_excess)
    rule = business_rules.substitution_rule_for(snapshot.target_sku)
    remaining_substitute = (
        {rule.substitute_sku: snapshot.substitute_on_hand} if rule else {}
    )
    expedite_budget = {
        "remaining": max(
            business_rules.EXPEDITE_MAX_UNITS_PER_WEEK
            - snapshot.expedite_units_committed_this_week,
            0.0,
        )
    }

    order = business_rules.MITIGATION_PREFERENCE_ORDER

    def sort_key(indexed: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        index, option = indexed
        action_type = str(option.get("action_type", "other")).lower()
        rank = order.index(action_type) if action_type in order else len(order)
        return (rank, index)

    validated: list[ValidatedOption] = []
    for _, option in sorted(enumerate(proposed_options), key=sort_key):
        action_type = str(option.get("action_type", "other")).lower()

        if action_type == "transfer":
            validated.append(_validate_transfer(option, snapshot, remaining_excess))
        elif action_type == "substitute":
            validated.append(
                _validate_substitution(option, snapshot, remaining_substitute)
            )
        elif action_type == "expedite":
            validated.append(_validate_expedite(option, snapshot, expedite_budget))
        else:
            # An action the validator has no rule for is credited zero units.
            # It is kept in the report so the planner can still read it, but it
            # is never allowed to close part of the shortage on trust.
            validated.append(
                ValidatedOption(
                    action_type="other",
                    description=option.get("action", ""),
                    proposed_units=float(option.get("estimated_units", 0) or 0),
                    approved_units=0.0,
                    verdict=REJECTED,
                    reasons=[
                        "No deterministic rule covers this action type, so no units are "
                        "credited against the shortage. A planner has to assess it."
                    ],
                )
            )

    approved_total = sum(o.approved_units for o in validated)
    residual = max(snapshot.shortage - approved_total, 0.0)

    warnings: list[str] = []
    if snapshot.hours_until_required is None and any(
        o.action_type in ("transfer", "expedite") for o in validated
    ):
        warnings.append(
            "Delivery timing was not checked: the window until the required date "
            "is unknown. Transfer and expedite lead times have not been verified."
        )
    if approved_total > snapshot.shortage > 0:
        warnings.append(
            f"The approved actions total {approved_total:.0f} units against a shortage of "
            f"{snapshot.shortage:.0f}. Taking all of them would over-procure by "
            f"{approved_total - snapshot.shortage:.0f} units."
        )
    if residual > 0:
        warnings.append(
            f"{residual:.0f} units of the shortage remain uncovered by feasible actions. "
            f"The shortage response SOP asks for unresolved shortages to be escalated."
        )
    for option in validated:
        if option.verdict == REJECTED:
            warnings.append(
                f"Rejected: {option.description or option.action_type} — "
                f"{' '.join(option.reasons)}"
            )

    # Nothing in this module may mark an action as safe to execute.
    for option in validated:
        assert option.action_type in business_rules.ACTION_TYPES_REQUIRING_HUMAN_APPROVAL
        option.requires_human_approval = True

    return ValidationReport(
        options=validated,
        shortage=snapshot.shortage,
        approved_units_total=approved_total,
        residual_shortage=residual,
        warnings=warnings,
        timing_checked=snapshot.hours_until_required is not None,
    )
