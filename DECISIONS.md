# Decisions

The choices that shaped this prototype, what I traded away for each, and what
changed once I ran it for real.

The [README](README.md) shows what it does. [PRODUCT_SPEC.md](PRODUCT_SPEC.md)
has the requirements and the verified results. This is the reasoning.

---

## 1. AI investigates; fixed code decides

**The decision.** Claude reads the planner's question, suggests ways to close
the shortage, and writes the covering paragraph. It does not calculate the
shortage, decide whether a quantity is possible, decide whether a policy allows
something, or approve anything.

**Why AI suits the investigation.** The work before a decision is assembly:
pull from four systems, find the three relevant paragraphs across five
documents, notice that one affected recipe is excluded from a substitution. The
inputs are messy, some of the rules are prose, and the useful output is a
shortlist with reasons. That is what language models are good at, and it is the
part that eats a planner's day.

**Why it does not suit the decision.** Committing stock, paying a surcharge, or
changing what a customer receives needs the same answer every time and needs to
be checkable afterwards. A model that is right most of the time is useful for
drafting and unacceptable for arithmetic someone will act on.

**What I gave up.** A single agent call with tools would have been far less
code. It would also mean that when an answer came out wrong I could not tell
whether the database query, the search, the arithmetic, or the model's reading
was at fault. Splitting the workflow into named steps costs lines and buys the
ability to debug by layer. Running it for real settled that: when the final
summary came out malformed, I knew within a minute it was the last step, not
the investigation.

---

## 2. The substitution bug, and why prompting could not fix it

**What happened.** An early version recommended substituting more units of the
alternative ingredient than the site actually had. There was no step between
Claude's suggestions and the planner. Claude produced options with quantities
attached and they went straight through to review as written. Nothing compared
a quantity to the stock, so nothing could catch it.

**Why the recommendation was impossible.** A substitution sized to the full
1,800-unit shortage breaks two rules at once. Berlin holds **600** units of the
alternative, and the policy allows a site to use alternate stock only "up to
the physical on-hand quantity". Separately, one of the three affected recipes —
Creamy Chicken Pasta — is explicitly not approved for it, so even with
unlimited stock the substitution could not cover that recipe's 2,200 units.

**Why it is a good failure to design around.** The recommendation reads as
correct. The alternative genuinely is approved for that ingredient, the
retrieval genuinely found the right document, and a quantity matching the
shortage looks like precision rather than a mistake.

**Why a better prompt was not the answer.** The policy text was already in the
prompt. It said the limit. Claude had it and still produced a larger number. I
could add "never exceed the on-hand quantity" to the instructions and that
would make it rarer. It would not make it stop, and a safety property that
holds most of the time is not a safety property.

**What I built instead.** `validate_mitigation_options`, a step every
suggestion passes through. It holds the stock figures and the policy limits and
it decides the quantity. Claude proposes; the rules decide. A test asserts the
only route into the review step comes from the checker, so the original gap
cannot quietly reopen.

**What it does in controlled testing.** Given an 1,800-unit substitution
suggestion, it approves **600** and gives both reasons: the unapproved recipe,
and the stock on hand. The original 1,800 stays on screen next to the 600. This
is covered by the mocked tests and is what `app.py --demo` shows.

**What happened on the live runs — stated carefully.** Across four complete
real-API runs, Claude proposed four options and **all four passed unchanged**.
It sized the substitution to the 600 units that exist and excluded the
unapproved recipe by itself. So on those runs the checker **confirmed** Claude's
numbers. It did not correct them, and I am not going to claim it did. The live
runs are evidence that the copilot produces a sound plan on this scenario. The
evidence that the guardrail works is the forced-failure tests, which is exactly
why they exist.

**What it costs.** Every action type needs a rule written for it. Adding a
fourth kind of mitigation means code, not a prompt change. That cost is the
feature: nobody can add a new way to spend money without someone deciding its
limits.

**How it fails.** Closed. An action type with no rule is credited zero units
and left visible for the planner. Passing unknown actions through as valid
would make the checker useless exactly when something unexpected happened.

---

## 3. Policy limits are settings, not retrieved text

**The decision.** The 1,000-unit transfer cap, the 1,200-unit weekly expedite
allowance, the 48-hour notice, the 8% surcharge, and the list of approved
recipes live in `src/business_rules.py`. The documents are still retrieved and
still shown to the planner.

**Why not read the limits from the documents at runtime.** That was my first
instinct and it is the more impressive-sounding design: the system reads the
policy and enforces what it reads. In practice it means a number that gates
spending is produced by parsing prose with a language model. "Up to 1,200
additional units per calendar week" is easy to read correctly and also easy to
read as 1,200 per day, or to apply to the wrong supplier, or to miss the word
"additional". I would be moving the failure I had just fixed one layer down and
making it harder to see.

