# Product Spec — Supply Chain Exception Copilot

Who this is for, what it has to do, how I know it works, and what it
deliberately does not do.

The [README](README.md) covers what the prototype does and the worked example.
[DECISIONS.md](DECISIONS.md) covers why it is built this way. This document is
the requirements and the honest state of the build.

## Target user

**A supply chain planner at a meal-kit company.**

They are responsible for each site having the ingredients it needs for the next
production run. They own the outcome — a run short of chicken is their
problem — but most of the levers belong to someone else. A transfer needs the
other site to agree. An expedite needs the supplier to confirm in writing. A
substitution changes what a customer receives.

What they are good at is judging which lever to pull. What wastes their time is
assembling the picture that judgement needs.

Secondary user: a procurement manager, who mainly cares about the supplier
options, the surcharge, and whether a commitment is within contract.

## The problem

A projected shortage is a question whose answer is scattered:

| To find out | You look at |
| --- | --- |
| How much is needed | The demand forecast, by recipe |
| What that means in units | The recipe ingredient quantities |
| What is in the building | Stock by site and ingredient |
| What is arriving | Purchase orders, with dates and status |
| What is affected | Which recipes use the ingredient |
| What is available elsewhere | Other sites' stock *and their own demand* |
| What is allowed | Supplier contract, substitution policy, transfer policy |

Three things make this harder than a dashboard:

1. **Some of the answer is in prose.** Transfer caps, expedite allowances and
   substitution approvals live in documents, not fields.
2. **Dates matter and are easy to get wrong.** A confirmed delivery arriving
   the morning after production is worthless and looks identical to a useful
   one in a list.
3. **Every option has a limit stored somewhere else.** The stock figure for the
   alternative ingredient is in the warehouse system; whether you may use it
   for a given recipe is in a policy document.

It is also time-sensitive, which is exactly when someone skips checking the
third constraint.

## User story

> As a supply chain planner, when an ingredient is projected short, I want the
> shortage explained with its causes and the feasible options already checked
> against our stock and our policies, so I can choose quickly without
> assembling the picture myself or finding out at execution time that the
> recommendation was never possible.

## Product objectives

1. **One pass instead of four systems and three documents.**
2. **Never present an impossible recommendation as possible.** A reduced or
   refused option with its reason is more useful than a confident wrong number.
3. **Keep the planner in the decision.** The copilot investigates and
   recommends. It does not act.
4. **Make every number traceable** to a named source, and every policy claim to
   a named document.
5. **Be honest about what is left.** If the feasible options do not close the
   shortage, say so and by how much.

## Functional requirements

Every row below is implemented. The right-hand column says how it is verified
and with what — "real API" means Claude and the document search actually ran.

| # | Requirement | Verified by |
| --- | --- | --- |
| F1 | Work out the ingredient, site and date from a typed question | Real API (3 tests) |
| F2 | Calculate units required from demand and recipe quantities | Offline + real API |
| F3 | Count only deliveries that are confirmed **and** arrive in time | Offline + real API |
| F4 | Calculate the shortage, floored at zero | Offline (16 tests) |
| F5 | Skip the investigation entirely when there is no shortage | Offline + mocked |
| F6 | Identify affected recipes and each one's contribution | Offline + real API |
| F7 | Find spare stock at other sites, net of their own demand | Offline + real API |
| F8 | Retrieve the supplier contract for the ingredient's own supplier | Real API (5 tests) |
| F9 | Retrieve the substitution and transfer policies | Real API |
| F10 | Produce options as typed, quantified suggestions | Real API |
| F11 | Cap any quantity to what the stock allows | Offline (44 tests) + mocked |
| F12 | Cap any quantity to what the policy limits allow | Offline + mocked |
| F13 | Refuse impossible actions, with a reason | Offline + mocked |
| F14 | Refuse actions that cannot arrive in time | Offline (10 tests) |
| F15 | Prevent two options claiming the same stock | Offline |
| F16 | Name the binding rule and its source document on every change | Offline |
| F17 | Report the original suggestion next to the approved figure | Offline + mocked |
| F18 | Report the shortage still open afterwards | Offline + mocked |
| F19 | Flag over-ordering when the options exceed the shortage | Offline |
| F20 | Stop and wait for a human before anything counts as approved | Mocked + real API |
| F21 | Accept approve, reject, and partial approval | Mocked + real API |
| F22 | Build the final summary from checked data only | Offline (33 tests) |
| F23 | Fact-check Claude's written paragraph and withhold it if wrong | Offline + mocked |
| F24 | Run the investigation and checks with no database and no keys | `app.py --demo` |
| F25 | Keep the hard-coded limits consistent with the documents | Offline (17 tests) |
| F26 | Keep credentials out of logs, errors and the repository | Offline (21 tests) |

