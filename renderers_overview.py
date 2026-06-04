"""Render Phase 1 inventory documents (config-driven, model-agnostic):

* model-docs/README.md
* model-docs/architecture/overview.md
* model-docs/lineage/lineage.md
* model-docs/CHANGELOG.md

All repo-specific narratives come from ``model-docs/.docgen.toml`` via
:mod:`scripts.docgen.config`. The engine emits neutral placeholders when
narrative fields are empty.
"""
from __future__ import annotations

import re
from collections import Counter

from . import md
from .config import Config
from .lineage import Lineage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mermaid_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)[:60] or "n"


def _solution_display(model_name: str, cfg: Config) -> str:
    return cfg.solution.display_name or model_name


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
def _exec_summary_bullets(lin: Lineage, cfg: Config) -> list[str]:
    model = lin.model
    table_count = len(model.tables)
    measure_count = sum(len(t.measures) for t in model.tables)
    column_count = sum(len(t.columns) for t in model.tables)
    rel_count = len(model.relationships)
    expr_count = len(model.expressions)
    role_count = len(model.roles)
    page_count = sum(len(r.pages) for r in lin.reports)
    visual_count = sum(len(p.visuals) for r in lin.reports for p in r.pages)
    df_ref_count = len(lin.dataflow_refs)
    df_export_count = len(lin.dataflows)
    flow_count = len(lin.orchestration_flows or [])
    folders: Counter = Counter()
    for t in model.tables:
        for m in t.measures:
            top = (m.display_folder.split("\\")[0] if m.display_folder else "") or "(none)"
            folders[top] += 1

    primary_ws = cfg.workspaces.primary or lin.primary_workspace_id or "<unknown>"
    dataset_ws = cfg.workspaces.dataset or lin.dataset_workspace_id or primary_ws
    secondary_ws = cfg.workspaces.secondary or lin.secondary_workspace_ids
    ws_line = (
        f"primary `{primary_ws}`"
        + (f" · dataset `{dataset_ws}`" if dataset_ws and dataset_ws != primary_ws else "")
        + (f" · secondary {', '.join(f'`{w}`' for w in secondary_ws)}" if secondary_ws else "")
    )

    bullets = [
        f"**Solution name:** `{model.name}` (semantic model + thin report).",
        f"**Storage mode:** Import; semantic-model compatibility level `{model.compatibility_level or 'unknown'}`.",
        f"**Semantic model size:** {table_count} tables, {column_count} columns, {measure_count:,} measures, {rel_count} relationships, {expr_count} shared expressions.",
        f"**Row-Level Security:** {role_count} role(s) defined — see {md.link('semantic model documentation', 'model/')}.",
        f"**Reports:** {len(lin.reports)} thin report(s) with {page_count} pages and ~{visual_count} visuals total; built on the same `.pbip` semantic model.",
        f"**Upstream dataflows referenced:** {df_ref_count} unique dataflow(s) across workspace(s): {ws_line}.",
        f"**Dataflow JSON exports available:** {df_export_count} of the referenced dataflows are exported under [`dataflows/`](../dataflows/) for full M-code documentation.",
        f"**Orchestration flows documented:** {flow_count} (see [`model-docs/orchestration/`](orchestration/)).",
    ]
    if cfg.narratives.upstream_platforms:
        bullets.append(f"**Primary upstream platform(s):** {cfg.narratives.upstream_platforms.split('.')[0].strip()}.")
    if cfg.solution.calendar_summary:
        bullets.append(f"**Calendar model:** {cfg.solution.calendar_summary}")
    if cfg.solution.business_domains:
        bullets.append(f"**Key business domains:** {cfg.solution.business_domains}")
    bullets.append(
        "**Top measure folders by count:** "
        + ", ".join(f"`{name}` ({n})" for name, n in folders.most_common(5))
    )
    bullets.append(
        f"**Documentation regenerated:** `{md.TODAY}` from PBIP source. Run `python -m scripts.docgen.generate` to refresh."
    )
    bullets.append(
        "**Audience:** Power BI Developers, Data Engineers, Operations/Support, Product Managers, Business End Users — see the audience line at the top of each document."
    )
    return bullets


