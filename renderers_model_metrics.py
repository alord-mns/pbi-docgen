"""Renderers for `01-model-and-metrics.md` — concept, measure, and table cards.

Card treatments by measure role (see :mod:`scripts.docgen.dax_refs`):

* **router**  → metric *concept card*: the RAG entry point. Inlines every
  SWITCH branch (selector value → target metric → one-line derivation) so the
  agent answers a metric question in one hop.
* **metric**  → full *measure card*: complete DAX + full SQL source trace +
  reverse "Selected by" pointer to its concept card.
* **compute** → compact *stub card*: intermediate measure, one-line DAX + links.
* **base**    → folded into the owning *table card*.
* **selector**→ not a standalone card; documented as a router's driver.

Table / entity cards carry grain, columns, upstream dataflow + SQL views, the
folded base measures, the measures that consume the table, and a dedicated
**Downstream impact** section (report pages).
"""
from __future__ import annotations

from . import cards
from . import dax_refs
from . import md
from .cards import DocContext


# ---------------------------------------------------------------------------
# Metric concept card (router family)
# ---------------------------------------------------------------------------
def render_concept_card(ctx: DocContext, router: dax_refs.MeasureClass) -> cards.Card:
    name = router.name
    parts: list[str] = []

    drivers = ", ".join(f"`{d}`" for d in router.drivers) or "_(none detected)_"
    parts.append(f"**Selector driver(s):** {drivers}")
    parts.append("")
    parts.append(f"**Home table:** `{router.table}`")

    # Routing table — the heart of the concept card.
    switch = router.switch
    branches = switch.branches if switch and switch.is_switch else []
    parts.append("")
    parts.append("### Routing")
    parts.append("")
    if branches:
        parts.append("| Selector value | Target metric | What it computes |")
        parts.append("|---|---|---|")
        for branch in branches:
            label = md.md_escape_pipe(branch.label or "(default)")
            target = branch.target_measure
            if target:
                tlink = cards.ref_link(ctx, target)
                summary = md.md_escape_pipe(cards.measure_inline_summary(ctx, target))
            else:
                tlink = "_(inline expression)_"
                summary = md.md_escape_pipe(
                    cards.collapse_ws(branch.raw_result, 160)
                ) if getattr(branch, "raw_result", "") else "—"
            parts.append(f"| `{label}` | {tlink} | {summary} |")
    else:
        targets = router.branch_targets or sorted(router.measure_refs)
        if targets:
            for t in targets:
                parts.append(f"- {cards.ref_link(ctx, t)} — {cards.measure_inline_summary(ctx, t)}")
        else:
            parts.append("_No routed targets detected; see DAX below._")

    parts.append("")
    parts.append("### Definition (router DAX)")
    parts.append("")
    measure = ctx.measure_by_name.get(name)
    parts.append(md.code_block(measure.expression if measure else "", "dax"))

    parts.append("")
    parts.append("### Used on report pages")
    parts.append("")
    parts.append(cards.page_list(ctx, ctx.measure_pages.get(name, set())))

    card = cards.Card(
        anchor=cards.card_anchor("concept", name),
        title=name,
        kind="Metric concept (router)",
        subtitle=f"routes over {drivers}",
        body="\n".join(parts),
    )
    return card


