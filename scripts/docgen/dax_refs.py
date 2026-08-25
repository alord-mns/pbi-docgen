"""DAX reference extraction, SWITCH-branch parsing, and measure-role
classification.

Portable and model-agnostic: this module interprets DAX. Every value that
*names* a convention (selector-measure prefixes, atomic-base prefixes) is
injected by the caller from ``.docgen.toml`` — nothing solution-specific is
hard-coded here.

Three layers, each usable on its own:

1. **Reference extraction** — ``build_index`` + ``extract_refs`` turn a DAX
   string into resolved column / measure references (plus an ``unresolved``
   bucket for anything the model index cannot place). String literals and
   comments are masked first so brackets, parentheses and commas inside them
   never confuse the parser.

2. **SWITCH parsing** — ``parse_switch`` resolves single-variable
   ``VAR … RETURN`` indirection and returns the per-branch
   ``label → target measure`` mapping that concept cards need.

3. **Role classification** — ``classify_measures`` ports the dependency-graph
   taxonomy (selector / router / metric / compute / base) from the Segment-1
   curation pass into the engine, operating on the in-memory model rather than
   re-parsing TMDL. Visual-usage counts are injected by the caller (the engine
   already walks PBIR elsewhere), keeping this module free of report-parsing
   concerns.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import tmdl


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_SELECTOR = "selector"
ROLE_ROUTER = "router"
ROLE_METRIC = "metric"
ROLE_COMPUTE = "compute"
ROLE_BASE = "base"

VALID_ROLES = frozenset(
    {ROLE_SELECTOR, ROLE_ROUTER, ROLE_METRIC, ROLE_COMPUTE, ROLE_BASE}
)


# ---------------------------------------------------------------------------
# Literal / comment masking
# ---------------------------------------------------------------------------
def mask_literals(expr: str) -> str:
    """Return ``expr`` with string literals and comments replaced by spaces.

    Newlines are preserved so line-oriented scans keep their geometry; every
    other masked character becomes a single space, leaving a string the same
    length as the input so offsets line up with the original text.
    """
    if not expr:
        return expr
    out = list(expr)
    i = 0
    n = len(expr)
    in_str = False
    while i < n:
        ch = expr[i]
        if in_str:
            if ch == '"':
                if i + 1 < n and expr[i + 1] == '"':  # escaped quote
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                in_str = False
                out[i] = " "
            elif ch != "\n":
                out[i] = " "
            i += 1
            continue
        if ch == '"':
            in_str = True
            out[i] = " "
            i += 1
            continue
        if ch == "/" and i + 1 < n and expr[i + 1] == "/":  # // line comment
            while i < n and expr[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "-" and i + 1 < n and expr[i + 1] == "-":  # -- line comment
            while i < n and expr[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and expr[i + 1] == "*":  # /* block */
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (
                expr[i] == "*" and i + 1 < n and expr[i + 1] == "/"
            ):
                if expr[i] != "\n":
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
# Model index
# ---------------------------------------------------------------------------
@dataclass
class DaxIndex:
    """Pre-computed lookups over a model, built once and reused."""

    measure_names: set[str] = field(default_factory=set)
    measure_by_name: dict[str, tmdl.Measure] = field(default_factory=dict)
    columns_by_table: dict[str, set[str]] = field(default_factory=dict)
    column_to_tables: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    table_names: set[str] = field(default_factory=set)


def build_index(model: tmdl.Model) -> DaxIndex:
    """Build a :class:`DaxIndex` from a parsed model."""
    index = DaxIndex()
    for table in model.tables:
        index.table_names.add(table.name)
        cols = index.columns_by_table.setdefault(table.name, set())
        for column in table.columns:
            cols.add(column.name)
            index.column_to_tables[column.name].add(table.name)
        for measure in table.measures:
            index.measure_names.add(measure.name)
            index.measure_by_name[measure.name] = measure
    return index


# ---------------------------------------------------------------------------
# Reference extraction
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ColumnRef:
    table: str
    column: str

    @property
    def qualified(self) -> str:
        return f"{self.table}[{self.column}]"


@dataclass
class DaxRefs:
    columns: set[ColumnRef] = field(default_factory=set)
    measures: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)


# Optional quoted/unquoted table prefix immediately followed by a bracket.
_REF_RE = re.compile(
    r"(?:'((?:[^']|'')*)'|([A-Za-z_]\w*))?\[([^\]]+)\]"
)


def extract_refs(expr: str, index: DaxIndex) -> DaxRefs:
    """Resolve column / measure references in a DAX expression.

    Qualified references (``Table[X]`` / ``'Quoted Table'[X]``) resolve against
    the table's columns first, then the global measure set. Bare references
    (``[X]``) resolve to a measure, else to a uniquely-named column, else land
    in ``unresolved``.
    """
    refs = DaxRefs()
    if not expr:
        return refs
    masked = mask_literals(expr)
    for m in _REF_RE.finditer(masked):
        member = m.group(3).strip()
        if not member:
            continue
        quoted, bare_tbl = m.group(1), m.group(2)
        if quoted is not None:
            table = quoted.replace("''", "'")
        else:
            table = bare_tbl or ""
        if table:
            cols = index.columns_by_table.get(table)
            if cols is not None and member in cols:
                refs.columns.add(ColumnRef(table, member))
            elif member in index.measure_names:
                refs.measures.add(member)
            elif table in index.table_names:
                # Unknown member on a known table — treat as a column ref so the
                # source trace still has something to chase.
                refs.columns.add(ColumnRef(table, member))
            else:
                refs.unresolved.add(f"{table}[{member}]")
        else:
            if member in index.measure_names:
                refs.measures.add(member)
            else:
                tables = index.column_to_tables.get(member)
                if tables and len(tables) == 1:
                    refs.columns.add(ColumnRef(next(iter(tables)), member))
                else:
                    refs.unresolved.add(f"[{member}]")
    return refs


def referenced_measures(expr: str, index: DaxIndex) -> set[str]:
    """Convenience: just the measure names referenced by ``expr``."""
    return extract_refs(expr, index).measures


# ---------------------------------------------------------------------------
# Low-level expression splitting
# ---------------------------------------------------------------------------
_OPENERS = "([{"
_CLOSERS = ")]}"


def _match_paren(masked: str, open_idx: int) -> int:
    """Return the index of the ``)`` that closes the ``(`` at ``open_idx``."""
    depth = 0
    for i in range(open_idx, len(masked)):
        ch = masked[i]
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth == 0:
                return i
    return len(masked)


def _split_top_level(original: str, masked: str) -> list[str]:
    """Split ``original`` on top-level commas located via ``masked``."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(masked):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(original[start:i])
            start = i + 1
    parts.append(original[start:])
    return parts


def _depth0_keywords(masked: str) -> list[tuple[str, int, int]]:
    """Return ``(WORD, start, end)`` for VAR / RETURN tokens at paren depth 0."""
    out: list[tuple[str, int, int]] = []
    depth = 0
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch in _OPENERS:
            depth += 1
            i += 1
        elif ch in _CLOSERS:
            depth -= 1
            i += 1
        elif depth == 0 and (ch.isalpha() or ch == "_"):
            j = i
            while j < n and (masked[j].isalnum() or masked[j] == "_"):
                j += 1
            word = masked[i:j].upper()
            if word in ("VAR", "RETURN"):
                out.append((word, i, j))
            i = j
        else:
            i += 1
    return out


_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def effective_expression(expr: str) -> str:
    """Resolve single-variable ``VAR … RETURN`` indirection.

    ``VAR result = SWITCH(...) RETURN result`` collapses to the SWITCH so the
    branch parser sees through the wrapper. Multi-variable / computed RETURNs
    are returned unchanged.
    """
    if not expr:
        return expr
    seen: set[str] = set()
    current = expr
    while True:
        masked = mask_literals(current)
        keywords = _depth0_keywords(masked)
        returns = [k for k in keywords if k[0] == "RETURN"]
        if not returns:
            return current
        ret_start = returns[-1][2]
        return_expr = current[ret_start:].strip()
        token = return_expr.strip()
        if not (_IDENT_RE.match(token) and token not in seen):
            return return_expr
        # Build VAR name -> value slices from this scope.
        var_values: dict[str, str] = {}
        var_kws = [k for k in keywords if k[0] == "VAR"]
        boundaries = [k[1] for k in keywords]
        for kw, start, end in var_kws:
            name_match = re.match(r"\s*([A-Za-z_]\w*)\s*=", current[end:])
            if not name_match:
                continue
            name = name_match.group(1)
            value_start = end + name_match.end()
            nexts = [b for b in boundaries if b > start]
            value_end = min(nexts) if nexts else ret_start
            var_values[name] = current[value_start:value_end].strip()
        if token not in var_values:
            return return_expr
        seen.add(token)
        current = var_values[token]


# ---------------------------------------------------------------------------
# SWITCH parsing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Branch:
    label: str  # the selector value (or condition for SWITCH(TRUE()))
    target_measure: str | None  # resolved single measure, if any
    raw_result: str  # the full branch result expression


@dataclass
class SwitchInfo:
    is_switch: bool = False
    selector_refs: list[str] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)


