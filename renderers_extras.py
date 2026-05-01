"""Render data-source, dataflow, glossary, report, app, runbook, release-notes docs.

All repo-specific narrative comes from ``docs/.docgen.toml`` via
:mod:`scripts.docgen.config`. Renderers fall back to neutral placeholders
when a config field is empty.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from . import md
from . import tmdl
from . import dataflow as dfmod
from . import pbir as pbirmod
from .config import Config
from .lineage import Lineage


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------
def render_glossary(lin: Lineage, cfg: Config) -> str:
    display = cfg.solution.display_name or lin.model.name
    body = [md.HEADER, "# Business Glossary & Definitions\n"]
    body.append(md.section_purpose(
        f"Plain-language definitions of the business terms, KPIs, and acronyms used across the {display} solution.",
        "Business End Users", "Product Managers", "Power BI Developers",
    ))

    body.append("\n## Business Terms\n")
    terms: dict[str, str] = {}
    for t in lin.model.tables:
        if t.description and not t.is_calculation_group:
            terms[t.name] = t.description.strip()
    for t in lin.model.tables:
        for c in t.columns:
            if c.description and len(c.description) > 8:
                key = f"{t.name}[{c.name}]"
                terms.setdefault(key, c.description.strip())
    if terms:
        for k in sorted(terms):
            body.append(f"- **{k}** — {md.md_escape_pipe(terms[k])}")
    else:
        body.append(f"_{md.PLACEHOLDER} — populate with business term definitions used in the report._")
    body.append("")

    body.append("\n## Key Metrics\n")
    headline = list(cfg.headline_metrics)
    if headline:
        for name in headline:
            body.append(f"- **{name}** — see corresponding measure(s) in [`docs/measures/`](measures/) and acronym definitions below.")
    else:
        body.append(f"_{md.PLACEHOLDER} — populate `[headline_metrics].names` in `docs/.docgen.toml` to spotlight key KPIs._")
    body.append("")

    body.append("\n## Acronyms & Abbreviations\n")
    if cfg.acronyms:
        body.append("| Term | Definition |")
        body.append("| --- | --- |")
        for k in sorted(cfg.acronyms):
            body.append(f"| `{k}` | {md.md_escape_pipe(cfg.acronyms[k])} |")
    else:
        body.append(f"_{md.PLACEHOLDER} — populate `[acronyms]` in `docs/.docgen.toml`._")
    body.append("")

    body.append("\n## Maintenance Note\n")
    body.append("Update this glossary whenever:")
    body.append("- A new measure is added or renamed in the semantic model.")
    body.append("- A new business term enters use in the report (page titles, slicer labels, bookmark names).")
    body.append("- A clarification request is raised by a business user — capture the answer here so the next reader benefits.")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Data sources (one file per upstream system) — config-driven
# ---------------------------------------------------------------------------
def render_data_sources(lin: Lineage, cfg: Config) -> dict[str, str]:
    files: dict[str, str] = {}
    sources = cfg.data_sources

    # Index README
    idx = [md.HEADER, "# Data Sources\n"]
    idx.append(md.section_purpose(
        "Per-source connection, refresh, and ownership detail. Credentials are never recorded here — only the connection mechanism.",
        "Data Engineers", "Operations/Support",
    ))
    if sources:
        idx.append("\n| Source | File |\n| --- | --- |")
        for ds in sources:
            fn = md.safe_filename(ds.name) + ".md"
            idx.append(f"| {ds.name} | {md.link(fn, fn)} |")
        idx.append("")
    else:
        idx.append(f"\n_{md.PLACEHOLDER} — populate `[[data_sources]]` entries in `docs/.docgen.toml` so the engine emits one file per upstream system._")
    files["README.md"] = "\n".join(idx)

    for ds in sources:
        body = [md.HEADER, f"# Data Source — {ds.name}\n"]
        body.append(md.section_purpose(
            f"Connection, schedule, and dependency detail for `{ds.name}`.",
            "Data Engineers", "Operations/Support",
        ))
        body.append("\n## Source Description\n")
        body.append(ds.purpose or md.PLACEHOLDER)
        body.append("\n## Connection Details\n")
        body.append(f"- **Mechanism:** {ds.mechanism or md.PLACEHOLDER}")
        body.append(f"- **Host / endpoint:** {ds.host or md.PLACEHOLDER}")
        body.append("- **Credentials:** stored in the Power BI service against the dataflow / dataset; **never** committed to this repository. Replace with `{{CREDENTIAL_PLACEHOLDER}}` when documenting locally.")
        body.append("\n## Data Extracted\n")
        if ds.objects:
            body.append("| Object | Description | Type |")
            body.append("| --- | --- | --- |")
            for obj in ds.objects:
                a = obj[0] if len(obj) > 0 else ""
                b = obj[1] if len(obj) > 1 else ""
                c = obj[2] if len(obj) > 2 else ""
                body.append(f"| {a} | {md.md_escape_pipe(b)} | {c} |")
        else:
            body.append(f"_{md.PLACEHOLDER} — list extracted objects in the source's `objects` array in `docs/.docgen.toml`._")
        body.append("\n## Data Volume & Performance\n")
        body.append(f"- Approximate row count: {md.PLACEHOLDER}")
        body.append("- Query folding: connector-dependent — SQL-style connectors (`Sql.Database`, `Databricks.Catalogs`) fold; file-based connectors (SharePoint, Excel, CSV) do not fold.")
        body.append("- Incremental refresh policy: see [`docs/dataflows/`](../dataflows/) for per-dataflow refresh policies.")
        body.append("\n## Refresh Schedule\n")
        body.append(f"- **Frequency:** {ds.freshness or md.PLACEHOLDER}")
        body.append(f"- **Trigger mechanism:** Power BI Service scheduled refresh and/or orchestration flows — see [`docs/orchestration/`](../orchestration/) and [`docs/ops/runbook.md`](../ops/runbook.md).")
        body.append("\n## Data Quality & Transformation Notes\n")
        body.append("- All transformation logic lives in the dataflows — see [`docs/dataflows/`](../dataflows/).")
        body.append("- Specific data-quality rules / known data caveats: " + md.PLACEHOLDER)
        body.append("\n## Owner & Contact\n")
        body.append(f"- Source system owner: {md.PLACEHOLDER}")
        body.append(f"- Documentation owner: {md.PLACEHOLDER}")
        body.append("\n## Dependencies\n")
        # Detect dataflows that use any of the configured connector tokens.
        tokens = ds.connector_match or []
        consumers: list[str] = []
        if tokens:
            for d in lin.dataflows:
                df_sources = d.primary_data_sources()
                if any(tok in df_sources for tok in tokens):
                    consumers.append(d.name)
        if consumers:
            for c in sorted(consumers):
                body.append(f"- {md.link(c, '../dataflows/' + md.safe_filename(c) + '.md')}")
        else:
            body.append("- See [`docs/dataflow-references.md`](../dataflow-references.md) for the full dataflow inventory.")
        body.append("")
        files[md.safe_filename(ds.name) + ".md"] = "\n".join(body)
    return files


# ---------------------------------------------------------------------------
# Dataflows (one file per exported JSON + cross-references)
# ---------------------------------------------------------------------------
def _summarise_m_steps(m_code: str) -> list[str]:
    """Produce a short bullet list summarising the major Power Query steps."""
    lines: list[str] = []
    for raw in m_code.splitlines():
        s = raw.strip()
        if not s.startswith("#\""):
            continue
        m = re.match(r'#"([^"]+)"\s*=', s)
        if not m:
            continue
        step = m.group(1)
        if step.startswith("Source") or step.lower() in {"navigation", "navigation 1", "navigation 2", "navigation 3"}:
            continue
        if step in lines:
            continue
        lines.append(step)
    return lines


def render_dataflows(lin: Lineage) -> dict[str, str]:
    files: dict[str, str] = {}
    # Index
    idx = [md.HEADER, "# Power BI Dataflows\n"]
    idx.append(md.section_purpose(
        "Per-dataflow documentation: input sources, output entities, transformation steps, and downstream dependencies.",
        "Data Engineers", "Power BI Developers",
    ))
    idx.append("\n| Dataflow | Entities | Queries | Sources |")
    idx.append("| --- | --- | --- | --- |")
    for d in lin.dataflows:
        fn = md.safe_filename(d.name) + ".md"
        idx.append(
            f"| {md.link(d.name, fn)} | {len(d.entities)} | {len(d.queries)} | "
            f"{md.md_escape_pipe(', '.join(d.primary_data_sources()) or '-')} |"
        )
    idx.append(f"\n_Total: {len(lin.dataflows)} dataflow(s) exported under [`dataflows/`](../../dataflows/)._\n")
    idx.append(
        "\nFor the authoritative ID-to-name mapping see "
        f"{md.link('docs/dataflow-references.md', '../dataflow-references.md')}.\n"
    )
    files["README.md"] = "\n".join(idx)

    for d in lin.dataflows:
        body = [md.HEADER, f"# Dataflow — {d.name}\n"]
        body.append(md.section_purpose(
            f"Power BI dataflow `{d.name}` — purpose, inputs, outputs, transformations, and downstream consumers.",
            "Data Engineers", "Power BI Developers",
        ))
        body.append("\n## Dataflow Description\n")
        if d.description:
            body.append(d.description)
        else:
            body.append(md.UNKNOWN.replace("needs business input", "no description set in the dataflow JSON"))
        body.append(f"\n_Last modified (per export): `{d.modified_time or '-'}` · Source file: `{Path(d.source_file).name}`_\n")

        body.append("\n## Input Sources\n")
        body.append("| Source | Type | How accessed |")
        body.append("| --- | --- | --- |")
        for src in d.primary_data_sources():
            body.append(f"| `{src}` | connector | Power Query |")
        if not d.primary_data_sources():
            body.append("| _(no recognised connector — see queries below)_ | — | — |")
        body.append("")

        body.append("\n## Output Entities\n")
        body.append("| Entity | Attribute count | Refresh policy | Description |")
        body.append("| --- | --- | --- | --- |")
        for e in d.entities:
            body.append(
                f"| `{e.name}` | {len(e.attributes)} | `{e.refresh_policy or '-'}` | "
                f"{md.md_escape_pipe(e.description) or '-'} |"
            )
        body.append("")
        for e in d.entities:
            body.append(f"\n### Attributes — `{e.name}`\n")
            body.append("| Name | Type |")
            body.append("| --- | --- |")
            for a in e.attributes:
                body.append(f"| `{a.name}` | `{a.data_type}` |")
            body.append("")

        body.append("\n## Key Transformation Steps\n")
        for q in d.queries:
            if q.is_parameter:
                continue
            steps = _summarise_m_steps(q.expression)
            body.append(f"\n### Query `{q.name}`\n")
            if steps:
                for s in steps:
                    body.append(f"- {s}")
            else:
                body.append("- _Single navigation step (no further transformations)._")
            body.append("")

        body.append("\n## Parameter Usage\n")
        params = [q for q in d.queries if q.is_parameter]
        if params:
            body.append("| Parameter | Default | Purpose |")
            body.append("| --- | --- | --- |")
            for q in params:
                default = q.expression.strip().splitlines()[0]
                body.append(f"| `{q.name}` | `{md.md_escape_pipe(default)[:80]}` | connector parameter |")
        else:
            body.append("_No parameter queries detected._")
        body.append("")

        body.append("\n## Schedule and Partitioning\n")
        body.append(f"- **Refresh schedule:** {md.PLACEHOLDER} (set in the Power BI Service against the dataflow).")
        body.append("- **Enhanced compute engine:** " + md.PLACEHOLDER)
        body.append("- **Incremental refresh policy:** detected from CDM JSON — see the *Refresh policy* column on each entity above (`FullRefreshPolicy` = full reload).")
        body.append("")

        body.append("\n## Dependencies\n")
        # downstream consumers (model tables)
        consumers: set[str] = set()
        for ref in lin.dataflow_refs.values():
            df_name = lin.short_id_to_name.get(ref.dataflow_id)
            if df_name == d.name:
                consumers.update(ref.consumers)
        body.append("**Downstream consumers (semantic model):**")
        if consumers:
            for c in sorted(consumers):
                body.append(f"- `{c}`")
        else:
            body.append(f"- {md.UNKNOWN.replace('needs business input', 'no traceable consumers found in the model — could be referenced by another solution or via cross-workspace expression')}")
        body.append("\n**Upstream:** see *Input sources* above.")
        body.append("")

        body.append("\n## Error Handling\n")
        body.append(f"- {md.PLACEHOLDER} — known failure modes / fallback behaviour.")
        body.append("")

        body.append("\n## Owner / Developer\n")
        body.append(f"- Owner: {md.PLACEHOLDER}")
        body.append(f"- Developer: {md.PLACEHOLDER}")
        body.append("")

        files[md.safe_filename(d.name) + ".md"] = "\n".join(body)
    return files


# ---------------------------------------------------------------------------
# Reports (single file per `.pbir` report)
# ---------------------------------------------------------------------------
def _format_visual_row(v: pbirmod.Visual) -> str:
    title = v.title or v.visual_type
    fields = ", ".join(f"`{f.entity}.{f.member}`" for f in v.fields[:5])
    if len(v.fields) > 5:
        fields += f" … ({len(v.fields)} total)"
    return f"| `{v.visual_type}` | {md.md_escape_pipe(title) or '-'} | {md.md_escape_pipe(fields) or '-'} |"


def render_reports(lin: Lineage) -> dict[str, str]:
    files: dict[str, str] = {}
    reports = lin.reports or [lin.report]

    for rep in reports:
        rname = rep.name or lin.model.name
        body = [md.HEADER, f"# Report — {rname}\n"]
        body.append(md.section_purpose(
            f"Per-page documentation for the `{rname}` thin report. Each section explains intent, slicers, filters, and the visuals on the page.",
            "Power BI Developers", "Business End Users",
        ))

        body.append("\n## Report Overview\n")
        body.append(f"- **Connected dataset:** same `.pbip` semantic model — `{lin.model.name}`.")
        body.append(f"- **Pages:** {len(rep.pages)}")
        body.append(f"- **Visuals:** {sum(len(p.visuals) for p in rep.pages)}")
        body.append(f"- **Target audience:** {md.PLACEHOLDER}")
        body.append(f"- **Business questions answered:** {md.PLACEHOLDER}")
        body.append("")

        body.append("\n## Pages Summary\n")
        body.append("| # | Page | Visuals | Page filters | Purpose |")
        body.append("| --- | --- | --- | --- | --- |")
        for i, p in enumerate(rep.pages, 1):
            body.append(
                f"| {i} | `{md.md_escape_pipe(p.display_name or p.folder)}` | {len(p.visuals)} | "
                f"{len(p.filters)} | {md.PLACEHOLDER} |"
            )
        body.append("")

        body.append("\n## Detailed Page Descriptions\n")
        for p in rep.pages:
            page_label = p.display_name or p.folder
            body.append(f"\n### `{page_label}`\n")
            body.append(f"_Internal name: `{p.name}` · {p.width}×{p.height} px · displayOption: `{p.display_option}`_\n")
            body.append(f"**Intent.** {md.PLACEHOLDER} (suggest 1–2 sentences capturing the story this page tells).")
            body.append("")
            if p.filters:
                body.append("**Page-level filters:**")
                for flt in p.filters:
                    ent = flt.field.entity or "(unbound)"
                    prop = flt.field.member or ""
                    body.append(f"- `{ent}[{prop}]` — {flt.type or 'Categorical'} ({flt.how_created or 'User'})")
                body.append("")
            slicers = [v for v in p.visuals if v.visual_type == "slicer"]
            if slicers:
                body.append("**Slicers:**")
                for s in slicers:
                    fields = ", ".join(f"`{f.entity}.{f.member}`" for f in s.fields)
                    body.append(f"- {md.md_escape_pipe(s.title) or '_(unlabelled)_'} — {fields or '_no field bound_'}")
                body.append("")
            non_slicer = [v for v in p.visuals if v.visual_type != "slicer"]
            if non_slicer:
                body.append("**Visuals:**")
                body.append("| Type | Title | Fields |")
                body.append("| --- | --- | --- |")
                for v in non_slicer:
                    body.append(_format_visual_row(v))
                body.append("")
            body.append("**Drill-through / cross-report links.** " + md.PLACEHOLDER)
            body.append("")
        body.append("\n## Bookmarks & Navigation\n")
        body.append("- Bookmark metadata not parsed by the documentation generator — populate manually if bookmarks are used. " + md.PLACEHOLDER)
        body.append("")
        body.append("\n## Usage Tips\n")
        body.append("- The report is a thin report connected live to the published semantic model: opening it in Power BI Desktop requires access to the workspace.")
        body.append(f"- {md.PLACEHOLDER} — add report-specific tips (comparator selectors, drill paths, filter conventions).")
        body.append("")
        body.append("\n## Known Issues or Exclusions\n")
        body.append(f"- {md.PLACEHOLDER}")
        body.append("")
        body.append("\n## Report-Specific Calculations\n")
        body.append("_Report-level measures are not parsed by the documentation generator. All measures live in the semantic model — see [`docs/measures/`](../measures/)._")
        body.append("")
        files[md.safe_filename(rname) + ".md"] = "\n".join(body)

    # Reports README index
    idx = [md.HEADER, "# Reports\n"]
    idx.append(md.section_purpose(
        "Index of report documentation files. Each file documents a single thin report.",
        "Power BI Developers", "Business End Users",
    ))
    idx.append("")
    for rep in reports:
        rname = rep.name or lin.model.name
        idx.append(f"- {md.link(rname, md.safe_filename(rname) + '.md')} — {len(rep.pages)} page(s)")
    idx.append("")
    files["README.md"] = "\n".join(idx)
    return files


# ---------------------------------------------------------------------------
# Power BI App
# ---------------------------------------------------------------------------
def render_app(lin: Lineage, cfg: Config) -> dict[str, str]:
    name = cfg.app.name or (cfg.solution.display_name + " App" if cfg.solution.display_name else "Power BI App")
    fn = md.safe_filename(name) + ".md"
    body = [md.HEADER, f"# Power BI App — {name}\n"]
    body.append(md.section_purpose(
        "App-level documentation: contents, navigation, settings, release process, and support contacts.",
        "Business End Users", "Operations/Support", "Product Managers",
    ))

    body.append("\n## App Overview\n")
    body.append(f"- **App name:** `{name}`")
    workspace = cfg.workspaces.dataset or cfg.workspaces.primary or lin.primary_workspace_id or md.PLACEHOLDER
    body.append(f"- **Workspace:** `{workspace}`")
    body.append(f"- **Purpose:** {cfg.app.purpose or md.PLACEHOLDER}")
    body.append(f"- **Intended audience:** {cfg.app.audience or md.PLACEHOLDER}")
    body.append("")

    body.append("\n## Included Content\n")
    body.append("| Report / Dashboard | Pages | Section in App |")
    body.append("| --- | --- | --- |")
    for rep in (lin.reports or [lin.report]):
        body.append(f"| `{rep.name}` | {len(rep.pages)} | {md.PLACEHOLDER} |")
    body.append("")

    body.append("\n## Navigation & Usage\n")
    body.append(f"- Users access the app from the Power BI Service (Apps → search for `{name}`).")
    body.append(f"- Default landing page: {md.PLACEHOLDER}.")
    body.append(f"- Recommended starting bookmark / view: {md.PLACEHOLDER}.")
    body.append("")

    body.append("\n## App Settings\n")
    body.append(f"- Persistent filters: {md.PLACEHOLDER}")
    body.append(f"- Copy permissions: {md.PLACEHOLDER}")
    body.append(f"- App audiences (Power BI per-user): {md.PLACEHOLDER}")
    body.append("")

    body.append("\n## Release Process\n")
    body.append("1. Developer commits PBIP changes to this repository on a feature branch.")
    body.append("2. Pull request review by the Lead Power BI Developer.")
    body.append("3. Merge to `main`; deploy to the Power BI workspace via deployment pipeline / manual publish.")
    body.append("4. Update the app via *Power BI Service → Workspace → Update app*.")
    body.append("5. Update [`docs/CHANGELOG.md`](../CHANGELOG.md) and [`docs/ReleaseNotes.md`](../ReleaseNotes.md).")
    body.append(f"6. Notify users via {md.PLACEHOLDER}.")
    body.append("")

    body.append("\n## Contact / Support\n")
    body.append(f"- App owner: {md.PLACEHOLDER}")
    body.append(f"- Support inbox: {md.PLACEHOLDER}")
    body.append(f"- Out-of-hours escalation: {md.PLACEHOLDER}")
    body.append("")
    files = {fn: "\n".join(body)}

    idx = [md.HEADER, "# Power BI Apps\n"]
    idx.append(md.section_purpose(
        "Index of Power BI App documentation files.",
        "Business End Users", "Operations/Support",
    ))
    idx.append(f"\n- {md.link(name, fn)}\n")
    files["README.md"] = "\n".join(idx)
    return files


# ---------------------------------------------------------------------------
# Operations runbook
# ---------------------------------------------------------------------------
def render_runbook(lin: Lineage, cfg: Config) -> str:
    body = [md.HEADER, "# Operations Runbook & Support Guide\n"]
    body.append(md.section_purpose(
        "Operational guide covering refresh workflow, monitoring, common-issue playbooks, contacts, and recovery.",
        "Operations/Support", "Power BI Developers", "Data Engineers",
    ))

    primary_ws = cfg.workspaces.primary or lin.primary_workspace_id
    dataset_ws = cfg.workspaces.dataset or lin.dataset_workspace_id or primary_ws

    body.append("\n## Operations Overview\n")
    body.append(f"- **Responsible team:** {md.PLACEHOLDER}")
    body.append(f"- **Support hours:** {md.PLACEHOLDER}")
    body.append(f"- **SLA targets:** {md.PLACEHOLDER}")
    body.append(f"- **Primary workspace:** `{primary_ws or md.PLACEHOLDER}`")
    if dataset_ws and dataset_ws != primary_ws:
        body.append(f"- **Dataset workspace:** `{dataset_ws}`")
    body.append("")

    body.append("\n## Refresh Workflow\n")
    body.append("End-to-end refresh sequence (see [`docs/orchestration/`](../orchestration/) for the per-flow detail):")
    body.append("1. **Upstream pipelines complete** — source platforms populate the consumed tables. Owner: " + md.PLACEHOLDER + ".")
    body.append(f"2. **Power BI Dataflows refresh** in the primary workspace (`{(primary_ws or md.PLACEHOLDER)[:8]}…`). {len(lin.dataflows)} dataflow(s) — see [`docs/dataflows/`](../dataflows/).")
    if lin.orchestration_flows:
        body.append(f"3. **Orchestration flows** ({len(lin.orchestration_flows)}) trigger / monitor refreshes and post notifications. See [`docs/orchestration/`](../orchestration/).")
    body.append("4. **Semantic model refresh** runs after dataflow completion (typical lag {{PLACEHOLDER}} minutes).")
    body.append("5. **Subscriptions / email alerts** fire on dataset success.")
    body.append("6. **Validation step:** check the workspace refresh history.")
    body.append("")

    body.append("\n## Monitoring & Alerts\n")
    body.append("- **Refresh failure notifications:** Power BI Service emails the dataset / dataflow owner. Configure additional contacts via `Workspace → Settings`.")
    body.append("- **Capacity monitoring:** " + md.PLACEHOLDER + " (e.g. Fabric Capacity Metrics app).")
    body.append("- **Recommended monitoring dashboard:** " + md.PLACEHOLDER + ".")
    body.append("")

    body.append("\n## Common Issues & Playbooks\n")
    for scenario, steps in (
        ("Refresh Failure", [
            "**Symptoms.** Power BI Service refresh history shows `Failed`; subscribed users have not received the weekly email.",
            "**Diagnosis steps.**",
            "1. Open the workspace → dataflow / dataset → *Refresh history* and view the error.",
            "2. Inspect the failed step in Power Query (Edit credentials? gateway offline? schema change?).",
            "3. Check upstream Databricks job status if a `Databricks.Catalogs` query failed.",
            "**Resolution steps.**",
            "1. Re-authenticate the data source if credentials expired.",
            "2. If the error is *Column 'X' not found*, reconcile with the upstream owner before patching the dataflow.",
            "3. Trigger a manual refresh once root cause is fixed.",
            f"**Escalation.** {md.PLACEHOLDER}",
        ]),
        ("Data Discrepancy", [
            "**Symptoms.** Numbers in the report disagree with another source of truth (Trading deck, Finance ledger).",
            "**Diagnosis steps.**",
            "1. Confirm both sources reference the same `fiscalWeek`, `channel`, and `BU` filter context.",
            "2. Identify which fact table feeds the visual (see [`docs/measures/`](../measures/) for the measure → table mapping).",
            "3. Compare row counts vs the upstream Databricks table.",
            "**Resolution steps.**",
            "1. If the upstream is correct, raise an incident with the dataflow owner.",
            "2. If the model logic is at fault, open a PR fixing the measure / dataflow and add a regression test by capturing pre/post numbers in `docs/CHANGELOG.md`.",
            f"**Escalation.** {md.PLACEHOLDER}",
        ]),
        ("Performance Degradation", [
            "**Symptoms.** Page render time exceeds 5 s; users report slow slicers.",
            "**Diagnosis steps.**",
            "1. Run *Performance Analyzer* in Power BI Desktop on the affected page.",
            "2. Capture the slow query DAX and run it against the model via DAX Studio.",
            "3. Inspect storage-engine vs formula-engine split.",
            "**Resolution steps.**",
            "1. Replace inefficient `FILTER` patterns with `KEEPFILTERS` / `CALCULATE`.",
            "2. Check for unintended bidirectional filters introduced by new bridge tables.",
            "3. Consider an aggregation table for high-cardinality fact joins.",
            f"**Escalation.** {md.PLACEHOLDER}",
        ]),
        ("Access Issues", [
            "**Symptoms.** A user can't see expected data, or sees a *You don't have access* prompt.",
            "**Diagnosis steps.**",
            "1. Confirm Entra ID group membership for the workspace / app.",
            "2. Check Row-Level Security role assignments — see [`docs/model/`](../model/) for role definitions.",
            "**Resolution steps.**",
            "1. Add the user to the appropriate Entra ID group.",
            "2. Re-test by running *Test as role* in the Power BI Service.",
            f"**Escalation.** {md.PLACEHOLDER}",
        ]),
    ):
        body.append(f"\n### {scenario}\n")
        for step in steps:
            body.append(step)
        body.append("")

    body.append("\n## Contacts & Escalation\n")
    body.append("| Issue type | L1 contact | L2 escalation |")
    body.append("| --- | --- | --- |")
    for kind in ("Refresh failure", "Data discrepancy", "Performance degradation", "Access issue"):
        body.append(f"| {kind} | {md.PLACEHOLDER} | {md.PLACEHOLDER} |")
    body.append("")

    body.append("\n## Maintenance Tasks\n")
    body.append("- **Weekly:** verify dataflow + dataset refresh success; review failure logs.")
    body.append("- **Monthly:** review unused measures (use [`docs/measures/`](../measures/) report-usage column); archive obsolete pages.")
    body.append("- **Quarterly:** capacity review; access audit (Entra ID groups, RLS roles); rotate dataflow credentials per security policy.")
    body.append("- **Annual:** end-of-year fiscal calendar update — confirm `Calendar`, `RefCalendar`, `DailyCalendar` cover the new year.")
    body.append("")

    body.append("\n## Backup & Recovery\n")
    body.append("- **Source of truth:** this Git repository — every PBIP / TMDL change is committed. Restore by checking out a known-good commit and re-publishing.")
    body.append("- **Service-side backup:** Power BI does not provide point-in-time backups; if a published model is corrupted, redeploy from PBIP.")
    body.append("- **Manual export:** [`scripts/02_export_selected_dataflows.ps1`](../../scripts/02_export_selected_dataflows.ps1) and [`scripts/01_list_dataflows.py`](../../scripts/01_list_dataflows.py) are used to refresh the `dataflows/` JSON exports for documentation.")
    body.append("")

    body.append("\n## Compliance Checks\n")
    body.append(f"- Access audit: {md.PLACEHOLDER} cadence")
    body.append(f"- Data reconciliation against finance ledger: {md.PLACEHOLDER} cadence")
    body.append(f"- Sensitivity-label review: {md.PLACEHOLDER} cadence")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Release notes template
# ---------------------------------------------------------------------------
def render_release_notes() -> str:
    return f"""{md.HEADER}# Release Notes — {{Date / Version}}

## What's New
- _[Feature description in business-friendly language]_

## Changes to Metrics or Definitions
- _[Metric name]: [old definition] → [new definition]. See the [glossary](glossary.md)._

## Fixes and Improvements
- _[Description]_

## Impact on Users
- _[Any action required or behaviour change]_

## Where to Find Help
- [Glossary](glossary.md)
- [Operations runbook](ops/runbook.md)
- Support: {md.PLACEHOLDER}
"""


__all__ = [
    "render_glossary",
    "render_data_sources",
    "render_dataflows",
    "render_reports",
    "render_app",
    "render_runbook",
    "render_release_notes",
]
