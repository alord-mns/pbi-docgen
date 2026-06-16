"""Renderers for `03-reports.md` — one self-sufficient card per report page.

Each page card lists its slicers, filters, and the measures it uses with a
one-line inline definition (so a RAG chunk answers "what does this page show
and how is it calculated" without a second hop), plus the backing tables and
dataflows that feed it.
"""
from __future__ import annotations

from . import cards
from . import md
from .cards import DocContext


def _entity_to_dataflow(ctx: DocContext) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in sorted(ctx.dataflows, key=lambda x: x.name):
        for e in d.entities:
            out.setdefault(e.name, d.name)
    return out


def render_page_card(ctx: DocContext, rep, page, ent2df: dict[str, str]) -> cards.Card:
    label = page.display_name or page.folder

    slicers = [v for v in page.visuals if v.visual_type == "slicer"]
    other = [v for v in page.visuals if v.visual_type != "slicer"]

    # Measures used on the page (distinct, ordered).
    measures: list[str] = []
    seen: set[str] = set()
    tables: set[str] = set()
    for v in page.visuals:
        for f in v.fields:
            if f.entity:
                tables.add(f.entity)
            if f.kind == "Measure" and f.member and f.member not in seen:
                seen.add(f.member)
                measures.append(f.member)

    # Backing dataflows via measure → tables → dataflow entities.
    backing_tables: set[str] = set(tables)
    for m in measures:
        backing_tables |= ctx.trace.measure_to_tables.get(m, set())
    dataflows: set[str] = set()
    for t in sorted(backing_tables):
        for ent in ctx.trace.table_to_dataflow_entities.get(t, []):
            if ent in ent2df:
                dataflows.add(ent2df[ent])

    parts: list[str] = []
    parts.append(
        f"_Report: `{md.md_escape_pipe(rep.name or ctx.model.name)}` · "
        f"internal name: `{page.name}` · {len(page.visuals)} visual(s)_"
    )

    if page.filters:
        parts.append("")
        parts.append("### Page filters")
        parts.append("")
        for flt in page.filters:
            ent = flt.field.entity or "(unbound)"
            prop = flt.field.member or ""
            parts.append(f"- `{ent}[{prop}]` — {flt.type or 'Categorical'} ({flt.how_created or 'User'})")

    if slicers:
        parts.append("")
        parts.append("### Slicers")
        parts.append("")
        for s in slicers:
            flds = ", ".join(f"`{f.entity}.{f.member}`" for f in s.fields) or "_no field bound_"
            parts.append(f"- {md.md_escape_pipe(s.title) or '_(unlabelled)_'} — {flds}")

    if measures:
        parts.append("")
        parts.append("### Metrics shown")
        parts.append("")
        parts.append("| Metric | Inline definition |")
        parts.append("|---|---|")
        for m in measures[:40]:
            summary = cards.measure_inline_summary(ctx, m)
            link = cards.ref_link(ctx, m)
            parts.append(f"| {link} | {md.md_escape_pipe(summary)} |")
        if len(measures) > 40:
            parts.append("")
            parts.append(f"_…and {len(measures) - 40} more metric(s) on this page._")

    if other:
        parts.append("")
        parts.append("### Visuals")
        parts.append("")
        parts.append("| Type | Title | Fields |")
        parts.append("|---|---|---|")
        for v in other:
            title = md.md_escape_pipe(v.title or v.visual_type) or "-"
            flds = ", ".join(f"`{f.entity}.{f.member}`" for f in v.fields[:5])
            if len(v.fields) > 5:
                flds += f" … ({len(v.fields)} total)"
            parts.append(f"| `{v.visual_type}` | {title} | {md.md_escape_pipe(flds) or '-'} |")

    parts.append("")
    parts.append("### Backing data")
    parts.append("")
    if backing_tables:
        known = sorted(t for t in backing_tables if t in ctx.table_by_name)
        unknown = sorted(t for t in backing_tables if t not in ctx.table_by_name)
        rendered: list[str] = [f"[{t}](#{cards.card_anchor('table', t)})" for t in known]
        rendered += [f"`{t}` _(unresolved binding)_" for t in unknown]
        parts.append("**Tables:** " + ", ".join(rendered))
    else:
        parts.append("**Tables:** _none traced._")
    parts.append("")
    if dataflows:
        parts.append("**Dataflows:** " + ", ".join(
            f"[{d}](#{cards.card_anchor('dataflow', d)})" for d in sorted(dataflows)
        ))
    else:
        parts.append("**Dataflows:** _none traced._")

    return cards.Card(
        anchor=cards.card_anchor("page", f"{rep.name}::{page.name}"),
        title=label,
        kind="Report page",
        subtitle=f"{len(measures)} metric(s)",
        body="\n".join(parts),
    )


def render_report_overview(ctx: DocContext, rep) -> cards.Card:
    rname = rep.name or ctx.model.name
    parts: list[str] = []
    parts.append(
        f"**Pages:** {len(rep.pages)} · "
        f"**Visuals:** {sum(len(p.visuals) for p in rep.pages)} · "
        f"**Connected model:** `{ctx.model.name}`"
    )
    parts.append("")
    parts.append("### Pages")
    parts.append("")
    parts.append("| # | Page | Visuals | Filters |")
    parts.append("|---|---|---|---|")
    for i, p in enumerate(rep.pages, 1):
        label = p.display_name or p.folder
        anchor = cards.card_anchor("page", f"{rep.name}::{p.name}")
        parts.append(f"| {i} | [{md.md_escape_pipe(label)}](#{anchor}) | {len(p.visuals)} | {len(p.filters)} |")
    return cards.Card(
        anchor=cards.card_anchor("report", rname),
        title=rname,
        kind="Report",
        subtitle=f"{len(rep.pages)} page(s)",
        body="\n".join(parts),
    )


def render_reports(ctx: DocContext) -> str:
    ent2df = _entity_to_dataflow(ctx)
    cardlist: list[cards.Card] = []
    reports = ctx.reports or [ctx.lin.report]
    for rep in sorted(reports, key=lambda r: r.name or ""):
        cardlist.append(render_report_overview(ctx, rep))
        for page in rep.pages:
            cardlist.append(render_page_card(ctx, rep, page, ent2df))

    total_pages = sum(len(r.pages) for r in reports)
    intro = (
        f"**Reports:** {len(reports)} · **Pages:** {total_pages}.\n\n"
        "Each page card lists its slicers, filters, and the metrics it shows "
        "with a one-line inline definition, plus the backing tables and dataflows."
    )
    return cards.render_bundle(
        file_title="Reports",
        purpose=(
            "Self-sufficient cards for every report page — slicers, filters, "
            "metrics with inline definitions, and the upstream tables and "
            "dataflows that feed the page."
        ),
        audiences=("Power BI developers", "Business end users"),
        intro=intro,
        cards=cardlist,
    )


__all__ = ["render_page_card", "render_report_overview", "render_reports"]