def _single_measure(text: str, index: DaxIndex) -> str | None:
    refs = extract_refs(text, index)
    if len(refs.measures) == 1 and not refs.columns:
        return next(iter(refs.measures))
    return None


def _branch_pairs(args: list[str]) -> tuple[list[tuple[str, str]], str | None]:
    """Split SWITCH value/result args into pairs plus an optional default."""
    default: str | None = None
    items = list(args)
    if len(items) % 2 == 1:
        default = items.pop()
    pairs = [(items[i], items[i + 1]) for i in range(0, len(items), 2)]
    return pairs, default


def parse_switch(expr: str, index: DaxIndex) -> SwitchInfo:
    """Parse a top-level ``SWITCH`` (after VAR/RETURN deref) into branches."""
    effective = effective_expression(expr)
    masked = mask_literals(effective)
    stripped = masked.lstrip()
    if not stripped.upper().startswith("SWITCH"):
        return SwitchInfo()
    open_idx = masked.find("(", len(masked) - len(stripped))
    if open_idx < 0:
        return SwitchInfo()
    close_idx = _match_paren(masked, open_idx)
    inner_masked = masked[open_idx + 1 : close_idx]
    inner_orig = effective[open_idx + 1 : close_idx]
    args = _split_top_level(inner_orig, inner_masked)
    if len(args) < 2:
        return SwitchInfo()
    selector_arg = args[0].strip()
    rest = args[1:]
    is_true_form = selector_arg.upper().replace(" ", "") in ("TRUE()", "TRUE")

    branches: list[Branch] = []
    selector_refs: list[str] = []
    pairs, default = _branch_pairs(rest)

    if is_true_form:
        ordered: list[str] = []
        for condition, result in pairs:
            for measure in sorted(extract_refs(condition, index).measures):
                if measure not in ordered:
                    ordered.append(measure)
            branches.append(
                Branch(condition.strip(), _single_measure(result, index), result.strip())
            )
        selector_refs = ordered
    else:
        selector_refs = sorted(extract_refs(selector_arg, index).measures)
        for value, result in pairs:
            branches.append(
                Branch(value.strip(), _single_measure(result, index), result.strip())
            )
    if default is not None:
        branches.append(Branch("(default)", _single_measure(default, index), default.strip()))

    return SwitchInfo(is_switch=True, selector_refs=selector_refs, branches=branches)


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------
@dataclass
class MeasureClass:
    name: str
    table: str
    role: str
    secondary_roles: list[str] = field(default_factory=list)
    measure_refs: set[str] = field(default_factory=set)
    drivers: list[str] = field(default_factory=list)  # selector measures it branches on
    branch_targets: list[str] = field(default_factory=list)
    switch: SwitchInfo | None = None
    fan_in: int = 0
    fan_out: int = 0
    used_visuals: int = 0
    dax_lines: int = 0
    evidence: str = ""

    @property
    def is_router(self) -> bool:
        return self.role == ROLE_ROUTER

    @property
    def is_metric(self) -> bool:
        return self.role == ROLE_METRIC or ROLE_METRIC in self.secondary_roles