## Success criteria

### Met and verified

**The arithmetic.** 12,400 required, 8,900 on hand, 1,700 arriving in time,
1,800 short — reproduced from live PostgreSQL and from the data files. The late
delivery (PO-882, 1,400 units, arriving 2026-09-30) is excluded and reported
separately with the reason.

**The investigation.** Three affected recipes identified, contributing 6,200 +
4,000 + 2,200 = 12,400. Spare stock: Hamburg 900, Munich 200. Alternative
ingredient in Berlin: 600 units.

**Retrieval.** All six known-answer questions pass, three of them through the
document search, each naming the document the evidence came from. A contract
search for one supplier cannot return another supplier's terms. A search for
"emergency chicken delivery at short notice" reaches the expedite clause, which
shares no words with it.

**The checks.** No recommendation can reach a planner with a quantity larger
than the stock or the policy allows. 44 tests cover the caps and refusals,
including options competing for the same stock, options that cannot arrive in
time, and malformed suggestions. The checker never increases a quantity.
Anything it has no rule for is credited zero.

**Human approval.** The workflow pauses at a real LangGraph interrupt. Approve,
reject and partial approval all work, exercised against the real APIs:
approving all four options covers 1,800 and leaves 0; approving two covers
1,500 and leaves 300; rejecting leaves the full 1,800 standing.

**The summary.** Built from checked fields. Claude's written paragraph is
checked against those same figures and withheld if it states a quantity that is
not one of them.

### What the real runs showed about the checker

On four complete real-API runs, Claude proposed four options and **all four
passed unchanged**. It sized the substitution to the 600 units that exist and
excluded the unapproved recipe itself.

So the checker confirmed rather than corrected. That is worth stating plainly
because it is easy to present as stronger evidence than it is: these runs show
the copilot producing a sound plan, not the guardrail catching a bad one. The
evidence that the guardrail works is the 54 mocked tests and the demo, where a
deliberately wrong suggestion is forced through and reduced from 1,800 to 600.

### Not demonstrable

**That retrieval holds up at scale.** Six questions over five short documents.
With a realistic corpus the wrong supplier's contract could come back and the
checker would apply the wrong limits with no sign of trouble.

**That suggestions are sensible, not merely possible.** Nothing scores
judgement quality. An option can be feasible and unwise.

**That the copilot handles the shortages it has not seen.** One ingredient at
one site on one date, with a scenario designed to contain real conflicts.

## Important edge cases

All handled and tested.

**Dates and deliveries.** A confirmed delivery arriving the day after is
excluded — the difference between an 1,800-unit shortage and a 400-unit one. A
date with no forecast produces no demand rather than an error.

**Quantities.** A surplus floors at zero rather than going negative. A surplus
of one ingredient never offsets a shortage of another. Missing stock counts as
zero, not as a failure.

**Competing claims.** Two transfers from one site cannot both claim its spare
units. Two substitutions cannot both claim the same stock. Two expedites share
one weekly allowance.

**Transfers.** A site with nothing spare is refused, not reduced to zero
quietly. A suggestion with no named sending site is refused because it cannot
be checked. A site cannot transfer to itself, or send what it does not hold.

**Substitutions.** Capped to stock on hand, and to the demand belonging to
approved recipes only. Refused when the only recipes named are unapproved ones,
or when the ingredient has no approved alternative.

**Timing.** A transfer needs 24 hours, an expedite 48. With 24 hours to go the
transfer is allowed and the expedite is refused. When the window is unknown the
option is flagged as unchecked rather than assumed fine.

**Malformed suggestions.** Zero, negative or missing quantities are refused. An
action type with no rule is credited zero units. An unrecognised action type
does not slip through. No suggestions at all leaves the full shortage reported.

**Model and search failures.** If Claude's paragraph cannot be generated, the
checked summary is still produced — the numbers never needed the model. If the
document search fails, the error surfaces rather than being swallowed, and it
carries no credentials.

**Not handled.** A shortage at two sites at once, where fixing one changes the
other's spare stock. A transfer that would take a sending site below a safety
stock level, because the data has no safety stock.

## Human approval requirements

Every action type — transfer, substitute, expedite, or anything unclassified —
requires human approval. This is a list of action types rather than a rule with
exceptions, because all of them move physical stock, spend money, or change
what a customer receives.