**So why retrieve the documents at all.** Because the planner needs the rule in
its own words. "Reduced to 1,200 units" is an instruction. The contract clause
explaining the surcharge, the notice period and the written-confirmation
requirement is context for a decision. Retrieval serves the human; the settings
serve the checker.

**The cost, and what I did about it.** Documents and settings can drift apart,
and then the copilot enforces a rule nobody agreed. 17 tests check that each
enforced number still appears in the document it claims to come from, and that
the substitution policy still excludes the recipe the code excludes. It is a
text search, so it catches a changed number — the realistic failure — and would
not catch a rewritten sentence that kept the same figure.

**What production would need instead.** Limits in a table with an owner, an
effective date and a change history. Settings in a source file are right for a
prototype and wrong for a business where contracts get renegotiated.

---

## 4. Every action needs approval, with no exceptions tier

**The decision.** All four action types require human approval. The workflow
stops at a real LangGraph interrupt and does not resume until a decision comes
back.

**The alternative I considered.** Auto-approving small, cheap, reversible
actions — a 50-unit transfer between two sites that both have plenty — and
reserving the human for anything expensive. That is the usual shape for this
kind of tool and it would make the copilot more useful.

**Why I did not.** Every action here has an external counterparty. A transfer
needs the sending site to agree. An expedite needs the supplier's written
confirmation. A substitution changes what arrives in someone's box. None of
them is really the copilot's to take, however small, and "reversible" is doing a
lot of work when the stock is already on a truck.

The practical reason matters too: a threshold is a product decision that needs
an owner. I would be inventing the number separating "safe to automate" from
"needs a person", with no operator to agree it and no incident history to
calibrate against. A made-up threshold would look like a feature and function
as an unexamined risk.

**What would change my mind.** An operator willing to own the threshold, and a
few months of logged decisions showing which recommendations humans approve
without changing. Then the threshold is derived rather than guessed.

---

## 5. Approve, reject, or approve part of it

**The decision.** The planner can approve everything, approve a subset, or
reject the lot. Partial approval is not an afterthought — it is the realistic
case.

**Why.** The four options total exactly the 1,800-unit shortage, which makes
"approve everything" look like the obvious answer. It usually is not. A planner
might take the Hamburg transfer and the substitution and leave the supplier
request alone, because it costs 8% more and is not confirmed until NordFarm
agrees in writing. The tool should not make that choice feel like a deviation.

**What it produces.** Approving the Hamburg transfer and the substitution
covers 1,500 units, and the summary says plainly that 300 are still open and
which unchosen options would cover them. Rejecting everything leaves the full
1,800 standing and says so.

**One detail I had to decide.** What happens if a planner approves an option
the checker refused. I allow it and credit zero units, and tell them at the
time that it adds nothing. Blocking it would imply the checker outranks the
human; crediting it would let approval conjure stock. Recording the decision
honestly and showing the arithmetic is the only option that is true to both.

**What I got wrong here.** My first version had three decision states and
rendered anything that was not "pending" or "rejected" as an approval. A
planner who quit without deciding got a summary reading "The planner approved:"
followed by an empty list. Found by running the terminal app with deliberately
unclear input. There are now explicit states for stopping without deciding and
for approving nothing.

---

## 6. The summary is built from checked data, not written by the model

**The decision.** `src/summary.py` assembles the final summary from verified
fields — the shortage from the arithmetic, the quantities from the checker, the
decision from what the planner chose. Claude is then asked for a two-or-three
sentence covering paragraph, and every quantity in that paragraph is checked
against those same figures. If it states a number that is not one of them, the
paragraph is dropped and the checked summary is shown on its own.

**Why.** This was the last place a wrong number could reach a planner. Every
other step was guarded, and then the final text — the part people actually
read — was free prose generated from a context window. Instructing the model to
use the approved figures is not enforcement.

**Why keep a model-written paragraph at all.** Because a table of numbers is not
a summary. The paragraph says which recipes are at risk and what the planner
just chose, in a sentence someone can forward. Dropping the model entirely
would cost that, and the fact check makes keeping it safe enough.

**Where this is still imperfect, and I want to be clear about it.** The check
compares numbers, not claims. A paragraph using only correct figures but
describing a refused option as approved would pass. That is why the wording
about what was approved comes from fixed code and not from the model. The fact
check contains the risk; it does not remove it.

