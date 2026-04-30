"""Render Phase 1 inventory documents:

* docs/README.md
* docs/architecture/overview.md
* docs/lineage/lineage.md
* docs/CHANGELOG.md
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import md
from .lineage import Lineage


def _exec_summary_bullets(lin: Lineage) -> list[str]:
    model = lin.model
    table_count = len(model.tables)
    measure_count = sum(len(t.measures) for t in model.tables)
    column_count = sum(len(t.columns) for t in model.tables)
    rel_count = len(model.relationships)
    expr_count = len(model.expressions)
    role_count = len(model.roles)
    page_count = len(lin.report.pages)
    visual_count = sum(len(p.visuals) for p in lin.report.pages)
    df_ref_count = len(lin.dataflow_refs)
    df_export_count = len(lin.dataflows)
    folders = Counter()
    for t in model.tables:
        for m in t.measures:
            top = (m.display_folder.split("\\")[0] if m.display_folder else "") or "(none)"
            folders[top] += 1

    bullets = [
        f"**Solution name:** `{model.name}` (semantic model + thin report).",
        f"**Storage mode:** Import (Power Query M against Power BI Dataflows and SharePoint/Excel sources); compatibility level `{model.compatibility_level or 'unknown'}`.",
        f"**Semantic model size:** {table_count} tables, {column_count} columns, {measure_count:,} measures, {rel_count} relationships, {expr_count} shared expressions.",
        f"**Row-Level Security:** {role_count} role(s) defined — see {md.link('semantic model documentation', 'model/')}.",
        f"**Report:** {page_count} pages with ~{visual_count} visuals; built as a thin report on the same `.pbip` semantic model.",
        f"**Upstream dataflows referenced:** {df_ref_count} unique dataflow(s) across one primary workspace `{lin.primary_workspace_id or '<unknown>'}` (plus optional secondary workspaces).",
        f"**Dataflow JSON exports available:** {df_export_count} of the referenced dataflows have been exported under [`dataflows/`](../dataflows/) for full M-code documentation.",
        "**Primary upstream platform:** Azure Databricks (`beam_prod` catalog) via Power BI Dataflows; supplemented by SharePoint Online (Excel manual inputs) and Adobe Analytics.",
        "**Calendar model:** custom 4-4-5 fiscal calendar with `Calendar`, `RefCalendar`, and `DailyCalendar` providing fiscal week / period / quarter / year alignment, including LY and LW comparator relationships.",
        "**Key business domains:** Sales (Orders & Dispatch — SRIV / SREV / GSM / FPP / RP); Margin (incl. Buying Margin & Markdown); Stock (incl. MP Stock); Forecast (Budget / QRF / FC / projection scenarios); Returns; Online Customer Metrics; Footfall & Transactions.",
        f"**Top measure folders by count:** "
        + ", ".join(f"`{name}` ({n})" for name, n in folders.most_common(5)),
        f"**Documentation regenerated:** `{md.TODAY}` from PBIP source. Run `python -m scripts.docgen.generate` to refresh.",
        "**Audience:** Power BI Developers, Data Engineers, Operations/Support, Product Managers, Business End Users — see the audience line at the top of each document.",
    ]
    return bullets


def render_readme(lin: Lineage) -> str:
    bullets = _exec_summary_bullets(lin)
    body = []
    body.append(md.HEADER)
    body.append(f"# {lin.model.name} — Documentation Home\n")
    body.append(md.section_purpose(
        "Top-level entry point for the FH&B Weekly Power BI solution. Use the document guide to navigate to the audience-specific sections you need.",
        "All audiences (Power BI Developers, Data Engineers, Operations/Support, Product Managers, Business End Users)",
    ))
    body.append("\n## Solution Name and Purpose\n")
    body.append(
        f"`{lin.model.name}` is the **FH&B Weekly** reporting solution covering Furniture, Homewares & Beauty (FH&B) sales, margin, stock, and forecast performance on a weekly cadence. It comprises a Power BI semantic model (`.pbip` / TMDL), a connected thin report, and a published Power BI App that serves the FH&B finance and trading community.\n"
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
    body.append(f"| {md.link('End-to-end lineage', 'lineage/lineage.md')} | Developers, Data Engineers | Mermaid lineage diagram from upstream sources through to report pages. |")
    body.append(f"| {md.link('Semantic model', f'model/{md.safe_filename(lin.model.name)}.md')} | Developers, Data Engineers | Tables, columns, relationships, calculated tables, RLS, time-intelligence. |")
    body.append(f"| {md.link('Measures index', 'measures/README.md')} | Developers, Business End Users | Index of all DAX measures, organised by display folder. |")
    body.append(f"| {md.link('Glossary', 'glossary.md')} | Business End Users, PMs | Plain-language definitions of business terms, KPIs, and acronyms. |")
    body.append(f"| {md.link('Data sources', 'data-sources/')} | Data Engineers, Ops | Per-source connection, refresh, and ownership detail. |")
    body.append(f"| {md.link('Dataflows', 'dataflows/')} | Data Engineers, Developers | One file per upstream dataflow with M code, entities, dependencies. |")
    body.append(f"| {md.link('Reports', 'reports/')} | Developers, End Users | Per-page descriptions, slicers, filters, bookmarks, usage tips. |")
    body.append(f"| {md.link('Power BI App', 'app/')} | End Users, Ops, PMs | App contents, navigation, release process. |")
    body.append(f"| {md.link('Operations runbook', 'ops/runbook.md')} | Operations/Support | Refresh workflow, monitoring, playbooks, contacts. |")
    body.append(f"| {md.link('Change log', 'CHANGELOG.md')} | All | History of solution and documentation changes. |")
    body.append(f"| {md.link('Release notes template', 'ReleaseNotes.md')} | PMs, End Users | Template populated each release. |")
    body.append("")

    body.append("\n## Revision Info\n")
    body.append(f"- Last regenerated: `{md.TODAY}`")
    body.append(f"- Solution version: {md.PLACEHOLDER}")
    body.append(f"- Generator: `scripts/docgen/` (run `python -m scripts.docgen.generate`)")
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
def _mermaid_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)[:60] or "n"


def render_architecture(lin: Lineage) -> str:
    model = lin.model
    body = [md.HEADER, f"# Architecture & Solution Overview\n"]
    body.append(md.section_purpose(
        "High-level view of how data flows from upstream platforms through dataflows into the semantic model, the report, and the published app.",
        "Power BI Developers", "Data Engineers", "Operations/Support", "Product Managers",
    ))

    # Architecture diagram
    body.append("\n## Architecture Diagram\n")
    body.append("```mermaid")
    body.append("flowchart LR")
    body.append("  subgraph SRC[\"Upstream platforms\"]")
    body.append("    DBR[(Azure Databricks<br/>beam_prod)]")
    body.append("    SP[(SharePoint Online<br/>Excel manual inputs)]")
    body.append("    EDW[(EDW / Sql.Database)]")
    body.append("    ADO[(Adobe Analytics)]")
    body.append("  end")
    body.append("  subgraph DF[\"Power BI Dataflows (Workspace " + (lin.primary_workspace_id[:8] if lin.primary_workspace_id else "primary") + ")\"]")
    # Use exported dataflow names
    df_nodes: list[str] = []
    for d in lin.dataflows[:24]:
        nid = _mermaid_id("DF_" + d.name)
        body.append(f"    {nid}[\"{d.name}\"]")
        df_nodes.append(nid)
    body.append("  end")
    body.append(f"  subgraph SM[\"Semantic Model — {model.name}\"]")
    body.append(f"    SMNODE[/\"{len(model.tables)} tables · {sum(len(t.measures) for t in model.tables):,} measures\"/]")
    body.append("  end")
    body.append("  subgraph RPT[\"Thin Report\"]")
    body.append(f"    RPTNODE[/\"{len(lin.report.pages)} pages\"/]")
    body.append("  end")
    body.append("  subgraph APP[\"Power BI App\"]")
    body.append("    APPNODE[/\"{{PLACEHOLDER}} App\"/]")
    body.append("  end")
    body.append("  DBR --> DF")
    body.append("  SP --> DF")
    body.append("  EDW --> DF")
    body.append("  ADO --> DF")
    body.append("  DF --> SM")
    body.append("  SM --> RPT")
    body.append("  RPT --> APP")
    body.append("```")
    body.append("")

    body.append("\n## Component Descriptions\n")
    body.append("- **Upstream platforms.** Source systems that hold the raw data: an Azure Databricks workspace (`beam_prod` catalog, schemas `finance_azlab_prod` and `masterdata_present_prod`) accessed via the `Databricks.Catalogs` connector; SharePoint Online sites containing Excel and CSV manual inputs; an EDW SQL endpoint (`Sql.Database`) used by selected dimension dataflows; and Adobe Analytics for online customer metrics.")
    body.append(f"- **Power BI Dataflows.** {len(lin.dataflows)} exported dataflows under [`dataflows/`](../../dataflows/) prepare conformed entities for the semantic model. Each dataflow is documented per [`docs/dataflows/`]({md.link('dataflows', '../dataflows/').split('](')[1].rstrip(')')}).")
    body.append(f"- **Semantic model (`{model.name}`).** Import-mode tabular model containing {len(model.tables)} tables (fact + dim + bridge + selector tables for field parameters), {sum(len(t.measures) for t in model.tables):,} DAX measures, and {len(model.relationships)} relationships. Built and version-controlled as a `.pbip` project (PBIP/TMDL).")
    body.append(f"- **Thin report.** {len(lin.report.pages)} pages organised into trading scorecard, channel/BU performance, derisk forecasts, returns, online metrics, and reference areas. The report is connected live to the published semantic model.")
    body.append("- **Power BI App.** Distribution wrapper packaging the report (and any partner reports) for the FH&B finance and trading community. App-specific metadata is captured in [`docs/app/`](../app/).")
    body.append("")

    body.append("\n## Scope Boundaries\n")
    body.append("**Included.**")
    body.append("- The `.pbip` solution under [`src/semantic-model/`](../../src/semantic-model/), associated TMDL definitions, and the connected report.")
    body.append("- All dataflows referenced by the semantic model in the primary workspace.")
    body.append("- Upstream Databricks tables consumed via dataflows.")
    body.append("")
    body.append("**Excluded.**")
    body.append("- Source systems that feed Databricks (operational ERP / OMS / e-commerce platforms) — out of scope for this solution.")
    body.append("- Adjacent reports owned by other teams that may consume the same dataflows.")
    body.append("- Workspace-level access control configuration (managed via Entra ID groups).")
    body.append("")

    body.append("\n## Key Stakeholders\n")
    body.append("| Role | Team / Person | Responsibility |")
    body.append("| --- | --- | --- |")
    body.append(f"| Solution Owner / Product | {md.PLACEHOLDER} | Roadmap, prioritisation, business sign-off |")
    body.append(f"| Lead Power BI Developer | {md.PLACEHOLDER} | Semantic model + report ownership |")
    body.append(f"| Data Engineering Lead | {md.PLACEHOLDER} | Dataflow ownership, upstream Databricks tables |")
    body.append(f"| Operations / Support | {md.PLACEHOLDER} | Refresh monitoring, incident response |")
    body.append(f"| Business SMEs | {md.PLACEHOLDER} | Metric definitions, glossary upkeep |")
    body.append("")

    body.append("\n## Assumptions and Constraints\n")
    body.append("- Dataflows refresh on a scheduled cadence in their owning workspace; the semantic model refresh is gated on dataflow completion (see [`ops/runbook.md`](../ops/runbook.md)).")
    body.append("- The custom fiscal calendar is the single source of truth for week / period alignment; any new fact table must join via `fiscalWeek`.")
    body.append("- Storage mode is Import; there is no DirectQuery on this model.")
    body.append("- Sensitivity labels and workspace-level access control are configured outside this repository.")
    body.append("- Manual SharePoint inputs (forecasts, commentary, availability) are owned by business users; their availability gates the corresponding dataflow refreshes.")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# End-to-end lineage
# ---------------------------------------------------------------------------
def render_lineage(lin: Lineage) -> str:
    body = [md.HEADER, "# End-to-End Data Lineage\n"]
    body.append(md.section_purpose(
        "Traces every model table and report page back to its upstream dataflow and source system. Used to assess change-impact for upstream changes.",
        "Power BI Developers", "Data Engineers", "Operations/Support",
    ))

    # Build node sets
    df_nodes: dict[str, str] = {}
    for ref in lin.dataflow_refs.values():
        nid = _mermaid_id("DF_" + ref.dataflow_id)
        label = lin.short_id_to_name.get(ref.dataflow_id, f"DF {ref.short_id}…")
        df_nodes[ref.dataflow_id] = nid
    table_nodes = {t.name: _mermaid_id("T_" + t.name) for t in lin.model.tables}
    page_nodes = {
        (p.display_name or p.folder): _mermaid_id("P_" + (p.display_name or p.folder))
        for p in lin.report.pages
    }

    body.append("\n## Lineage Diagram\n")
    body.append(
        "_Diagram shows distinct upstream platforms → referenced dataflows → "
        f"the {len(lin.model.tables)} model tables → report pages. "
        "For brevity, only tables and pages connected via traceable references "
        "are drawn; unreferenced selector / parameter tables are listed in the "
        "tables further below._\n"
    )
    body.append("```mermaid")
    body.append("flowchart LR")
    body.append("  subgraph SRC[Upstream]")
    body.append("    DBR[(Databricks)]")
    body.append("    SP[(SharePoint)]")
    body.append("    EDW[(EDW SQL)]")
    body.append("    ADO[(Adobe)]")
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
    body.append("  subgraph R[Report]")
    drawn_pages: set[str] = set()
    for label, nid in page_nodes.items():
        if label in {p for pages in lin.table_to_pages.values() for p in pages}:
            body.append(f"    {nid}[/\"{label}\"/]")
            drawn_pages.add(label)
    body.append("  end")
    body.append("  DBR --> DF")
    body.append("  SP --> DF")
    body.append("  EDW --> DF")
    body.append("  ADO --> DF")
    # Dataflow → Table edges
    for tname, refs in lin.table_to_dataflows.items():
        if tname not in drawn_tables:
            continue
        for key in refs:
            df_id = key.split("::", 1)[1]
            if df_id in df_nodes:
                body.append(f"  {df_nodes[df_id]} --> {table_nodes[tname]}")
    # Table → Page edges (one per pair)
    for tname, pages in lin.table_to_pages.items():
        if tname not in drawn_tables:
            continue
        for page_label in pages:
            if page_label not in drawn_pages:
                continue
            body.append(f"  {table_nodes[tname]} --> {page_nodes[page_label]}")
    body.append("```")
    body.append("")

    body.append("\n## Textual Description\n")
    body.append(
        "1. **Upstream platforms** — Azure Databricks (`beam_prod` catalog) is the primary store, "
        "providing fact_* and dim_* tables under the `finance_azlab_prod` and `masterdata_present_prod` "
        "schemas; SharePoint Online provides Excel/CSV manual inputs (forecasts, trading commentary, "
        "manual availability); an EDW SQL endpoint provides selected dimensions; Adobe Analytics provides "
        "online customer metrics."
    )
    body.append(
        "2. **Power BI Dataflows** in the primary workspace clean and conform these inputs into entities. "
        "Naming convention: `<area>.<x>` — see [`docs/dataflows/`](../dataflows/)."
    )
    body.append(
        "3. **Shared expressions** in `expressions.tmdl` create intermediate tables that combine multiple "
        "dataflow entities (e.g. `SRIVcompositeOnlineForecastDispatchActualised` joins forecast and actuals)."
    )
    body.append(
        "4. **Semantic model tables** load from a single dataflow entity per table partition; "
        f"{len(lin.model.relationships)} relationships connect facts to conformed dimensions "
        "(`Calendar`, `Stores`, `Channels`, `Products`)."
    )
    body.append(
        "5. **Report pages** consume measures from the model; field references are tracked per visual to "
        "support the measure → page traceability used elsewhere in this documentation set."
    )
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
    body.append("")

    body.append("\n## Change Impact Notes\n")
    body.append("- **Calendar / RefCalendar / DailyCalendar** (dataflow `132e6a64-…`) are referenced by virtually every fact table via `fiscalWeek` — any change to the calendar grain or week numbering propagates everywhere.")
    body.append("- **Shared expressions** in `expressions.tmdl` (`Intermediate tables` query group) materialise joins between forecast and actuals; renaming or changing the schema of any source dataflow entity will break these.")
    body.append("- **Bridge tables** (`BridgeSalesType`, `BridgeMPPlan`) drive cross-filtering; changing their grain will affect every measure that depends on `SalesType`/`SalesRecognitionType` filtering.")
    body.append("- **`1_Measures`** is the centralised measure host table; its measures are referenced by hundreds of visuals — renames must be applied via Tabular Editor or via search-and-replace across both the TMDL and PBIR.")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------
def render_changelog() -> str:
    return f"""{md.HEADER}# Change Log

This file tracks notable changes to the FH&B Weekly Power BI solution and its
documentation. Entries follow the convention used by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [{md.TODAY}] — Initial documentation generation

- **Added:** First end-to-end documentation set generated from the PBIP repository
  by `scripts/docgen/generate.py`. Covers `docs/README.md`,
  `docs/architecture/overview.md`, `docs/lineage/lineage.md`,
  `docs/model/`, `docs/measures/`, `docs/glossary.md`, `docs/data-sources/`,
  `docs/dataflows/`, `docs/reports/`, `docs/app/`, `docs/ops/runbook.md`,
  and `docs/ReleaseNotes.md`.
- **Added:** Documentation generator package `scripts/docgen/` with TMDL, PBIR,
  dataflow, lineage, and validation modules.
- **Author:** {md.PLACEHOLDER}
"""


__all__ = [
    "render_readme",
    "render_architecture",
    "render_lineage",
    "render_changelog",
]
