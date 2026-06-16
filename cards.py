"""Card framework for the agent knowledge base.

The documentation set is consumed by a Microsoft 365 declarative (RAG) Copilot
agent that retrieves **single chunks** and does not traverse links. The unit of
value is therefore a self-sufficient *card*: one addressable concept (a metric
family, a measure, a table, a dataflow, a report page) carrying its full
upstream trace and downstream impact inline.

This module provides:

* :class:`Card` — a renderable unit (H2 heading + fixed-order body);
* :class:`DocContext` — the bundle of parsed artefacts + precomputed reverse
  maps every renderer needs, built once by :func:`build_context`;
* shared formatting helpers (one-line measure summaries, full source-trace
  tables, page lists) so concept cards can inline a target's derivation without
  the agent needing a second hop;
* :func:`render_bundle` — assembles a list of cards into one flat Markdown file
  with banner, purpose block, and a per-file table of contents.

Everything is deterministic and idempotent: all set / dict iteration is sorted
so re-running the generator produces byte-identical output.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from . import config as configmod
from . import dataflow as dfmod
from . import dax_refs
from . import lineage as lineagemod
from . import md
from . import pbir as pbirmod
from . import sourcetrace
from . import sqlsource
from . import tmdl


# ---------------------------------------------------------------------------
# Card model
# ---------------------------------------------------------------------------
@dataclass
class Card:
    """A single self-sufficient documentation chunk."""

    anchor: str  # stable in-file anchor (slug)
    title: str  # H2 heading text — the addressable concept name
    kind: str  # human label, e.g. "Metric concept", "Table", "Report page"
    subtitle: str = ""  # one-line summary shown directly under the heading
    keywords: tuple[str, ...] = ()  # synonyms / aliases to aid retrieval
    body: str = ""  # pre-rendered Markdown sections


def card_anchor(kind: str, name: str) -> str:
    """Build a stable, collision-free anchor from a card kind + concept name.

    ``md.slugify`` is lossy — it strips punctuation, so distinct concept names
    (``Buying Margin`` vs ``Buying Margin %``) can collapse to the same slug. A
    short hash of the exact ``(kind, name)`` is appended to guarantee a unique,
    deterministic anchor. Because the anchor is a pure function of the inputs,
    a cross-reference link computed independently always matches the card's own
    anchor without any shared registry.
    """
    slug = md.slugify(f"{kind}-{name}")
    digest = hashlib.sha1(f"{kind}\x00{name}".encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}" if slug else f"{kind}-{digest}"


def render_card(card: Card) -> str:
    """Render one card to Markdown (anchor + heading + body)."""
    lines = [f'<a id="{card.anchor}"></a>', "", f"## {card.title}", ""]
    meta = f"**Type:** {card.kind}"
    if card.subtitle:
        meta += f" · {card.subtitle}"
    lines.append(meta)
    if card.keywords:
        lines.append("")
        lines.append("**Also known as:** " + ", ".join(card.keywords))
    lines.append("")
    body = (card.body or "").strip("\n")
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_bundle(
    *,
    file_title: str,
    purpose: str,
    audiences: tuple[str, ...],
    cards: list[Card],
    intro: str = "",
) -> str:
    """Assemble cards into one flat Markdown file with banner + ToC."""
    parts: list[str] = [md.HEADER.rstrip(), "", f"# {file_title}", ""]
    parts.append(md.section_purpose(purpose, *audiences))
    if intro.strip():
        parts.append("")
        parts.append(intro.strip())
    if cards:
        parts.append("")
        parts.append("## Contents")
        parts.append("")
        for card in cards:
            label = card.title.replace("|", "\\|")
            parts.append(f"- [{label}](#{card.anchor}) — {card.kind}")
    parts.append("")
    for card in cards:
        parts.append("---")
        parts.append("")
        parts.append(render_card(card).rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Document context
# ---------------------------------------------------------------------------
@dataclass
class DocContext:
    cfg: configmod.Config
    model: tmdl.Model
    reports: list[pbirmod.Report]
    dataflows: list[dfmod.Dataflow]
    flows: list
    lin: lineagemod.Lineage
    cls: dax_refs.Classification
    trace: sourcetrace.SourceTrace
    sql_catalog: sqlsource.SqlCatalog
    # ---- precomputed reverse maps (built by build_context) ----
    measure_pages: dict[str, set[str]] = field(default_factory=dict)
    selected_by: dict[str, list[str]] = field(default_factory=dict)
    concept_of: dict[str, str] = field(default_factory=dict)
    table_measures: dict[str, set[str]] = field(default_factory=dict)
    measure_by_name: dict[str, tmdl.Measure] = field(default_factory=dict)
    table_by_name: dict[str, tmdl.Table] = field(default_factory=dict)

    def role(self, name: str) -> str:
        return self.cls.role(name)


def build_context(
    *,
    cfg: configmod.Config,
    model: tmdl.Model,
    reports: list[pbirmod.Report],
    dataflows: list[dfmod.Dataflow],
    flows: list,
    lin: lineagemod.Lineage,
    cls: dax_refs.Classification,
    trace: sourcetrace.SourceTrace,
    sql_catalog: sqlsource.SqlCatalog,
) -> DocContext:
    """Build the shared context + reverse maps used by every renderer."""
    ctx = DocContext(
        cfg=cfg,
        model=model,
        reports=reports,
        dataflows=dataflows,
        flows=flows,
        lin=lin,
        cls=cls,
        trace=trace,
        sql_catalog=sql_catalog,
    )

    # measure name -> report page labels (bare member name).
    measure_pages: dict[str, set[str]] = {}
    for rep in reports:
        for page in rep.pages:
            label = page.display_name or page.folder
            for visual in page.visuals:
                for fr in visual.fields:
                    if fr.kind == "Measure" and fr.member:
                        measure_pages.setdefault(fr.member, set()).add(label)
    ctx.measure_pages = measure_pages

    # router branch target -> routers that select it (the "Selected by" link).
    selected_by: dict[str, list[str]] = {}
    concept_of: dict[str, str] = {}
    for router in sorted(cls.of_role(dax_refs.ROLE_ROUTER), key=lambda m: m.name):
        for target in router.branch_targets:
            selected_by.setdefault(target, [])
            if router.name not in selected_by[target]:
                selected_by[target].append(router.name)
            concept_of.setdefault(target, router.name)
    ctx.selected_by = selected_by
    ctx.concept_of = concept_of

    # table -> measures that (transitively) reference its columns.
    table_measures: dict[str, set[str]] = {}
    for measure_name, tables in trace.measure_to_tables.items():
        for table_name in tables:
            table_measures.setdefault(table_name, set()).add(measure_name)
    ctx.table_measures = table_measures

    ctx.measure_by_name = dict(cls.index.measure_by_name)
    ctx.table_by_name = {t.name: t for t in model.tables}
    return ctx


# ---------------------------------------------------------------------------
# Shared formatting helpers
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def collapse_ws(text: str, cap: int = 0) -> str:
    """Collapse all runs of whitespace to single spaces; optionally cap length."""
    out = _WS_RE.sub(" ", (text or "").strip())
    if cap and len(out) > cap:
        out = out[: cap - 1].rstrip() + "\u2026"
    return out


def measure_dax_oneline(ctx: DocContext, name: str, cap: int = 220) -> str:
    """The measure's DAX collapsed to a single line (capped)."""
    measure = ctx.measure_by_name.get(name)
    if measure is None or not measure.expression.strip():
        return "_(no DAX)_"
    return "`" + collapse_ws(measure.expression, cap) + "`"


