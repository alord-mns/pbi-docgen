# Power BI Documentation Requirements — Card-Based Agent Knowledge Base

**Purpose.** This file is the **requirements contract** for the model-agnostic
documentation engine under [`scripts/docgen/`](../scripts/docgen/). The engine
generates a knowledge base optimised for a Microsoft 365 **declarative
(retrieval-augmented) Copilot agent**, not a human-browsable doc tree. Retrieval
is single-chunk: the agent fetches one card and answers from it **without
following links**. Every card must therefore be **self-sufficient in one hop**.

This contract is enforced by [`scripts/docgen/validate.py`](../scripts/docgen/validate.py).
A red `python -m scripts.docgen.validate` run blocks any "documentation done"
claim.

---

## 1. Design principles

1. **Cards, not pages.** The unit of documentation is a *card*: an anchored
   `## Heading` block that fully answers one question (what a metric means and
   how it is calculated, what a table contains and what depends on it, what a
   report page shows, what a dataflow feeds). A card never says "see the X
   page" for its core content.
2. **Flat files.** Cards are concatenated into four flat Markdown files (§3).
   No per-folder tree, no `README.md` index pages.
3. **Self-sufficiency over DRY.** Inline a one-line definition or source
   summary rather than linking out. Cross-reference links are navigational
   aids only; the answer must already be in the card.
4. **Evidence only.** Every fact is derivable from repository evidence (TMDL,
   PBIR, dataflow / orchestration JSON, SQL exports, `.docgen.toml`). Missing
   evidence surfaces a `{{PLACEHOLDER}}` or an explicit "not traced" — never a
   guess.
5. **Deterministic + idempotent.** Re-running the generator with unchanged
   inputs produces byte-identical files. Anchors are pure functions of
   `(kind, name)` so a link computed anywhere matches the card's own anchor.
6. **Text and tables only.** No Mermaid or images — a RAG chunk cannot render
   them.
7. **Secret-safe.** Workspace and dataset GUIDs are allowed. Recipient emails,
   Teams thread IDs, and connector connection GUIDs are redacted (§6).

---

## 2. Source artefacts (read-only)

| Artefact | Location | Used for |
|---|---|---|
| Semantic model (TMDL) | `src/semantic-model/**/definition/` | tables, columns, measures, relationships, roles |
| Thin reports (PBIR) | `src/thin-reports/**/definition/` | pages, visuals, slicers, filters, field bindings |
| Dataflows (JSON) | `dataflows/*.json` | entities, Power Query, Databricks navigation |
| SQL exports | `sql/*.sql` | physical column derivations and row scope (`WHERE` / `HAVING` filters) (`CREATE VIEW`) |
| Orchestration (JSON) | `orchestration/**` | triggers, refresh targets, notifications |
| Canvas Power Apps (unpacked) | `power-apps/**/CanvasManifest.json` (+ sibling `Src/`, `Connections/`, `DataSources/`) | app metadata, screens, connectors, read/write data sources |
| Per-repo content | `model-docs/.docgen.toml` | narratives, acronyms, data-source descriptions, workspace IDs, measure-role prefixes, SQL overrides |

**Report scope.** The embedded report inside the `.pbip` project
(`src/semantic-model/**/*.Report/`) is development-only and is **excluded** via
`[paths].excluded_report_definitions`. Only `src/thin-reports/` reports are in
scope.

The engine is **strictly read-only** over all source artefacts.

---

## 3. Required outputs (exact paths)

All output lives directly under `model-docs/`. Four core generated files, plus
two optional presence-driven files — `04-source-code.md` (emitted whenever SQL /
dataflow source artefacts are present; opt out with `[source_code] enabled =
false` in `.docgen.toml`) and `05-power-apps.md` (emitted whenever one or more
unpacked canvas Power Apps are discovered under the `[paths]
power_apps_definitions` glob) — plus the protected inputs.

