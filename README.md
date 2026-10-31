# Supply Chain Exception Copilot

A working prototype that helps a supply-chain planner investigate an ingredient
shortage, and checks every recommendation against the inventory and the
operating rules before a person is asked to approve it.

All data in this repository is synthetic. It was written for this project and
is not derived from any company's systems, suppliers, recipes, or forecasts.

## Overview

A meal-kit operation has to get the right ingredients to the right fulfillment
center before production starts. When a shortage shows up, a planner has to
work out how bad it is, what it breaks, and what can be done about it — usually
by opening several systems and two or three policy documents.

This prototype does that investigation in one pass. It pulls the demand
forecast, the recipes, the stock on hand, and the incoming purchase orders;
works out the shortage; finds which recipes are affected; retrieves the
supplier contract and the relevant policies; and asks Claude to propose
mitigation options.

Then it does the part that matters most. Every option Claude proposes is
checked by fixed rules against the actual inventory and the actual policy
limits. Quantities that are not possible get cut back to what is, actions that
are not allowed get rejected, and each one carries the reason and the document
it came from. The planner sees the checked numbers, never the raw proposal, and
the workflow stops and waits for them.

## The Problem

Berlin needs 12,400 units of chicken breast for one day's production. It has
8,900 in the building. A confirmed purchase order will bring 1,700 more in
time, so 10,600 will be available against 12,400 of demand. That is a shortage
of 1,800 units.

Working that out is the easy part, and it is still annoying: the demand lives
in a forecast, the per-box quantities live in the recipes, the stock lives in a
warehouse system, and the purchase orders live somewhere else again. One of the
POs for this ingredient is for 1,400 units and is confirmed, but it arrives the
morning after production. Counting it would make the shortage look like 400
units instead of 1,800.

The hard part is what to do. There are three options and all of them have
limits:

- **Move stock from another site.** Hamburg and Munich both hold chicken, but
  they need most of it for their own production that day. Between them they can
  release 1,100 units, and a single urgent transfer of a chilled ingredient is
  capped at 1,000.
- **Use the approved alternate ingredient.** Berlin holds 600 units of it, and
  it is approved for two of the three affected recipes but not the third.
- **Ask the supplier to expedite.** The contract allows up to 1,200 extra units
  a week, needs 48 hours notice, costs 8% more per unit, and is not committed
  until the supplier confirms in writing.

Each of those numbers is in a different place, and three of them are buried in
prose in a contract or a policy document. A recommendation that ignores any one
of them looks completely reasonable and cannot be executed.

## What I Built

A Python prototype built on LangGraph. I have verified each of these by running
it:

- **Gathers the facts from one query path.** Demand forecast, recipe bill of
  materials, stock on hand, and confirmed purchase orders, from PostgreSQL.
- **Counts only purchase orders that arrive in time.** Confirmed *and* landing
  on or before the date the stock is needed. The late PO is excluded and the
  report says why.
- **Calculates the shortage with fixed arithmetic.** No model is asked to do
  subtraction.
- **Stops early when there is nothing wrong.** If the projection shows enough
  stock, the workflow skips the retrieval and the planning model call entirely.
- **Finds the affected recipes** and how many units each one contributes,
  largest first.
- **Finds genuinely spare stock at the other sites.** A site's spare stock is
  what it holds minus its own forecast for the same date, so the copilot never
  proposes fixing one site by breaking another.
- **Retrieves the relevant policies** — the supplier contract, the substitution
  policy, the transfer policy — using vector search over the documents.
- **Asks Claude for mitigation options** as typed, quantified proposals rather
  than prose.
- **Checks every proposal against the facts and the rules.** This is the
  centre of the project. Each option is approved as proposed, capped to the
  largest feasible quantity, or rejected — with the binding rule and its source
  document named.
- **Reports the shortage that is still open** after everything feasible, and
  flags it for escalation.
- **Stops for a person.** The workflow pauses before any action is treated as
  approved, and shows the planner the validated quantities.

## How It Works

Five kinds of work, kept deliberately separate:

| Kind | Example | Handled by |
| --- | --- | --- |
| Facts | How many units are in Berlin? | PostgreSQL |
| Written rules | Does the contract allow expediting? | Vector search over the documents |
| Arithmetic | What is the shortage? | Fixed Python |
| Judgement | Which combination of options makes sense? | Claude |
| Sequence | What runs next, and what stops? | LangGraph |

The workflow runs in this order. The tag says what kind of work each step is.

