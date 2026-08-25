"""Render the overview bundle (``00-overview.md``) — the agent KB entry point.

Cards only, text and tables (no Mermaid): a solution summary, a text
architecture card, a glossary + acronym card, an end-to-end dependency table,
and a refresh / change-impact card. All repo-specific narrative comes from
``model-docs/.docgen.toml`` via :mod:`scripts.docgen.config`.
"""
from __future__ import annotations

from collections import Counter

from . import cards
from . import md
from .cards import DocContext
from .config import Config
from .lineage import Lineage


def _solution_display(model_name: str, cfg: Config) -> str:
    return cfg.solution.display_name or model_name


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
        f"**Solution name:** `{model.name}` (semantic model + thin reports).",
        f"**Storage mode:** Import; semantic-model compatibility level `{model.compatibility_level or 'unknown'}`.",
        f"**Semantic model size:** {table_count} tables, {column_count} columns, {measure_count:,} measures, {rel_count} relationships, {expr_count} shared expressions.",
        f"**Row-Level Security:** {role_count} role(s) defined.",
        f"**Reports:** {len(lin.reports)} thin report(s) with {page_count} pages and ~{visual_count} visuals total; built on the same `.pbip` semantic model.",
        f"**Upstream dataflows referenced:** {df_ref_count} unique dataflow(s) across workspace(s): {ws_line}.",
        f"**Dataflow JSON exports available:** {df_export_count} (see the Data Pipeline file for full M-code documentation).",
        f"**Orchestration flows documented:** {flow_count} (see the Data Pipeline file).",
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
    return bullets


__all__ = ["render_overview"]


# ---------------------------------------------------------------------------
# Card-based overview (00-overview.md) — text/tables only, no Mermaid
# ---------------------------------------------------------------------------
def _overview_solution_card(ctx: DocContext) -> cards.Card:
    lin = ctx.lin
    cfg = ctx.cfg
    parts: list[str] = []
    if cfg.solution.purpose:
        parts.append(f"> {md.md_escape_pipe(cfg.solution.purpose.strip())}")
        parts.append("")
    parts.append("### Key facts")
    parts.append("")
    for b in _exec_summary_bullets(lin, cfg):
        parts.append(f"- {b}")
    return cards.Card(
        anchor=cards.card_anchor("overview", "solution"),
        title=f"{_solution_display(lin.model.name, cfg)} — Solution Overview",
        kind="Overview",
        subtitle="executive summary",
        body="\n".join(parts),
    )


def _overview_architecture_card(ctx: DocContext) -> cards.Card:
    lin = ctx.lin
    cfg = ctx.cfg
    model = lin.model
    parts: list[str] = []
    parts.append("### Components")
    parts.append("")
    if cfg.narratives.upstream_platforms:
        parts.append("- **Upstream platforms.** " + cfg.narratives.upstream_platforms.strip())
    parts.append(
        f"- **Dataflows.** {len(lin.dataflows)} exported dataflow(s) prepare conformed entities — see the Data Pipeline file."
    )
    if lin.orchestration_flows:
        parts.append(
            f"- **Orchestration.** {len(lin.orchestration_flows)} workflow(s) trigger and monitor refreshes."
        )
    parts.append(
        f"- **Semantic model (`{model.name}`).** Import-mode tabular model: "
        f"{len(model.tables)} tables, {sum(len(t.measures) for t in model.tables):,} measures, "
        f"{len(model.relationships)} relationships."
    )
    parts.append(
        f"- **Thin reports.** {len(lin.reports)} report(s), "
        f"{sum(len(r.pages) for r in lin.reports)} page(s) — see the Reports file."
    )
    parts.append("")
    parts.append("### Scope")
    parts.append("")
    parts.append("**Included:** the `.pbip` solution, its dataflows, and orchestration flows that refresh them.")
    parts.append("")
    parts.append("**Excluded:** source operational systems, adjacent reports owned by other teams, and workspace access control.")
    return cards.Card(
        anchor=cards.card_anchor("overview", "architecture"),
        title="Architecture",
        kind="Overview",
        subtitle="components & scope",
        body="\n".join(parts),
    )


