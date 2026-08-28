# Using docgen on another Power BI solution

The documentation engine under [`scripts/docgen/`](../scripts/docgen/) is a
**portable product**. It contains no solution-specific logic: every fact about
a particular Power BI solution lives in one config file. Adding it to a repo is
one git command, a config file, and a run.

You do **not** need a new repository. The engine installs into whatever repo
your solution already lives in.

**Expected effort:** install and first run take minutes. Getting all quality
gates green depends mostly on how complete the target model's TMDL descriptions
are.

> Developing the engine itself, rather than using it? See
> [`maintaining-docgen.md`](maintaining-docgen.md). The reasoning
> behind this setup is in
> [`docgen-distribution-design.md`](docgen-distribution-design.md).

---

## 0. Prerequisites

| Requirement | Why |
|---|---|
| Python 3.11+ | `tomllib` is stdlib from 3.11. No other dependency, no virtualenv. |
| git | For the one-command install and update. A ZIP fallback exists if you don't have it. |
| Target repo holds Power BI source as **files** | PBIP format: TMDL semantic model + PBIR reports. The engine never contacts the Power BI service. |
| Power Platform CLI (`pac`) | **Only** if the solution has canvas Power Apps (see step 3). |

---

## 1. Install the engine

Run this **inside your existing solution repo** — there is nothing to create,
and nothing to delete afterwards:

```powershell
git subtree add --prefix scripts/docgen https://github.com/alord-mns/pbi-docgen dist --squash
```

> **Brand-new repository?** `git subtree add` needs at least one commit to exist
> first. On a repo with no commits it fails with a misleading pair of errors:
>
> ```
> fatal: ambiguous argument 'HEAD': unknown revision or path not in the working tree.
> fatal: working tree has modifications.  Cannot add.
> ```
>
> Nothing is actually modified — there is simply no `HEAD` yet for subtree to
> compare against. Make any commit (`git commit --allow-empty -m "Initial commit"`
> will do) and re-run. You do not need to push first.

That vendors the engine into `scripts/docgen/`. Later, to pick up a new release:

```powershell
git subtree pull --prefix scripts/docgen https://github.com/alord-mns/pbi-docgen dist --squash
```

`--squash` keeps each update to a single commit rather than importing the
engine's whole history. `dist` is the published branch carrying the package
contents — don't point at `main`.

**Use `scripts/docgen/` as the prefix.** Nothing in the engine requires it, but
it is the documented convention, and it means every command in this guide works
verbatim. Vendor to `tools/docgen/` and you'd be typing
`python -m tools.docgen.generate` everywhere instead.

### What the install gives you — and what it doesn't

`dist` is a flattened copy of the engine package, so subtree delivers that and
nothing else:

| Appears in your repo | Not included |
|---|---|
| `scripts/docgen/*.py` — the engine | The guides (this file included) — read them on the `pbi-docgen` repo page |
| `scripts/docgen/README.md` — internals | A `.docgen.toml` — `init` writes you a starter in step 4 |
| `scripts/docgen/documentation_req.md` — the output contract | `model-docs/` — created by `init` and `generate`, not by the install |

Everything lands under `scripts/docgen/`; nothing else in your repo is touched.
Nothing solution-specific ever arrives with the engine, which is the point — no
other team's model names, workspace IDs, or business content come with it.

### No git access?

Download the repo ZIP and extract `scripts/docgen/` into the same place. You
lose the one-command update — you re-download to upgrade — but everything else
is identical.

### Optional Copilot prompts

Not part of the engine, so they aren't vendored. Copy them from the
`pbi-docgen` repo if you want them:

| File | Purpose |
|---|---|
| `.github/instructions/docgen.instructions.md` | Coding rules if you'll modify the engine |
| `.github/prompts/generateDocumentation.prompt.md` + `.md` | Drives the engine and triages gate failures |
| `.github/prompts/addMeasureDescriptions.prompt.md` + `.md` | Helps author TMDL descriptions |

## 2. Lay out the source artefacts

The engine discovers everything by glob. Defaults live in
`scripts/docgen/config.py`; override any of them under `[paths]` in
`.docgen.toml` if the target repo is laid out differently.