| File | Contents |
|---|---|
| `00-overview.md` | Solution summary, architecture (text), glossary + acronyms, end-to-end dependency table, refresh / change-impact. The entry point. |
| `01-model-and-metrics.md` | One card per metric concept (router), measure (metric / compute), and table. The bulk of the knowledge base. |
| `02-data-pipeline.md` | One card per data source, dataflow, and orchestration flow, plus a refresh runbook. Each dataflow card carries a **downstream impact** section (including any **Written by Power App(s)** write-back back-reference). |
| `03-reports.md` | A **Report Catalog** index card listing every report and a **Page Index** card listing every page, then one card per report and report page (slicers, filters, metrics with inline definitions, backing tables / dataflows). |
| `04-source-code.md` *(optional, presence-driven)* | One **source-lineage (code)** card per consumed dataflow entity, co-locating its SQL source (a full Databricks view **or** an inline native SQL query such as `DB2.Database([Query=…])`, decoded from the M) and Power Query M, linked up to the model table(s) it feeds; plus SQL-only cards for exported views with no model consumer and one redacted **connection parameters** card. Host / server literals in connector calls are redacted. |
| `05-power-apps.md` *(optional, presence-driven)* | One **Power App (canvas)** card per unpacked canvas app: overview (form factor, screens), connectors, connected data sources (read vs read/write), and a **Downstream (pipeline)** section linking write-back targets to the dataflows that read them. |

If `01-model-and-metrics.md` exceeds the renderer's size budget (~450 KB) it is
split on whole-card boundaries into numbered parts: part 1 keeps the canonical
name `01-model-and-metrics.md`, and overflow parts are suffixed
`01-model-and-metrics-02.md`, `01-model-and-metrics-03.md`, … Cards are never
divided, every card stays self-sufficient, and cross-references resolve across
all parts. The split keeps each file small enough to chunk cleanly for
single-chunk retrieval.

**Protected inputs** (never swept, never machine-overwritten): `documentation_req.md`,
`dataflow-references.md`, `.docgen.toml`, `generation-log.md`. Everything else
under `model-docs/` is regenerated wholesale on every run.

---

## 4. Card anatomy

Every card renders as:

```
<a id="{kind}-{slug}-{hash}"></a>

## {Concept name}

**Type:** {kind} · {one-line subtitle}

[**Also known as:** {synonyms}]

{body sections}
```

- The **anchor** is `slugify(kind-name)` plus a 10-character hash of the exact
  `(kind, name)` — guaranteeing uniqueness even when two names slug-collide.
- The **Type** line classifies the card and gives a one-line summary.
- Bodies use `###` sub-sections. Required sub-sections per card kind:

| Card kind | Required sub-sections |
|---|---|
| Metric concept (router) | Routing table (selector value → target metric → inline definition); router definition; used-on report pages |
| Measure (metric) | Definition (DAX); **Source trace** (per-column physical → SQL view:line, with N/M accounting); references; used-on report pages |
| Measure (compute) | DAX one-liner; references; sources; pages (folded stub) |
| Table / entity | Upstream source (dataflow entities → SQL views; plus a **Source code** up-link to the matching source-lineage card when `04-source-code.md` is enabled); columns; base measures (folded); measures using this table; **Downstream impact** (report pages) |
| Data source | Connection mechanism, host, freshness (credentials never recorded) |
| Dataflow | Output entities → Databricks views (or inline native SQL query targets); **Linked dataflows** (entities sourced from another dataflow via `PowerPlatform.Dataflows`, listed with dataflow / workspace IDs, when present, with an ambiguity note when a referenced entity name is produced by more than one dataflow); **Row filters / exclusions** (backing-view / native-query `WHERE` / `HAVING`, static vs dynamic); **Downstream impact** (model tables → measures → report pages). Connectors may include native-database markers (e.g. `DB2.Database`) and a `Native SQL query` token. |
| Orchestration flow | Schedule; refresh targets; notifications (redacted) |
| Power App (canvas) *(optional)* | **Overview** (form factor, screen list, unpacked-source folder); **Connectors** (display name, tier, API family); **Data sources** (name, type, read vs read/write access, backing store); a write-back note listing read/write targets; and a **Downstream (pipeline)** section linking each write-back target to the dataflow(s) whose Power Query M reads it by name. The app author is never rendered (PII). |
| Report index (catalog) | Single aggregate card listing **every** report (count, pages, visuals, curated purpose from `[reports]`), each linked to its own report card |
| Page index | Single aggregate card listing **every** page across all reports (report, page, visual / filter counts), each linked to its own page card |
| Report page | Page filters; slicers; metrics shown (with inline definition); visuals (field-bearing only — distinct configurations with a repeat count; field-less chrome such as shapes / textboxes / buttons omitted and tallied); backing data |
| Source lineage (code) *(optional)* | **Chain** line (model table(s) ← dataflow · entity ← view); an **Ambiguous source** note when the entity name is produced by more than one dataflow; the SQL section — **SQL view (Databricks)** or **Native SQL query** (full view text / decoded inline query, or a documented absence note); **Dataflow M (entity query)** (full Power Query with connector hosts redacted). A dedicated **Connection parameters** card summarises shared parameters with redacted values. |