**What it caught in testing.** A paragraph claiming "I have placed the order"
is withheld — there is an explicit check for claims that an action was taken,
because that is the most damaging thing the model could say in a tool whose
whole point is that nothing has happened yet.

---

## 7. Exclude late deliveries rather than counting and footnoting them

**The decision.** The availability figure counts only purchase orders that are
confirmed *and* arrive on or before the date the stock is needed. A delivery
landing the next morning is excluded and listed separately with the reason.

**Why it matters more than it sounds.** There are two confirmed orders for this
ingredient: 1,700 units arriving the day before, and 1,400 arriving the day
after. Counting both gives a 400-unit shortage. Counting the one that arrives in
time gives 1,800. Those two numbers lead to completely different decisions —
400 units is covered by Hamburg's spare stock alone, while 1,800 cannot be
closed without going to the supplier.

**The alternative.** Include all confirmed inbound and note the timing. A lot of
inventory reporting works that way, and it means the headline number is wrong
in a way that is technically disclosed. The planner then has to do the date
arithmetic themselves, which is the error-prone assembly work the copilot
exists to remove.

I put the filter in the database query rather than in the Python, so the rows
reaching the calculation are already the right rows and no later step can
forget to apply it.

---

## 8. Timing is a feasibility check, not a footnote

**The decision.** An option is refused if it cannot physically arrive before the
stock is needed. A transfer between sites takes 24 hours; an expedite needs 48
hours notice.

**Why I added it.** This was the gap I found when I reviewed my own checker
against what the policies actually say. Every quantity was checked and nothing
checked the clock. Stock that exists but lands after the production run is
worth nothing, so a 1,200-unit expedite with 24 hours to go is not a mitigation
however much the supplier has available.

**What it produces.** At 24 hours' notice the Hamburg transfer is still allowed
and the expedite is refused. That is the correct recommendation and the earlier
version would have offered both.

**The judgement call.** What to do when the window is unknown. I report it as
unchecked and flag it, rather than assuming there is enough time. Assuming would
be inventing a fact, which is the thing this whole layer exists to prevent. The
option still appears with its quantity; the planner is told the timing was not
verified.

---

## 9. A site's spare stock is net of its own demand

**The decision.** Spare stock at another site is what it holds minus its own
forecast for the same date, floored at zero.

**Why.** The naive version — look for sites with stock — produces
recommendations that solve one site's shortage by creating another's. Hamburg
holds 3,300 units, which looks like plenty against an 1,800-unit shortage, but
it needs 2,400 for its own production. Its real spare stock is 900. This also
matches what the transfer policy requires: a site may only send stock if it
keeps enough for its own confirmed forecast.

**The known flaw.** The calculation ignores the sending site's own incoming
deliveries, so the figure is a floor rather than an estimate — a site may be
able to release more than it is credited with. I left it because being
conservative about other people's stock is the safer direction, and fixing it
properly means modelling each site's full position rather than one ingredient's.
It is still wrong, and it is in the limitations.

---

## 10. Make it runnable with nothing installed

**The decision.** `src/scenario.py` reads the same data files and produces rows
in the same shape the database queries return, so the arithmetic and the checks
run — and are tested — with no database, no keys and no dependencies.

**Why.** The full workflow needs Docker, PostgreSQL with pgvector, and two API
keys. That is a reasonable stack and a terrible front door. Anyone opening this
repository to judge whether I can build something would have to set all of that
up before seeing anything work, and most people will not.

**What it buys.** `python3 app.py --demo` runs the whole investigation and the
before-and-after of the checks. 224 of the 310 tests run on a bare Python. The
claims in the README are reproducible in about ten seconds.

**What it costs.** Two code paths load rows. They share the arithmetic and the
checks — which is where a bug would actually hurt — but the loading logic is
duplicated and the two could disagree. A reproducible prototype with one
duplicated layer beats a cleaner one nobody can run. I would not make that
trade in production, where there would be one data source.

**The alternative I rejected.** Mocking the database in tests. That tests the
arithmetic against my assumptions about what the database returns, which is a
weaker thing to know, and it gives nobody a way to actually run the project.

---

## 11. Make the model produce typed actions

**The decision.** Each option comes back with an action type from a fixed set, a
quantity, and for a transfer the sending site — not just a sentence.

**Why.** This is what makes checking possible at all. "Transfer some chicken
from Hamburg" cannot be compared to anything. `transfer`, `HAM`, `900` can be
compared to Hamburg's spare stock and the transfer cap. The schema is not there
to tidy the output; it is there so fixed rules have something to check.

It also means a malformed suggestion fails visibly. An option with no sending
site is refused because it cannot be checked, rather than interpreted
generously.

