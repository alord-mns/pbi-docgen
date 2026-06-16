"""Full-depth source tracing: measure → model columns → SQL derivations.

Joins the three foundation layers built in Phase A:

* ``dax_refs`` — which model columns / measures a measure depends on;
* ``sqlsource`` — the SQL view that derives each physical column;
* ``lineage`` — the parsed dataflows that feed each model table.

The trace follows two distinct layers that must **not** be conflated:

1. **Model → dataflow entity.** A model table's partition appends a chain of
   shared expressions (``factSalesOrdersGMOR``, ``BBOrders`` → ``BBPOS`` …);
   walking that chain yields ``[entity="X"]`` navigations. Each ``X`` is the
   name of a *dataflow query* (the dataflow's output entity).
2. **Dataflow entity → Databricks view.** Walking *that dataflow query's* own M
   (and any intra-dataflow query refs / cross-dataflow ``[entity=]`` deps)
   yields the terminal ``[Name="Y", Kind="Table"]`` Databricks tables. Each
   ``Y`` is a physical view whose name matches a ``sql/*.sql`` file stem.

The two names (``X`` and ``Y``) often coincide because a dataflow query is
deliberately named after the view it reads, but they are different objects at
different layers. Resolving them separately keeps the trace correct even when
the names diverge. Unresolved hops are recorded with a reason that names the
missing entity, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import dataflow as dfmod
from . import dax_refs
from . import lineage as lineagemod
from . import sqlsource
from . import tmdl

# Guard against pathological shared-expression recursion depth.
_MAX_EXPR_DEPTH = 25

# Identifier tokens inside an M body: bare ``Name`` or quoted ``#"Name"``.
_IDENT_RE = re.compile(r'#"((?:[^"]|"")*)"|([A-Za-z_][\w.]*)')


@dataclass(frozen=True)
class SourceDerivation:
    table: str  # model table
    column: str  # model column
    source_column: str  # physical column name
    dataflow_entity: str  # the [entity="X"] hop (dataflow query), if any
    view: str  # the Databricks view / SQL file stem (Y), if matched
    sql: sqlsource.SqlColumn | None = None
    reason: str = ""  # why no SQL derivation, when sql is None

    @property
    def resolved(self) -> bool:
        return self.sql is not None


@dataclass
class SourceTrace:
    # Model table -> dataflow entity names (the X hop).
    table_to_dataflow_entities: dict[str, list[str]] = field(default_factory=dict)
    # Model table -> Databricks views it reads directly (DirectQuery, rare).
    table_to_direct_views: dict[str, list[str]] = field(default_factory=dict)
    # Dataflow entity name -> Databricks view names (the X -> Y hop).
    dataflow_entity_to_views: dict[str, list[str]] = field(default_factory=dict)
    # Model table -> all resolved Databricks views (union of both hops).
    table_to_views: dict[str, list[str]] = field(default_factory=dict)
    measure_to_columns: dict[str, set[dax_refs.ColumnRef]] = field(default_factory=dict)
    measure_to_tables: dict[str, set[str]] = field(default_factory=dict)
    measure_to_sql: dict[str, list[SourceDerivation]] = field(default_factory=dict)
    dataflow_to_measures: dict[str, set[str]] = field(default_factory=dict)

    def derivations(self, measure: str) -> list[SourceDerivation]:
        return self.measure_to_sql.get(measure, [])


def _walk_m(start: str, expr_by_name: dict[str, str], collect) -> None:
    """Walk an M body and every same-scope expression it references.

    ``collect`` is called with each M body encountered. ``expr_by_name`` maps
    sibling expression / query names to their bodies (model shared expressions,
    or a single dataflow's queries). Cycle- and depth-guarded.
    """

    def walk(m_code: str, seen: set[str], depth: int) -> None:
        if depth > _MAX_EXPR_DEPTH or not m_code:
            return
        collect(m_code)
        masked = dax_refs.mask_literals(m_code)
        for quoted, bare in _IDENT_RE.findall(masked):
            ref = quoted.replace('""', '"') if quoted else bare
            if ref in expr_by_name and ref not in seen:
                seen.add(ref)
                walk(expr_by_name[ref], seen, depth + 1)

    walk(start, set(), 0)


def _resolve_table_dataflow_entities(
    table: tmdl.Table,
    model_expr_by_name: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Hop 1: model table -> (dataflow entity names, direct Databricks views).

    Walks the table's partition(s) plus the model shared-expression chain,
    collecting ``[entity="X"]`` dataflow references and any direct
    ``[Name="Y", Kind="Table"]`` Databricks references (the latter only for
    tables that bypass dataflows).
    """
    entities: list[str] = []
    direct_views: list[str] = []

    def collect(m_code: str) -> None:
        for ent in sqlsource.extract_dataflow_entities(m_code):
            if ent not in entities:
                entities.append(ent)
        for tbl in sqlsource.extract_databricks_tables(m_code):
            if tbl.table not in direct_views:
                direct_views.append(tbl.table)

    for partition in table.partitions:
        if partition.source_kind == "entity":
            if partition.source and partition.source not in entities:
                entities.append(partition.source)
        elif partition.source_kind in ("m", ""):
            _walk_m(partition.source, model_expr_by_name, collect)
    return entities, direct_views


def _build_dataflow_entity_to_views(
    dataflows: list[dfmod.Dataflow],
) -> dict[str, list[str]]:
    """Hop 2: dataflow entity name -> Databricks view names.

    For every dataflow query, walk its intra-dataflow reference graph to the
    terminal ``[Name=…, Kind="Table"]`` Databricks tables, and record any
    cross-dataflow ``[entity="…"]`` dependencies. A transitive closure then
    resolves chained (computed) entities that read from other dataflows.
    """
    direct: dict[str, list[str]] = {}
    deps: dict[str, set[str]] = {}
    for df in dataflows:
        expr_by_name = {q.name: q.expression for q in df.queries}
        for query in df.queries:
            if query.is_parameter:
                continue
            views: list[str] = []
            ext: set[str] = set()

            def collect(m_code: str, views=views, ext=ext) -> None:
                for tbl in sqlsource.extract_databricks_tables(m_code):
                    if tbl.table not in views:
                        views.append(tbl.table)
                ext.update(sqlsource.extract_dataflow_entities(m_code))

            _walk_m(query.expression, expr_by_name, collect)
            # Same-dataflow refs are already walked; keep only external deps.
            ext -= set(expr_by_name)
            bucket = direct.setdefault(query.name, [])
            for view in views:
                if view not in bucket:
                    bucket.append(view)
            deps.setdefault(query.name, set()).update(ext)

    def resolve(name: str, seen: frozenset[str]) -> list[str]:
        if name in seen:
            return []
        seen = seen | {name}
        out = list(direct.get(name, []))
        for dep in sorted(deps.get(name, set())):
            for view in resolve(dep, seen):
                if view not in out:
                    out.append(view)
        return out

    return {name: resolve(name, frozenset()) for name in sorted(set(direct) | set(deps))}


def build_source_trace(
    model: tmdl.Model,
    classification: dax_refs.Classification,
    sql_catalog: sqlsource.SqlCatalog,
    lineage: lineagemod.Lineage,
) -> SourceTrace:
    """Build the measure → SQL derivation and dataflow → measure maps."""
    index = classification.index
    trace = SourceTrace()

    # ---- hop 2 map: dataflow entity -> Databricks views ----
    trace.dataflow_entity_to_views = _build_dataflow_entity_to_views(lineage.dataflows)

    # ---- hop 1: model table -> dataflow entities (+ direct views) ----
    model_expr_by_name: dict[str, str] = {e.name: e.expression for e in model.expressions}
    column_obj: dict[tuple[str, str], tmdl.Column] = {}
    # Per-table ordered (dataflow_entity, view) candidate pairs for resolution.
    candidate_pairs: dict[str, list[tuple[str, str]]] = {}
    for table in model.tables:
        entities, direct_views = _resolve_table_dataflow_entities(table, model_expr_by_name)
        trace.table_to_dataflow_entities[table.name] = entities
        trace.table_to_direct_views[table.name] = direct_views
        pairs: list[tuple[str, str]] = []
        views: list[str] = []
        for entity in entities:
            for view in trace.dataflow_entity_to_views.get(entity, []):
                pairs.append((entity, view))
                if view not in views:
                    views.append(view)
        for view in direct_views:
            pairs.append(("", view))
            if view not in views:
                views.append(view)
        candidate_pairs[table.name] = pairs
        trace.table_to_views[table.name] = views
        for column in table.columns:
            column_obj[(table.name, column.name)] = column

    # ---- per-measure direct column / measure references ----
    direct_columns: dict[str, set[dax_refs.ColumnRef]] = {}
    for mc in classification.by_name.values():
        measure = index.measure_by_name.get(mc.name)
        expr = measure.expression if measure else ""
        direct_columns[mc.name] = dax_refs.extract_refs(expr, index).columns

    # ---- transitive expansion to terminal columns ----
    def expand(name: str, seen: set[str]) -> set[dax_refs.ColumnRef]:
        if name in seen:
            return set()
        seen.add(name)
        cols = set(direct_columns.get(name, set()))
        mc = classification.by_name.get(name)
        if mc:
            for ref in mc.measure_refs:
                cols |= expand(ref, seen)
        return cols

    for name in classification.by_name:
        cols = expand(name, set())
        trace.measure_to_columns[name] = cols
        trace.measure_to_tables[name] = {c.table for c in cols}

    # ---- resolve each terminal column to a SQL derivation ----
    for name, cols in trace.measure_to_columns.items():
        derivations: list[SourceDerivation] = []
        for ref in sorted(cols, key=lambda c: (c.table, c.column)):
            column = column_obj.get((ref.table, ref.column))
            if column is None:
                derivations.append(
                    SourceDerivation(ref.table, ref.column, "", "", "", None, "column not in model")
                )
                continue
            if column.is_calculated:
                derivations.append(
                    SourceDerivation(ref.table, ref.column, "", "", "", None, "calculated column (DAX)")
                )
                continue
            source_column = column.source_column or column.name
            pairs = candidate_pairs.get(ref.table, [])
            if not pairs:
                entities = trace.table_to_dataflow_entities.get(ref.table, [])
                if entities:
                    reason = "no SQL export for dataflow entity(ies): " + ", ".join(entities)
                    df_entity = entities[0]
                else:
                    reason = "no source entity resolved for table"
                    df_entity = ""
                derivations.append(
                    SourceDerivation(ref.table, ref.column, source_column, df_entity, "", None, reason)
                )
                continue
            resolved: SourceDerivation | None = None
            for df_entity, view in pairs:
                sql_col = sql_catalog.derivation(view, source_column)
                if sql_col is not None:
                    resolved = SourceDerivation(
                        ref.table, ref.column, source_column, df_entity, view, sql_col
                    )
                    break
            if resolved is None:
                views = []
                for _, view in pairs:
                    if view not in views:
                        views.append(view)
                df_entity0, view0 = pairs[0]
                reason = "column not found in SQL view(s): " + ", ".join(views)
                resolved = SourceDerivation(
                    ref.table, ref.column, source_column, df_entity0, view0, None, reason
                )
            derivations.append(resolved)
        trace.measure_to_sql[name] = derivations

    # ---- dataflow -> downstream measures (via the tables they feed) ----
    for name, tables in trace.measure_to_tables.items():
        for table_name in tables:
            for df_key in lineage.table_to_dataflows.get(table_name, set()):
                trace.dataflow_to_measures.setdefault(df_key, set()).add(name)

    return trace


__all__ = ["SourceDerivation", "SourceTrace", "build_source_trace"]
