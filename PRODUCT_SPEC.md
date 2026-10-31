# Product Spec — Supply Chain Exception Copilot

How I approached the problem: who it is for, what it has to do, how I would
know it works, and what is actually built versus what is still an intention.

The README explains what the prototype does and shows the worked example.
[DECISIONS.md](DECISIONS.md) covers the tradeoffs. This document is the
requirements and the honest state of the build.

## Target User

**A supply-chain planner at a meal-kit operation.**

They are responsible for making sure each fulfillment center has the
ingredients it needs for the next production run. They own the outcome — a
production run short of chicken is their problem — but most of the levers
belong to other people. A transfer needs the other site's agreement. An
expedite needs the supplier's written confirmation. A substitution changes what
a customer receives.

What they are good at is judgement about which lever to pull. What wastes their
time is assembling the picture that judgement needs.

Secondary user: a procurement manager, who cares mainly about the supplier
options, the surcharge, and whether a commitment is within contract.

## The Operational Problem

A projected shortage is a question with the answer scattered across systems:

| To find out | You look at |
| --- | --- |
| How much is needed | The demand forecast, by recipe |
| How much that means in units | The recipe bill of materials |
| What is in the building | Inventory by site and SKU |
| What is arriving | Open purchase orders, with dates and status |
| What is affected | Which recipes consume the ingredient |
| What is available elsewhere | Other sites' inventory *and their own demand* |
| What is allowed | Supplier contract, substitution policy, transfer policy |

Three things make this harder than a dashboard:

1. **Some of the answer is in prose.** Transfer caps, expedite allowances and
   substitution approvals live in documents, not fields.
2. **Dates matter and are easy to get wrong.** A confirmed purchase order that
   arrives the morning after production is worthless, and it looks identical to
   a useful one in a list.
3. **Every option has a limit that is not where the option is.** The stock
   figure for the alternate ingredient is in the warehouse system; whether you
   may use it for a given recipe is in a policy document.

It is also time-sensitive. This investigation happens under pressure, which is
exactly when someone skips checking the third constraint.

## User Story

> As a supply-chain planner, when an ingredient is projected short, I want the
> shortage explained with its causes and the feasible mitigation options
> already checked against our stock and our policies, so I can choose an action
> quickly without assembling the picture myself or discovering at execution
> time that the recommendation was never possible.

## Product Objectives

1. **Collapse the investigation into one pass.** One question, one grounded
   answer, instead of four systems and three documents.
2. **Never present an infeasible recommendation as feasible.** A capped or
   rejected option with its reason is more useful than a confident wrong
   number.
3. **Keep the planner in the decision.** The copilot investigates and
   recommends. It does not act.
4. **Make every number traceable.** Each figure comes from a named source and
   each policy claim names its document.
5. **Be honest about what is left.** If the feasible options do not close the
   shortage, say so and say by how much.

## Functional Requirements

### Implemented and verified

| # | Requirement | Verified by |
| --- | --- | --- |
| F1 | Calculate required units from forecast demand and the recipe bill of materials | `test_calculations.py`, `test_scenario.py` |
| F2 | Count only purchase orders that are confirmed **and** arrive by the required date | `test_scenario.py` |
| F3 | Calculate the projected shortage as required minus on hand minus inbound, floored at zero | `test_calculations.py` |
| F4 | Skip the investigation entirely when there is no shortage | `test_graph_structure.py` |
| F5 | Identify which recipes consume the SKU and how much each contributes | `test_calculations.py`, `test_scenario.py` |
| F6 | Find spare stock at other sites, net of each site's own demand for the same date | `test_scenario.py` |
| F7 | Retrieve the supplier contract, substitution policy and transfer policy for the SKU | Code path present; not covered by automated tests |
| F8 | Produce mitigation options as typed, quantified proposals | Schema in `src/nodes.py`; not covered by automated tests |
| F9 | Cap every proposed quantity to what the inventory allows | `test_validation.py` |
| F10 | Cap every proposed quantity to what the policy limits allow | `test_validation.py` |
| F11 | Reject actions that are not possible, with a reason | `test_validation.py` |
| F12 | Name the binding rule and its source document on every cap or rejection | `test_validation.py` |
| F13 | Report the proposed quantity alongside the approved one | `test_validation.py` |
| F14 | Report the shortage still open after all feasible actions | `test_validation.py` |
| F15 | Flag over-procurement when the approved actions exceed the shortage | `test_validation.py` |
| F16 | Stop the workflow for human approval before any action is treated as approved | `test_graph_structure.py`; interrupt observed in code |
| F17 | Run the deterministic path with no database and no API keys | `scripts/run_scenario_offline.py` |
| F18 | Keep the hard-coded limits consistent with the policy documents | `test_business_rules.py` |