| Artefact | Default glob | Required |
|---|---|---|
| Semantic model (TMDL) | `pbi/semantic-model/*.SemanticModel/definition` | **Yes** |
| Thin reports (PBIR) | `pbi/thin-reports/*.Report/definition` | **Yes** |
| Reports excluded from docs | `pbi/semantic-model/*.Report/definition` | — |
| Dataflow JSON exports | `dataflows/*.json` | No |
| SQL view exports | `sql/*.sql` | No |
| Orchestration workflow JSON | `orchestration/**/definition.json` | No |
| Canvas Power Apps (unpacked) | `power-apps/**/CanvasManifest.json` | No |

Two rules follow from this table:

- **A semantic model and at least one report are mandatory.** The engine
  exits with a clear message if either glob matches nothing.
- **Everything else is presence-driven.** No dataflows, no SQL, no flows, no
  apps? Those sections are simply omitted — there is no flag to set, and no
  gate will demand them.

### Which report gets documented

Whether the report attached to the semantic model should be documented is a
question only you can answer — it may be a genuine user-facing report, or a
development artefact left in the PBIP. Declare it:

```toml
[report_scope]
include_model_attached_report = true   # or false
```

`init` writes a starting value by looking at your layout: `false` when separate
thin reports exist, `true` when the model-attached report is the only one. Both
are guesses. **Correct it if it is wrong** — a solution can perfectly well have
thin reports *and* a user-facing report attached to the model, and no amount of
folder inspection can tell that apart from a leftover development report.

The flag overrides `excluded_report_definitions` for that report only, so you can
still use the exclusion globs to drop other reports.

If you configure this by hand and get it wrong, the symptom is a build failure
saying every matched report was excluded. `doctor` names that cause explicitly
rather than reporting a glob that matched nothing.

Match the SQL export filenames to the Databricks view / table names they define
(`sql/fact_orders.sql` for view `fact_orders`). That filename **is** the join
key for the two-hop source trace.

---

## 3. Unpack canvas Power Apps (only if you have them)

A `.msapp` is a binary archive, so the engine cannot read it. Commit the
*unpacked* source instead — the engine never runs `pac` itself:

```powershell
pac canvas unpack --msapp 'My App.msapp' --sources 'power-apps/My-App'
```

Add `*.msapp` to `.gitignore` and commit the unpacked folder. Presence of
`power-apps/**/CanvasManifest.json` is what turns on `05-power-apps.md`.

---

## 4. Write `model-docs/.docgen.toml`

Run `init` once to scaffold the repo and write a commented starter config:

```powershell
python -m scripts.docgen.init
```

It needs Python only — git was just for the install. Before writing anything it
**detects your existing layout**: if your PBIP source already sits at paths that
differ from the defaults, the generated `[paths]` block points at where your
files actually are rather than imposing this engine's folder names. Folders are
only scaffolded for a genuinely empty repo, and it also adds the `__pycache__/`
and `*.msapp` entries to `.gitignore`.

The `[paths]` block is always written out in full, even where it matches the
engine defaults. That is deliberate: your layout is pinned in your own config,
so a future engine release cannot move it out from under you.

`init` is **non-destructive and safe to re-run**. It never modifies anything that
already exists — above all an existing `.docgen.toml`, which is your own
configuration — and reports every item as created or skipped.

This file is the **only** place solution-specific facts belong. Fill in the
starter `init` wrote for you; the engine still runs if you leave fields empty,
but it emits placeholders rather than prose.

Minimum to avoid placeholder-heavy output:

```toml
[solution]
display_name = "Your Solution Name"
short_name   = "YSN"
purpose      = "One paragraph on what the solution does and who uses it."

[workspaces]
primary = "<workspace GUID where dataflows live>"
dataset = "<workspace GUID where the dataset lives, if different>"
```

Then fill in the content that drives the prose. Anything left empty renders a
`{{PLACEHOLDER}}` or an "Unknown" marker rather than a guess:

