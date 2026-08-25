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

# Power BI visual types that are pure page chrome (decoration / navigation).
# When such a visual carries no field binding it adds only noise to the card and
# fragments it across retrieval chunks, so it is omitted from the Visuals table
# (its count still feeds the header "N visual(s)" total). These are platform
# visual-type identifiers, not solution-specific strings.
_CHROME_VISUAL_TYPES = frozenset(
    {"shape", "basicShape", "textbox", "actionButton", "image"}
)

# Cap on the number of distinct visual configurations listed per page.
_MAX_VISUAL_ROWS = 40


def _entity_to_dataflow(ctx: DocContext) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in sorted(ctx.dataflows, key=lambda x: x.name):
        for e in d.entities:
            out.setdefault(e.name, d.name)
    return out


def render_page_card(ctx: DocContext, rep, page, ent2df: dict[str, str]) -> cards.Card:
    label = page.display_name or page.folder
    report_label = rep.name or ctx.model.name
    if report_label.endswith(".Report"):
        report_label = report_label[: -len(".Report")]
    # Page names are not unique across reports (e.g. several reports each have a
    # "Headlines" page). Stamp the report onto the card title so the card — and,
    # via _stamp_section_headings, every "### Slicers · …" sub-section chunk — is
    # self-locating and retrievable for the *specific* report being asked about.
    page_title = f"{label} — {report_label}"

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
        # Drop field-less page chrome (shapes / textboxes / buttons / images):
        # it carries no analytical binding and only fragments the card. Then
        # collapse repeated identical visual configurations (large pages repeat
        # the same pivotTable many times) into one row with a count, so the card
        # stays small enough to retrieve as one coherent chunk.
        rows: list[tuple[str, str, str]] = []
        counts: dict[tuple[str, str, str], int] = {}
        for v in other:
            if not v.fields and v.visual_type in _CHROME_VISUAL_TYPES:
                continue
            flds = ", ".join(f"`{f.entity}.{f.member}`" for f in v.fields[:5])
            if len(v.fields) > 5:
                flds += f" … ({len(v.fields)} total)"
            title = md.md_escape_pipe(v.title or v.visual_type) or "-"
            key = (v.visual_type, title, md.md_escape_pipe(flds) or "-")
            if key not in counts:
                counts[key] = 0
                rows.append(key)
            counts[key] += 1
        if rows:
            chrome_omitted = len(other) - sum(counts.values())
            parts.append("")
            parts.append("### Visuals")
            parts.append("")
            parts.append("| Type | Title | Fields | Count |")
            parts.append("|---|---|---|---|")
            for vtype, title, flds in rows[:_MAX_VISUAL_ROWS]:
                parts.append(f"| `{vtype}` | {title} | {flds} | {counts[(vtype, title, flds)]} |")
            notes: list[str] = []
            if len(rows) > _MAX_VISUAL_ROWS:
                notes.append(f"{len(rows) - _MAX_VISUAL_ROWS} more distinct visual(s) omitted")
            if chrome_omitted:
                notes.append(
                    f"{chrome_omitted} decorative chrome visual(s) "
                    "(shapes / textboxes / buttons / images) omitted"
                )
            if notes:
                parts.append("")
                parts.append("_… " + "; ".join(notes) + "._")

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
        title=page_title,
        kind="Report page",
        subtitle=f"{len(measures)} metric(s)",
        body="\n".join(parts),
    )


def _report_purpose(ctx: DocContext, rep) -> str:
    """Curated one-line purpose for a report, looked up from ``[reports]``.

    Matches by exact report name, then by the name without a trailing
    ``.Report`` suffix. Returns an empty string when no purpose is configured,
    so the caller can render a placeholder rather than invent one.
    """
    purposes = ctx.cfg.report_purposes
    name = rep.name or ""
    if name in purposes:
        return purposes[name].strip()
    stem = name[:-7] if name.endswith(".Report") else name
    return purposes.get(stem, "").strip()