### Not built

| # | Requirement | Status |
| --- | --- | --- |
| N1 | An interface for the planner to review and approve options | Not built. The workflow pauses correctly; resuming it requires writing Python. |
| N2 | Write-back — creating a draft transfer or purchase order on approval | Not built, and deliberately out of scope for a prototype. |
| N3 | An audit record of who approved what, on which facts | Not built. |
| N4 | Recommending a specific combination of options that covers the shortage without overshoot | Not built. Options are validated individually; overshoot is flagged, not resolved. |
| N5 | Multiple SKUs, multiple sites, or a date range in one run | Not built. One SKU, one site, one date. |
| N6 | An evaluation harness for retrieval quality and proposal quality | Not built. Three of six golden questions are automated. |
| N7 | Forecast uncertainty | Not built. The forecast is treated as a fixed number. |
| N8 | Authentication, access control, rate limiting | Not built. |

## Success Criteria

### Met

- The shortage arithmetic is correct for the scenario and for the cases where
  there is no shortage. Verified against the CSV data by 25 tests.
- A purchase order arriving after the required date does not count toward
  availability, and is reported separately with the reason.
- A site's spare stock never includes stock it needs for its own forecast.
- No recommendation can reach the planner with a quantity larger than the
  inventory or the policy allows. 34 tests cover the caps and rejections,
  including two proposals competing for the same stock.
- An action the validator has no rule for is credited zero units rather than
  trusted.
- The validator never increases a proposed quantity.
- Every option reaching the planner is marked as requiring human approval, and
  the only path into the human-review step comes from the validator.
- Each enforced limit still appears in the document it claims to come from.

### Not yet demonstrable

- **That retrieval returns the right policy for the right SKU.** The code looks
  up the SKU's supplier before fetching a contract, which is better than the
  hard-coded supplier it replaced, but nothing tests it. With five documents
  retrieval is hard to get wrong; that is a property of the sample size, not of
  the design.
- **That the model's proposals are sensible before validation.** The validator
  catches infeasible quantities. It cannot catch a proposal that is feasible
  and unwise.
- **That the final written summary reflects the validated numbers.** The prompt
  instructs it to use approved quantities rather than proposed ones. Nothing
  enforces that, and an LLM writing the summary is the last place a wrong
  number could still enter.

## Important Edge Cases

Each of these is handled and tested.

**Dates and purchase orders**
- A confirmed PO arriving the day after the required date is excluded. In the
  scenario this is the difference between a 1,800-unit shortage and a 400-unit
  one.
- A date with no forecast produces no demand and no shortage, rather than an
  error.

**Quantities**
- A surplus does not produce a negative shortage; it floors at zero.
- A surplus of one SKU never offsets a shortage of another.
- Missing inventory for a SKU counts as zero on hand, not as a failure.

**Competing claims on the same stock**
- Two transfer proposals from the same site cannot both claim its spare stock.
- Two substitution proposals cannot both claim the same on-hand units.
- Two expedite proposals share one weekly contract allowance.

**Transfers**
- A site with nothing spare is rejected, not reduced to zero silently.
- A proposal with no named sending site is rejected, because it cannot be
  checked.
- A site cannot transfer to itself.
- A site not holding the SKU at all is rejected.

**Substitutions**
- Capped to physical stock on hand.
- Capped to the demand belonging to approved recipes only.
- Rejected when the only recipes named are unapproved ones.
- Rejected when there is no approved substitute for the SKU.

**Malformed or unexpected proposals**
- Zero, negative or missing quantities are rejected rather than guessed.
- An action type with no validation rule is credited zero units.
- An unrecognised action type does not slip through as valid.
- No proposals at all leaves the full shortage reported as open.

**Not handled**
- A shortage at two sites at once, where fixing one changes the other's spare
  stock. Each run looks at one site.
- A transfer that would drop a sending site below a safety stock level, because
  the data has no safety stock.

## Human Approval Requirements

Every action type the copilot can propose — transfer, substitute, expedite, or
anything unclassified — requires human approval. This is a list of action types
rather than a rule with exceptions, because all of them move physical stock,
spend money, or commit the business to a supplier or a customer-facing change.

**What the system may do:** read data, calculate, retrieve policies, propose
options, validate them, and draft a request.

**What it may not do:** create or change a purchase order, move stock, contact
a supplier, change a forecast, or change a production plan.