def _overview_glossary_card(ctx: DocContext) -> cards.Card:
    cfg = ctx.cfg
    parts: list[str] = []
    parts.append("### Acronyms & abbreviations")
    parts.append("")
    if cfg.acronyms:
        parts.append("| Term | Definition |")
        parts.append("|---|---|")
        for k in sorted(cfg.acronyms):
            parts.append(f"| `{k}` | {md.md_escape_pipe(cfg.acronyms[k])} |")
    else:
        parts.append(f"_{md.PLACEHOLDER} — populate `[acronyms]` in `.docgen.toml`._")

    terms: dict[str, str] = {}
    for t in ctx.model.tables:
        if t.description and not t.is_calculation_group:
            terms[t.name] = t.description.strip()
    if terms:
        parts.append("")
        parts.append("### Business terms")
        parts.append("")
        for k in sorted(terms):
            parts.append(f"- **{k}** — {md.md_escape_pipe(terms[k])}")
    return cards.Card(
        anchor=cards.card_anchor("overview", "glossary"),
        title="Glossary & Acronyms",
        kind="Overview",
        subtitle="business terms",
        keywords=tuple(sorted(cfg.acronyms)),
        body="\n".join(parts),
    )


def _overview_lineage_card(ctx: DocContext) -> cards.Card:
    lin = ctx.lin
    parts: list[str] = []
    if ctx.cfg.narratives.lineage_narrative:
        for i, bullet in enumerate(ctx.cfg.narratives.lineage_narrative, 1):
            parts.append(f"{i}. {bullet}")
        parts.append("")
    parts.append("### Dependency table")
    parts.append("")
    parts.append("| Component | Type | Depends on |")
    parts.append("|---|---|---|")
    for t in sorted(lin.model.tables, key=lambda x: x.name):
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
        parts.append(f"| `{t.name}` | model table | {md.md_escape_pipe(dep_label)} |")
    for f in lin.orchestration_flows or []:
        targets = ", ".join(
            f"{t.kind} {(lin.short_id_to_name.get(t.object_id) or t.object_id[:8])}"
            for t in f.refresh_targets
        )
        parts.append(f"| `{f.name}` | orchestration flow | refreshes: {md.md_escape_pipe(targets) or '-'} |")
    return cards.Card(
        anchor=cards.card_anchor("overview", "lineage"),
        title="End-to-End Lineage",
        kind="Overview",
        subtitle="dependency table",
        body="\n".join(parts),
    )


def _overview_change_impact_card(ctx: DocContext) -> cards.Card:
    parts: list[str] = []
    parts.append("### Refresh & orchestration")
    parts.append("")
    if ctx.flows:
        for flow in sorted(ctx.flows, key=lambda f: f.name):
            parts.append(f"- `{flow.name}` — {len(flow.refresh_targets)} refresh target(s).")
    else:
        parts.append("_No orchestration flows configured._")
    parts.append("")
    parts.append("### Change-impact notes")
    parts.append("")
    if ctx.cfg.narratives.change_impact_notes:
        for note in ctx.cfg.narratives.change_impact_notes:
            parts.append(f"- {note}")
    else:
        parts.append(f"- {md.PLACEHOLDER} — populate `[narratives].change_impact_notes` in `.docgen.toml`.")
    return cards.Card(
        anchor=cards.card_anchor("overview", "change-impact"),
        title="Refresh & Change Impact",
        kind="Overview",
        subtitle="operations",
        body="\n".join(parts),
    )


def render_overview(ctx: DocContext) -> str:
    cardlist = [
        _overview_solution_card(ctx),
        _overview_architecture_card(ctx),
        _overview_glossary_card(ctx),
        _overview_lineage_card(ctx),
        _overview_change_impact_card(ctx),
    ]
    intro = (
        "Start here. This file carries the solution summary, architecture, "
        "glossary, end-to-end lineage, and refresh/change-impact notes. "
        "Detailed cards live in the model-and-metrics, data-pipeline, and "
        "reports files."
    )
    return cards.render_bundle(
        file_title=f"{_solution_display(ctx.model.name, ctx.cfg)} — Overview",
        purpose=(
            "Top-level entry point: what the solution is, how data flows through "
            "it, the glossary, and where to look next."
        ),
        audiences=("All audiences",),
        intro=intro,
        cards=cardlist,
    )
