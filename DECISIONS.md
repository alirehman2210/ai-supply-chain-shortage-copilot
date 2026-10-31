# Decisions

The choices that shaped this prototype, the alternatives I weighed, and what
changed once I tested it. The README shows what it does;
[PRODUCT_SPEC.md](PRODUCT_SPEC.md) has the requirements. This is the reasoning.

---

## 1. Use AI for the investigation, not for the decision

**The decision.** Claude reads the planner's question, proposes mitigation
options, and writes the summary. It does not calculate the shortage, judge
whether a quantity is feasible, or approve anything.

**Why AI is a good fit for the investigation.** The work before a decision is
mostly assembly: pull from four systems, find the three relevant paragraphs
across five documents, notice that one of the affected recipes is excluded from
a substitution. It is the kind of task where the inputs are messy, the rules
are written in prose, and the useful output is a shortlist with reasons. A
language model is genuinely good at that, and it is the part that eats a
planner's time.

**Why it is a bad fit for the decision.** Committing stock, spending money on a
surcharge, or changing what a customer receives are consequential and need to
be the same answer every time. They also need to be checkable afterwards. A
model that is right 95% of the time is useful for drafting and unacceptable for
arithmetic that someone will act on.

**The alternative I rejected.** One `agent()` call with tools, letting the model
decide which lookups to make and in what order. It would have been less code.
It would also have meant that when an answer came out wrong, I could not tell
whether the SQL was wrong, the retrieval missed, the arithmetic was wrong, or
the model misread correct context. Splitting the workflow into explicit steps
costs lines of code and buys the ability to debug by layer.

---

## 2. Fixed rules validate quantities, and the model cannot overrule them

**The decision.** A deterministic validator sits between the model's proposals
and the planner. It holds the inventory figures and the policy limits, and it
decides the approved quantity for every option.

**What prompted it.** A substitution recommendation for more units of the
alternate ingredient than the site had. The full account is in the README; the
part that matters here is *why prompting could not fix it*.

The retrieved policy text was in the prompt. It said a site may use alternate
inventory up to its physical on-hand quantity. The model had that text and
still produced a larger number. I could have added "never exceed the on-hand
quantity" to the instructions, and that would have reduced how often it
happened. It would not have made it stop, and a safety property that holds most
of the time is not a safety property. A planner cannot use a system where the
answer is right often enough.

**Why this is the right split.** The validator's job is arithmetic and
comparison — is 1,800 larger than 600, is RCP-PASTA in the approved set. That
is exactly the kind of work where code is both cheaper and perfect, and where
a model is expensive and merely very good.

**What it costs.** Every action type the copilot can recommend needs a
validation rule written for it. Adding a fourth kind of mitigation means adding
code, not just changing a prompt. I think that cost is the feature: it means
nobody can add a new way to spend money without someone deciding what its
limits are.

**How it fails.** Closed. An action type with no rule is credited zero units
against the shortage and left in the report for the planner to assess. The
alternative — passing unrecognised actions through as valid — would make the
validator useless exactly when something unexpected happened.

---

## 3. Policy limits are configuration, not retrieved text

**The decision.** The 1,000-unit transfer cap, the 1,200-unit weekly expedite
allowance, the 48-hour notice period, the 8% surcharge, and the list of
recipes the substitute is approved for all live in `src/business_rules.py` as
constants. The policy documents are still retrieved, and still shown to the
planner.

**Why not read the limits from the documents at runtime.** That was my first
instinct, and it is the more impressive-sounding design: the system reads the
policy and enforces what it reads, so updating a document updates behaviour. In
practice it means a number that gates spending is produced by parsing prose
with a language model. "Up to 1,200 additional units per calendar week" is easy
to read correctly and also easy to read as 1,200 per day, or to apply to the
wrong supplier, or to miss the word "additional". I would be moving the failure
I had just fixed one layer down and making it harder to see.

**Why retrieve the documents at all, then.** Because the planner needs the rule
in its own words. "Reduced to 1,200 units" is an instruction. The contract
clause explaining the surcharge, the notice period, and the written-confirmation
requirement is context for a decision. The retrieval serves the human; the
configuration serves the validator.

**The cost, and what I did about it.** The documents and the constants can drift
apart, and if they do the copilot enforces a rule nobody agreed to.
`tests/test_business_rules.py` checks that each enforced number still appears in
the document it claims to come from, and that the substitution policy still
excludes the recipe the code excludes. It is a text search, so it catches a
changed number — the realistic failure — and would not catch a rewritten
sentence that kept the same figure. Each constant also names its source
document in a comment, so the two are updated together.

**What production would need instead.** Policy limits in a table with an owner,
an effective date, and a change history, read by the validator at runtime.
Constants in a source file are right for a prototype and wrong for a business
where a contract gets renegotiated.

---

## 4. Every action needs human approval, with no exceptions tier