def measure_source_summary(ctx: DocContext, name: str, max_cols: int = 4) -> str:
    """One-line summary of a measure's resolved physical sources."""
    derivs = ctx.trace.derivations(name)
    resolved: list[str] = []
    for d in derivs:
        if d.resolved:
            token = f"{d.view}.{d.source_column}"
            if token not in resolved:
                resolved.append(token)
    unresolved = sum(1 for d in derivs if not d.resolved and d.source_column)
    if resolved:
        shown = resolved[:max_cols]
        extra = len(resolved) - len(shown)
        summary = ", ".join(f"`{t}`" for t in shown)
        if extra > 0:
            summary += f" (+{extra} more)"
        if unresolved:
            summary += f"; {unresolved} column(s) not SQL-resolved"
        return summary
    if unresolved:
        return f"_no SQL-export-resolved source ({unresolved} column(s) unresolved)_"
    return "_no physical column source (computed from other measures)_"


def measure_inline_summary(ctx: DocContext, name: str) -> str:
    """A compact, self-sufficient one-liner for a measure used in another card.

    Combines the collapsed DAX with the resolved physical source so a concept
    card's branch line answers "what does this compute?" in one hop.
    """
    return f"{measure_dax_oneline(ctx, name, cap=160)} — sources: {measure_source_summary(ctx, name)}"


