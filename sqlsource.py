"""SQL view + dataflow-source parsing for full-depth lineage tracing.

This module turns the upstream-most artefacts into a lookup that answers:
"given a physical column on a source entity, what is its derivation, and in
which SQL file / line?"

Two inputs are parsed:

1. **SQL view exports** (``sql/*.sql``) — reference exports of the Databricks
   views that back each dataflow entity. Each file is a
   ``CREATE OR REPLACE VIEW <catalog.schema.table> AS SELECT … FROM …``. The
   parser captures the fully-qualified view name and, for every top-level
   SELECT item, the output column name + its full derivation expression +
   source line. File stem == Databricks table == dataflow entity name by
   convention; ``[sql_sources]`` in ``.docgen.toml`` overrides exceptions.

2. **Dataflow M** (``dataflows/*.json`` query expressions) — the Databricks
   ``Source{[Name="…", Kind="Database/Schema/Table"]}`` navigation chain, which
   yields the ``catalog.schema.table`` an entity reads from. Used to confirm
   the SQL mapping and to describe entities that have no SQL export.

Read-only and solution-agnostic: derivation length caps and any naming
exceptions are parameters supplied by the caller from config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DERIVATION_CAP = 400


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SqlColumn:
    name: str  # output column, e.g. "bimev_num"
    expression: str  # full (capped) SELECT-item derivation
    line: int  # 1-based source line
    truncated: bool = False


@dataclass
class SqlView:
    entity: str  # file stem == table name == dataflow entity
    catalog: str = ""
    schema: str = ""
    table: str = ""
    source_file: str = ""  # repo-relative path
    columns: dict[str, SqlColumn] = field(default_factory=dict)
    _lower: dict[str, str] = field(default_factory=dict)  # lowercased name -> exact

    @property
    def fqn(self) -> str:
        parts = [p for p in (self.catalog, self.schema, self.table) if p]
        return ".".join(parts)

    def column(self, name: str) -> SqlColumn | None:
        if name in self.columns:
            return self.columns[name]
        exact = self._lower.get(name.lower())
        return self.columns.get(exact) if exact else None


@dataclass(frozen=True)
class DatabricksTable:
    catalog: str = ""
    schema: str = ""
    table: str = ""

    @property
    def fqn(self) -> str:
        parts = [p for p in (self.catalog, self.schema, self.table) if p]
        return ".".join(parts)


@dataclass
class SqlCatalog:
    views_by_entity: dict[str, SqlView] = field(default_factory=dict)

    def view(self, entity: str) -> SqlView | None:
        return self.views_by_entity.get(entity)

    def derivation(self, entity: str, column: str) -> SqlColumn | None:
        view = self.views_by_entity.get(entity)
        return view.column(column) if view else None


# ---------------------------------------------------------------------------
# SQL masking
# ---------------------------------------------------------------------------
def _mask_sql(sql: str) -> str:
    """Replace string literals and comments with spaces (length preserved)."""
    out = list(sql)
    i = 0
    n = len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if in_str:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":  # escaped quote
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                in_str = False
                out[i] = " "
            elif ch != "\n":
                out[i] = " "
            i += 1
            continue
        if ch == "'":
            in_str = True
            out[i] = " "
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":  # -- line comment
            while i < n and sql[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":  # /* block */
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (sql[i] == "*" and i + 1 < n and sql[i + 1] == "/"):
                if sql[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# SQL view parsing
# ---------------------------------------------------------------------------
_CREATE_VIEW_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+"
    r"(?:if\s+not\s+exists\s+)?([`\w.]+)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z_]\w*")


def _find_select_list(masked: str) -> tuple[int, int] | None:
    """Return ``(list_start, from_start)`` byte offsets for the top SELECT list.

    ``list_start`` is just after the first top-level SELECT keyword;
    ``from_start`` is the matching top-level FROM (paren depth 0, CASE depth 0).
    """
    paren = 0
    case = 0
    select_start: int | None = None
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "(":
            paren += 1
            i += 1
            continue
        if ch == ")":
            paren -= 1
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (masked[j].isalnum() or masked[j] == "_"):
                j += 1
            word = masked[i:j].lower()
            if word == "case":
                case += 1
            elif word == "end":
                if case > 0:
                    case -= 1
            elif word == "select" and select_start is None and paren == 0:
                select_start = j
            elif word == "from" and select_start is not None and paren == 0 and case == 0:
                return select_start, i
            i = j
            continue
        i += 1
    return None


def _split_select_items(original: str, masked: str, base: int) -> list[tuple[str, int]]:
    """Split a SELECT list into ``(item_text, offset_in_original)`` at base."""
    items: list[tuple[str, int]] = []
    paren = 0
    case = 0
    start = 0
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "(":
            paren += 1
            i += 1
        elif ch == ")":
            paren -= 1
            i += 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (masked[j].isalnum() or masked[j] == "_"):
                j += 1
            word = masked[i:j].lower()
            if word == "case":
                case += 1
            elif word == "end" and case > 0:
                case -= 1
            i = j
        elif ch == "," and paren == 0 and case == 0:
            items.append((original[start:i], base + start))
            start = i + 1
            i += 1
        else:
            i += 1
    tail = original[start:]
    if tail.strip():
        items.append((tail, base + start))
    return items


def _output_column(item_original: str, item_masked: str) -> str:
    """Return the output column name of a SELECT item."""
    alias = _alias_after_as(item_masked)
    if alias is not None:
        return item_original[alias[0] : alias[1]].strip().strip("`\"")
    # No AS — use the trailing identifier of a dotted/simple reference.
    trailing = re.search(r"([`\"]?[A-Za-z_]\w*[`\"]?)\s*$", item_original.strip())
    if trailing:
        return trailing.group(1).strip("`\"")
    return item_original.strip()


def _alias_after_as(item_masked: str) -> tuple[int, int] | None:
    """Return (start,end) of the identifier after the last top-level AS."""
    paren = 0
    case = 0
    last: tuple[int, int] | None = None
    i = 0
    n = len(item_masked)
    while i < n:
        ch = item_masked[i]
        if ch == "(":
            paren += 1
            i += 1
        elif ch == ")":
            paren -= 1
            i += 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (item_masked[j].isalnum() or item_masked[j] == "_"):
                j += 1
            word = item_masked[i:j].lower()
            if word == "case":
                case += 1
            elif word == "end" and case > 0:
                case -= 1
            elif word == "as" and paren == 0 and case == 0:
                # capture the next identifier
                k = j
                while k < n and item_masked[k].isspace():
                    k += 1
                if k < n and (item_masked[k].isalpha() or item_masked[k] in "_`\""):
                    e = k
                    while e < n and (item_masked[e].isalnum() or item_masked[e] in "_`\""):
                        e += 1
                    last = (k, e)
            i = j
        else:
            i += 1
    return last


def _split_fqn(raw: str) -> tuple[str, str, str]:
    parts = [p.strip("`\"") for p in raw.split(".")]
    parts = [p for p in parts if p]
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return "", parts[0], parts[1]
    if len(parts) == 1:
        return "", "", parts[0]
    return "", "", ""


def parse_sql_view(
    path: Path,
    *,
    entity: str | None = None,
    repo_root: Path | None = None,
    derivation_cap: int = DEFAULT_DERIVATION_CAP,
) -> SqlView:
    """Parse a single ``CREATE VIEW`` SQL file into a :class:`SqlView`."""
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_sql(text)
    rel = str(path.relative_to(repo_root)) if repo_root else path.name
    view = SqlView(entity=entity or path.stem, source_file=rel.replace("\\", "/"))

    create = _CREATE_VIEW_RE.search(masked)
    if create:
        view.catalog, view.schema, view.table = _split_fqn(
            text[create.start(1) : create.end(1)]
        )
    if not view.table:
        view.table = view.entity

    bounds = _find_select_list(masked)
    if not bounds:
        return view
    list_start, from_start = bounds
    list_original = text[list_start:from_start]
    list_masked = masked[list_start:from_start]
    for item_text, offset in _split_select_items(list_original, list_masked, list_start):
        item = item_text.strip()
        if not item:
            continue
        item_masked = masked[offset : offset + len(item_text)]
        # Re-align masked slice to the stripped item.
        lead = len(item_text) - len(item_text.lstrip())
        col = _output_column(item, item_masked[lead : lead + len(item)])
        if not col:
            continue
        expr = re.sub(r"\s+", " ", item).strip()
        truncated = len(expr) > derivation_cap
        if truncated:
            expr = expr[:derivation_cap].rstrip() + " …"
        line = text.count("\n", 0, offset + lead) + 1
        sql_col = SqlColumn(name=col, expression=expr, line=line, truncated=truncated)
        view.columns[col] = sql_col
        view._lower[col.lower()] = col
    return view


def load_sql_catalog(
    sql_files: list[Path],
    *,
    overrides: dict[str, str] | None = None,
    repo_root: Path | None = None,
    derivation_cap: int = DEFAULT_DERIVATION_CAP,
) -> SqlCatalog:
    """Parse all SQL view files into a :class:`SqlCatalog` keyed by entity.

    ``overrides`` maps a dataflow entity name to a SQL filename (stem or
    ``name.sql``) for cases where the file stem does not match the entity.
    """
    catalog = SqlCatalog()
    by_stem: dict[str, Path] = {}
    for path in sorted(sql_files):
        view = parse_sql_view(
            path, repo_root=repo_root, derivation_cap=derivation_cap
        )
        catalog.views_by_entity[view.entity] = view
        by_stem[path.stem] = path

    for entity, target in (overrides or {}).items():
        stem = target[:-4] if target.lower().endswith(".sql") else target
        path = by_stem.get(stem)
        if path is None:
            continue
        view = parse_sql_view(
            path, entity=entity, repo_root=repo_root, derivation_cap=derivation_cap
        )
        catalog.views_by_entity[entity] = view
    return catalog


# ---------------------------------------------------------------------------
# Dataflow M navigation parsing
# ---------------------------------------------------------------------------
_NAV_RE = re.compile(
    r"\[\s*Name\s*=\s*\"([^\"]+)\"\s*,\s*Kind\s*=\s*\"(Database|Schema|Table)\"\s*\]"
)


def extract_databricks_tables(m_code: str) -> list[DatabricksTable]:
    """Return the ``catalog.schema.table`` triples referenced in an M query.

    Walks the ``Source{[Name=…, Kind="Database"]}…{Kind="Schema"}…{Kind="Table"}``
    navigation chain. A query that joins or appends multiple tables yields one
    triple per ``Kind="Table"`` step, each carrying the most recent
    Database/Schema seen before it.
    """
    catalog = ""
    schema = ""
    tables: list[DatabricksTable] = []
    for name, kind in _NAV_RE.findall(m_code or ""):
        if kind == "Database":
            catalog = name
        elif kind == "Schema":
            schema = name
        elif kind == "Table":
            tables.append(DatabricksTable(catalog=catalog, schema=schema, table=name))
    return tables


# Power BI / Power Platform dataflow entity navigation, e.g.
# ``…{[entity="fact_orders_fhbwk"]}[Data]`` or ``{[entity="X",version=""]}``.
# This is a *dataflow-layer* reference (a dataflow query name), distinct from a
# Databricks ``[Name=…, Kind="Table"]`` *storage-layer* reference. They must not
# be conflated even when a dataflow query is deliberately named after its view.
_ENTITY_RE = re.compile(r"\[\s*entity\s*=\s*\"([^\"]+)\"")


def extract_dataflow_entities(m_code: str) -> list[str]:
    """Return the dataflow entity names an M query reads via ``[entity="…"]``.

    These are *dataflow-layer* references — the names of queries/entities in a
    (possibly different) Power BI dataflow — not Databricks table names. Use
    :func:`extract_databricks_tables` for the storage-layer references.
    """
    seen: list[str] = []
    for match in _ENTITY_RE.findall(m_code or ""):
        if match and match not in seen:
            seen.append(match)
    return seen


__all__ = [
    "DEFAULT_DERIVATION_CAP",
    "SqlColumn",
    "SqlView",
    "DatabricksTable",
    "SqlCatalog",
    "parse_sql_view",
    "load_sql_catalog",
    "extract_databricks_tables",
    "extract_dataflow_entities",
]