def render_report_catalog(ctx: DocContext) -> cards.Card:
    """A single self-sufficient card listing every report in the solution.

    Answers "what reports exist?" from one chunk, so the agent never has to
    stitch the per-report cards together. Purpose text is curated in
    ``[reports]`` of ``.docgen.toml``; when absent a placeholder is shown rather
    than an invented description.
    """
    reports = ctx.reports or [ctx.lin.report]
    reports = sorted(reports, key=lambda r: r.name or "")
    total_pages = sum(len(r.pages) for r in reports)
    total_visuals = sum(len(p.visuals) for r in reports for p in r.pages)

    parts: list[str] = []
    parts.append(
        f"**Reports:** {len(reports)} · **Pages:** {total_pages} · "
        f"**Visuals:** {total_visuals} · **Connected model:** `{ctx.model.name}`"
    )
    parts.append("")
    parts.append("The complete set of thin reports built on this semantic model. "
                 "Each links to its own card listing pages, slicers, filters, and metrics.")
    parts.append("")
    parts.append("### Reports")
    parts.append("")
    parts.append("| # | Report | Pages | Visuals | Purpose |")
    parts.append("|---|---|---|---|---|")
    for i, rep in enumerate(reports, 1):
        rname = rep.name or ctx.model.name
        anchor = cards.card_anchor("report", rname)
        visuals = sum(len(p.visuals) for p in rep.pages)
        purpose = _report_purpose(ctx, rep)
        purpose_txt = md.md_escape_pipe(purpose) if purpose else (
            md.PLACEHOLDER + " — add to `[reports]` in `.docgen.toml`"
        )
        parts.append(
            f"| {i} | [{md.md_escape_pipe(rname)}](#{anchor}) | {len(rep.pages)} | "
            f"{visuals} | {purpose_txt} |"
        )
    aliases = (
        "report catalog", "report catalogue", "report index", "list of reports",
        "all reports", "which reports", "report inventory", "reports available",
    )
    return cards.Card(
        anchor=cards.card_anchor("report-catalog", "all"),
        title="Report Catalog",
        kind="Report index",
        subtitle=f"{len(reports)} report(s)",
        keywords=aliases + tuple(r.name for r in reports if r.name),
        body="\n".join(parts),
    )


def render_page_index(ctx: DocContext) -> cards.Card:
    """A single self-sufficient card listing every page across every report.

    Answers "list all pages" / "which page is X on" from one chunk, the
    page-level counterpart to the Report Catalog. Pages keep their in-report
    order; each row links to its own page card for visual / metric detail.
    """
    reports = ctx.reports or [ctx.lin.report]
    reports = sorted(reports, key=lambda r: r.name or "")
    total_pages = sum(len(r.pages) for r in reports)

    parts: list[str] = []
    parts.append(
        f"**Pages:** {total_pages} across **{len(reports)}** report(s). "
        "One row per page; follow a row to that page's card for its visuals, "
        "slicers, filters, and metrics."
    )
    parts.append("")
    parts.append("### Pages")
    parts.append("")
    parts.append("| # | Report | Page | Visuals | Filters |")
    parts.append("|---|---|---|---|---|")
    row = 0
    for rep in reports:
        rname = rep.name or ctx.model.name
        rlink = f"[{md.md_escape_pipe(rname)}](#{cards.card_anchor('report', rname)})"
        for page in rep.pages:
            row += 1
            label = page.display_name or page.folder
            anchor = cards.card_anchor("page", f"{rep.name}::{page.name}")
            parts.append(
                f"| {row} | {rlink} | [{md.md_escape_pipe(label)}](#{anchor}) | "
                f"{len(page.visuals)} | {len(page.filters)} |"
            )
    return cards.Card(
        anchor=cards.card_anchor("page-index", "all"),
        title="Page Index",
        kind="Page index",
        subtitle=f"{total_pages} page(s)",
        keywords=(
            "page index", "list of pages", "all pages", "which pages",
            "page inventory", "every page", "pages available", "find a page",
        ),
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
    cardlist.append(render_report_catalog(ctx))
    cardlist.append(render_page_index(ctx))
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


__all__ = [
    "render_page_card",
    "render_report_overview",
    "render_report_catalog",
    "render_page_index",
    "render_reports",
]
