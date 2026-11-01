# Supply Chain Exception Copilot

A working prototype that helps a supply chain planner investigate an ingredient
shortage. It gathers the facts, asks Claude for ways to fix the shortage, then
checks every suggestion against the stock that actually exists and the rules in
the company's own policies — before a person decides what to do.

All data here is synthetic. It was written for this project and is not taken
from any company's systems, suppliers, recipes, or forecasts.

## The problem

A meal-kit company packs recipe boxes at several sites. Each site needs the
right ingredients in the building before production starts. When something is
going to run short, a planner has to answer three questions quickly: how bad is
it, what does it break, and what can we do about it.

Answering that means opening several systems. The demand sits in a forecast.
How much of each ingredient a recipe uses sits in the recipe data. Stock sits
in a warehouse system. Incoming deliveries sit with purchase orders. And the
rules about what you are allowed to do — whether you can move stock between
sites, whether a substitute is approved, how much extra the supplier will
send — are written in prose, in documents.

That last part is what makes it hard. Every option has a limit, and the limit
is never stored next to the option. A plan that ignores one of those limits
looks completely sensible and cannot actually be carried out.

## What I built

A command-line prototype that does the whole investigation in one pass.

A planner types a question like *"Why is CHK-250 at risk in Berlin on
2026-09-29 and what can we do about it?"* The copilot then:

- works out which ingredient, which site, and which date the question is about
- pulls the demand, the recipe quantities, the stock on hand, and the incoming
  deliveries
- calculates the shortage, counting only deliveries that arrive in time
- finds which recipes are affected and by how much
- finds spare stock at the other sites, after each of those sites covers its
  own production
- looks up the supplier contract and the relevant policies
- asks Claude for ways to close the gap
- **checks each suggestion against the real stock and the real rules**
- stops and waits for the planner to approve or reject

**Why AI is worth using here.** The hard part is not the arithmetic — it is
reading a vague question, knowing which four systems to pull from, and finding
the three relevant paragraphs buried in five policy documents. That reading and
assembling is what a language model is genuinely good at, and it is the part
that eats a planner's afternoon.

**And why it is not left to decide.** The same model that reads well will also,
now and then, suggest moving 1,800 units when 600 exist. So the checking step
is the heart of this project: Claude suggests, and fixed Python code decides
what is actually possible. A suggestion can be allowed as written, reduced to
the largest number that works, or refused outright — and whichever happens, the
planner is told why and which document the rule came from.

Nothing is ever carried out. Approving an option records a decision. It does
not move stock, place an order, or contact a supplier.

## How it works

Five different jobs, kept deliberately separate:

**Claude** reads the planner's question and works out what to look up. Later it
suggests ways to fix the shortage, and writes a short covering paragraph at the
end. It is used for reading and judgement, nothing else.

**PostgreSQL** holds the facts: demand, recipes, stock, purchase orders. Asking
a database how many units are in Berlin gives the same answer every time.

**pgvector** is a PostgreSQL add-on that lets the project search the policy
documents by meaning rather than by exact words. Searching for "emergency
chicken delivery" finds the contract clause about "expedited supply with 48
hours notice", even though they share no words. The documents are turned into
lists of numbers once, up front, and searched by how close those numbers are.

**Python** does the arithmetic and the rule checking. The shortage calculation
and every limit check are ordinary code. No model is asked to subtract numbers
or to decide whether 1,800 is more than 600.

**LangGraph** controls the order. It runs each step in turn, skips the
expensive half when there is no shortage, and — importantly — can pause the
whole workflow at the approval step and pick it up again later from exactly
where it stopped.

### What Claude is not allowed to do

Claude never calculates the shortage, never decides whether a quantity is
possible, never decides whether a policy allows something, and never approves
anything. Those live in `src/calculations.py` and `src/validation.py`, and
neither file imports a model client. There are tests that fail if that ever
changes.

## The example scenario

Chicken breast (`CHK-250`) at the Berlin site, for 2026-09-29. These figures
come from the real run against the database, not from a description.

| | Units |
| --- | ---: |
| Demand needs | 12,400 |
| In the building | 8,900 |
| Arriving in time | 1,700 |
| So available | 10,600 |
| **Short by** | **1,800** |