---

## 5. Measure classification

Measures are classified into five roles by a config-driven engine
(`[measures]` in `.docgen.toml`: `selector_prefixes`, `base_prefixes`):

| Role | Meaning | Card |
|---|---|---|
| **selector** | Field-parameter / disconnected slicer driver | referenced only (no own card) |
| **router** | `SWITCH`/`IF` that dispatches to other measures by a selector | **concept card** |
| **metric** | Headline business measure | **measure card** with full source trace |
| **compute** | Intermediate helper referenced by other measures | compact **stub card** |
| **base** | Atomic aggregation wrapper (e.g. `SUM(...)`) | **folded** into its table card |

The **source trace** is a deterministic two-hop resolution, never a guess:

1. **Model → dataflow entity.** Walk the table partition and shared expressions
   to the `[entity="X"]` reference(s). `X` is a *dataflow query name*.
2. **Dataflow entity → SQL view.** Walk that dataflow query's Power Query graph
   to the terminal Databricks `[Name="Y", Kind="Table"]`. `Y` is a *view name*
   matching `sql/Y.sql`. The output column's derivation is read from that file.

The model's `[entity="X"]` and the Databricks `[Name="Y"]` are **different
layers** that may share a string only by naming convention; they are resolved
as two separate hops.

---

## 6. Redaction (mandatory)

The following must be redacted to `{{PLACEHOLDER}}` with a parenthetical note:

- Recipient **email addresses** (notifications).
- **Teams thread IDs** (`19:…@thread.v2`).
- Connector **connection GUIDs** from `connectionsMap.json` and canvas-app
  `Connections/Connections.json` (the app **author** identity is never
  rendered).

Allowed in clear text: workspace IDs, dataset IDs, dataflow IDs, SharePoint
site URLs (paths, not credentials).

---

## 7. Quality gates (enforced by `validate.py`)

`python -m scripts.docgen.validate` must pass all gates:

1. **Knowledge-base files present** — all four core files exist (plus
   `04-source-code.md` when SQL / dataflow source is present, and
   `05-power-apps.md` when one or more canvas Power Apps are present).
2. **Card anchors unique** — no duplicate `<a id>` across the knowledge base.
3. **Cross-reference integrity** — every `](#anchor)` link resolves to an
   existing anchor knowledge-base-wide.
4. **Measure coverage** — every router has a concept card, every metric /
   compute has a measure card, every base measure is folded into a table card.
5. **Concept routing tables** — every concept card carries a routing table.
6. **Metric source-trace sections** — every metric card carries a source-trace
   section with accounting.
7. **Dataflow downstream impact** — every dataflow card carries a
   downstream-impact section.
8. **Acronyms documented** — every acronym configured in `.docgen.toml` appears
   in the overview.
9. **No sensitive data** — no secret patterns, recipient emails, or Teams
   thread IDs in any output file.
10. **Source-lineage cards carry code** *(only when `04-source-code.md` is
    present)* — every source-lineage card carries a SQL or Power Query code
    block, or an explicit documented-absence note.
11. **Power App cards complete** *(only when `05-power-apps.md` is present)* —
    every Power App card carries a connectors section and a data-sources
    section.

The validator is **read-only**; it prints a report and sets its exit code but
never edits the generated files.

---

## 8. Regeneration workflow

```
python -m scripts.docgen.generate    # rewrite the four files (sweeps stale output)
python -m scripts.docgen.validate    # enforce the gates above
```

On Windows set `$env:PYTHONIOENCODING='utf-8'` first — the renderers emit
`→`, `…`, and `·` which the legacy console code page cannot encode.

Per-repo narrative content (purpose, acronyms, data-source descriptions,
workspace IDs, lineage narrative) is edited in `model-docs/.docgen.toml`, **not**
in the generated files.
