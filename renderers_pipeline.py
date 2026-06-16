"""Renderers for `02-data-pipeline.md` — source, dataflow, orchestration cards.

Each card is self-sufficient and bidirectional: a dataflow card carries its
upstream connectors **and** a dedicated **Downstream impact** section listing
the model tables, measures, and report pages that depend on it (derived from
the two-hop source trace, never guessed). Orchestration cards redact the items
flagged by §5 of ``documentation_req.md`` (recipient emails, Teams thread IDs).
"""
from __future__ import annotations

from pathlib import Path

from . import cards
from . import md
from . import orchestration as orcmod
from .cards import DocContext


# ---------------------------------------------------------------------------
# Redaction + trigger helpers (owned here now)
# ---------------------------------------------------------------------------
def _redact_recipient(raw: str) -> str:
    if not raw:
        return md.PLACEHOLDER
    if raw.startswith("19:"):
        return md.PLACEHOLDER + " _(Teams thread ID redacted)_"
    if "@" in raw:
        return md.PLACEHOLDER + " _(recipient email redacted)_"
    return md.md_escape_pipe(raw)


def _trigger_summary(flow: orcmod.Flow) -> str:
    if not flow.trigger:
        return "Started manually or by an external trigger not described in the workflow metadata."
    parts = [f"`{flow.trigger.type}` trigger"]
    if flow.trigger.frequency:
        parts.append(f"every `{flow.trigger.interval}` `{flow.trigger.frequency}`")
    if flow.trigger.week_days:
        parts.append(f"on {', '.join(flow.trigger.week_days)}")
    if flow.trigger.hours:
        parts.append(f"at {', '.join(flow.trigger.hours)}")
    if flow.trigger.time_zone:
        parts.append(f"(`{flow.trigger.time_zone}`)")
    return " ".join(parts) + "."