**One delivery is deliberately not counted.** PO-882 brings 1,400 units, is
confirmed, and arrives on 2026-09-30 — the morning after production. Counting
it would make this look like a 400-unit problem instead of an 1,800-unit one,
and those two numbers lead to completely different decisions. 400 units is
covered by spare stock at one other site. 1,800 cannot be solved without going
to the supplier.

Three recipes need this ingredient:

| Recipe | Units needed |
| --- | ---: |
| Chicken Teriyaki | 6,200 |
| Tuscan Chicken | 4,000 |
| Creamy Chicken Pasta | 2,200 |

And here is what is available to fix it, with the limit that applies to each:

| Source | Units | The catch |
| --- | ---: | --- |
| Spare stock in Hamburg | 900 | Holds 3,300 but needs 2,400 for its own production |
| Spare stock in Munich | 200 | Holds 2,000 but needs 1,800 for its own production |
| Approved alternative in Berlin | 600 | Not approved for Creamy Chicken Pasta |
| Supplier expedite | 1,200 | Per week, 48 hours notice, costs 8% more, needs written confirmation |

Spare stock plus the alternative comes to 1,700 against an 1,800 shortage. The
scenario was built that way on purpose: if moving stock around were enough, the
contract limits would never be tested.

## The recommendations it produced

On the real runs, Claude proposed four options. All four passed the checks:

| Option | Units |
| --- | ---: |
| Draft an urgent transfer of 900 units from Hamburg | 900 |
| Draft an urgent transfer of 200 units from Munich | 200 |
| Use the 600 units of the alternative, in Chicken Teriyaki and Tuscan Chicken only | 600 |
| Request 100 units expedited from the supplier | 100 |
| **Total** | **1,800** |

Two things worth noticing. Claude sized the substitution to the 600 units that
actually exist, and it excluded Creamy Chicken Pasta by itself — the recipe
that is not approved for the alternative. And the expedite option still came
with its warning attached: 8% surcharge, 48 hours notice, not committed until
the supplier confirms in writing.

So on these runs the checking step **confirmed** Claude's numbers rather than
correcting them. That is a good outcome and it is not evidence the checks work.
The evidence for that is in the tests, where a deliberately wrong suggestion is
forced through, and in the demo mode below, where an 1,800-unit substitution
gets cut to 600. See [DECISIONS.md](DECISIONS.md) for why that distinction
matters.

## What gets checked

Every suggestion is checked against six things:

**Is the stock there?** A transfer cannot exceed what the sending site can
spare after covering its own production. A substitution cannot exceed the units
physically in the building.

**Is the substitute approved for these recipes?** The alternative ingredient is
approved for two of the three affected recipes and explicitly not for the
third. A suggestion naming only the unapproved recipe is refused. One naming
all three is reduced, and the planner is told which recipe was dropped.

**Does it breach a supplier or policy limit?** A single urgent transfer of a
chilled ingredient is capped at 1,000 units. The supplier will send at most
1,200 extra units a week.

**Can it arrive in time?** A transfer between sites takes 24 hours. An expedite
needs 48 hours notice. With one day to go, moving stock still works and asking
the supplier does not — so the expedite is refused rather than offered.

**Are two suggestions quietly using the same stock?** Two transfers from the
same site cannot both claim its spare units. Two substitutions cannot both
claim the same 600. Two expedites share one weekly allowance.

**Does it actually close the gap?** The report states how much is still
uncovered after everything feasible, and says so plainly when the answer is
"not all of it".

Anything the checker has no rule for is credited **zero** units. It still
appears for the planner to read, but it can never close part of a shortage on
trust.

### Where the limits come from

The policy documents are retrieved and shown to the planner so they can read
each rule in its own words. But the numbers that decide feasibility — the
1,000-unit transfer cap, the 1,200-unit weekly allowance, which recipes the
alternative is approved for — are written as plain settings in
`src/business_rules.py`, each one naming the document it came from.

A limit read out of prose by a language model is a limit that can be misread.
The trade-off is that the settings and the documents can drift apart, so there
are tests that check each number still appears in the document it claims to
come from.

## Human approval

