# docgen — Power BI documentation engine

A small, model-agnostic Python engine that generates the contents of
[`model-docs/`](../../model-docs/) from the repository's source artefacts.

## What it is — and what it is not

**The whole pipeline is 100% deterministic and 100% Python. There is no
generative AI anywhere in the engine.** Given the same source tree and
the same `model-docs/.docgen.toml`, two runs produce byte-identical
output (after the first run stabilises on-disk).

| | |
|---|---|
| Language | Python 3.11+ (standard library only) |
| Inputs | TMDL, PBIR, dataflow JSON, orchestration JSON, `.docgen.toml` |
| Output | Markdown under `model-docs/` |
| Determinism | Required — enforced by the *idempotency* cardinal rule in [`.github/instructions/docgen.instructions.md`](../../.github/instructions/docgen.instructions.md) |
| AI involvement at runtime | **None** |

## How a run works

1. **Parsing** — pure deterministic readers walk:
   - TMDL files → `tmdl.py`
   - PBIR JSON → `pbir.py`
   - Dataflow JSON → `dataflow.py`
   - Power Automate / Logic App JSON → `orchestration.py`
2. **Graph build** — `lineage.py` joins the parsed objects into a
   source → dataflow → table → page → report graph using plain dict /
   set operations.
3. **Rendering** — the `renderers_*.py` modules string-concatenate
   Markdown using hard-coded templates with values pulled from the
   parsed objects and from `model-docs/.docgen.toml`. No language
   model, no summarisation, no paraphrasing.
4. **Writing** — `md.write()` only writes files whose content has
   actually changed; unchanged files keep their mtime so `git status`
   reflects real edits.
5. **Sweep** — any file under `model-docs/` or `model-docs-txt/` that
   existed before the run but was not produced by it is removed
   (protected files are kept by name).
6. **Generation log** — a one-line entry is prepended to
   [`model-docs/generation-log.md`](../../model-docs/generation-log.md)
   recording the changed / removed / unchanged counts.

## Why "regenerate everything, write only what changed"

The engine treats `model-docs/` as **fully owned, fully replaceable
output** — every document is re-rendered from source on every run.
There is no incremental "patch only what changed" mode. This is a
deliberate choice; the alternative was considered and rejected.

**Why regenerate everything:**

- Cross-cutting docs (the home README, the lineage graph, the
  glossary, the validation summary) depend on the *whole* model. A
  one-measure source change still affects counts in the README,
  edges in the lineage diagram, and the measures index. Trying to
  decide which subset to regenerate would require a full dependency
  graph between sources and renderers, with cache-invalidation bugs
  the moment it got anything wrong.
- It makes idempotency a *property of construction*, not a
  property to maintain. If the engine only rewrote some files, a
  renamed measure could leave an orphan page; a deleted table could
  leave dangling cross-references; a renderer template change would
  only take effect for the files happening to be regenerated. Full
  regeneration plus a sweep of orphaned files eliminates that whole
  class of bug.
- The output is a couple of dozen files and rendering takes a few
  seconds. There is no real performance problem to solve.
- Git already provides the "what changed" view for free —
  `git diff --stat model-docs/` after a run tells you exactly which
  documents moved.

**Why also check before writing:**

Naïvely rewriting every file on every run would mark every file's
mtime as changed, which makes `git status` noisy and makes the diff
view less useful. So `md.write()` reads any existing file first,
compares (ignoring the daily `_Last generated:` line which would
otherwise churn every doc every day), and **skips the write if the
content is identical**. The combination gives you the best of both
worlds:

- *Logically* every file is regenerated, so renames / deletions /
  template changes propagate cleanly and orphans get swept.
- *Physically* only the files whose content actually changed get
  rewritten, so git sees a small, meaningful diff.

The result is reported in stdout at the end of each run, e.g.
`write summary: 2 changed · 0 removed · 60 unchanged`, and recorded
in `model-docs/generation-log.md` as an audit trail of separate run
events over time.

## Where the prose in the generated docs comes from

This is worth being explicit about, because human-readable narrative
can look AI-written when it isn't:

| Prose you see in the docs | Where it comes from |
|---|---|
| Section headings, table headers, fixed intro sentences ("Per-page documentation for…") | Hard-coded string literals in the renderers |
| Table / measure / column descriptions | Verbatim from TMDL `description` fields you wrote |
| Dataflow descriptions | Verbatim from the dataflow JSON `description` field |
| Acronym definitions, headline metric names, data-source purposes, narrative bullets, app name / purpose | Verbatim from `model-docs/.docgen.toml` |
| Counts, file lists, page tables, lineage diagram nodes | Computed from the parsed source artefacts |
| `{{PLACEHOLDER}}` / `**Unknown — needs business input**` | Emitted when neither the source nor the config provides a value |

This is by design — cardinal rule #2 in
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md)
is *"never invent business meaning"*, and the docgen-specific rules in
[`.github/instructions/docgen.instructions.md`](../../.github/instructions/docgen.instructions.md)
require idempotent, evidence-only generation. If the engine summarised
with an AI, two runs could produce different prose for the same input,
the validation gates would be noisy, and any business meaning would be
a hallucination risk.

## Where AI does enter the picture

Only on the *upstream* side, never inside the engine:

- You can use GitHub Copilot to draft TMDL `description` fields — the
  [`addMeasureDescriptions`](../../.github/prompts/addMeasureDescriptions.prompt.md)
  prompt does this. Those descriptions are then committed to the TMDL
  files and consumed verbatim by the engine.
- You can use Copilot to draft narrative entries for `.docgen.toml`.
  Same pattern — AI helps you write the source, but the source is
  committed and the engine just renders it.

So the chain is:

```
AI-assisted authoring of source / config (optional)
        │
        ▼
    git commit
        │
        ▼
Deterministic Python renders docs from committed evidence
```

The output you see under `model-docs/` is a pure function of the
source tree plus the config file. That is precisely why idempotency
holds.

## Module layout

| Module | Responsibility |
|---|---|
| `config.py` | Load and validate `model-docs/.docgen.toml` |
| `tmdl.py` | Parse TMDL into model / tables / columns / measures / relationships / expressions / roles |
| `pbir.py` | Parse PBIR into pages / visuals / slicers / filters |
| `dataflow.py` | Parse `dataflows/*.json` |
| `orchestration.py` | Parse Power Automate / Logic App workflow JSON |
| `power_apps.py` | Parse unpacked canvas Power Apps; resolve app write-back → dataflow edges |
| `sqlsource.py` | Parse SQL view exports and inline native queries into per-column derivations |
| `sourcetrace.py` | Deterministic two-hop model → dataflow-entity → SQL-view source trace |
| `dax_refs.py` | DAX reference extraction and the five-role measure classifier |
| `lineage.py` | Build the source → dataflow → table → page → report → Power BI App graph, with orchestration overlays |
| `md.py` | Markdown writing primitives (idempotent, write-on-change) |
| `cards.py` | Card model (`Card`, `card_anchor`, `render_bundle`) and the shared `DocContext` |
| `renderers_*.py` | One renderer per knowledge-base file |
| `doctor.py` | Preflight check: resolve globs, report what will be emitted |
| `init.py` | One-time scaffold: detect layout, write a starter `.docgen.toml` |
| `generate.py` | Orchestrator: load config, parse, render, sweep, log |
| `validate.py` | Quality-gate runner |

## Running it

From the repo root:

```powershell
python -m scripts.docgen.init       # one-time scaffold + starter config
python -m scripts.docgen.doctor     # optional preflight
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

`generate` rewrites `model-docs/` (preserving protected files) and the
plain-text mirror `model-docs-txt/`. `validate` runs the quality gates
and prints a pass/fail table, exiting non-zero on failure.

## Related documents

- [`.github/prompts/generateDocumentation.prompt.md`](../../.github/prompts/generateDocumentation.prompt.md)
  — reusable Copilot prompt that drives the engine and triages failures.
- [`.github/instructions/docgen.instructions.md`](../../.github/instructions/docgen.instructions.md)
  — coding rules for the engine itself (no solution-specific strings,
  idempotency required, read-only over source).
- [`docs/using-docgen.md`](../../docs/using-docgen.md) — installing and using
  the engine on another Power BI repository.
- [`docs/maintaining-docgen.md`](../../docs/maintaining-docgen.md) — developing
  and releasing the engine itself.
- [`documentation_req.md`](documentation_req.md)
  — the canonical specification of every document the engine produces.