# ---------------------------------------------------------------------------
# Data-source card (config-driven)
# ---------------------------------------------------------------------------
def render_source_cards(ctx: DocContext) -> list[cards.Card]:
    out: list[cards.Card] = []
    for src in sorted(ctx.cfg.data_sources, key=lambda s: s.name):
        parts: list[str] = []
        if src.purpose:
            parts.append(f"> {md.md_escape_pipe(src.purpose)}")
            parts.append("")
        for label, value in (
            ("Connection mechanism", src.mechanism),
            ("Host", src.host),
            ("Freshness", src.freshness),
        ):
            if value:
                parts.append(f"- **{label}:** {md.md_escape_pipe(value)}")
        if src.connector_match:
            parts.append("- **Connector match:** " + ", ".join(f"`{c}`" for c in src.connector_match))
        if src.objects:
            parts.append("")
            parts.append("**Objects:**")
            for obj in src.objects:
                parts.append(f"- `{'.'.join(str(p) for p in obj if p)}`")
        if len(parts) <= 1:
            parts.append(f"_{md.PLACEHOLDER} — describe this source in `[[data_sources]]` of `.docgen.toml`._")
        parts.append("")
        parts.append("_Credentials are never recorded here — only the connection mechanism._")
        out.append(
            cards.Card(
                anchor=cards.card_anchor("source", src.name),
                title=src.name,
                kind="Data source",
                subtitle=md.md_escape_pipe(src.mechanism),
                body="\n".join(parts),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Dataflow card
# ---------------------------------------------------------------------------
def render_dataflow_card(ctx: DocContext, d) -> cards.Card:
    entities = {e.name for e in d.entities}

    # Downstream: model tables reading any of this dataflow's entities.
    down_tables = sorted(
        t.name
        for t in ctx.model.tables
        if entities & set(ctx.trace.table_to_dataflow_entities.get(t.name, []))
    )
    down_measures: set[str] = set()
    down_pages: set[str] = set()
    for t in down_tables:
        down_measures |= ctx.table_measures.get(t, set())
        down_pages |= ctx.lin.table_to_pages.get(t, set())

    # Databricks views this dataflow reads (per entity).
    views: list[str] = []
    for e in sorted(entities):
        for v in ctx.trace.dataflow_entity_to_views.get(e, []):
            if v not in views:
                views.append(v)

    parts: list[str] = []
    if d.description:
        parts.append(f"> {md.md_escape_pipe(d.description)}")
        parts.append("")
    parts.append(
        f"**Entities:** {len(d.entities)} · **Queries:** {len(d.queries)} · "
        f"**Connectors:** {', '.join(d.primary_data_sources()) or '—'}"
    )
    parts.append("")
    parts.append(f"_Source file: `{Path(d.source_file).name}` · last modified (export): `{d.modified_time or '—'}`_")

    parts.append("")
    parts.append("### Output entities")
    parts.append("")
    parts.append("| Entity | Attributes | Databricks view(s) |")
    parts.append("|---|---|---|")
    for e in sorted(d.entities, key=lambda x: x.name):
        ev = ctx.trace.dataflow_entity_to_views.get(e.name, [])
        ev_txt = ", ".join(f"`{v}`" for v in ev) or "—"
        parts.append(f"| `{md.md_escape_pipe(e.name)}` | {len(e.attributes)} | {ev_txt} |")

    parts.append("")
    parts.append("### Downstream impact")
    parts.append("")
    if down_tables:
        parts.append("**Model tables fed:** " + ", ".join(
            f"[{t}](#{cards.card_anchor('table', t)})" for t in down_tables
        ))
    else:
        parts.append("**Model tables fed:** _none traced._")
    parts.append("")
    if down_measures:
        sample = sorted(down_measures)[:25]
        parts.append(f"**Measures affected:** {len(down_measures)} (e.g. " + ", ".join(
            cards.ref_link(ctx, m) for m in sample
        ) + (" …)" if len(down_measures) > len(sample) else ")"))
    else:
        parts.append("**Measures affected:** _none traced._")
    parts.append("")
    parts.append("**Report pages affected:** " + cards.page_list(ctx, down_pages))

    return cards.Card(
        anchor=cards.card_anchor("dataflow", d.name),
        title=d.name,
        kind="Dataflow",
        subtitle=(f"reads {', '.join(f'`{v}`' for v in views[:3])}" if views else "Power Query transforms"),
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Orchestration flow card
# ---------------------------------------------------------------------------
def render_flow_card(ctx: DocContext, flow) -> cards.Card:
    parts: list[str] = []
    if flow.description:
        parts.append(f"> {md.md_escape_pipe(flow.description)}")
        parts.append("")
    parts.append(f"**Schedule:** {_trigger_summary(flow)}")
    if flow.workspace_ids:
        parts.append("")
        parts.append("**Workspaces:** " + ", ".join(f"`{w}`" for w in sorted(flow.workspace_ids)))

    if flow.refresh_targets:
        parts.append("")
        parts.append("### Refresh targets")
        parts.append("")
        parts.append("| Kind | Object | Resolved name |")
        parts.append("|---|---|---|")
        for tgt in flow.refresh_targets:
            name = ""
            if tgt.kind == "dataflow":
                name = ctx.lin.short_id_to_name.get(tgt.object_id, "")
            elif tgt.kind == "dataset":
                name = ctx.model.name
            obj = tgt.object_id[:8] + ("…" if len(tgt.object_id) > 8 else "")
            parts.append(f"| {tgt.kind} | `{obj}` | {md.md_escape_pipe(name) or '—'} |")

    if flow.notifications:
        parts.append("")
        parts.append("### Notifications")
        parts.append("")
        parts.append("| Channel | Mechanism | Recipient |")
        parts.append("|---|---|---|")
        for n in flow.notifications:
            parts.append(
                f"| {n.channel} | `{n.mechanism}` | {_redact_recipient(n.raw_recipient or n.recipient)} |"
            )

    return cards.Card(
        anchor=cards.card_anchor("flow", flow.name),
        title=flow.name,
        kind="Orchestration flow",
        subtitle=f"{len(flow.refresh_targets)} refresh target(s)",
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Runbook card
# ---------------------------------------------------------------------------
def render_runbook_card(ctx: DocContext) -> cards.Card:
    parts: list[str] = []
    parts.append("Operational runbook for the refresh pipeline.")
    parts.append("")
    parts.append("### Refresh orchestration")
    parts.append("")
    if ctx.flows:
        for flow in sorted(ctx.flows, key=lambda f: f.name):
            parts.append(
                f"- [{flow.name}](#{cards.card_anchor('flow', flow.name)}) — "
                f"{_trigger_summary(flow)} {len(flow.refresh_targets)} refresh target(s)."
            )
    else:
        parts.append("_No orchestration flows configured._")
    parts.append("")
    parts.append("### Failure handling")
    parts.append("")
    parts.append(f"- {md.PLACEHOLDER} — document known failure modes and recovery steps.")
    return cards.Card(
        anchor=cards.card_anchor("runbook", "refresh"),
        title="Refresh runbook",
        kind="Runbook",
        body="\n".join(parts),
    )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------
def render_data_pipeline(ctx: DocContext) -> str:
    cardlist: list[cards.Card] = []
    cardlist.extend(render_source_cards(ctx))
    for d in sorted(ctx.dataflows, key=lambda x: x.name):
        cardlist.append(render_dataflow_card(ctx, d))
    for flow in sorted(ctx.flows, key=lambda f: f.name):
        cardlist.append(render_flow_card(ctx, flow))
    cardlist.append(render_runbook_card(ctx))

    intro = (
        f"**Dataflows:** {len(ctx.dataflows)} · "
        f"**Orchestration flows:** {len(ctx.flows)} · "
        f"**Data sources:** {len(ctx.cfg.data_sources)}.\n\n"
        "Each dataflow card carries its upstream connectors and a "
        "**Downstream impact** section (model tables → measures → report pages) "
        "derived from the source trace."
    )

    return cards.render_bundle(
        file_title="Data Pipeline",
        purpose=(
            "Self-sufficient cards for every upstream data source, dataflow, and "
            "orchestration flow that feeds the semantic model — each with its "
            "Databricks views and downstream blast radius inline."
        ),
        audiences=("Data engineers", "Operations / Support"),
        intro=intro,
        cards=cardlist,
    )


__all__ = [
    "render_source_cards",
    "render_dataflow_card",
    "render_flow_card",
    "render_runbook_card",
    "render_data_pipeline",
]