**The decision.** All four action types require human approval. The workflow
stops at a real LangGraph interrupt and does not resume until a decision comes
back.

**The alternative I considered.** Auto-approving small, cheap, reversible
actions — a 50-unit transfer between two sites that both have plenty — and
reserving the human for anything expensive. This is the usual shape for
human-in-the-loop systems and it would make the copilot more useful.

**Why I did not.** Two reasons, one principled and one practical.

The principled one: every action here has an external counterparty. A transfer
needs the sending site to agree. An expedite needs the supplier's written
confirmation. A substitution changes what arrives in a customer's box. None of
them is really the copilot's to take, however small, and "reversible" is doing
a lot of work in a physical supply chain where the stock is already on a truck.

The practical one: a threshold is a product decision that needs an owner. I
would be inventing the number that separates "safe to automate" from "needs a
person", with no operator to agree it and no incident history to calibrate it.
A made-up threshold in a portfolio project would look like a feature and
function as an unexamined risk.

**What I would need to change my mind.** An operator willing to own the
threshold, and a few months of logged decisions showing which recommendations
humans approve without changing. Then the threshold is derived rather than
guessed.

---

## 5. Show the rejected and capped options, not just the feasible ones

**The decision.** An option that was cut from 1,800 units to 600 stays in the
report, showing both numbers and the reason. Rejected options stay too.

**The alternative.** Show the planner only what is feasible. It is a cleaner
report and it is what a confident product would do.

**Why not.** Two things are lost. The first is that the planner needs to know
the shortage is not covered — if the substitution silently became 600 units,
the 1,200-unit gap looks like it was always the plan rather than the
consequence of a constraint. The second is calibration. A planner who sees that
the copilot proposed three times the available stock learns something useful
about how much to trust the next recommendation. Hiding the correction makes
the system look more reliable than it is, which is the opposite of helpful for
a tool whose output someone has to act on.

---

## 6. Exclude purchase orders that arrive late, rather than counting and footnoting them

**The decision.** The availability projection counts only purchase orders that
are confirmed *and* arrive on or before the date the stock is needed. A PO
landing the next morning is excluded, and listed separately with the reason.

**Why this matters more than it sounds.** In the scenario there are two
confirmed POs for the ingredient: 1,700 units arriving the day before, and
1,400 arriving the day after. Counting both gives a 400-unit shortage. Counting
the one that arrives in time gives 1,800. The two answers lead to completely
different decisions — 400 units is covered by Hamburg's spare stock alone,
while 1,800 cannot be covered without going to the supplier.

**The alternative.** Include all confirmed inbound and note the timing. That is
how a lot of inventory reporting works, and it means the headline number is
wrong in a way that is technically disclosed. The planner then has to do the
date arithmetic themselves, which is exactly the error-prone assembly work the
copilot is supposed to remove.

I put the filter in the SQL query rather than in the Python, so the rows that
reach the calculation are already the right rows and there is no later step
that could forget to apply it.

---

## 7. A site's spare stock is net of its own demand

**The decision.** Spare stock at another site is what it holds minus its own
forecast requirement for the same date, floored at zero.

**Why.** The naive version — look for sites with stock — produces
recommendations that solve one site's shortage by creating another's. Hamburg
holds 3,300 units, which looks like plenty against an 1,800-unit shortage, but
it needs 2,400 for its own production that day. Its real spare stock is 900.

This also matches what the transfer policy actually requires: a site may only
send stock if it retains enough for its own confirmed forecast.

**The known flaw.** The calculation ignores the sending site's own incoming
purchase orders, so the figure is a floor rather than an estimate — a site may
be able to release more than it is credited with. I left it that way because
being conservative about other people's stock is the safer direction, and
because fixing it properly means modelling each site's full position rather
than one SKU's. It is still wrong, and it is in the limitations.

---

## 8. Make the deterministic half runnable with nothing installed

**The decision.** `src/scenario.py` reads the same synthetic CSVs and produces
rows in the same shape the SQL tools return, so the arithmetic and the
validator can run — and be tested — with no database, no API keys and no
dependencies.

**What prompted it.** The full workflow needs Docker, PostgreSQL with pgvector,
an Anthropic key and an OpenAI key. That is a reasonable stack and a bad front
door. Anyone looking at this repository to judge whether I can build something
would have to set all of that up before seeing anything work, and most people
will not.

**What it buys.** `python3 -m scripts.run_scenario_offline` prints the whole
shortage investigation and the before-and-after of validation. 95 of the 105
tests run on the standard library. The claims in the README are reproducible by
anyone in about ten seconds.

**What it costs.** Two code paths load rows: the SQL queries and the CSV
loader. They share the arithmetic and the validator, which is where a bug would
actually hurt, but the loading logic is duplicated and the two could disagree.
I decided a reproducible prototype with one duplicated layer beats a
theoretically cleaner one that nobody can run. I would not make that tradeoff
in production, where there would be one data source.