| Section | Effect if empty |
|---|---|
| `[narratives] upstream_platforms` | Overview architecture paragraph is a placeholder. |
| `[narratives] lineage_narrative` | End-to-end dependency bullets missing. |
| `[narratives] change_impact_notes` | Change-impact guidance missing. |
| `[acronyms]` | Glossary is thin; the acronym gate has nothing to check. |
| `[headline_metrics] names` | No KPI spotlight in the overview. |
| `[[data_sources]]` | Data-source cards fall back to bare connector names. |
| `[reports]` | Report Catalog shows a placeholder purpose per report. |
| `[powerbi_app]` | Power BI *distribution* App details omitted. |

### Measure naming conventions (optional, but shapes the output)

```toml
[measures]
selector_prefixes = ["Select "]   # field-parameter / slicer driver measures
base_prefixes     = ["Base "]     # atomic SUM/COUNT wrapper measures
```

These encode *your* naming conventions. They are **independent** — set either,
both, or neither. Only two of the five measure roles depend on them; `metric`
and `compute` are derived structurally from the DAX dependency graph and from
how often each measure appears in report visuals, so they work on any model.

**`selector_prefixes`** enables the router / metric-concept layer, where one
measure `SWITCH`es between variants chosen by a slicer or field parameter. If
the target model doesn't use that pattern, **leave it empty and lose nothing** —
the engine emits zero concept cards because there are genuinely no concepts to
route, and every measure is documented directly instead. Set it only if the
model really does use the pattern.

**`base_prefixes`** controls *folding*. Measures matching these prefixes are
collapsed into rows on their table card instead of getting a card of their own.
Leave it empty and every leaf measure gets a full metric card with a source
trace — nothing is lost, but `01-model-and-metrics.md` grows and may split into
more parts. Set it if the model has an atomic-wrapper convention.

> Roles are never guessed from DAX shape alone: a leaf measure could equally be
> a headline KPI (`Total Sales`) or a plumbing wrapper (`Base Sales Value`).
> The safe default is a full card, so folding is opt-in by name.

### Optional valve

```toml
[source_code]
enabled = false   # suppress 04-source-code.md even though SQL / M exist
```

Source-code cards are emitted automatically whenever dataflow or SQL source is
present. Set `enabled = false` only if raw SQL is sensitive in that repo.

> **Naming note.** `[powerbi_app]` is the Power BI *distribution* App (the
> audience app publishing reports). It is unrelated to canvas Power Apps, which
> are configured via `[paths] power_apps_definitions`. The legacy section name
> `[app]` is still read for backward compatibility.

---

## 5. Preflight

From the repo root:

```powershell
python -m scripts.docgen.doctor
```

This is read-only. It resolves every glob, reports what matched, lists which
knowledge-base files will be emitted, and flags config fields that would render
as placeholders. It also prints the engine version and the repo root it
resolved, which is the first thing to check if paths look wrong. Fix anything
marked `BLOCKER` before going further — `WARN` rows are quality hints, not
stoppers.

---

## 6. Generate and validate