The workflow stops before anything is treated as approved, using LangGraph's
pause-and-resume. The planner sees the checked numbers — never Claude's
original suggestion on its own — along with the reason for every reduction and
refusal, and how much of the shortage would still be open.

They can then approve everything, approve some of it, or reject the lot. All
three work, and all three were run against the real APIs:

| Decision | Covered | Still short |
| --- | ---: | ---: |
| Approve all four | 1,800 | 0 |
| Approve the Hamburg transfer and the substitution | 1,500 | 300 |
| Reject everything | 0 | 1,800 |

Partial approval is the interesting one. Approving two of the four options
covers 1,500 units and the summary says plainly that 300 are still open, and
which of the unchosen options would cover them.

Approving an option that was refused is allowed, and it adds nothing. The
planner is told so at the time, because approval is a decision and not a way to
create stock.

**This is a recorded decision and nothing more.** No transfer, purchase order,
or supplier contact happens. Every screen says so.

## Testing

```bash
python3 -m unittest discover tests
```

310 tests, all passing. They are deliberately in layers, because "the tests
pass" means different things depending on what was actually exercised.

| Layer | Tests | What is real |
| --- | ---: | --- |
| Offline unit | 221 | Nothing external. Pure arithmetic and rule checking. |
| Offline, needs LangGraph | 13 | The compiled workflow's shape. |
| Mocked integration | 54 | Real workflow, real database. Claude and the document search are stubbed. |
| Real end-to-end | 22 | Everything real: Claude, embeddings, pgvector, PostgreSQL. |

**224 of the 310 run with nothing installed at all** — no database, no keys, no
dependencies.

The 22 real tests make paid API calls, so they only run when you ask for them:

```bash
RUN_E2E=1 python -m unittest tests.test_end_to_end
```

They took 67 seconds and all 22 passed. They check that Claude reads the
question correctly, that the right documents come back, that the shortage is
1,800, that no approved quantity exceeds what was proposed, that no
substitution exceeds the stock on hand, and that the workflow pauses.

### The six known-answer questions

```bash
python3 -m scripts.run_golden_questions
```

Six questions with answers worked out by hand. All six pass. Three are answered
from the database and need nothing installed. The other three need the document
search, and each reports which document the evidence came from, so you can see
the right policy was found rather than just the right answer:

| Question | Answer | Found in |
| --- | --- | --- |
| Can the alternative go in Creamy Chicken Pasta? | No | `substitution_policy.md` |
| Weekly expedite limit? | 1,200 units | `supplier_nordfarm_contract.md` |
| Does a transfer need approval? | Yes | `warehouse_transfer_policy.md` |

### What the testing does not cover

Retrieval was checked with six specific questions against five small
documents. That is not a broad evaluation of search quality, and good results
on five documents say little about five hundred. There is no scoring of whether
Claude's suggestions are *sensible* — only whether they are *possible*. A
suggestion can be feasible and still unwise, and nothing here catches that.

## How to run it

### The quick way: no setup at all

```bash
python3 app.py --demo
```

No database, no API keys, no dependencies. It runs the real investigation and
the real checks on the synthetic data, using a fixed list of suggestions in
place of Claude's. The third of those suggestions asks to substitute 1,800
units, which is three times what Berlin holds — so this is also the quickest
way to watch the checker reduce a suggestion and explain itself.

### The full prototype

```bash
python3 app.py
```

This needs four things:

1. **PostgreSQL with pgvector.** The included `docker-compose.yml` starts it,
   bound to your own machine only.
2. **An Anthropic API key**, for Claude.
3. **An OpenAI API key**, for turning the documents into searchable numbers.
4. **The Python dependencies**, in `requirements.txt`.

Set it up once:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

Copy the template and add your own keys:

```bash
cp .env.example .env
```