@dataclass
class Classification:
    by_name: dict[str, MeasureClass]
    index: DaxIndex
    selector_prefixes: tuple[str, ...] = ()
    base_prefixes: tuple[str, ...] = ()

    def role(self, name: str) -> str:
        mc = self.by_name.get(name)
        return mc.role if mc else ""

    def of_role(self, role: str) -> list[MeasureClass]:
        return [mc for mc in self.by_name.values() if mc.role == role]

    def counts(self) -> dict[str, int]:
        out = {r: 0 for r in (ROLE_ROUTER, ROLE_METRIC, ROLE_COMPUTE, ROLE_BASE, ROLE_SELECTOR)}
        for mc in self.by_name.values():
            out[mc.role] = out.get(mc.role, 0) + 1
        return out


def _has_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(p.lower()) for p in prefixes if p)


def classify_measures(
    model: tmdl.Model,
    *,
    visual_usage: dict[str, int] | None = None,
    selector_prefixes: tuple[str, ...] = (),
    base_prefixes: tuple[str, ...] = (),
    index: DaxIndex | None = None,
) -> Classification:
    """Classify every measure in ``model`` by dependency-chain role.

    ``visual_usage`` maps measure name → number of report visuals it appears
    in (injected by the caller). ``selector_prefixes`` identify picker / driver
    measures; ``base_prefixes`` identify atomic source-wrapper measures. Both
    come from ``.docgen.toml`` so this module stays solution-agnostic.
    """
    index = index or build_index(model)
    usage = visual_usage or {}
    selector_prefixes = tuple(selector_prefixes)
    base_prefixes = tuple(base_prefixes)

    measures = list(tmdl.iter_measures(model))
    names = {m.name for m in measures}

    def is_selector(name: str) -> bool:
        return _has_prefix(name, selector_prefixes)

    # ---- dependency graph (measure -> measure) ----
    refs_by: dict[str, set[str]] = {}
    callers: dict[str, set[str]] = defaultdict(set)
    for m in measures:
        refs = {r for r in referenced_measures(m.expression, index) if r != m.name and r in names}
        refs_by[m.name] = refs
        for r in refs:
            callers[r].add(m.name)
    for name in names:
        refs_by.setdefault(name, set())
        callers.setdefault(name, set())
    fan_out = {name: len(refs_by[name]) for name in names}
    fan_in = {name: len(callers[name]) for name in names}

    drivers_by = {
        m.name: sorted({r for r in refs_by[m.name] if is_selector(r)})
        for m in measures
    }
    router_names = {
        name for name, drivers in drivers_by.items() if drivers and not is_selector(name)
    }

    # Metrics = business endpoints: visual-facing, router-selected, or terminal.
    router_branch_targets: set[str] = set()
    for router in router_names:
        drivers = set(drivers_by[router])
        for ref in refs_by[router]:
            if ref in drivers or is_selector(ref):
                continue
            router_branch_targets.add(ref)

    def is_base(name: str) -> bool:
        if is_selector(name):
            return False
        if _has_prefix(name, base_prefixes):
            return True
        return not refs_by[name] and not drivers_by[name]

    visual_metrics = {
        m.name for m in measures if usage.get(m.name, 0) > 0 and not is_selector(m.name)
    }
    terminal_metrics = {
        m.name
        for m in measures
        if not refs_by[m.name] and not is_selector(m.name) and not _has_prefix(m.name, base_prefixes)
    }
    metric_names = (visual_metrics | router_branch_targets | terminal_metrics) - router_names

    by_name: dict[str, MeasureClass] = {}
    for m in measures:
        name = m.name
        drivers = drivers_by[name]
        base = is_base(name)
        metric = name in metric_names or (base and name in router_branch_targets)
        selector = is_selector(name)
        router = name in router_names

        if selector:
            role = ROLE_SELECTOR
        elif router:
            role = ROLE_ROUTER
        elif metric:
            role = ROLE_METRIC
        elif base:
            role = ROLE_BASE
        else:
            role = ROLE_COMPUTE

        secondary: list[str] = []
        if role != ROLE_BASE and base:
            secondary.append(ROLE_BASE)
        if role != ROLE_METRIC and metric:
            secondary.append(ROLE_METRIC)

        if router:
            evidence = "selector_driver=" + ";".join(drivers)
        elif name in router_branch_targets:
            router_callers = sorted(callers[name] & router_names)
            evidence = "router_branch_target=" + ";".join(router_callers)
        elif usage.get(name, 0) > 0:
            evidence = f"visual_usage={usage[name]}"
        elif base:
            evidence = "atomic_base"
        else:
            evidence = "dependency_intermediate"

        switch = parse_switch(m.expression, index) if router else None
        branch_targets = sorted(
            {b.target_measure for b in switch.branches if b.target_measure}
        ) if switch else []

        by_name[name] = MeasureClass(
            name=name,
            table=m.table,
            role=role,
            secondary_roles=secondary,
            measure_refs=refs_by[name],
            drivers=drivers,
            branch_targets=branch_targets,
            switch=switch,
            fan_in=fan_in[name],
            fan_out=fan_out[name],
            used_visuals=usage.get(name, 0),
            dax_lines=(m.expression.count("\n") + 1) if m.expression else 0,
            evidence=evidence,
        )

    return Classification(
        by_name=by_name,
        index=index,
        selector_prefixes=selector_prefixes,
        base_prefixes=base_prefixes,
    )


__all__ = [
    "ROLE_SELECTOR",
    "ROLE_ROUTER",
    "ROLE_METRIC",
    "ROLE_COMPUTE",
    "ROLE_BASE",
    "VALID_ROLES",
    "mask_literals",
    "DaxIndex",
    "build_index",
    "ColumnRef",
    "DaxRefs",
    "extract_refs",
    "referenced_measures",
    "effective_expression",
    "Branch",
    "SwitchInfo",
    "parse_switch",
    "MeasureClass",
    "Classification",
    "classify_measures",
]