**How it is enforced.** The workflow uses a real LangGraph interrupt at
`human_review`. Execution stops there and does not resume until a decision is
passed back in. The validator also marks every option as requiring approval,
and asserts that each action type is one it knows needs approval, so an option
cannot be constructed that claims otherwise.

**What the planner is shown:** the validated quantities, not the model's
originals; the original proposal next to each approved figure; the reason for
every cap and rejection with its source document; and the shortage that would
remain if they approved everything.

**Honest limit:** the pause is real and tested, but there is no screen. In
practice this means the approval boundary is correct in the workflow and not
yet usable by a planner. That is the single biggest gap in the project.

## Safety and Reliability Requirements

| Requirement | How it is met |
| --- | --- |
| Arithmetic is never delegated to a model | `src/calculations.py` has no model client; a test asserts it |
| Feasibility is never delegated to a model | `src/validation.py` has no model client; a test asserts it |
| Policy limits cannot be misread from prose at runtime | Limits are configuration in `src/business_rules.py`, each naming its source document |
| Configuration cannot drift from the documents unnoticed | `test_business_rules.py` checks each number still appears in its document |
| No proposal can bypass validation | Only one edge leads into `human_review`, from the validator; asserted by test |
| A validator failure cannot inflate a quantity | The validator never increases a proposed quantity; asserted by test |
| Unknown actions fail closed | Credited zero units, kept visible for the planner |
| Unresolved shortages are surfaced, not absorbed | Residual shortage is reported and flagged for escalation |
| Claims are traceable | Figures come from named structured sources; policy claims name their document |

**Reliability gaps.** Model calls have no retry or fallback handling, so an API
error fails the run. The offline path and the PostgreSQL path load rows through
different code and share only the arithmetic, so a bug in one loader would not
be caught by the other. There is no logging or tracing wired up; LangSmith
variables exist in `.env.example` but nothing in the code reads them.

## Validation Results

```
python3 -m unittest discover tests
105 tests, 21 classes, 5 files — OK
```

95 tests run on the standard library alone. The 10 covering the compiled
LangGraph workflow skip if LangGraph is not installed.

**Scenario figures, verified against the CSV data:** 12,400 required; 8,900 on
hand; 1,700 confirmed inbound arriving in time; 10,600 available; 1,800 short.
PO-882 (1,400 units, confirmed, arriving one day late) excluded. Three recipes
affected, contributing 6,200 + 4,000 + 2,200 = 12,400. Hamburg 900 units spare,
Munich 200. Alternate ingredient in Berlin: 600 units.

**Validator behaviour, reproducible via `scripts/run_scenario_offline.py`:**

| Proposed | Approved | Verdict |
| --- | ---: | --- |
| 900 units transfer from Hamburg | 900 | ok |
| 500 units transfer from Munich | 200 | capped — Munich has 200 spare |
| 1,800 units substitution | 600 | capped — 600 on hand; one recipe unapproved |
| 1,500 units expedited | 1,200 | capped — weekly contract allowance |

Flagged: approving all four would over-procure by 1,100 units against an
1,800-unit shortage.

**What was not run.** The full graph has not been executed end to end. That
needs Docker, PostgreSQL with pgvector, an Anthropic key and an OpenAI key, and
none of those were available in the environment where this was built and
documented. What is verified is that the graph compiles, that its wiring is
correct, and that every deterministic step it depends on produces the right
numbers. The two model calls and the vector retrieval are unverified.

## Known Limitations

**Scope.** One SKU, one site, one date. A real planner works across a list and
a week. The forecast is a fixed number with no confidence interval.

**Data.** All synthetic, and deliberately small: five recipes, three sites, ten
ingredients, five policy documents. Retrieval over five documents is close to
trivial, so good retrieval results here say little about retrieval at scale.
There is no safety stock, no production cutoff time, no warehouse capacity, no
public holidays, and no partial deliveries.

**Spare stock is a floor, not an estimate.** The calculation ignores the
sending site's own incoming purchase orders, so a site may be able to release
more than the figure shows. This is the conservative direction, but it is still
wrong.

**The loop is not closed.** Nothing is written back anywhere, and there is no
interface for the approval step the whole design is built around.

**The final summary is model-written.** It is instructed to use the validated
figures and is the one remaining place a wrong number could reach a planner.
Rendering that summary from the validated data with fixed code would remove the
last gap; the model would then only write the surrounding prose.

**Two code paths.** The offline runner exists so the project is runnable
without setup, and it duplicates the row-loading logic the SQL tools do. The
arithmetic and the validator are shared, which is where the risk actually is,
but the duplication is real.