# ---------------------------------------------------------------------------
# Full measure card (metric role)
# ---------------------------------------------------------------------------
def render_measure_card(ctx: DocContext, mc: dax_refs.MeasureClass) -> cards.Card:
    name = mc.name
    measure = ctx.measure_by_name.get(name)
    parts: list[str] = []

    home = f"**Home table:** `{mc.table}`"
    if measure and measure.display_folder:
        home += f" · **Display folder:** {measure.display_folder}"
    if measure and measure.format_string:
        home += f" · **Format:** `{measure.format_string}`"
    parts.append(home)

    if measure and measure.description.strip():
        parts.append("")
        parts.append(f"> {md.md_escape_pipe(measure.description.strip())}")

    selectors = ctx.selected_by.get(name, [])
    if selectors:
        links = ", ".join(cards.concept_link(ctx, r) for r in selectors)
        parts.append("")
        parts.append(f"**Selected by:** {links}")

    parts.append("")
    parts.append("### Definition (DAX)")
    parts.append("")
    parts.append(md.code_block(measure.expression if measure else "", "dax"))

    parts.append("")
    parts.append("### Source trace")
    parts.append("")
    acc = cards.trace_accounting(ctx, name)
    if acc:
        parts.append(acc)
        parts.append("")
    parts.append(cards.source_trace_table(ctx, name))

    refs = sorted(mc.measure_refs)
    if refs:
        parts.append("")
        parts.append("### References measures")
        parts.append("")
        parts.append(", ".join(cards.ref_link(ctx, r) for r in refs))

    parts.append("")
    parts.append("### Used on report pages")
    parts.append("")
    parts.append(cards.page_list(ctx, ctx.measure_pages.get(name, set())))

    return cards.Card(
        anchor=cards.card_anchor("measure", name),
        title=name,
        kind="Measure (metric)",
        subtitle=cards.measure_source_summary(ctx, name),
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Compute stub card
# ---------------------------------------------------------------------------
def render_compute_stub(ctx: DocContext, mc: dax_refs.MeasureClass) -> cards.Card:
    name = mc.name
    parts: list[str] = []
    parts.append(f"**Home table:** `{mc.table}` · intermediate (compute) measure.")

    concept = ctx.concept_of.get(name)
    if concept:
        parts.append("")
        parts.append(f"**Part of concept:** {cards.concept_link(ctx, concept)}")

    parts.append("")
    parts.append(f"**DAX:** {cards.measure_dax_oneline(ctx, name)}")

    refs = sorted(mc.measure_refs)
    if refs:
        parts.append("")
        parts.append("**References:** " + ", ".join(cards.ref_link(ctx, r) for r in refs))

    src = cards.measure_source_summary(ctx, name)
    parts.append("")
    parts.append(f"**Sources:** {src}")

    pages = ctx.measure_pages.get(name, set())
    if pages:
        parts.append("")
        parts.append(f"**Used on report pages:** {cards.page_list(ctx, pages)}")

    return cards.Card(
        anchor=cards.card_anchor("measure", name),
        title=name,
        kind="Measure (compute)",
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Table / entity card
# ---------------------------------------------------------------------------
def render_table_card(ctx: DocContext, table) -> cards.Card:
    name = table.name
    parts: list[str] = []

    desc = table.description.strip() if table.description else ""
    if desc:
        parts.append(f"> {md.md_escape_pipe(desc)}")
        parts.append("")

    flags = []
    if table.is_hidden:
        flags.append("hidden")
    if table.is_calculated:
        flags.append("calculated")
    if table.is_calculation_group:
        flags.append("calculation group")
    meta = f"**Columns:** {len(table.columns)} · **Measures:** {len(table.measures)}"
    if flags:
        meta += " · " + ", ".join(flags)
    parts.append(meta)

    # Upstream source.
    entities = ctx.trace.table_to_dataflow_entities.get(name, [])
    views = ctx.trace.table_to_views.get(name, [])
    parts.append("")
    parts.append("### Upstream source")
    parts.append("")
    if entities:
        parts.append("**Dataflow entity(ies):** " + ", ".join(f"`{e}`" for e in entities))
    if views:
        parts.append("")
        parts.append("**Databricks view(s):** " + ", ".join(f"`{v}`" for v in views))
    if not entities and not views:
        if table.is_calculated:
            parts.append("_Calculated table (defined in DAX); no external source._")
        else:
            parts.append("_No external dataflow / SQL source resolved._")

    # Columns with source mapping.
    if table.columns:
        parts.append("")
        parts.append("### Columns")
        parts.append("")
        parts.append("| Column | Type | Source column | Notes |")
        parts.append("|---|---|---|---|")
        for col in sorted(table.columns, key=lambda c: c.name):
            notes = []
            if col.is_calculated:
                notes.append("calculated")
            if col.is_hidden:
                notes.append("hidden")
            src = f"`{col.source_column}`" if col.source_column else "—"
            parts.append(
                f"| `{md.md_escape_pipe(col.name)}` | {col.data_type or '—'} | {src} | {', '.join(notes) or '—'} |"
            )

    # Folded base measures.
    base_measures = sorted(
        m.name
        for m in table.measures
        if ctx.cls.role(m.name) == dax_refs.ROLE_BASE
    )
    if base_measures:
        parts.append("")
        parts.append("### Base measures (atomic wrappers)")
        parts.append("")
        for bn in base_measures:
            parts.append(f"- `{bn}` — {cards.measure_dax_oneline(ctx, bn, cap=120)}")

    # Measures consuming this table.
    consumers = sorted(ctx.table_measures.get(name, set()))
    parts.append("")
    parts.append("### Measures using this table")
    parts.append("")
    if consumers:
        shown = consumers[:30]
        parts.append(f"{len(consumers)} measure(s) reference columns on this table, including:")
        parts.append("")
        parts.append(", ".join(cards.ref_link(ctx, m) for m in shown))
        if len(consumers) > len(shown):
            parts.append("")
            parts.append(f"_…and {len(consumers) - len(shown)} more._")
    else:
        parts.append("_None._")

    # Downstream impact.
    parts.append("")
    parts.append("### Downstream impact")
    parts.append("")
    parts.append("**Report pages referencing this table:** " + cards.page_list(ctx, ctx.lin.table_to_pages.get(name, set())))

    return cards.Card(
        anchor=cards.card_anchor("table", name),
        title=name,
        kind="Table / entity",
        subtitle=(f"{len(table.columns)} columns" + (f", source `{views[0]}`" if views else "")),
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------
def render_model_and_metrics(ctx: DocContext) -> str:
    cls = ctx.cls
    counts = cls.counts()

    routers = sorted(cls.of_role(dax_refs.ROLE_ROUTER), key=lambda m: m.name)
    metrics = sorted(cls.of_role(dax_refs.ROLE_METRIC), key=lambda m: m.name)
    computes = sorted(cls.of_role(dax_refs.ROLE_COMPUTE), key=lambda m: m.name)
    tables = sorted(ctx.model.tables, key=lambda t: t.name)

    cardlist: list[cards.Card] = []
    for r in routers:
        cardlist.append(render_concept_card(ctx, r))
    for m in metrics:
        cardlist.append(render_measure_card(ctx, m))
    for c in computes:
        cardlist.append(render_compute_stub(ctx, c))
    for t in tables:
        cardlist.append(render_table_card(ctx, t))

    intro = (
        f"**Measure roles:** {counts.get(dax_refs.ROLE_ROUTER, 0)} router · "
        f"{counts.get(dax_refs.ROLE_METRIC, 0)} metric · "
        f"{counts.get(dax_refs.ROLE_COMPUTE, 0)} compute · "
        f"{counts.get(dax_refs.ROLE_BASE, 0)} base · "
        f"{counts.get(dax_refs.ROLE_SELECTOR, 0)} selector. "
        f"**Tables:** {len(tables)}.\n\n"
        "Metric **concept cards** are the entry point: start there for any "
        "business-metric question. Each routes to per-channel **measure cards** "
        "that carry the full SQL source trace. **Base** measures are folded into "
        "their **table card**; **selector** measures are documented on the "
        "router that uses them."
    )

    return cards.render_bundle(
        file_title="Model & Metrics",
        purpose=(
            "Self-sufficient cards for every metric family, measure, and table "
            "in the semantic model — each carrying its full upstream source "
            "trace (DAX → columns → dataflow entity → Databricks view) and "
            "downstream report impact."
        ),
        audiences=("Business analysts", "Report developers", "Data engineers"),
        intro=intro,
        cards=cardlist,
    )


__all__ = [
    "render_concept_card",
    "render_measure_card",
    "render_compute_stub",
    "render_table_card",
    "render_model_and_metrics",
]