```text
understand_question            LLM     turn the question into a date, site, SKU
get_demand                     SQL
get_recipe_requirements        SQL
calculate_required_inventory   RULES   boxes x quantity per box
check_inventory                SQL
check_purchase_orders          SQL     confirmed and arriving in time only
calculate_shortage             RULES   required - on hand - inbound
      |
      +-- no shortage ------> generate_final_response
      |
      +-- shortage
            calculate_downstream_impact  RULES   which recipes are affected
            check_other_fcs              SQL     spare stock elsewhere
            search_supplier_contract     RAG
            search_substitution_policy   RAG
            search_transfer_policy       RAG
            generate_mitigation_options  LLM     proposes typed options
            validate_mitigation_options  RULES   caps, rejects, explains
            human_review                 HUMAN   stops here
            generate_final_response      LLM     writes the summary
```

### What the model is and is not responsible for

Claude does three things: it reads the planner's question and decides what to
look up, it proposes mitigation options, and it writes the final summary.

It does not calculate the shortage, decide whether a quantity is feasible,
decide whether a policy allows an action, or approve anything. Those are fixed
rules in `src/calculations.py` and `src/validation.py`, and neither file imports
a model client — there is a test that checks that.

### Where the rules come from

The policy documents are retrieved and shown to the planner so they can read
each rule in its own words. But the *numbers* that decide feasibility — the
1,000-unit transfer cap, the 1,200-unit weekly expedite allowance, which
recipes the alternate ingredient is approved for — are written as configuration
in `src/business_rules.py`, with each one naming the document it came from.

A limit parsed out of prose by a language model is a limit that can be misread,
and the whole point of the validator is that it cannot be talked out of its
answer. The tradeoff is that the documents and the configuration can drift
apart, so `tests/test_business_rules.py` checks that each number still appears
in the document it claims to come from.

### When information is missing or an option breaks a rule

- A purchase order that arrives too late is excluded from the projection, and
  listed separately with the reason.
- A transfer from a site with nothing spare is rejected, not reduced to zero
  and hidden.
- A proposal with no quantity, a zero quantity, or no named source site is
  rejected rather than guessed at.
- An action type the validator has no rule for is credited **zero** units
  against the shortage. It still appears in the report for the planner to
  assess, but it is never allowed to close part of a shortage on trust.
- If the feasible options do not cover the shortage, the remainder is reported
  and flagged for escalation. The prototype does not pretend a problem is
  solved.

## The Bug That Shaped This Project

During earlier development, the copilot recommended a substitution for more
units of the alternate ingredient than the site actually had. That is the
failure this project is now built around.

**What went wrong.** There was no step between the model's proposals and the
planner. Claude received the facts and the retrieved policy text, produced
mitigation options with quantities attached, and those options went straight
through to the human-review step as written. Nothing compared a proposed
quantity to the stock on hand, so nothing could catch it.

**Why the recommendation was not feasible.** A substitution sized to the
shortage breaks two separate rules at once:

1. **Berlin holds 600 units of the alternate ingredient.** The substitution
   policy allows a site to use alternate inventory "up to the physical on-hand
   quantity". A 1,800-unit substitution is three times what is in the building.
2. **One of the three affected recipes is not approved for it.** Creamy Chicken
   Pasta is explicitly excluded, because the alternate ingredient's preparation
   spec has not been validated for that recipe's sauce process. So even with
   unlimited stock, the substitution cannot cover that recipe's 2,200 units.

What makes it a good failure to design around is that the recommendation reads
as completely correct. The alternate SKU genuinely is approved for that
ingredient, the retrieval genuinely had found the right document, and a
quantity that matches the shortage looks like precision rather than a mistake.

The problem was not really that a model got a number wrong. It was that a wrong
number had an unobstructed path to a planner. The retrieved policy text was
right there in the prompt and had been read; the quantity still did not follow
from it. A firmer instruction in the prompt would make that rarer without ever
making it stop, and "rarer" is not a safety property.

**How it is caught now.** I confirmed while reviewing this code that the
workflow had no validation step — proposals went from
`generate_mitigation_options` straight to `human_review` — and added one:
`validate_mitigation_options`, a deterministic node every proposal has to pass
through. It holds the inventory figures and the policy limits and it decides
the quantity. The model proposes; the rules decide. There is a test asserting
that the only edge into `human_review` comes from the validator, so the path
that caused this cannot quietly reappear.

**What the corrected behaviour looks like.** Feeding the validator a 1,800-unit
substitution proposal against this scenario — which is what
`scripts/run_scenario_offline.py` does, so you can reproduce it:

```text
[CAPPED] Substitute CHK-250-B for the full remaining shortage
  proposed 1,800 units  ->  approved 600 units
  - CHK-250-B is not approved for RCP-PASTA; only the approved recipes are
    counted (substitution_policy.md).
  - Reduced to 600 units. BER only holds that many CHK-250-B, and a site may
    only substitute up to its physical on-hand quantity (substitution_policy.md,
    quantity constraint).
```

