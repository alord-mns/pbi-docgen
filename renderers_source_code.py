"""Renderer for ``04-source-code.md`` — the raw-code companion to the model docs.

The four core knowledge-base files answer *what a metric means* and *where a
number comes from* in prose (the two-hop source trace). This file answers the
different question *"show me the actual SQL / Power Query code"* without bloating
those cards, which would shatter their single-chunk retrieval.

The lineage is a many-to-many graph (one dataflow file → many entities; a model
table ← possibly many dataflows), but the **SQL view ↔ dataflow entity** link is
a clean 1:1 spine. Cards are therefore grained at that spine: one
**source-lineage card** per dataflow entity that a model table consumes,
co-locating the full Databricks view and the entity's Power Query M, with links
*up* to the consuming model table(s) and the parent dataflow. Views with no
consuming entity get a SQL-only card so no exported code is missing. Shared
connection parameters are factored into a single card with their (infrastructure)
values redacted.

Read-only over sources and idempotent: all iteration is sorted and code is
echoed verbatim (newline-normalised), except a conservative scrub that redacts
host literals passed to M connector functions.
"""
from __future__ import annotations

import re

from . import cards
from . import md
from .cards import DocContext

# Connector functions whose first quoted argument is a host / server identifier.
_CONNECTOR_HOST_RE = re.compile(
    r'((?:Sql\.Databases?|Web\.Contents|Odbc\.DataSource|OData\.Feed|'
    r'AzureBlobStorage\.\w+|AzureDataLake\.\w+)\s*\(\s*)"[^"]*"'
)
# Databricks connectors take (host, httpPath, …) as the first two literals; both
# are infrastructure identifiers. They are usually parameter *variables*, but
# redact the literal form when present.
_DATABRICKS_HOST_RE = re.compile(
    r'(Databricks\.\w+\s*\(\s*)"[^"]*"(\s*,\s*)"[^"]*"'
)
_REDACTED_HOST = '"{{redacted-host}}"'


def _scrub_m(m_code: str) -> str:
    """Redact host / server literals passed to M connector functions.

    Newlines are normalised to ``\\n`` first so the emitted code block is
    byte-stable across platforms (the dataflow JSON stores M with CRLF).

    The Databricks path uses parameter *variables* (``pHostName``), so entity M
    is normally clean; this catches the occasional inline
    ``Sql.Database("host", …)`` or ``Databricks.Catalogs("host", "path", …)``.
    Schema / table names are not sensitive and are left intact.
    """
    normalised = m_code.replace("\r\n", "\n").replace("\r", "\n")
    scrubbed = _CONNECTOR_HOST_RE.sub(
        lambda mo: mo.group(1) + _REDACTED_HOST, normalised
    )
    scrubbed = _DATABRICKS_HOST_RE.sub(
        lambda mo: mo.group(1) + _REDACTED_HOST + mo.group(2) + '"{{redacted-path}}"',
        scrubbed,
    )
    return scrubbed


def _entity_query_index(ctx: DocContext) -> dict[str, list[tuple[str, str]]]:
    """Map dataflow entity (query) name -> [(dataflow name, M expression)]."""
    idx: dict[str, list[tuple[str, str]]] = {}
    for df in sorted(ctx.dataflows, key=lambda d: d.name):
        for q in df.queries:
            if q.is_parameter or not q.expression:
                continue
            idx.setdefault(q.name, []).append((df.name, q.expression))
    for key in idx:
        idx[key] = sorted(idx[key])
    return idx


def _parameter_queries(ctx: DocContext) -> list[tuple[str, str]]:
    """Distinct (parameter name, dataflow name) pairs across all dataflows."""
    seen: set[tuple[str, str]] = set()
    for df in ctx.dataflows:
        for q in df.queries:
            if q.is_parameter and q.name:
                seen.add((q.name, df.name))
    return sorted(seen)


def _sql_block(ctx: DocContext, view_name: str) -> list[str]:
    """Render the fenced SQL block for a view, or ``[]`` if not exported."""
    view = ctx.sql_catalog.view(view_name)
    if not view or not view.raw:
        return []
    if view.origin == "native-query":
        label = (
            f"Native SQL query for `{view.fqn or view_name}` — inline in "
            f"`{view.source_file}`:"
        )
    else:
        label = f"View `{view.fqn or view_name}` — source `{view.source_file}`:"
    lines = [label, ""]
    lines.append("```sql")
    lines.append(view.raw.strip())
    lines.append("```")
    lines.append("")
    return lines


def _sql_heading(ctx: DocContext, views: list[str]) -> str:
    """Heading for the SQL section, reflecting whether sources are native."""
    origins = {
        ctx.sql_catalog.view(v).origin
        for v in views
        if ctx.sql_catalog.view(v)
    }
    if origins == {"native-query"}:
        return "### Native SQL query"
    if "native-query" in origins:
        return "### SQL source (Databricks view / native query)"
    return "### SQL view (Databricks)"