def source_trace_table(ctx: DocContext, name: str) -> str:
    """Full measure → SQL derivation table (one row per terminal column)."""
    derivs = ctx.trace.derivations(name)
    if not derivs:
        return "_No column references — computed entirely from other measures._"
    header = (
        "| Model column | Physical column | Dataflow entity | SQL view : line | Derivation / status |\n"
        "|---|---|---|---|---|"
    )
    rows: list[str] = []
    for d in sorted(derivs, key=lambda x: (x.table, x.column)):
        model_col = f"`{d.table}[{d.column}]`"
        phys = f"`{d.source_column}`" if d.source_column else "—"
        entity = f"`{d.dataflow_entity}`" if d.dataflow_entity else "—"
        if d.resolved:
            loc = f"`{d.view}` : {d.sql.line}"
            deriv = "`" + md.md_escape_pipe(collapse_ws(d.sql.expression, 140)) + "`"
        else:
            loc = f"`{d.view}`" if d.view else "—"
            deriv = f"_{md.md_escape_pipe(d.reason)}_"
        rows.append(f"| {model_col} | {phys} | {entity} | {loc} | {deriv} |")
    return header + "\n" + "\n".join(rows)


def trace_accounting(ctx: DocContext, name: str) -> str:
    """One-line resolved / unresolved accounting for a measure's source trace."""
    derivs = ctx.trace.derivations(name)
    if not derivs:
        return ""
    resolved = sum(1 for d in derivs if d.resolved)
    return f"_Source trace: {resolved}/{len(derivs)} column derivation(s) resolved to SQL._"


def page_list(ctx: DocContext, pages: set[str] | list[str]) -> str:
    """Render a sorted set of report page labels, or an explicit 'none'."""
    items = sorted({p for p in pages if p})
    if not items:
        return "_None._"
    return ", ".join(items)


def measure_link(ctx: DocContext, name: str) -> str:
    """Link to a measure's own card by its stable anchor."""
    return f"[{name}](#{card_anchor('measure', name)})"


def concept_link(ctx: DocContext, name: str) -> str:
    """Link to a metric concept card by its stable anchor."""
    return f"[{name}](#{card_anchor('concept', name)})"


def ref_link(ctx: DocContext, name: str) -> str:
    """Role-aware link to whichever card actually documents ``name``.

    A measure reference must point at the card that exists for that measure's
    role, not blindly at a ``measure-`` anchor:

    * **router** → its concept card;
    * **metric** / **compute** → its own measure card;
    * **base** → the table card it is folded into;
    * **selector** (or unknown) → no card; rendered as inline code.
    """
    mc = ctx.cls.by_name.get(name)
    if mc is None:
        return f"`{name}`"
    if mc.role == dax_refs.ROLE_ROUTER:
        return concept_link(ctx, name)
    if mc.role in (dax_refs.ROLE_METRIC, dax_refs.ROLE_COMPUTE):
        return measure_link(ctx, name)
    if mc.role == dax_refs.ROLE_BASE:
        return f"[{name}](#{card_anchor('table', mc.table)})"
    return f"`{name}`"


__all__ = [
    "Card",
    "DocContext",
    "build_context",
    "card_anchor",
    "render_card",
    "render_bundle",
    "collapse_ws",
    "measure_dax_oneline",
    "measure_source_summary",
    "measure_inline_summary",
    "source_trace_table",
    "trace_accounting",
    "page_list",
    "measure_link",
    "concept_link",
    "ref_link",
]