def render_readme(lin: Lineage, cfg: Config) -> str:
    bullets = _exec_summary_bullets(lin, cfg)
    display = _solution_display(lin.model.name, cfg)
    body: list[str] = []
    body.append(md.HEADER)
    body.append(f"# {display} — Documentation Home\n")
    body.append(md.section_purpose(
        f"Top-level entry point for the {display} Power BI solution. Use the document guide below to navigate to the audience-specific sections you need.",
        "All audiences (Power BI Developers, Data Engineers, Operations/Support, Product Managers, Business End Users)",
    ))
    body.append("\n## Solution Name and Purpose\n")
    if cfg.solution.purpose:
        body.append(cfg.solution.purpose.strip() + "\n")
    else:
        body.append(
            f"`{lin.model.name}` is a Power BI solution comprising a semantic model (`.pbip` / TMDL), one or more thin reports, and a published Power BI App. {md.PLACEHOLDER} _(populate `[solution].purpose` in `model-docs/.docgen.toml`)_\n"
        )

    body.append("\n## Executive Summary\n")
    for b in bullets:
        body.append(f"- {b}")
    body.append("")

    body.append("\n## Document Guide\n")
    body.append("| Document | Audience | Description |")
    body.append("| --- | --- | --- |")
    body.append(f"| {md.link('README', 'README.md')} | All | This page. |")
    body.append(f"| {md.link('Architecture overview', 'architecture/overview.md')} | Developers, Data Engineers, Ops, PMs | High-level architecture diagram, components, scope, stakeholders. |")
    body.append(f"| {md.link('End-to-end lineage', 'lineage/lineage.md')} | Developers, Data Engineers | Mermaid lineage diagram from upstream sources through dataflows, orchestration, and the model to report pages. |")
    body.append(f"| {md.link('Semantic model', f'model/{md.safe_filename(lin.model.name)}.md')} | Developers, Data Engineers | Tables, columns, relationships, calculated tables, RLS, time-intelligence. |")
    body.append(f"| {md.link('Measures index', 'measures/README.md')} | Developers, Business End Users | Index of all DAX measures, organised by display folder. |")
    body.append(f"| {md.link('Glossary', 'glossary.md')} | Business End Users, PMs | Plain-language definitions of business terms, KPIs, and acronyms. |")
    body.append(f"| {md.link('Data sources', 'data-sources/')} | Data Engineers, Ops | Per-source connection, refresh, and ownership detail. |")
    body.append(f"| {md.link('Dataflows', 'dataflows/')} | Data Engineers, Developers | One file per upstream dataflow with M code, entities, dependencies. |")
    body.append(f"| {md.link('Orchestration', 'orchestration/')} | Ops, Data Engineers, Developers | Per-flow refresh, notification, and dependency documentation. |")
    body.append(f"| {md.link('Reports', 'reports/')} | Developers, End Users | Per-page descriptions, slicers, filters, bookmarks, usage tips. |")
    body.append(f"| {md.link('Power BI App', 'app/')} | End Users, Ops, PMs | App contents, navigation, release process. |")
    body.append(f"| {md.link('Operations runbook', 'ops/runbook.md')} | Operations/Support | Refresh workflow, monitoring, playbooks, contacts. |")
    body.append(f"| {md.link('Change log', 'CHANGELOG.md')} | All | History of solution and documentation changes. |")
    body.append(f"| {md.link('Release notes template', 'ReleaseNotes.md')} | PMs, End Users | Template populated each release. |")
    body.append("")

    body.append("\n## Revision Info\n")
    body.append(f"- Last regenerated: `{md.TODAY}`")
    body.append(f"- Solution version: {md.PLACEHOLDER}")
    body.append("- Generator: `scripts/docgen/` (run `python -m scripts.docgen.generate`)")
    body.append("")

    body.append("\n## Quality Gates Status\n")
    body.append("Run `python -m scripts.docgen.validate` to refresh the section below.\n")
    body.append("<!-- VALIDATION:START -->")
    body.append("_Validation has not been run since the last documentation regeneration._")
    body.append("<!-- VALIDATION:END -->")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Architecture overview