def _entity_card(
    ctx: DocContext,
    entity: str,
    views: list[str],
    tables: list[str],
    entity_idx: dict[str, list[tuple[str, str]]],
) -> cards.Card:
    parts: list[str] = []

    queries = entity_idx.get(entity, [])
    df_names = sorted({df for df, _ in queries})
    tbl_links = ", ".join(
        f"[{t}](#{cards.card_anchor('table', t)})" for t in sorted(tables)
    )
    df_links = ", ".join(
        f"[{d}](#{cards.card_anchor('dataflow', d)})" for d in df_names
    )
    view_str = ", ".join(f"`{v}`" for v in views) if views else "_no SQL export_"
    parts.append(
        f"**Chain:** table(s) {tbl_links or '—'} \u2190 dataflow {df_links or '—'} "
        f"\u00b7 entity `{entity}` \u2190 view {view_str}"
    )
    parts.append("")

    if entity in ctx.trace.ambiguous_entities:
        parts.append(
            f"**Ambiguous source:** the entity name `{md.md_escape_pipe(entity)}` is produced "
            "by more than one dataflow, so the lineage below may conflate them. Resolve by "
            "renaming the colliding entities, or disambiguate the reference by `dataflowId`."
        )
        parts.append("")

    parts.append(_sql_heading(ctx, views))
    parts.append("")
    sql_lines: list[str] = []
    for v in views:
        sql_lines.extend(_sql_block(ctx, v))
    if sql_lines:
        parts.extend(sql_lines)
    else:
        parts.append(
            "_No SQL export for this entity (e.g. CSV / manual upload / "
            "hand-maintained table)._"
        )
        parts.append("")

    parts.append("### Dataflow M (entity query)")
    parts.append("")
    if queries:
        for df_name, expr in queries:
            parts.append(f"From dataflow `{df_name}`:")
            parts.append("")
            parts.append("```powerquery-m")
            parts.append(_scrub_m(expr).strip())
            parts.append("```")
            parts.append("")
    else:
        parts.append("_No dataflow M query found for this entity._")
        parts.append("")

    keywords = [entity]
    keywords.extend(views)
    keywords.extend(f"SQL for {v}" for v in views)
    keywords.append(f"M code for {entity}")

    return cards.Card(
        anchor=cards.card_anchor("source-lineage", entity),
        title=f"Source lineage \u00b7 {entity}",
        kind="Source lineage (code)",
        subtitle=(f"view `{views[0]}`" if views else "Power Query M only"),
        keywords=tuple(dict.fromkeys(keywords)),
        body="\n".join(parts).rstrip(),
    )


def _view_only_card(ctx: DocContext, view_name: str) -> cards.Card:
    """SQL-only card for an exported view with no consuming model entity."""
    parts: list[str] = [
        f"**View:** `{view_name}` \u2014 exported Databricks view with no "
        "currently-resolved model consumer.",
        "",
        _sql_heading(ctx, [view_name]),
        "",
    ]
    parts.extend(_sql_block(ctx, view_name))
    return cards.Card(
        anchor=cards.card_anchor("source-lineage", view_name),
        title=f"Source lineage \u00b7 {view_name}",
        kind="Source lineage (code)",
        subtitle="SQL view (no resolved model consumer)",
        keywords=(view_name, f"SQL for {view_name}"),
        body="\n".join(parts).rstrip(),
    )


def _connection_card(ctx: DocContext, params: list[tuple[str, str]]) -> cards.Card:
    parts: list[str] = [
        "Shared Power Query parameters referenced by the dataflow M queries in "
        "the cards above. Their values are upstream **infrastructure identifiers** "
        "(Databricks host / SQL-warehouse path) and are redacted here.",
        "",
        "| Parameter | Dataflow | Value |",
        "|---|---|---|",
    ]
    for name, df_name in params:
        parts.append(
            f"| `{md.md_escape_pipe(name)}` | `{md.md_escape_pipe(df_name)}` "
            "| _(redacted infrastructure identifier)_ |"
        )
    return cards.Card(
        anchor=cards.card_anchor("connection", "parameters"),
        title="Connection parameters",
        kind="Source lineage (code)",
        subtitle="shared dataflow connection parameters (redacted)",
        keywords=("connection parameters", "pHostName", "pHTTPPath", "Databricks host"),
        body="\n".join(parts),
    )


def render_source_code(ctx: DocContext) -> str:
    """Assemble ``04-source-code.md``: source-lineage + connection-params cards."""
    entity_idx = _entity_query_index(ctx)

    # Invert table -> entities into entity -> consuming tables.
    entity_to_tables: dict[str, set[str]] = {}
    for table in sorted(ctx.trace.table_to_dataflow_entities):
        for ent in ctx.trace.table_to_dataflow_entities[table]:
            entity_to_tables.setdefault(ent, set()).add(table)

    cardlist: list[cards.Card] = []
    emitted_views: set[str] = set()
    for entity in sorted(entity_to_tables):
        views = list(ctx.trace.dataflow_entity_to_views.get(entity, []))
        cardlist.append(
            _entity_card(ctx, entity, views, sorted(entity_to_tables[entity]), entity_idx)
        )
        emitted_views.update(views)

    # SQL-only cards for exported views not reached through a consuming entity.
    for view_name in sorted(ctx.sql_catalog.views_by_entity):
        if view_name in emitted_views:
            continue
        view = ctx.sql_catalog.view(view_name)
        if not view or not view.raw:
            continue
        cardlist.append(_view_only_card(ctx, view_name))
        emitted_views.add(view_name)

    params = _parameter_queries(ctx)
    if params:
        cardlist.append(_connection_card(ctx, params))

    intro = (
        f"**Source-lineage cards:** {len(cardlist)}. "
        "Each card co-locates the full Databricks SQL view and the dataflow "
        "Power Query M that builds one source entity, linked up to the model "
        "table(s) it feeds. Use these for *\u201cshow me the actual SQL / M code\u201d* "
        "questions; use the **Source trace** section of a measure card for "
        "*\u201cwhere does this number come from\u201d*."
    )

    return cards.render_bundle(
        file_title="Source Code",
        purpose=(
            "The raw SQL view and Power Query M behind each source entity, grouped "
            "along the 1:1 view\u2194entity spine and linked to the model tables they "
            "feed \u2014 the literal-code companion to the prose source traces."
        ),
        audiences=("Data engineers", "Analytics engineers"),
        intro=intro,
        cards=cardlist,
    )


__all__ = ["render_source_code"]