`.env.example` holds placeholders only. You supply your own credentials, and
`.env` is ignored by git so it is never committed. Keys from
[console.anthropic.com](https://console.anthropic.com/settings/keys) and
[platform.openai.com](https://platform.openai.com/api-keys).

Check each key with one tiny request before going further:

```bash
python -m scripts.verify_anthropic_key
```

```bash
python -m scripts.verify_openai_key
```

Start the database:

```bash
docker compose up -d
```

Create the tables:

```bash
psql postgresql://postgres:postgres@localhost:5432/supply_chain -f sql/schema.sql
```

Load the synthetic data:

```bash
python -m scripts.load_structured_data
```

Index the policy documents. This is the one setup step that calls a paid API,
and it costs a fraction of a cent for all five documents:

```bash
python -m scripts.ingest_rag_docs
```

Then run it:

```bash
python3 app.py
```

Pick one of the four sample questions or type your own, watch the
investigation, review the options, and approve or reject. If anything is
missing, the app says exactly what and points you at the demo mode instead of
failing part-way through.

## Limitations

**It is a prototype.** It is not connected to any real supply chain, and all
the data is synthetic.

**Approval records a decision and nothing else.** There is no write-back: an
approved transfer does not become a transfer request anywhere, and no system is
updated. There is also no audit trail — no stored record of who approved what,
on which facts, at what time. A real tool would need both.

**One ingredient, one site, one date per run.** A real planner is looking at a
list across a week. Fixing a shortage at one site would also change what
another site can spare, and the prototype does not model that.

**Spare stock is a floor, not an estimate.** The calculation ignores the
sending site's own incoming deliveries, so a site may be able to release more
than it is credited with. Conservative, but still wrong.

**The forecast is treated as certain.** One number, no range.

**The world model is simple.** No safety stock, no production cut-off times, no
warehouse capacity, no partial deliveries, no public holidays. Some
recommendations would be infeasible for reasons the checker cannot see.

**The full version needs local services and your own API credentials.** It is
not something you can click and try.

**No authentication, and no multi-user safety.** Two people investigating at
once could both plan to use the same 600 units.

## What I would build next

1. **Write-back with an audit record.** An approved option should create a
   draft transfer or purchase request, stored with who approved it, the facts
   at the time, and the checker's reasoning. This is what turns a decision into
   work, and it is the biggest gap.
2. **A real evaluation of search quality.** Six questions over five documents is
   a smoke test. With a realistic number of documents, the wrong supplier's
   contract comes back and the checker dutifully applies the wrong limits.
3. **Recommend a combination, not just options.** Each option is checked on its
   own, and the report flags when approving everything would over-order. It
   cannot yet propose the best mix that covers the gap without overshoot.
4. **A whole week and a whole list**, rather than one ingredient at one site on
   one day.
5. **Policy limits as data with owners and dates**, so a renegotiated contract
   is a record someone changes rather than an edit to a source file.

## Project files

| File | What's in it |
| --- | --- |
| [README.md](README.md) | This page: the problem, what it does, the example, the results, how to run it. |
| [PRODUCT_SPEC.md](PRODUCT_SPEC.md) | The user, the requirements, what is verified and how, and what is deliberately out of scope. |
| [DECISIONS.md](DECISIONS.md) | The decisions behind it, the trade-offs, and what changed after testing. |
| [app.py](app.py) | The one command you run. Full mode and demo mode. |
| [src/calculations.py](src/calculations.py) | The arithmetic. No model, no database. |
| [src/validation.py](src/validation.py) | The checker. The heart of the project. |
| [src/business_rules.py](src/business_rules.py) | The limits, each naming its source document. |
| [src/document_metadata.py](src/document_metadata.py) | Reads each policy document's header, so a search knows which ingredients it covers. |
| [src/summary.py](src/summary.py) | Builds the final summary from checked data, and fact-checks Claude's paragraph against it. |
| [src/review_ui.py](src/review_ui.py) | The screens a planner sees and the decision they make. |
| [src/nodes.py](src/nodes.py) | One function per step, labelled by the kind of work it does. |
| [src/graph.py](src/graph.py) | The order the steps run in. |
| [src/scenario.py](src/scenario.py) | Reads the data files so the demo runs without a database. |
| [src/tools/](src/tools/) | The database queries and the document search. |
| [data/structured/](data/structured/) | Synthetic demand, recipes, stock, purchase orders. |
| [data/rag_docs/](data/rag_docs/) | Synthetic supplier contract and operating policies. |
| [scripts/](scripts/) | Setup, the key checks, the known-answer questions, the secrets scanner. |
| [tests/](tests/) | 310 tests in four layers. |
| [sql/schema.sql](sql/schema.sql) | The database tables, including the one holding the searchable documents. |