**The system may** read data, calculate, retrieve policies, suggest options,
check them, and draft a request.

**The system may not** create or change a purchase order, move stock, contact a
supplier, change a forecast, or change a production plan.

**How it is enforced.** A real LangGraph interrupt at the review step. Nothing
past it runs until a decision is passed back. The only route into that step is
out of the checker, asserted by test. The checker also marks every option as
requiring approval.

**What the planner sees:** the checked quantities, the original suggestion
alongside each one, the reason for every reduction and refusal with its source
document, and how much would still be open.

**Approving a refused option is allowed and adds nothing.** The planner is told
so at the time.

**What approval is.** A recorded decision inside this prototype. It is not a
purchase, not an inventory movement, and not a delivery. Every screen says so,
and tests assert that wording is present.

## Safety and reliability requirements

| Requirement | How it is met |
| --- | --- |
| Arithmetic is never delegated to a model | `src/calculations.py` has no model client; asserted by test |
| Feasibility is never delegated to a model | `src/validation.py` has no model client; asserted by test |
| Policy limits cannot be misread from prose at runtime | Limits are settings in `src/business_rules.py`, each naming its document |
| Settings cannot drift from the documents unnoticed | 17 tests check each number still appears in its document |
| No suggestion can bypass the checks | One edge into the review step, from the checker; asserted by test |
| A quantity can never be increased | Asserted by test |
| Unknown actions fail closed | Credited zero units, kept visible |
| Unresolved shortages are surfaced | Reported and flagged for escalation |
| The summary cannot carry an invented number | Built from checked fields; Claude's paragraph fact-checked and withheld on failure |
| A crash cannot leak a credential | Connection strings and provider errors pass through a redaction helper; 21 tests |
| Keys never reach the repository | `.env` ignored and untracked; scanner checks the working tree and every commit |

### Reliability gaps

Model calls have no retry or fallback, so an API error fails the run — though
the checked summary still survives a failure of the final paragraph. There is
no logging or tracing wired up. The demo path and the database path load rows
through different code and share only the arithmetic and the checks, so a bug
in one loader would not be caught by the other.

## Verified results

```
python3 -m unittest discover tests     310 tests, all passing
```

| Layer | Tests | What is real |
| --- | ---: | --- |
| Offline unit | 221 | Nothing external |
| Offline, needs LangGraph | 13 | The compiled workflow's shape |
| Mocked integration | 54 | Real workflow and database; Claude and search stubbed |
| Real end-to-end | 22 | Claude, embeddings, pgvector, PostgreSQL |

224 of the 310 run with nothing installed. The 22 real tests need `RUN_E2E=1`
so no test run spends money by accident; they completed in 67 seconds with all
22 passing.

**Six of six known-answer questions pass**, three through the document search.

**Four complete real-API workflow runs**, all calculating the 1,800-unit
shortage, retrieving the three correct documents, and pausing for review.

**Document indexing:** 10 chunks across 5 documents, all 1,536 dimensions,
matching the database column.

**Cost of the verification round:** about $0.70, of which two workflow sessions
were measured exactly at $0.0595 and $0.1237. The rest is an estimate from
observed per-run token counts.

## Out of scope for the prototype

These are deliberate, not missing work.

| Not built | Why |
| --- | --- |
| Write-back to any system | A prototype should not be able to commit stock |
| An audit trail | Needs a real identity and a real store |
| Several ingredients or sites per run | Keeps the scenario legible |
| Forecast uncertainty | No forecasting model here |
| Authentication, access control | Local prototype |
| A web interface | A terminal is enough to demonstrate the decision |

## Known limitations

**Synthetic data, by design.** Five recipes, three sites, ten ingredients, five
policy documents. Not connected to any real supply chain. Retrieval over five
short documents is close to trivial, so good results here say little about
scale.

**Single-scenario scope.** One ingredient, one site, one date per run.

**Spare stock is a floor.** It ignores the sending site's own incoming
deliveries, so a site may be able to release more than it is credited with.

**The loop is not closed.** Approval records a decision; nothing is written
back, and there is no audit record of who approved what on which facts.

**The world model is simple.** No safety stock, production cut-offs, warehouse
capacity, partial deliveries or holidays.

**The final paragraph is still model-written.** It is checked against the
verified numbers and withheld when it fails, which contains the risk rather
than removing it. The checks compare quantities, not claims: a paragraph using
only correct numbers but describing a refused option as approved would pass. The
wording about what was approved comes from fixed code for that reason.

**The full version needs local services and your own API credentials.** Not
something you can click and try.