---

## 12. LangGraph, rather than a script that calls the steps in order

**The problem.** The workflow is a sequence with two awkward properties: it
branches, and it stops. If there is no shortage, the expensive half — three
retrieval calls and the planning model call — should not run at all. And at the
approval step it has to stop completely and continue later, with the facts it
gathered still intact.

**The alternative.** A plain Python script calling each step in order. For the
happy path that is simpler, and honestly it would be enough right up until the
pause.

**Why I chose it.** Stopping mid-workflow and resuming from the same place is a
checkpointing problem, not a prompting one. LangGraph's `interrupt` does it
properly: execution halts, the gathered state is held, and passing a decision
back in continues from exactly that point. Writing that by hand means inventing
a state store and a resume protocol, which is the part of the project least
worth hand-rolling.

**The trade-off.** A framework dependency, and its own vocabulary for what is,
on the happy path, a straight line. For a prototype of this size that is a real
cost and I would not pretend otherwise.

**How testing showed it worked.** 13 tests assert the graph's shape rather than
its output — including that exactly one edge leads into the human-review step,
and that the no-shortage branch reaches neither retrieval nor the planning call.
The pause and resume were then exercised against the real API: approve, reject,
and partial approval all resumed correctly and reported the facts from the
original investigation.

---

## 13. Retrieval into the same database, and why retrieval at all

**The problem.** The limits that decide whether a recommendation is possible are
written in prose — a contract clause, a substitution policy. Something has to
get the right paragraphs in front of the model.

**The alternatives.** Three of them. Rely on the model's own knowledge, which
fails immediately: these documents are synthetic and it has never seen them.
Paste all five documents into every prompt, which works today and gets steadily
worse as a policy library grows, in both cost and accuracy. Or run a dedicated
vector service alongside Postgres.

**Why I chose it.** The project already needs PostgreSQL for demand, stock and
purchase orders. pgvector adds similarity search to that same database, so one
connection string and one backup cover the facts and the documents together.
For a prototype that is one moving part instead of two.

**The trade-off, stated plainly.** Five short documents do not need vector
search. Pasting them all into the prompt would answer the six golden questions
just as well. There is also no vector index, so the search is an exact scan
rather than an approximate one — precise and instant at 10 passages, and not
what a large library would use. And there is no reranking step: the query
returns the three nearest passages that pass the filters, whatever their
scores.

I built it anyway because it is the layer a real policy library would need, and
because building it small is how I found out which part of it actually matters.
That turned out not to be the similarity scoring. It was the metadata.

**How testing showed it worked.** All six golden questions pass, three of them
through retrieval, and each reports which document the evidence came from — so
the check is that the right policy was found, not just that the answer looked
right. On the real end-to-end runs, all three retrieval calls returned their
correct document.

---

## 14. The metadata bug that taught me filtering matters more than similarity

