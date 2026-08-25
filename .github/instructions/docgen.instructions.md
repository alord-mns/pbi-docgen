---
applyTo: "scripts/docgen/**/*.py"
description: Rules for the model-agnostic documentation engine under scripts/docgen/.
---

# Docgen engine rules

[`scripts/docgen/`](../../scripts/docgen/) is a **portable, model-agnostic**
documentation engine. It is intended to be copied verbatim to other
Power BI repositories that follow the same layout. Per-repository content
(workspace IDs, narratives, acronyms, data-source descriptions) lives in
[`model-docs/.docgen.toml`](../../model-docs/.docgen.toml) and is loaded at runtime.

## Cardinal rules

1. **No solution-specific strings in code.** No "FH&B", no "FHB Weekly",
   no specific workspace GUIDs, no specific dataflow names, no specific
   measure names, no specific report names. If you need such a value at
   runtime, read it from `.docgen.toml` via [`config.py`](../../scripts/docgen/config.py).
2. **Read-only over source artefacts.** The engine must never modify
   `.pbip`, TMDL, PBIR, dataflow JSON, or orchestration JSON. Parsers
   open files for reading only.
3. **Generated output owns `model-docs/`** (except the protected set declared
   in [`generateDocumentation.prompt.md`](../prompts/generateDocumentation.prompt.md)).
   The engine emits exactly **four flat card files** (`00-overview.md`,
   `01-model-and-metrics.md`, `02-data-pipeline.md`, `03-reports.md`);
   `generate.py` sweeps and rewrites them on every run, deleting any stale
   output. The `01-model-and-metrics.md` bundle may split by size into
   `01-model-and-metrics-02.md`, `-03.md`, … on whole-card boundaries — these
   parts are engine-owned and swept alike. Do not "merge" or "preserve"
   generated files. The card contract is
   [`scripts/docgen/documentation_req.md`](../../scripts/docgen/documentation_req.md).
4. **Validate after every change.** When editing the engine, the
   acceptance test is `python -m scripts.docgen.generate` followed by
   `python -m scripts.docgen.validate` returning all gates green for
   this repo. Do not declare a change complete on the strength of a unit
   test alone.
5. **Idempotent generation.** Running `generate` twice with no input
   change must produce a byte-identical `model-docs/` tree. If you introduce
   randomness (dict ordering, timestamps, set iteration), sort it.

## Module layout (do not reorganise without discussion)

| Module | Responsibility |
|---|---|
| `config.py` | Load and validate `.docgen.toml`. |
| `tmdl.py` | Parse TMDL into model / tables / columns / measures / relationships / expressions / roles. |
| `pbir.py` | Parse PBIR into pages / visuals / slicers / filters. |
| `dataflow.py` | Parse `dataflows/*.json`. |
| `orchestration.py` | Parse Power Automate / Logic App workflow JSON. |
| `power_apps.py` | Parse unpacked canvas Power Apps (`CanvasManifest.json` + `Src/`, `Connections/`, `DataSources/`); resolve app write-back → dataflow edges. |
| `lineage.py` | Build the source → dataflow → table → page → report → Power BI App graph, with orchestration overlays. |
| `dax_refs.py` | DAX reference extraction, `SWITCH` parsing, and the five-role measure classifier. |
| `sqlsource.py` | Parse `sql/*.sql` `CREATE VIEW` exports into per-column derivations; extract Databricks / dataflow navigation from M. |
| `sourcetrace.py` | Deterministic two-hop model → dataflow-entity → SQL-view source trace. |
| `md.py` | Markdown writing primitives (banner, slugify, idempotent write). |
| `cards.py` | Card model (`Card`, `card_anchor`, `render_bundle`), shared `DocContext`, and cross-reference helpers. |
| `renderers_overview.py` | `00-overview.md`. |
| `renderers_model_metrics.py` | `01-model-and-metrics.md` (concept / measure / table cards). |
| `renderers_pipeline.py` | `02-data-pipeline.md` (source / dataflow / flow / runbook cards). |
| `renderers_reports.py` | `03-reports.md` (report / page cards). |
| `renderers_source_code.py` | `04-source-code.md` (source-lineage code cards; presence-driven). |
| `renderers_power_apps.py` | `05-power-apps.md` (canvas Power App cards; presence-driven). |
| `renderers_agent.py` | `model-docs/agent-instructions.md` — the generated agent system prompt. Not a knowledge-base file; never mirrored to `model-docs-txt/`. |
| `generate.py` | Orchestrator: load config, parse, classify, trace, build `DocContext`, render, write. |
| `doctor.py` | Read-only preflight: resolve every configured glob, report what will be emitted, flag placeholder-bound config. |
| `init.py` | One-time scaffold. Detects an existing layout before writing; strictly non-destructive and re-runnable. |
| `validate.py` | Card-based quality-gate runner (read-only). |

New card kinds are added inside the renderer that owns the file they belong to,
using `cards.Card` + `cards.card_anchor`; do not reintroduce per-folder document
trees or bulk-extend a renderer beyond its named file.

## Style

- Type hints on every public function. `from __future__ import annotations`
  at the top of new modules.
- Prefer `pathlib.Path` over `os.path`.
- No printing from library code — use the project logger. CLI entry
  points (`generate`, `validate`) may print structured progress.
- No third-party dependencies beyond what is already in
  `requirements.txt` without discussion.
- Errors that block generation must raise; warnings that the validator
  will catch may log and continue.

## Placeholders

When generated content needs a value that the engine cannot derive from
source artefacts:

- Solution-level missing narrative → `{{PLACEHOLDER}}` literal in the
  rendered file. The validator flags these.
- Unverifiable business meaning → `**Unknown — needs business input**`
  in prose. The validator flags these too.
- Redacted PII or secret → `{{PLACEHOLDER}}` for free text,
  `{{CONNECTION_PLACEHOLDER}}` for connector connection GUIDs.

Do not invent. Do not guess. Do not use `TODO` / `TBC` / `???`.

## Out of scope

- Publishing, deploying, or refreshing in the Power BI Service.
- Modifying source artefacts.
- Solution-specific business rules — those belong in `.docgen.toml` (or,
  if structural, in `documentation_req.md`).