# ---------------------------------------------------------------------------
def render_architecture(lin: Lineage, cfg: Config) -> str:
    model = lin.model
    display = _solution_display(model.name, cfg)
    primary_ws = cfg.workspaces.primary or lin.primary_workspace_id
    dataset_ws = cfg.workspaces.dataset or lin.dataset_workspace_id or primary_ws
    body = [md.HEADER, "# Architecture & Solution Overview\n"]
    body.append(md.section_purpose(
        "High-level view of how data flows from upstream platforms through dataflows and orchestration into the semantic model, the report(s), and the published app.",
        "Power BI Developers", "Data Engineers", "Operations/Support", "Product Managers",
    ))

    # ---- Architecture diagram ----
    body.append("\n## Architecture Diagram\n")
    body.append("```mermaid")
    body.append("flowchart LR")
    # Upstream nodes derived from configured data sources (fallback: generic 'Upstream')
    body.append("  subgraph SRC[\"Upstream platforms\"]")
    if cfg.data_sources:
        for ds in cfg.data_sources:
            body.append(f"    {_mermaid_id('SRC_' + ds.name)}[(\"{ds.name}\")]")
    else:
        body.append("    UPSTREAM[(\"Upstream sources — see data-sources/\")]")
    body.append("  end")

    body.append(
        f"  subgraph DF[\"Power BI Dataflows (Workspace {(primary_ws or 'primary')[:8]})\"]"
    )
    for d in lin.dataflows[:24]:
        nid = _mermaid_id("DF_" + d.name)
        body.append(f"    {nid}[\"{d.name}\"]")
    body.append("  end")

    if lin.orchestration_flows:
        body.append("  subgraph ORC[\"Orchestration\"]")
        for f in lin.orchestration_flows:
            body.append(f"    {_mermaid_id('FL_' + f.name)}[[\"{f.name}\"]]")
        body.append("  end")

    ds_subgraph_label = (
        f"Semantic Model — {model.name} (Workspace {dataset_ws[:8]})"
        if dataset_ws and dataset_ws != primary_ws
        else f"Semantic Model — {model.name}"
    )
    body.append(f"  subgraph SM[\"{ds_subgraph_label}\"]")
    body.append(
        f"    SMNODE[/\"{len(model.tables)} tables · {sum(len(t.measures) for t in model.tables):,} measures\"/]"
    )
    body.append("  end")
    body.append("  subgraph RPT[\"Thin Report(s)\"]")
    for r in lin.reports:
        rid = _mermaid_id("RPT_" + r.name)
        body.append(f"    {rid}[/\"{r.name} ({len(r.pages)} pages)\"/]")
    body.append("  end")
    body.append("  subgraph APP[\"Power BI App\"]")
    app_label = cfg.app.name or md.PLACEHOLDER + " App"
    body.append(f"    APPNODE[/\"{app_label}\"/]")
    body.append("  end")

    if cfg.data_sources:
        for ds in cfg.data_sources:
            body.append(f"  {_mermaid_id('SRC_' + ds.name)} --> DF")
    else:
        body.append("  UPSTREAM --> DF")
    body.append("  DF --> SM")
    body.append("  SM --> RPT")
    body.append("  RPT --> APP")
    if lin.orchestration_flows:
        for f in lin.orchestration_flows:
            body.append(f"  {_mermaid_id('FL_' + f.name)} -.->|triggers| DF")
            body.append(f"  {_mermaid_id('FL_' + f.name)} -.->|refreshes| SM")
    body.append("```")
    body.append("")

    body.append("\n## Component Descriptions\n")
    if cfg.narratives.upstream_platforms:
        body.append("- **Upstream platforms.** " + cfg.narratives.upstream_platforms.strip())
    else:
        body.append(f"- **Upstream platforms.** {md.PLACEHOLDER} _(populate `[narratives].upstream_platforms` in `model-docs/.docgen.toml`)_")
    body.append(
        f"- **Power BI Dataflows.** {len(lin.dataflows)} exported dataflows under [`dataflows/`](../../dataflows/) prepare conformed entities for the semantic model. Each dataflow is documented in [`model-docs/dataflows/`](../dataflows/)."
    )
    if lin.orchestration_flows:
        body.append(
            f"- **Orchestration.** {len(lin.orchestration_flows)} workflow(s) trigger and monitor dataflow / dataset refreshes and post notifications. See [`model-docs/orchestration/`](../orchestration/)."
        )
    body.append(
        f"- **Semantic model (`{model.name}`).** Import-mode tabular model containing {len(model.tables)} tables, "
        f"{sum(len(t.measures) for t in model.tables):,} DAX measures, and {len(model.relationships)} relationships. "
        f"Built and version-controlled as a `.pbip` project (PBIP/TMDL)."
    )
    page_count = sum(len(r.pages) for r in lin.reports)
    body.append(
        f"- **Thin reports.** {len(lin.reports)} report(s) with {page_count} pages connected live to the published semantic model."
    )
    if cfg.app.name:
        body.append(
            f"- **Power BI App (`{cfg.app.name}`).** {cfg.app.purpose or 'Distribution wrapper packaging the report(s) for end users.'} See [`model-docs/app/`](../app/)."
        )
    else:
        body.append(
            f"- **Power BI App.** Distribution wrapper packaging the report(s) for end users. App-specific metadata is captured in [`model-docs/app/`](../app/)."
        )
    body.append("")

    body.append("\n## Scope Boundaries\n")
    body.append("**Included.**")
    body.append("- The `.pbip` solution under `src/semantic-model/`, associated TMDL definitions, and the connected thin report(s).")
    body.append("- All dataflows referenced by the semantic model.")
    body.append("- Orchestration flows under `orchestration/` that trigger dataflow / dataset refreshes.")
    body.append("")
    body.append("**Excluded.**")
    body.append(f"- Source systems that feed the documented platforms (operational ERP / OMS / e-commerce platforms) — out of scope. {md.PLACEHOLDER}")
    body.append("- Adjacent reports owned by other teams that may consume the same dataflows.")
    body.append("- Workspace-level access control configuration (managed via Entra ID groups).")
    body.append("- Development / test report files explicitly excluded via `[paths].excluded_report_definitions` in `model-docs/.docgen.toml`.")
    body.append("")

    body.append("\n## Key Stakeholders\n")
    body.append("| Role | Team / Person | Responsibility |")
    body.append("| --- | --- | --- |")
    body.append(f"| Solution Owner / Product | {md.PLACEHOLDER} | Roadmap, prioritisation, business sign-off |")
    body.append(f"| Lead Power BI Developer | {md.PLACEHOLDER} | Semantic model + report ownership |")
    body.append(f"| Data Engineering Lead | {md.PLACEHOLDER} | Dataflow ownership, upstream tables |")
    body.append(f"| Operations / Support | {md.PLACEHOLDER} | Refresh monitoring, incident response |")
    body.append(f"| Business SMEs | {md.PLACEHOLDER} | Metric definitions, glossary upkeep |")
    body.append("")

    body.append("\n## Assumptions and Constraints\n")
    body.append("- Dataflows refresh on a scheduled cadence in their owning workspace; the semantic model refresh is gated on dataflow completion (see [`ops/runbook.md`](../ops/runbook.md) and [`orchestration/`](../orchestration/)).")
    if cfg.solution.calendar_summary:
        body.append(f"- {cfg.solution.calendar_summary}")
    body.append("- Storage mode is Import; there is no DirectQuery on this model.")
    body.append("- Sensitivity labels and workspace-level access control are configured outside this repository.")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# End-to-end lineage