```powershell
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

`generate` renders the card knowledge base into `model-docs/` and a plain-text
mirror into `model-docs-txt/`:

| File | Emitted |
|---|---|
| `00-overview.md` | Always |
| `01-model-and-metrics.md` (+ numbered parts if oversized) | Always |
| `02-data-pipeline.md` | Always |
| `03-reports.md` | Always |
| `04-source-code.md` | When dataflow / SQL source is present |
| `05-power-apps.md` | When canvas Power Apps are present |
| `agent-instructions.md` | Always — the agent system prompt, **not** a knowledge-base file |

`validate` runs the quality gates and prints a pass/fail table, exiting
non-zero on failure.

Run both from the repo root and the engine finds everything itself. To run
against a solution you're *not* inside, add `--repo-root`:

```powershell
python -m scripts.docgen.generate --repo-root C:\dev\some-other-solution
```

> **Sweep warning.** Both `model-docs/` and `model-docs-txt/` are swept every
> run: any file not produced by that run is **deleted**, except the protected
> names `dataflow-references.md`, `.docgen.toml`, and `generation-log.md`.
> Protection is by filename only. Keep hand-authored docs outside those two
> folders.

---

## 7. Publish the knowledge base and wire up the agent

Generating the files is not the finish line — this is what turns them into a
working Q&A agent.

**Upload the card files** to whatever your agent retrieves from (a SharePoint
library, a Copilot agent's knowledge source, a vector store):

| Upload | Which |
|---|---|
| ✅ | `00-overview`, `01-model-and-metrics` (**including every numbered part**), `02-data-pipeline`, `03-reports`, and `04`/`05` if present |
| ❌ | `agent-instructions.md` — it is the system prompt, not knowledge. Uploading it pollutes retrieval |
| ❌ | `.docgen.toml`, `generation-log.md`, `documentation_req.md` — internal |

Use the Markdown from `model-docs/`. If your platform rejects `.md` uploads, use
the identical plain-text mirror in `model-docs-txt/` instead — same content, and
it deliberately contains no `agent-instructions.txt`.

**Set the system prompt.** Open `model-docs/agent-instructions.md` and paste
everything below the horizontal rule into your agent's instructions field. It is
generated for *your* solution: it uses your solution name, cites real metric
names from your model, and describes only the card types and files this run
actually produced.

It is regenerated on every run, so **adapt it where you deploy it** rather than
editing the file — your edits there would be overwritten. Some routing guidance
reflects how Microsoft 365 Copilot chunks documents; treat that as a sensible
default and adjust for other platforms.

**Then sanity-check it** with a handful of questions you already know the answer
to — one metric definition, one "where does this number come from", one "which
report shows X". If an answer is wrong, the fix is almost always missing TMDL
descriptions or empty `.docgen.toml` narrative fields, not the prompt.

Re-upload after each regeneration, or automate it — the files are deterministic,
so only genuinely changed documents differ between runs.

---

## 7. Iterate to green

| Symptom | Fix |
|---|---|
| Paths all wrong, or globs match nothing unexpectedly | Run `doctor` and check the **Repo root** row — it shows the resolved root and which rule found it. Pass `--repo-root` to override. |
| `no semantic-model definition matched` | Set `[paths] semantic_model_definition`. |
| `no report definitions matched` | Set `[paths] thin_report_definitions`, and check the exclusion glob isn't swallowing everything. |
| No concept cards, but the model *does* use a slicer-driven `SWITCH` pattern | Populate `[measures] selector_prefixes`. If the model has no such pattern, zero concept cards is the correct result — no action needed. |
| `01-model-and-metrics.md` is huge / splits into many parts | Populate `[measures] base_prefixes` so atomic wrappers fold into their table cards. |
| Measure cards lack meaning | Author TMDL `///` descriptions (the `addMeasureDescriptions` prompt helps). |
| Entities show no SQL source | Filenames under `sql/` must match the view names the dataflow M navigates to. |
| Acronym gate fails | Every acronym in `[acronyms]` must appear in the overview — remove stale entries. |
| Orchestration workspace unresolved | Add the GUID to `[workspaces] secondary`. |

Re-run `generate` then `validate` until green. A second `generate` should
report `0 changed` — that idempotency is the engine's core guarantee, so treat
perpetual churn as a bug.

---

## What the engine does **not** need

- No third-party Python packages, no virtualenv.
- No Power BI service access, no credentials, no network.
- No write access to source artefacts — it is strictly read-only over TMDL,
  PBIR, dataflow / orchestration JSON, SQL, and Power App source.
- No AI at runtime. Generation is fully deterministic; identical inputs give
  byte-identical output.

## Current limits

- Canvas Power Apps only — model-driven apps and Power Pages are not parsed.
- Power Fx (`Src/*.fx.yaml`) is not yet parsed, so app write-back edges are
  inferred from data-source names, not from `Patch` / `SubmitForm` calls.
- Orchestration means Power Automate / Logic App workflow JSON. Azure Data
  Factory and Fabric Data Pipelines are not parsed.
- Tabular Editor C# scripts and external `.pq` files are not parsed.
- No incremental builds — every run regenerates everything (by design; see
  [`scripts/docgen/README.md`](../scripts/docgen/README.md)).