The original 1,800 is still shown next to the approved 600. A planner needs to
see that the copilot wanted to move three times what exists, because that tells
them something about how much to trust the next recommendation.

## Worked Example

Chicken breast (`CHK-250`) at Berlin for 2026-09-29. Run it yourself with
`python3 -m scripts.run_scenario_offline`.

**The shortage**

| | Units |
| --- | ---: |
| Required by forecast demand | 12,400 |
| On hand | 8,900 |
| Confirmed inbound arriving in time | 1,700 |
| Projected available | 10,600 |
| **Projected shortage** | **1,800** |

One purchase order is left out: PO-882, 1,400 units, confirmed, arriving
2026-09-30 — the day after it is needed.

**What it affects**

| Recipe | Forecast boxes | Units needed |
| --- | ---: | ---: |
| Chicken Teriyaki | 6,200 | 6,200 |
| Tuscan Chicken | 4,000 | 4,000 |
| Creamy Chicken Pasta | 2,200 | 2,200 |

**What is available to fix it**

| Source | Units | Limit that applies |
| --- | ---: | --- |
| Hamburg spare stock | 900 | Holds 3,300, needs 2,400 for itself |
| Munich spare stock | 200 | Holds 2,000, needs 1,800 for itself |
| Alternate ingredient in Berlin | 600 | Not approved for Creamy Chicken Pasta |
| Supplier expedite | 1,200 | Per calendar week, 48h notice, 8% surcharge |

Internal stock alone comes to 1,700 against a 1,800 shortage, so this scenario
cannot be solved without going to the supplier. That is deliberate: a scenario
where moving stock around is enough would never exercise the contract limits.

**What the validator does with four proposals**

| Proposed | Verdict | Approved | Why |
| --- | --- | ---: | --- |
| 900 from Hamburg | ok | 900 | Within Hamburg's spare stock |
| 500 from Munich | capped | 200 | Munich only has 200 spare |
| 1,800 as substitute | capped | 600 | Only 600 on hand; one recipe not approved |
| 1,500 expedited | capped | 1,200 | Weekly contract allowance is 1,200 |

Approved total 2,900 against a 1,800 shortage, so the report also flags that
taking all four would over-procure by 1,100 units. The planner picks a
combination; the copilot does not pick for them.

## Testing and Results

```bash
python3 -m unittest discover tests
```

105 tests across 21 classes and 5 files. 95 of them run on the standard
library with nothing installed; the 10 that check the compiled LangGraph
workflow skip automatically if LangGraph is not present.

| File | Tests | What it covers |
| --- | ---: | --- |
| `test_validation.py` | 34 | Caps, rejections, shared budgets, malformed proposals |
| `test_scenario.py` | 25 | The headline numbers, against the actual CSV data |
| `test_business_rules.py` | 17 | Each limit still matches its policy document |
| `test_calculations.py` | 16 | The arithmetic, on small hand-built inputs |
| `test_graph_structure.py` | 13 | The wiring, including that nothing skips the validator |

The tests that matter most are the ones that encode the bug above:

- A 1,800-unit substitution proposal is capped to 600, the original 1,800 is
  still reported, and the remaining 1,200 shortage is reported rather than
  hidden.
- A proposal naming only the unapproved recipe is rejected outright.
- Two proposals cannot both claim the same 600 units, the same spare stock at
  one site, or the same weekly expedite allowance.
- An unrecognised action type is credited zero units.
- The validator never increases a proposed quantity.
- Only one edge leads into `human_review`, and it comes from the validator.

**What is verified and what is not.** The arithmetic, the constraint validation
and the graph wiring are covered by the tests above and by the offline runner,
and both run on the synthetic data in this repository. The two model calls and
the vector retrieval are **not** covered by automated tests — they need a
database and two API keys, and I have not built an evaluation harness for
retrieval quality or for the model's proposals. `evals/golden_questions.json`
holds six known-answer questions; the three that can be answered from the
structured data are asserted in `test_scenario.py`, and the three that depend
on retrieval are not yet automated.

These results describe synthetic data. The scenario was designed to contain
real conflicts — a late purchase order, spare stock that is not quite enough,
a substitute that is short and partly unapproved — because a scenario without
them would let any recommendation look correct.

## Technology

| | |
| --- | --- |
| Python 3 | The arithmetic, the validator, the tests |
| LangGraph | Workflow control: nodes, conditional routing, state, and the human interrupt |
| Claude | Parsing the question, proposing options, writing the summary |
| PostgreSQL | Demand, recipes, inventory, purchase orders |
| pgvector | Policy document embeddings, in the same database |
| OpenAI embeddings | Turning documents and queries into vectors for retrieval |

Two model providers because they do different jobs: Claude for reasoning and
writing, an embedding model for retrieval. pgvector rather than a separate
vector service because the project already needs PostgreSQL for the
operational data, so adding an extension beat adding a dependency.