# ---------------------------------------------------------------------------
def render_lineage(lin: Lineage, cfg: Config) -> str:
    body = [md.HEADER, "# End-to-End Data Lineage\n"]
    body.append(md.section_purpose(
        "Traces every model table and report page back to its upstream dataflow and source system. Used to assess change-impact for upstream changes.",
        "Power BI Developers", "Data Engineers", "Operations/Support",
    ))

    df_nodes: dict[str, str] = {}
    for ref in lin.dataflow_refs.values():
        df_nodes[ref.dataflow_id] = _mermaid_id("DF_" + ref.dataflow_id)
    table_nodes = {t.name: _mermaid_id("T_" + t.name) for t in lin.model.tables}
    page_nodes: dict[str, str] = {}
    for r in lin.reports:
        for p in r.pages:
            label = p.display_name or p.folder
            page_nodes[label] = _mermaid_id("P_" + label)

    body.append("\n## Lineage Diagram\n")
    body.append(
        f"_Diagram shows distinct upstream platforms → referenced dataflows → "
        f"the {len(lin.model.tables)} model tables → report pages → orchestration flows. "
        "For brevity, only tables and pages connected via traceable references are drawn._\n"
    )
    body.append("```mermaid")
    body.append("flowchart LR")
    body.append("  subgraph SRC[Upstream]")
    if cfg.data_sources:
        for ds in cfg.data_sources:
            body.append(f"    {_mermaid_id('SRC_' + ds.name)}[(\"{ds.name}\")]")
    else:
        body.append("    UPSTREAM[(Upstream)]")
    body.append("  end")
    body.append("  subgraph DF[Dataflows]")
    for ref in lin.dataflow_refs.values():
        nid = df_nodes[ref.dataflow_id]
        label = lin.short_id_to_name.get(ref.dataflow_id, f"DF {ref.short_id}")
        body.append(f"    {nid}[\"{label}\"]")
    body.append("  end")
    body.append("  subgraph M[Semantic model]")
    drawn_tables: set[str] = set()
    for t in lin.model.tables:
        if t.name in lin.table_to_dataflows or t.name in lin.table_to_pages:
            body.append(f"    {table_nodes[t.name]}([\"{t.name}\"])")
            drawn_tables.add(t.name)
    body.append("  end")
    body.append("  subgraph R[Reports]")
    drawn_pages: set[str] = set()
    referenced_pages = {p for pages in lin.table_to_pages.values() for p in pages}
    for label, nid in page_nodes.items():
        if label in referenced_pages:
            body.append(f"    {nid}[/\"{label}\"/]")
            drawn_pages.add(label)
    body.append("  end")
    if lin.orchestration_flows:
        body.append("  subgraph ORC[Orchestration]")
        for f in lin.orchestration_flows:
            body.append(f"    {_mermaid_id('FL_' + f.name)}[[\"{f.name}\"]]")
        body.append("  end")

    if cfg.data_sources:
        for ds in cfg.data_sources:
            body.append(f"  {_mermaid_id('SRC_' + ds.name)} --> DF")
    else:
        body.append("  UPSTREAM --> DF")
    for tname, refs in lin.table_to_dataflows.items():
        if tname not in drawn_tables:
            continue
        for key in sorted(refs):
            df_id = key.split("::", 1)[1]
            if df_id in df_nodes:
                body.append(f"  {df_nodes[df_id]} --> {table_nodes[tname]}")
    for tname, pages in lin.table_to_pages.items():
        if tname not in drawn_tables:
            continue
        for page_label in sorted(pages):
            if page_label not in drawn_pages:
                continue
            body.append(f"  {table_nodes[tname]} --> {page_nodes[page_label]}")
    if lin.orchestration_flows:
        for f in lin.orchestration_flows:
            body.append(f"  {_mermaid_id('FL_' + f.name)} -.->|triggers| DF")
            body.append(f"  {_mermaid_id('FL_' + f.name)} -.->|refreshes| M")
    body.append("```")
    body.append("")

    body.append("\n## Textual Description\n")
    if cfg.narratives.lineage_narrative:
        for i, bullet in enumerate(cfg.narratives.lineage_narrative, 1):
            body.append(f"{i}. {bullet}")
    else:
        body.append(f"_{md.PLACEHOLDER} — populate `[narratives].lineage_narrative` in `model-docs/.docgen.toml`._")
    body.append("")

    body.append("\n## Dependency Table\n")
    body.append("| Component | Type | Depends On |")
    body.append("| --- | --- | --- |")
    for t in lin.model.tables:
        deps = sorted(lin.table_to_dataflows.get(t.name, []))
        if deps:
            dep_label = ", ".join(
                lin.short_id_to_name.get(d.split("::", 1)[1], d.split("::", 1)[1][:8] + "…")
                for d in deps
            )
        elif t.is_calculation_group:
            dep_label = "_calculation group (DAX)_"
        elif t.is_calculated:
            dep_label = "_calculated table (DAX)_"
        else:
            dep_label = "_no traceable upstream — selector / parameter / static table_"
        body.append(f"| `{t.name}` | model table | {md.md_escape_pipe(dep_label)} |")
    for ref in lin.dataflow_refs.values():
        df_label = lin.short_id_to_name.get(ref.dataflow_id, f"DF {ref.short_id}")
        consumers = ", ".join(sorted(ref.consumers))
        body.append(f"| `{df_label}` | dataflow | upstream platform; consumers: {md.md_escape_pipe(consumers)} |")
    for f in lin.orchestration_flows or []:
        targets = ", ".join(
            f"{t.kind} {(lin.short_id_to_name.get(t.object_id) or t.object_id[:8])}"
            for t in f.refresh_targets
        )
        body.append(f"| `{f.name}` | orchestration flow | refreshes: {md.md_escape_pipe(targets) or '-'} |")
    body.append("")

    body.append("\n## Change Impact Notes\n")
    if cfg.narratives.change_impact_notes:
        for note in cfg.narratives.change_impact_notes:
            body.append(f"- {note}")
    else:
        body.append(f"- {md.PLACEHOLDER} _(populate `[narratives].change_impact_notes` in `model-docs/.docgen.toml`)_")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------