**The alternative I rejected.** Mocking the database in tests. That tests the
arithmetic against my assumptions about what the database returns, which is a
weaker thing to know, and it does not give anyone a way to actually run the
project.

---

## 9. Require the model to produce typed actions, not prose

**The decision.** The model returns each option with an `action_type` from a
fixed set, a quantity, and for a transfer the sending site — not just a
sentence.

**Why.** This is what makes validation possible at all. "Transfer some chicken
from Hamburg" cannot be checked against anything. `action_type: transfer`,
`source_fc: HAM`, `estimated_units: 900` can be checked against Hamburg's spare
stock and the transfer cap. The structured schema is not there to make the
output tidy; it is there so that fixed rules have something to compare.

It also means a malformed proposal fails visibly. An option with no source site
is rejected because it cannot be checked, rather than being interpreted
generously.

**Related change.** The prompt tells the model its quantities will be checked
and may be reduced. That is not a safety measure — the validator is the safety
measure — but there is no reason to encourage confident overshoot.

---

## 10. Things I got wrong and changed

**No validation at all.** The original workflow went from
`generate_mitigation_options` straight to `human_review`. This is the main
story of the project and it has its own section in the README. The fix is
`src/validation.py` plus a test asserting that the only edge into
`human_review` comes from the validator, so the gap cannot reopen quietly.

**A hard-coded supplier in the retrieval step.** The node that fetches the
supplier contract filtered on `supplier_id="SUP-NORD"` as a literal. For the
chicken scenario that is correct, and for any other ingredient it would
confidently retrieve the wrong supplier's contract and then validate against
the wrong terms. It now looks up which supplier owns the SKU first. Found by
reading the code rather than by a test, which tells me the retrieval layer is
the least tested part of the project — and it is, which is why an evaluation
harness is the second thing on the list in the README.

**A model parameter that would now fail.** The model client was configured with
`temperature=0`, which was the right way to ask for consistency when this was
written. Current Claude models reject the sampling parameters outright, so that
call would now return a 400. Removed; the two structured calls get their
consistency from output schemas instead. The configured model was also a
generation behind and is now `claude-opus-5-5`, set in `.env`.

**Dependencies the project never used.** `requirements.txt` listed `fastapi`,
`uvicorn`, `streamlit`, `pandas`, `ragas` and `pgvector`. None are imported
anywhere — they were aspirations about where the project might go. One of them,
`ragas`, does not even import successfully against the installed LangChain
version. I trimmed the file to the seven packages the code actually uses, with
a comment on each. A requirements file that lists things you are thinking about
is a requirements file nobody can trust.

**Documentation written for me rather than for a reader.** The repository had a
`LEARNING_GUIDE.md` framing the whole thing as a tutorial exercise, a
`NEXT_STEPS.md` with a phased learning plan, and a `BUILD_VALIDATION.txt`
holding the output of one script. Useful while building, misleading as a
description of what exists. The three documents now in the repository describe
the system; the test suite replaces the validation text file, because a claim
you can re-run beats a claim someone pasted.

---

## 11. What production would require

Roughly in dependency order. The first two are the ones that make the
difference between a demonstration and a tool.

1. **An approval interface.** The human-in-the-loop boundary is the core design
   claim and the least usable part of the build. The workflow pauses correctly;
   resuming it means writing Python. Until a planner can see validated options
   on a screen and approve one, everything else is theatre.

2. **Evaluation for the model-dependent steps.** The arithmetic and the
   validator are tested. Retrieval and proposal quality are not measured at
   all. With five documents that is survivable; with five hundred, the wrong
   supplier's contract comes back and the validator dutifully applies the wrong
   limits. Golden questions exist for this and only half of them are automated.

3. **Write-back with an audit record.** An approved option should produce a
   draft transfer or purchase-order request, stored with who approved it, the
   facts at the time, and the validator's reasoning. Without this the planner
   still does the data entry, and there is no record of why a decision was made.

4. **Policy limits as data with owners and effective dates.** Constants in a
   source file cannot express "this cap changed when we renegotiated in March".

5. **Render the final summary from validated data.** The summary is currently
   written by the model and instructed to use approved quantities rather than
   proposed ones. Nothing enforces it, which makes it the last place a wrong
   number can reach a planner. Fixed code should render the figures; the model
   should write the prose around them.

6. **Real operational constraints.** Safety stock, production cutoff times,
   warehouse capacity, partial deliveries, holidays. The current model of the
   world is simple enough that some recommendations would be infeasible for
   reasons the validator cannot see.

7. **Reliability basics.** Retries and fallbacks on the model calls, tracing,
   and inventory reservations so two concurrent investigations cannot both plan
   to use the same 600 units.

8. **Multi-SKU and multi-site runs**, where fixing one site's shortage updates
   another's spare stock before the next recommendation is made.