The model is set in `.env` (`CLAUDE_MODEL`), defaulting to `claude-opus-5-5`.
No sampling temperature is set — current Claude models reject that parameter,
and the two structured model calls get their consistency from output schemas.

## How to Run

**The deterministic workflow, with nothing installed.** This is the quickest
way to see what the prototype does. No database, no API keys, no dependencies.

```bash
python3 -m scripts.run_scenario_offline
```

It prints the shortage projection, the affected recipes, the spare stock
elsewhere, and then four proposed options before and after validation.

**The tests**, also with nothing installed:

```bash
python3 -m unittest discover tests
```

**The full workflow**, which needs Docker and two API keys. Set up the
environment:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

Copy the template and add your keys:

```bash
cp .env.example .env
```

Start PostgreSQL with pgvector:

```bash
docker compose up -d
```

Create the schema:

```bash
psql postgresql://postgres:postgres@localhost:5432/supply_chain -f sql/schema.sql
```

Load the synthetic data:

```bash
python -m scripts.load_structured_data
```

Embed the policy documents (this calls the embedding API):

```bash
python -m scripts.ingest_rag_docs
```

Run the workflow:

```bash
python -m src.graph
```

It will pause at `human_review` and print the state. Resuming it takes a
LangGraph `Command(resume=...)`, which this prototype does not wrap in an
interface — see the limitations below.

## Limitations and Next Steps

This is a prototype built to demonstrate a workflow and a safety boundary, not
a production system.

**What it does not do.** There is no interface: the human-approval step pauses
the workflow correctly, but resuming it means writing Python, so there is no
screen on which a planner actually approves anything. Nothing is written back
to any system, and approving an option does not create a transfer or a purchase
order — by design, but it means the loop is not closed. The scenario is one SKU
at one site on one date; it does not look at a whole network or a date range.
The forecast is taken as a fixed number with no uncertainty. A site's spare
stock ignores that site's own incoming purchase orders, which makes the figure
a floor rather than an estimate. There is no authentication and no audit trail.

**What the testing does not cover.** Retrieval quality and the model's
proposals are unmeasured, as described above. The offline runner and the
PostgreSQL path read the same CSV data through two different code paths that
share the arithmetic but not the row loading, so a bug in one loader would not
be caught by the other.

**What I would do first**, in order:

1. **An approval screen.** The human-in-the-loop boundary is the most
   important thing in the project and the least usable part of it. Until a
   planner can see the validated options and approve one, the rest is a
   demonstration.
2. **An evaluation harness for retrieval and proposals.** The six golden
   questions exist; three are automated. The ones that need retrieval are the
   ones most likely to break quietly, especially if more documents are added
   and the wrong supplier's contract starts coming back.
3. **Write-back with an audit record.** An approved option should create a
   draft transfer or purchase-order request, recorded with who approved it, the
   facts at the time, and the validator's reasoning.
4. **Validate combinations, not just single options.** Each option is checked
   on its own and the report flags over-procurement, but the copilot cannot yet
   recommend a specific combination that covers the shortage without overshoot.
5. **Multi-SKU and multi-date.** A real planner is looking at a week and a
   list, not one ingredient on one day.

## Project Files

| File | What's in it |
| --- | --- |
| [README.md](README.md) | This page. What it does, the example, the results, how to run it. |
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | The user, the requirements, the success criteria, the edge cases, and what is actually built. |
| [DECISIONS.md](DECISIONS.md) | The decisions that shaped it, the alternatives, and what changed after testing. |
| [src/calculations.py](src/calculations.py) | The arithmetic. No model, no database. |
| [src/validation.py](src/validation.py) | The constraint validator. The centre of the project. |
| [src/business_rules.py](src/business_rules.py) | The operating limits, each naming its source document. |
| [src/nodes.py](src/nodes.py) | One function per workflow step, tagged by the kind of work it does. |
| [src/graph.py](src/graph.py) | The wiring and the routing. |
| [src/scenario.py](src/scenario.py) | Reads the CSVs so the deterministic path runs without a database. |
| [src/state.py](src/state.py) | What gets passed from step to step. |
| [src/tools/](src/tools/) | The SQL queries and the vector search. |
| [data/structured/](data/structured/) | Synthetic demand, recipes, inventory, purchase orders. |
| [data/rag_docs/](data/rag_docs/) | Synthetic supplier contract and operating policies. |
| [scripts/](scripts/) | The offline runner, the data loader, the document indexer. |
| [tests/](tests/) | 105 tests. |
| [evals/golden_questions.json](evals/golden_questions.json) | Six known-answer questions; three automated so far. |
| [sql/schema.sql](sql/schema.sql) | PostgreSQL schema, including the vector table. |