def render_changelog(cfg: Config) -> str:
    display = cfg.solution.display_name or "the solution"
    return f"""{md.HEADER}# Change Log

This file tracks notable changes to {display} and its documentation. Entries
follow the convention used by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [{md.TODAY}] — Initial documentation generation

- **Added:** First end-to-end documentation set generated from the PBIP
  repository by `scripts/docgen/generate.py`. Covers `model-docs/README.md`,
  `model-docs/architecture/overview.md`, `model-docs/lineage/lineage.md`, `model-docs/model/`,
  `model-docs/measures/`, `model-docs/glossary.md`, `model-docs/data-sources/`,
  `model-docs/dataflows/`, `model-docs/orchestration/`, `model-docs/reports/`, `model-docs/app/`,
  `model-docs/ops/runbook.md`, and `model-docs/ReleaseNotes.md`.
- **Added:** Documentation generator package `scripts/docgen/` with TMDL,
  PBIR, dataflow, orchestration, lineage, and validation modules.
- **Added:** Per-repo configuration at `model-docs/.docgen.toml` carrying solution
  display name, workspace IDs, narratives, acronyms, and data-source detail.
- **Author:** {md.PLACEHOLDER}
"""


__all__ = [
    "render_readme",
    "render_architecture",
    "render_lineage",
    "render_changelog",
]
