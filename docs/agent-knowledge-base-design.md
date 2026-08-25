# Agent knowledge-base design

How and why `model-docs/` is shaped the way it is. This is the **rationale**
companion to the engine contract in
[`scripts/docgen/documentation_req.md`](../scripts/docgen/documentation_req.md) and the
engine rules in
[`.github/instructions/docgen.instructions.md`](../.github/instructions/docgen.instructions.md).
The original phased plan is preserved in
[`docs/agent-knowledge-base-redesign-plan.md`](agent-knowledge-base-redesign-plan.md).

## The consumer drives the design

The knowledge base is not written for humans browsing a folder tree. It is
written for a **Microsoft 365 declarative (retrieval-augmented) Copilot agent**.
That consumer has three hard constraints:

1. **Single-chunk retrieval.** The agent fetches the few chunks most similar to
   the user's question and answers from their text. It does **not** follow
   Markdown links to gather more context.
2. **No rendering.** A retrieved chunk is plain text to the model. Mermaid
   diagrams, images, and relative file links are inert.
3. **Opaque chunk boundaries.** We do not control exactly where the indexer
   splits a file, but a well-delimited `##` section is the natural unit.

Every design decision below follows from those three facts.

## Decision 1 — cards, not pages

The unit of documentation is a **card**: one anchored `##` section that fully
answers one question. A measure card states what the metric means *and* gives
its DAX *and* traces it to the physical SQL column *and* lists the report pages
it appears on — all in one section. The agent can answer "how is Buying Margin
calculated?" from that single card without retrieving anything else.

The alternative — a normalised doc tree where the measure description, its
lineage, and its report usage live in three different files — fails constraint 1:
the agent would retrieve the description chunk and have no way to pull in the
lineage chunk.

**Trade-off accepted:** cards repeat information (a one-line definition of a
metric appears both in its own card and inline in every report-page card that
shows it). For a RAG corpus, *self-sufficiency beats DRY*. Redundancy is a
feature, not a bug.

## Decision 2 — four flat files

Cards are concatenated into four flat files by audience-shaped theme:

| File | Question it answers |
|---|---|
| `00-overview.md` | "What is this solution, end to end?" |
| `01-model-and-metrics.md` | "What does this metric / table mean and how is it built?" |
| `02-data-pipeline.md` | "Where does the data come from and how is it refreshed?" |
| `03-reports.md` | "What does this report / page show?" |
| `04-source-code.md` *(optional)* | "Show me the actual SQL / Power Query code behind this source." (see Decision 10) |
| `05-power-apps.md` *(optional)* | "What Power App feeds this / where is data entered?" (presence-driven: emitted only when canvas Power Apps are present) |

Flat files (rather than one-file-per-card) keep the corpus easy to diff, index,
and regenerate wholesale, and they let related cards sit adjacent so the indexer
can co-locate them. Four files map cleanly onto the four questions above, which
also helps retrieval: a question about refresh is most similar to chunks in
`02`, a question about a visual to chunks in `03`. The optional fifth file
(Decision 10) isolates raw code so it never dilutes the prose cards; the
optional sixth file isolates canvas Power App metadata on the same
presence-driven basis.

## Decision 3 — registry-free deterministic anchors

Cross-references between cards must be stable and computable from *anywhere*
without a shared lookup table, because the renderers run independently per file.
The anchor for any card is a pure function of its identity:

```
card_anchor(kind, name) = slugify(f"{kind}-{name}")
                          + "-" + sha1(f"{kind}\x00{name}").hexdigest()[:10]
```

- `slugify` gives a human-readable stem but is **lossy** — it strips punctuation,
  so two distinct names can slug-collide.
- The 10-character SHA-1 suffix of the *exact* `(kind, name)` restores
  injectivity: distinct identities get distinct anchors, deterministically, with
  no registry.