**What happened.** The supplier contract's header reads `**Relevant SKUs:**
CHK-250, CHK-250-B`. The metadata parser looked for a label spelled exactly
`SKU`, so it matched nothing and the contract was indexed as covering no
ingredient at all.

That would have been harmless if "covers nothing" meant "answers for nothing".
It meant the opposite. The filter was `sku = %s OR sku IS NULL`, so a document
with no ingredient recorded matched *every* ingredient. A question about salmon
could be answered with the chicken supplier's terms — and the validator would
then enforce NordFarm's weekly cap against salmon without anything looking
wrong.

**This was a controlled finding, not a real failure.** I reproduced it with a
direct SQL query against the indexed data, and the end-to-end runs never
produced a wrong contract, because the supplier filter was covering for it. The
hole was only reachable when `get_supplier_for_sku()` returns nothing, which
happens for an ingredient with no supplier row. Worth separating from the
substitution bug in section 2, which was an actual bad recommendation.

**The fix, and the second bug underneath it.** The parser now reads every SKU
label variant into a list, and the column holds an array, because a contract
covering two ingredients cannot be stored in one text field — storing only the
first would have broken retrieval for the second.

Fixing that exposed a further fail-open. An empty list still meant "applies to
everything", which is correct for a transfer policy and dangerous for a
contract: any supplier contract whose header was written in an unrecognised way
would again answer for every ingredient. Document types that are
ingredient-specific by nature now fail closed — with no ingredients listed they
answer for nothing, which is noticeable, rather than for everything, which is
not.

**The trade-off.** Failing closed means a genuine contract with a malformed
header silently answers nothing until someone notices. I prefer that to the
alternative, because a missing document is visible in a way that a confidently
wrong one is not.

**How testing showed it worked.** 35 parser tests, including one asserting that
every ingredient-scoped document in the project names the ingredients it
covers. 24 retrieval tests run the real query against the real database with
only the embedding call stubbed, so the production SQL is what is being tested.
One of them inserts a supplier contract with no ingredients listed and checks it
answers for nothing, then removes it.

---

## 15. What broke, how I found it, and what changed

The useful part of building this was the failures. Almost all of them surfaced
by running the thing, or by a test failing for the right reason — only one came
from reading the code.

**No checking step at all.** The original gap, covered in section 2. Found
because a recommendation did not match the inventory.

**A hard-coded supplier in the retrieval step.** The node fetching the supplier
contract filtered on `SUP-NORD` as a literal. Correct for chicken; for any
other ingredient it would confidently retrieve the wrong supplier's contract
and then check against the wrong terms. It now looks up which supplier owns the
ingredient. Found by reading the code — which told me the retrieval layer was
the least tested part of the project, and it was. The metadata bug in section
14 was the other half of that same lesson, found later and separately.

**The final summary leaked the model's internal reasoning.** The first real
end-to-end run produced a summary beginning with a long base64 string. Current
Claude models reply with a list of content blocks — a thinking block carrying a
signature, then the text — and reading `.content` directly put the whole
structure into the planner's summary. Found by running it for real; no offline
or mocked test would have caught it, because the stub returned a plain string.
Fixed by extracting the text blocks, with six regression tests.

**Structured output using an unsupported method.** The library's default way of
getting typed results back uses forced tool calling, which current Claude models
reject; it warned it "will raise an OutputParserException if tool calls are not
generated". Working but fragile. Switched to constrained output, which is the
supported route and works with thinking enabled.

**A sampling parameter that would now fail outright.** The model client was
configured with `temperature=0`, which was the right way to ask for consistency
when it was written. Current models reject it with an error. Removed; the typed
calls get their consistency from their schemas.

**The checker mislabelled its own verdicts.** After adding the timing check, an
informational note — "timing not checked" — was enough to flip an option from
"allowed" to "reduced", even though no quantity had changed. Found by a test
failing for the right reason. A verdict now describes what happened to the
quantity or the scope, not whether a note was attached.

**The fact check failed on truthful sentences.** It read "250" out of the
ingredient code `CHK-250` and flagged it as an invented quantity. Found by a
test. The number pattern now ignores digits that are part of an identifier.

**An unedited placeholder counted as a configured key.** The setup check tested
whether the variable was non-empty, so a freshly copied `.env` passed and the
failure surfaced later as a provider authentication error. Found while checking
my own pre-flight output.

**My secrets scanner flagged the real keys in `.env`.** Once real credentials
were configured, the scanner failed — it was finding them where they are
supposed to be. That was the scanner being wrong, not a leak. It now scans the
surface a push would actually publish and separately proves `.env` is ignored. I
confirmed it still catches a planted key in a file git would include.

**An organisation-scoped API key.** The first key tried was created at the
organisation level rather than inside a workspace, so the API rejected any
request that did not name a workspace. That failed before inference, so nothing
was billed. Worth recording because the verification script separates that from
a bad key, which is what made the fix obvious — and the project now passes a
workspace id through when one is configured.

---

## 16. What production would require

Roughly in dependency order. The first two are the difference between a
demonstration and a tool.

1. **Write-back with an audit record.** An approved option should produce a
   draft transfer or purchase request, stored with who approved it, the facts at
   the time, and the checker's reasoning. Without this the planner still does
   the data entry, and there is no record of why a decision was made.

2. **Real evaluation of the search.** The arithmetic and the checks are tested
   properly. Search quality rests on six questions over five short documents.
   With five hundred documents the wrong supplier's contract comes back and the
   checker applies the wrong limits with no sign of trouble.

3. **Policy limits as data with owners and effective dates.** Settings in a
   source file cannot express "this cap changed when we renegotiated in March".

4. **Recommend a combination, not just options.** Each option is checked alone,
   and over-ordering is flagged rather than resolved. The copilot cannot yet
   propose the best mix that closes the gap without overshoot.

5. **Real operational constraints.** Safety stock, production cut-off times,
   warehouse capacity, partial deliveries, holidays. The current world model is
   simple enough that some recommendations would be infeasible for reasons the
   checker cannot see.

6. **Reliability basics.** Retries and fallbacks on the model calls, tracing,
   and stock reservations so two concurrent investigations cannot both plan to
   use the same 600 units.

7. **Several ingredients and sites at once**, where solving one shortage updates
   what is available for the next.
