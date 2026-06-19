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


@dataclass(frozen=True)
class SqlFilter:
    text: str  # single (capped) predicate, e.g. "stk.siteID = 20145"
    line: int  # 1-based source line of the clause keyword
    kind: str  # "where" | "join" | "having"
    dynamic: bool = False  # references a runtime date (GETDATE/DATEADD/…)
    truncated: bool = False


@dataclass
class SqlView:
    entity: str  # file stem == table name == dataflow entity
    catalog: str = ""
    schema: str = ""
    table: str = ""
    source_file: str = ""  # repo-relative path
    columns: dict[str, SqlColumn] = field(default_factory=dict)
    filters: list[SqlFilter] = field(default_factory=list)  # top-level row scope
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

# ---------------------------------------------------------------------------
# Row-scope (WHERE / HAVING) extraction
# ---------------------------------------------------------------------------
# Keywords that terminate a top-level predicate when scanning forward.
_FILTER_BOUNDARY = frozenset(
    {
        "select", "from", "where", "group", "having", "order", "window",
        "qualify", "limit", "offset", "union", "intersect", "except",
        "on", "join", "inner", "left", "right", "full", "cross",
        "lateral", "using", "returning",
    }
)
# Functions whose presence makes a predicate a runtime (date) window rather than
# a static exclusion.
_DYNAMIC_RE = re.compile(
    r"\b(getdate|getutcdate|sysdate|now|current_date|current_timestamp|dateadd)\b",
    re.IGNORECASE,
)


# Tokens after which an opening paren introduces a *query container* (CTE body
# or derived table) we descend into, rather than a value sub-query we skip.
_TRANSPARENT_BEFORE_PAREN = frozenset(
    {"as", "from", "join", "union", "all", "intersect", "except", "("}
)


def _where_having_starts(masked: str) -> list[tuple[str, int]]:
    """Return ``(keyword, end_offset)`` for each row-scope ``WHERE`` / ``HAVING``.

    A clause counts when it sits at **opaque-subquery depth 0** — i.e. it belongs
    to the outer query or to a CTE / derived-table body (transparent containers),
    but not to a value sub-query such as ``IN (SELECT … WHERE …)`` or
    ``= (SELECT … WHERE …)`` (opaque containers, whose inner ``WHERE`` is part of
    a value computation, not the view's row scope).
    """
    starts: list[tuple[str, int]] = []
    odepth = 0
    case = 0
    opaque_stack: list[bool] = []
    prev_sig: str | None = None
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "(":
            opaque = prev_sig not in _TRANSPARENT_BEFORE_PAREN
            opaque_stack.append(opaque)
            if opaque:
                odepth += 1
            prev_sig = "("
            i += 1
            continue
        if ch == ")":
            if opaque_stack and opaque_stack.pop():
                odepth -= 1
            prev_sig = ")"
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (masked[j].isalnum() or masked[j] == "_"):
                j += 1
            word = masked[i:j].lower()
            if word == "case":
                case += 1
            elif word == "end" and case > 0:
                case -= 1
            elif word in ("where", "having") and odepth == 0 and case == 0:
                starts.append((word, j))
            prev_sig = word
            i = j
            continue
        if not ch.isspace():
            prev_sig = ch
        i += 1
    return starts


def _find_pred_end(masked: str, start: int) -> int:
    """Return the offset where the predicate beginning at ``start`` ends.

    The predicate ends at the next clause-boundary keyword (at its own paren/CASE
    depth 0) or when the enclosing container's closing ``)`` is reached.
    """
    paren = 0
    case = 0
    i = start
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "(":
            paren += 1
            i += 1
            continue
        if ch == ")":
            if paren == 0:
                return i
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
            elif word == "end" and case > 0:
                case -= 1
            elif paren == 0 and case == 0 and word in _FILTER_BOUNDARY:
                return i
            i = j
            continue
        i += 1
    return n


def _has_top_level_or(masked: str) -> bool:
    """True if the slice contains an ``OR`` at paren/CASE depth 0."""
    paren = 0
    case = 0
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
            elif word == "end" and case > 0:
                case -= 1
            elif word == "or" and paren == 0 and case == 0:
                return True
            i = j
            continue
        i += 1
    return False


def _split_predicate(original: str, masked: str) -> list[tuple[str, int]]:
    """Split a predicate into ``(text, offset)`` on top-level ``AND``.

    A predicate containing a top-level ``OR`` is kept whole, because splitting it
    on ``AND`` would misrepresent operator precedence.
    """
    if _has_top_level_or(masked):
        return [(original, 0)]
    items: list[tuple[str, int]] = []
    paren = 0
    case = 0
    between = 0
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
            elif word == "between" and paren == 0 and case == 0:
                between += 1
            elif word == "and" and paren == 0 and case == 0:
                if between > 0:
                    # The AND that belongs to a BETWEEN x AND y, not a separator.
                    between -= 1
                else:
                    items.append((original[start:i], start))
                    start = j
            i = j
        else:
            i += 1
    tail = original[start:]
    if tail.strip():
        items.append((tail, start))
    return items


def _extract_filters(
    original: str, masked: str, derivation_cap: int
) -> list[SqlFilter]:
    """Extract row-scope ``WHERE`` / ``HAVING`` predicates as :class:`SqlFilter`.

    Clauses in the outer query and in CTE / derived-table bodies are captured;
    clauses inside value sub-queries (``IN (SELECT … WHERE …)``,
    ``= (SELECT … WHERE …)``) are skipped, as those compute a value rather than
    scope the view's rows. ``JOIN … ON`` predicates are intentionally not
    captured (they are mostly join keys, not exclusions). Predicates duplicated
    across CTEs are de-duplicated; ``BETWEEN x AND y`` is kept whole.
    """
    filters: list[SqlFilter] = []
    seen: set[str] = set()
    for word, end in _where_having_starts(masked):
        pred_end = _find_pred_end(masked, end)
        pred_original = original[end:pred_end]
        pred_masked = masked[end:pred_end]
        for sub_text, sub_off in _split_predicate(pred_original, pred_masked):
            txt = re.sub(r"\s+", " ", sub_text).strip().strip(",").strip()
            if not txt:
                continue
            truncated = len(txt) > derivation_cap
            if truncated:
                txt = txt[:derivation_cap].rstrip() + " …"
            key = f"{word}\u0000{txt}"
            if key in seen:
                continue
            seen.add(key)
            line = original.count("\n", 0, end + sub_off) + 1
            dynamic = bool(_DYNAMIC_RE.search(sub_text))
            filters.append(
                SqlFilter(
                    text=txt,
                    line=line,
                    kind=word,
                    dynamic=dynamic,
                    truncated=truncated,
                )
            )
    filters.sort(key=lambda f: (f.line, f.text))
    return filters

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
    view.filters = _extract_filters(text, masked, derivation_cap)
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