So a report-page card can emit a link to a measure card it has never seen,
computed solely from the measure's `(kind, name)`, and it will match that
measure card's own anchor byte-for-byte. Links are navigational only — the
answer is already inline — but when they do resolve they are always correct.
The `Cross-reference integrity` gate proves this holds across the whole corpus.

## Decision 4 — text and tables only, no Mermaid

Lineage and routing are expressed as Markdown **tables**, never diagrams
(constraint 2). A dependency that would be an arrow in a diagram becomes a row
("`factSalesOrders` → fed by dataflow `2.1a Sales Orders TRAS` → from view
`fact_orders_fhbwk`"). Tables survive chunking and are readable by the model;
diagrams are not.

## Decision 5 — the five-role measure classifier

The model has ~2,100 measures. Documenting all of them identically would bury
the ~666 that a business user actually asks about under ~1,100 internal helpers.
A config-driven classifier (`scripts/docgen/dax_refs.py`) assigns each measure a
role, and the role decides the card:

| Role | What it is | How it is documented |
|---|---|---|
| **selector** | field-parameter / slicer driver | referenced only, no card |
| **router** | `SWITCH`/`IF` that dispatches by a selector | **concept card** with a routing table |
| **metric** | headline business measure | **measure card** with full source trace |
| **compute** | intermediate helper | compact **stub card** |
| **base** | atomic aggregation wrapper (`SUM(...)`) | **folded** into its table card |

The classifier is configured per repo by prefix (`selector_prefixes`,
`base_prefixes` in `.docgen.toml`) so the engine stays model-agnostic. The big
win is the **concept card**: when a user asks about "Sales", the router that
switches between *Sales £*, *Sales units*, and *Sales LY* is documented once as
a concept, with a table mapping each selector value to the metric it resolves to
and that metric's one-line definition. That is exactly the mental model a
business user has, surfaced from the `SWITCH` evidence rather than invented.

## Decision 6 — the two-hop source trace

"Where does this number come from?" is answered deterministically, never
guessed, by resolving two *separate* hops:

1. **Model → dataflow entity.** Walk the measure's table back through its
   partition / shared expressions to a Power Query `[entity="X"]` reference.
   `X` is a **dataflow query name**.
2. **Dataflow entity → SQL view.** Walk that dataflow query's M graph to the
   terminal Databricks `[Name="Y", Kind="Table"]`. `Y` is a **view name**
   matching `sql/Y.sql`, and the output column's derivation is read from that
   file.

The subtlety the engine encodes: the model-layer `entity="X"` and the
Databricks-layer `Name="Y"` are **different namespaces**. They sometimes share a
string by naming convention, but the trace treats them as two independent hops
and never assumes `X == Y`. Each metric card reports its trace with an
accounting line ("Source trace: 34/41 columns resolved") so partial coverage is
visible rather than papered over. The `Metric source-trace` gate requires every
metric card to carry this section.

## Decision 7 — evidence only, broken bindings surfaced honestly

Nothing in the corpus is invented. Every fact derives from TMDL, PBIR, dataflow
or orchestration JSON, SQL exports, or the curated narrative in `.docgen.toml`.
Missing evidence renders a `{{PLACEHOLDER}}`, never a guess.

A consequence worth calling out: some PBIR report pages bind to entities that no
longer exist in the model (e.g. `dimChannel`, `ReportingView`). The report-page
card does **not** invent a link for these; it renders them as
`` `name` `` _(unresolved binding)_. The knowledge base therefore tells the
truth about broken bindings instead of hiding them — and the
`Cross-reference integrity` gate stays green because no dangling link is emitted.

## Decision 8 — deterministic, idempotent, gated

- **Idempotent.** All set/dict iteration is sorted; re-running `generate` with
  unchanged inputs produces byte-identical files (verified: `0 changed`). This
  keeps diffs meaningful and review cheap.
- **Wholesale regeneration.** The four files are owned by the engine and swept
  on every run. Three inputs under `model-docs/` are protected:
  `dataflow-references.md`, `.docgen.toml`, `generation-log.md`.
- **Plain-text mirror.** Some agent ingestion pipelines do not yet accept
  `.md` uploads. Each `0*-` knowledge-base file is therefore also written
  verbatim as a `.txt` under `model-docs-txt/` — a byte-for-byte "save as" with
  a different extension. That folder is generated and swept on the same run but
  is deliberately kept outside `model-docs/`, so it never participates in the
  validation gates. When agents accept Markdown directly this mirror can simply
  be dropped.
- **Quality-gated.** `scripts/docgen/validate.py` enforces nine gates
  (files present, unique anchors, link integrity, measure coverage, concept
  routing, metric source-trace, dataflow downstream impact, acronyms, no
  secrets). A red validate run blocks any "documentation done" claim. The
  validator is read-only — it never edits the corpus it checks.

## Decision 9 — chunk-resilient, self-identifying sections

Decision 1 makes each *card* self-sufficient, but the indexer does not chunk on
card boundaries — it can split a single card so that, for example, the
`### Definition (DAX)` section lands in a different chunk from the `## Title`
header. The danger: a bare `### Definition (DAX)` chunk containing only a code
block has **no text naming the measure**, so a search for that measure's name
never retrieves it. The agent then sees the header chunk (which names the card)
but not the definition chunk, and reports the card as "incomplete" or even
"missing". This was observed in practice for `Buying Margin Orders`.

Two structural rules make every chunk of a card independently retrievable by the
card's name, so completeness no longer depends on which chunk the indexer
returns:

1. **Every `### ` section heading is stamped with the card title.** A bare
   `### Definition (DAX)` is rendered as `### Definition (DAX) · Buying Margin
   Orders`. The stamping happens centrally in `cards.render_card`, so it applies
   uniformly to every card kind (measure, concept, table, dataflow, report
   page) and to every section. Headings inside fenced code blocks are left
   untouched, and the heading's leading text is unchanged, so the
   heading-substring validators (`### Source trace`, `### Routing`, `###
   Downstream impact`) still match.
2. **The one-line DAX is front-loaded into the header block.** A measure card's
   header now carries `**Definition (one-line):** <collapsed DAX>` directly
   under the home-table line — so the header chunk (the chunk most reliably
   retrieved by the measure's name) already answers "how is this calculated?"
   even when the full `### Definition (DAX)` section is split into a later chunk.

These two rules are *additive* — they only add identifying text, never remove a
fact — so per-card self-sufficiency (Decision 1) is strengthened, not traded.

An instruction-level **card-completion rule** for the agent ("if a retrieved
chunk shows a `##` card header but the section you need is absent, re-query by
the exact card title before answering") is a useful backstop, but it is
explicitly secondary: it costs a second hop, which the format is designed not to
rely on. The structural rules above remove the need for that hop in the common
case.

## Decision 10 — source-code cards at the view↔entity spine

The four core files answer *what a metric means* and *where a number comes from*
in prose (the Decision 6 two-hop trace). They deliberately do **not** carry the
raw SQL view text or Power Query M — pasting either into a measure or table card
would bloat it past the point where the indexer can retrieve it as one coherent
chunk (Decision 9). But developers genuinely need the literal code ("show me the
SQL / M behind this number"), so it is exposed in a separate, opt-in fifth file
`04-source-code.md`, gated by `[source_code] enabled` in `.docgen.toml`.

The lineage is a **graph, not a chain**: one dataflow file fans out to many
entities, and a model table can fan in from several dataflows. Grouping code
"one card per artefact" fragments a single logical source across three cards;
grouping "one card per full chain" fails because the chain is many-to-many.
The one clean **1:1 spine** is *SQL view ↔ dataflow entity*, so cards are
grained there: one **source-lineage (code)** card per consumed dataflow entity,
co-locating its full Databricks view and its entity M, with links *up* to the
consuming model table(s) and the parent dataflow. Three distinct namespaces are
kept separate and cross-linked — model table (`Calendar`) ≠ entity (`Weekly
Calendar`) ≠ view (`dim_weekly_calendar`).

Keying by **entity** (not view) is deliberate: table cards link down to their
source-lineage cards via `card_anchor("source-lineage", entity)` for each entity
in the trace, which resolves even for M-only entities that have no SQL export.
Exported views with no model consumer still get a SQL-only card so no code is
missing. Secrets are handled by **scrub-and-emit**: entity M is echoed verbatim
except a redaction pass over host / server literals passed to connector
functions (`Sql.Database`, `Databricks.Catalogs`, `Web.Contents`, …), and shared
connection parameters (`pHostName`, `pHTTPPath`) are factored into one card with
their infrastructure values redacted. A tenth gate enforces that every
source-lineage card carries code or an explicit absence note.

The routing split for the agent: *"where does this number come from"* → the
measure card's **Source trace** section (prose, always present); *"show me the
actual SQL / M / Power Query code"* → the matching **Source lineage (code)**
card.

## Why this beats a human doc site for this use case

A human doc site optimises for browsing: a table of contents, cross-links,
diagrams, DRY single-source-of-truth pages. A RAG corpus optimises for a model
answering one question from one retrieved chunk. The two goals are partly
opposed — links and normalisation *help* a human and *hurt* the agent. This
design picks the agent every time: self-sufficient cards, inlined definitions,
tables over diagrams, redundancy over normalisation. The result is a corpus an
M365 agent can answer from accurately in a single hop, regenerated
deterministically from source and held to ten automated gates.

## Considered and rejected

### Time-variant source-trace deduplication

**Idea.** ~75-80% of the model's measures are time variants of a base measure
(`Buying Margin Orders LY`, `… LW`, `… LY-1`, `… vs LY %`, …). Their DAX is
mostly a mechanical `CALCULATE([Base], USERELATIONSHIP(…, Calendar[fiscalWeek…]))`
shift, so each variant card repeats the base measure's source-trace rows. The
proposal was to replace those repeated rows with an *"inherits all source columns
from `[Base]`"* line plus a delta of only the columns the variant adds, to shrink
the `01-model-and-metrics*` files.

**Why it was rejected.** It reintroduces the exact failure mode the card format
exists to prevent (Decision 1, constraint 1). *"Inherits from `[Base]`"* is a
pointer the agent **cannot follow** — for a question like *"what does Buying
Margin Orders LY trace back to in SQL?"*, a retrieved `…LY` chunk would no longer
contain `fact_orders_fhbwk.bimev_num` / the `SUM(...)` expression / the source
line, forcing a second retrieval of the base card and a manual stitch. That
fragility would land on the **most numerous and most semantically confusable
cards in the model** — the same near-identical variant names that caused the
original retrieval miss this redesign was built to fix.

The benefit was also smaller than it first appeared: a simple base measure's
trace is only 1-4 short lines, so dedup removes roughly as many tokens as the
"inherits" sentence adds — a near-zero size win in exchange for a real loss of
per-card self-sufficiency. The genuinely large traces belong to router /
aggregator measures, which are not simple variants and would not qualify anyway.

**What already covers the need.** Two existing mechanisms give the density win
without breaking self-sufficiency:

- the **compact arrow source-trace encoding** (`model_col → entity → view:line`,
  with the SQL expression shown only for real computations and unresolved
  columns grouped) already removed the table scaffolding that bloated traces;
- the **measure lead sentence** already states *"Its value derives from
  `view.column`, …"* in prose using the variant's **own** resolved columns
  (which include the inherited ones), so a variant chunk answers
  "which views/columns" self-sufficiently with no second hop.

**If file size ever becomes a hard limit** (e.g. the indexer rejects a part),
the correct lever is a smaller `MODEL_METRICS_PART_BUDGET` — more, smaller files
— which preserves per-card completeness instead of trading it away. Total corpus
token count is not itself a constraint; per-file chunkability is, and splitting
already solves that.
