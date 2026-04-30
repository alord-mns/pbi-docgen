"""TMDL parser for Power BI semantic models.

Parses the tab-indented TMDL format under
``src/semantic-model/<name>.SemanticModel/definition/`` into plain
dataclasses suitable for rendering Markdown documentation.

The parser is intentionally tolerant: it handles only the constructs that
appear in the FH&B Weekly model (table / measure / column / partition /
calculationGroup / hierarchy / relationship / expression / role) and
leaves unrecognised lines on the owning entity's ``raw`` list so they can
be inspected later if needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Column:
    name: str
    data_type: str = ""
    format_string: str = ""
    source_column: str = ""
    summarize_by: str = ""
    is_hidden: bool = False
    is_calculated: bool = False  # ``column X = <DAX>``
    expression: str = ""  # DAX for calculated columns
    description: str = ""
    lineage_tag: str = ""
    display_folder: str = ""
    sort_by_column: str = ""
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class Measure:
    name: str
    expression: str = ""
    format_string: str = ""
    display_folder: str = ""
    description: str = ""
    is_hidden: bool = False
    lineage_tag: str = ""
    annotations: dict[str, str] = field(default_factory=dict)
    table: str = ""  # back-reference filled by load_model


@dataclass
class Partition:
    name: str
    mode: str = ""  # import / directQuery / calculated / m / etc.
    source_kind: str = ""  # m / calculated / entity
    source: str = ""  # M expression, DAX, or entity reference


@dataclass
class CalculationItem:
    name: str
    expression: str = ""
    ordinal: str = ""
    format_string: str = ""
    description: str = ""


@dataclass
class CalculationGroup:
    precedence: str = ""
    items: list[CalculationItem] = field(default_factory=list)


@dataclass
class HierarchyLevel:
    name: str
    column: str = ""
    ordinal: str = ""


@dataclass
class Hierarchy:
    name: str
    description: str = ""
    levels: list[HierarchyLevel] = field(default_factory=list)


@dataclass
class Table:
    name: str
    description: str = ""
    is_hidden: bool = False
    is_private: bool = False
    lineage_tag: str = ""
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)
    calculation_group: CalculationGroup | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    source_file: str = ""

    @property
    def is_calculated(self) -> bool:
        return any(p.source_kind == "calculated" for p in self.partitions)

    @property
    def is_calculation_group(self) -> bool:
        return self.calculation_group is not None


@dataclass
class Relationship:
    name: str
    from_table: str = ""
    from_column: str = ""
    to_table: str = ""
    to_column: str = ""
    is_active: bool = True
    cross_filtering_behavior: str = "singleDirection"
    cardinality: str = ""  # rarely set explicitly; default one-to-many


@dataclass
class Expression:
    name: str
    kind: str = ""  # M / parameter
    expression: str = ""
    lineage_tag: str = ""
    query_group: str = ""
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class TablePermission:
    table: str
    filter_expression: str = ""


@dataclass
class Role:
    name: str
    model_permission: str = ""
    table_permissions: list[TablePermission] = field(default_factory=list)
    annotations: dict[str, str] = field(default_factory=dict)


@dataclass
class Model:
    name: str = ""
    culture: str = ""
    compatibility_level: str = ""
    query_groups: list[str] = field(default_factory=list)
    annotations: dict[str, str] = field(default_factory=dict)
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    expressions: list[Expression] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
_QUOTED = re.compile(r"^'((?:[^']|'')*)'(.*)$")


def _strip_name(token: str) -> str:
    """Strip surrounding single quotes from a TMDL identifier."""
    token = token.strip()
    m = _QUOTED.match(token)
    if m:
        return m.group(1).replace("''", "'")
    return token


def _indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == "\t":
            n += 1
        else:
            break
    return n


def _split_first(line: str, sep: str = " ") -> tuple[str, str]:
    if sep in line:
        a, b = line.split(sep, 1)
        return a, b
    return line, ""


def _take_block(
    lines: list[str], start: int, base_indent: int
) -> tuple[list[str], int]:
    """Return ``(body_lines, next_index)`` for an entity starting at ``start``.

    Body includes every subsequent line indented strictly deeper than
    ``base_indent`` (blanks are kept inside the block).
    """
    body: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            body.append(line)
            i += 1
            continue
        if _indent(line) <= base_indent:
            break
        body.append(line)
        i += 1
    # Trim trailing blanks
    while body and body[-1].strip() == "":
        body.pop()
    return body, i


def _extract_property(line: str) -> tuple[str, str] | None:
    """Match ``key: value`` property lines."""
    s = line.strip()
    if not s or s.startswith("///") or s.startswith("annotation"):
        return None
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", s)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def _join_dax(body: list[str], own_indent: int) -> str:
    """Join the DAX/M body lines that follow ``measure X =`` etc.

    Skips property and annotation lines (which sit at ``own_indent + 1``)
    and dedents the body relative to its first non-blank line.
    """
    code: list[str] = []
    for line in body:
        if line.strip() == "":
            code.append("")
            continue
        if _indent(line) <= own_indent + 1:
            # property or annotation belonging to the entity
            continue
        code.append(line)
    # Strip leading/trailing blanks
    while code and code[0].strip() == "":
        code.pop(0)
    while code and code[-1].strip() == "":
        code.pop()
    if not code:
        return ""
    non_blank = [_indent(line) for line in code if line.strip()]
    if not non_blank:
        return ""
    min_indent = min(non_blank)
    out = "\n".join(line[min_indent:] if line else "" for line in code)
    # Strip optional triple-backtick fences
    out = out.strip()
    if out.startswith("```"):
        out = out[3:]
    if out.endswith("```"):
        out = out[:-3]
    return out.strip()


# ---------------------------------------------------------------------------
# Entity parsers
# ---------------------------------------------------------------------------
def _parse_annotations(body: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body:
        s = line.strip()
        if s.startswith("annotation "):
            rest = s[len("annotation ") :]
            if " = " in rest:
                k, v = rest.split(" = ", 1)
                out[k.strip()] = v.strip()
    return out


def _collect_descriptions(lines: list[str], idx: int) -> str:
    """Walk backwards through `///` comment lines preceding ``idx``."""
    parts: list[str] = []
    j = idx - 1
    while j >= 0:
        s = lines[j].strip()
        if s.startswith("///"):
            parts.append(s.lstrip("/").strip())
            j -= 1
        elif s == "":
            j -= 1
        else:
            break
    return "\n".join(reversed(parts)).strip()


def _parse_columns_measures(
    body: list[str], parent_indent: int
) -> tuple[list[Column], list[Measure], list[Partition], list[Hierarchy], CalculationGroup | None]:
    columns: list[Column] = []
    measures: list[Measure] = []
    partitions: list[Partition] = []
    hierarchies: list[Hierarchy] = []
    calc_group: CalculationGroup | None = None

    i = 0
    while i < len(body):
        line = body[i]
        s = line.strip()
        if not s or s.startswith("///") or s.startswith("annotation"):
            i += 1
            continue
        ind = _indent(line)
        if ind != parent_indent + 1:
            i += 1
            continue
        head, rest = _split_first(s)

        if head == "column":
            col, consumed = _parse_column(body, i, ind)
            columns.append(col)
            i = consumed
        elif head == "measure":
            m, consumed = _parse_measure(body, i, ind)
            measures.append(m)
            i = consumed
        elif head == "partition":
            p, consumed = _parse_partition(body, i, ind)
            partitions.append(p)
            i = consumed
        elif head == "hierarchy":
            h, consumed = _parse_hierarchy(body, i, ind)
            hierarchies.append(h)
            i = consumed
        elif head == "calculationGroup":
            calc_group, consumed = _parse_calculation_group(body, i, ind)
            i = consumed
        elif head == "changedProperty":
            i += 1  # ignored
        else:
            i += 1
    return columns, measures, partitions, hierarchies, calc_group


def _parse_column(
    body: list[str], idx: int, own_indent: int
) -> tuple[Column, int]:
    line = body[idx]
    s = line.strip()
    head, rest = _split_first(s)
    rest = rest.strip()

    is_calc = "=" in rest
    name_part, expr_part = (rest.split("=", 1) if is_calc else (rest, ""))
    col = Column(
        name=_strip_name(name_part.strip()),
        is_calculated=is_calc,
        description=_collect_descriptions(body, idx),
    )
    block, next_i = _take_block(body, idx + 1, own_indent)

    if is_calc:
        col.expression = _join_dax(block, own_indent) or expr_part.strip()

    for bl in block:
        prop = _extract_property(bl)
        if prop is None:
            continue
        k, v = prop
        if k == "dataType":
            col.data_type = v
        elif k == "formatString":
            col.format_string = v.strip('"')
        elif k == "sourceColumn":
            col.source_column = v.strip('"')
        elif k == "summarizeBy":
            col.summarize_by = v
        elif k == "isHidden":
            col.is_hidden = v.lower() == "true"
        elif k == "lineageTag":
            col.lineage_tag = v
        elif k == "displayFolder":
            col.display_folder = v.strip('"')
        elif k == "sortByColumn":
            col.sort_by_column = v
    col.annotations = _parse_annotations(block)
    return col, next_i


def _parse_measure(
    body: list[str], idx: int, own_indent: int
) -> tuple[Measure, int]:
    line = body[idx]
    s = line.strip()
    _, rest = _split_first(s)
    # Measure name may be quoted; expression starts after first "=" outside quotes
    name = ""
    expr_inline = ""
    if rest.startswith("'"):
        m = _QUOTED.match(rest)
        if m:
            name = m.group(1).replace("''", "'")
            tail = m.group(2).strip()
            if tail.startswith("="):
                expr_inline = tail[1:].strip()
        else:
            name = rest
    else:
        if "=" in rest:
            n, e = rest.split("=", 1)
            name = n.strip()
            expr_inline = e.strip()
        else:
            name = rest.strip()

    measure = Measure(name=name, description=_collect_descriptions(body, idx))
    block, next_i = _take_block(body, idx + 1, own_indent)
    measure.expression = _join_dax(block, own_indent) or expr_inline

    for bl in block:
        prop = _extract_property(bl)
        if prop is None:
            continue
        k, v = prop
        if k == "formatString":
            measure.format_string = v.strip('"')
        elif k == "displayFolder":
            measure.display_folder = v.strip('"')
        elif k == "isHidden":
            measure.is_hidden = v.lower() == "true"
        elif k == "lineageTag":
            measure.lineage_tag = v
    measure.annotations = _parse_annotations(block)
    return measure, next_i


def _parse_partition(
    body: list[str], idx: int, own_indent: int
) -> tuple[Partition, int]:
    line = body[idx]
    s = line.strip()
    _, rest = _split_first(s)
    name = rest.split("=")[0].strip()
    p = Partition(name=name)
    block, next_i = _take_block(body, idx + 1, own_indent)
    # Look for ``mode: import``, ``source = ...`` (multi-line M)
    for j, bl in enumerate(block):
        bs = bl.strip()
        if bs.startswith("mode:"):
            p.mode = bs.split(":", 1)[1].strip()
        elif bs.startswith("source"):
            # source = ``` ... ```  OR  source =\n  let ...
            after = bs[len("source") :].lstrip(" =").strip()
            if after.startswith("calculated"):
                p.source_kind = "calculated"
                # Body of calc is the lines after this property
                p.source = _join_dax(block[j + 1 :], own_indent)
            elif after.startswith("m") or after == "":
                p.source_kind = "m"
                p.source = _join_dax(block[j + 1 :], own_indent)
                if not p.source and after and after != "m":
                    p.source = after
            else:
                p.source = after
                p.source_kind = "m"
            break
    return p, next_i


def _parse_hierarchy(
    body: list[str], idx: int, own_indent: int
) -> tuple[Hierarchy, int]:
    line = body[idx]
    _, rest = _split_first(line.strip())
    h = Hierarchy(name=_strip_name(rest))
    block, next_i = _take_block(body, idx + 1, own_indent)
    j = 0
    while j < len(block):
        bs = block[j].strip()
        if bs.startswith("level "):
            lname = _strip_name(bs[len("level ") :])
            level = HierarchyLevel(name=lname)
            sub, j2 = _take_block(block, j + 1, _indent(block[j]))
            for sl in sub:
                prop = _extract_property(sl)
                if prop is None:
                    continue
                k, v = prop
                if k == "column":
                    level.column = v
                elif k == "ordinal":
                    level.ordinal = v
            h.levels.append(level)
            j = j2
        else:
            j += 1
    return h, next_i


def _parse_calculation_group(
    body: list[str], idx: int, own_indent: int
) -> tuple[CalculationGroup, int]:
    cg = CalculationGroup()
    block, next_i = _take_block(body, idx + 1, own_indent)
    j = 0
    while j < len(block):
        bs = block[j].strip()
        if bs.startswith("calculationItem "):
            ci_line = bs[len("calculationItem ") :]
            name_part, _, _expr = ci_line.partition("=")
            ci = CalculationItem(name=_strip_name(name_part.strip()))
            sub, j2 = _take_block(block, j + 1, _indent(block[j]))
            ci.expression = _join_dax(sub, _indent(block[j]))
            for sl in sub:
                prop = _extract_property(sl)
                if prop is None:
                    continue
                k, v = prop
                if k == "ordinal":
                    ci.ordinal = v
                elif k == "formatString":
                    ci.format_string = v
            cg.items.append(ci)
            j = j2
        else:
            prop = _extract_property(block[j])
            if prop and prop[0] == "precedence":
                cg.precedence = prop[1]
            j += 1
    return cg, next_i


# ---------------------------------------------------------------------------
# Top-level loaders
# ---------------------------------------------------------------------------
def parse_table_file(path: Path) -> Table:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # First non-blank line = ``table <name>``
    head_idx = next(i for i, l in enumerate(lines) if l.strip())
    head = lines[head_idx].strip()
    keyword, rest = _split_first(head)
    name = _strip_name(rest)
    table = Table(name=name, source_file=str(path))
    table.description = _collect_descriptions(lines, head_idx)

    body, _ = _take_block(lines, head_idx + 1, _indent(lines[head_idx]))

    # Table-level properties
    for bl in body:
        prop = _extract_property(bl)
        if prop is None:
            continue
        k, v = prop
        if k == "lineageTag":
            table.lineage_tag = v
        elif k == "isHidden":
            table.is_hidden = v.lower() == "true"
        elif k == "isPrivate":
            table.is_private = v.lower() == "true"
    table.annotations = _parse_annotations(body)

    cols, meas, parts, hiers, cg = _parse_columns_measures(body, _indent(lines[head_idx]))
    table.columns = cols
    table.measures = meas
    for m in table.measures:
        m.table = table.name
    table.partitions = parts
    table.hierarchies = hiers
    table.calculation_group = cg
    return table


def parse_relationships_file(path: Path) -> list[Relationship]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[Relationship] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("relationship "):
            name = s[len("relationship ") :].strip()
            r = Relationship(name=name)
            block, next_i = _take_block(lines, i + 1, _indent(line))
            for bl in block:
                prop = _extract_property(bl)
                if prop is None:
                    continue
                k, v = prop
                if k == "fromColumn":
                    if "." in v:
                        r.from_table, r.from_column = _split_table_column(v)
                elif k == "toColumn":
                    if "." in v:
                        r.to_table, r.to_column = _split_table_column(v)
                elif k == "isActive":
                    r.is_active = v.lower() == "true"
                elif k == "crossFilteringBehavior":
                    r.cross_filtering_behavior = v
                elif k == "fromCardinality":
                    r.cardinality = v
            out.append(r)
            i = next_i
        else:
            i += 1
    return out


def _split_table_column(value: str) -> tuple[str, str]:
    """Split ``Table.Column`` or ``'Quoted Table'.Column`` or ``Table.'Quoted Column'``."""
    if value.startswith("'"):
        m = _QUOTED.match(value)
        if m:
            tab = m.group(1).replace("''", "'")
            tail = m.group(2)
            if tail.startswith("."):
                col = tail[1:]
                return tab, _strip_name(col)
            return tab, ""
    if "." in value:
        tab, col = value.split(".", 1)
        return _strip_name(tab), _strip_name(col)
    return value, ""


def parse_expressions_file(path: Path) -> list[Expression]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[Expression] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("expression "):
            head = s[len("expression ") :]
            name_part, _, _expr_inline = head.partition("=")
            expr = Expression(name=_strip_name(name_part.strip()))
            block, next_i = _take_block(lines, i + 1, _indent(line))
            expr.expression = _join_dax(block, _indent(line))
            for bl in block:
                prop = _extract_property(bl)
                if prop is None:
                    continue
                k, v = prop
                if k == "lineageTag":
                    expr.lineage_tag = v
                elif k == "queryGroup":
                    expr.query_group = v.strip("'")
            expr.annotations = _parse_annotations(block)
            expr.kind = "M"
            out.append(expr)
            i = next_i
        else:
            i += 1
    return out


def parse_role_file(path: Path) -> Role:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    head_idx = next(i for i, l in enumerate(lines) if l.strip())
    head = lines[head_idx].strip()
    _, rest = _split_first(head)
    role = Role(name=_strip_name(rest))
    body, _ = _take_block(lines, head_idx + 1, _indent(lines[head_idx]))
    for bl in body:
        prop = _extract_property(bl)
        if prop is None:
            continue
        k, v = prop
        if k == "modelPermission":
            role.model_permission = v
    # tablePermission blocks
    i = 0
    while i < len(body):
        bs = body[i].strip()
        if bs.startswith("tablePermission "):
            tp_text = bs[len("tablePermission ") :]
            # `tablePermission TableName = <DAX>`
            if "=" in tp_text:
                tname, expr_inline = tp_text.split("=", 1)
                tp = TablePermission(
                    table=_strip_name(tname.strip()),
                    filter_expression=expr_inline.strip(),
                )
            else:
                tp = TablePermission(table=_strip_name(tp_text.strip()))
            sub, j = _take_block(body, i + 1, _indent(body[i]))
            extra = _join_dax(sub, _indent(body[i]))
            if extra:
                tp.filter_expression = extra
            role.table_permissions.append(tp)
            i = j
        else:
            i += 1
    role.annotations = _parse_annotations(body)
    return role


def parse_model_file(path: Path) -> dict[str, str | list[str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: dict[str, str | list[str]] = {
        "name": "Model",
        "culture": "",
        "annotations": {},
        "query_groups": [],
    }
    head_idx = next(i for i, l in enumerate(lines) if l.strip())
    head = lines[head_idx].strip()
    _, rest = _split_first(head)
    out["name"] = _strip_name(rest)
    body, _ = _take_block(lines, head_idx + 1, _indent(lines[head_idx]))
    annotations: dict[str, str] = {}
    query_groups: list[str] = []
    for bl in body:
        s = bl.strip()
        if s.startswith("queryGroup "):
            query_groups.append(_strip_name(s[len("queryGroup ") :]))
        elif s.startswith("annotation "):
            rest_a = s[len("annotation ") :]
            if " = " in rest_a:
                k, v = rest_a.split(" = ", 1)
                annotations[k.strip()] = v.strip()
        else:
            prop = _extract_property(bl)
            if prop and prop[0] == "culture":
                out["culture"] = prop[1]
    out["annotations"] = annotations
    out["query_groups"] = query_groups
    return out


def parse_database_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for line in text.splitlines():
        prop = _extract_property(line)
        if prop:
            out[prop[0]] = prop[1]
    return out


# ---------------------------------------------------------------------------
# Public façade
# ---------------------------------------------------------------------------
def load_model(definition_dir: Path) -> Model:
    """Load an entire semantic model from its ``definition/`` folder."""
    model = Model()
    model_meta = parse_model_file(definition_dir / "model.tmdl")
    model.name = str(model_meta.get("name", "Model"))
    model.culture = str(model_meta.get("culture", ""))
    qg = model_meta.get("query_groups", [])
    model.query_groups = list(qg) if isinstance(qg, list) else []
    ann = model_meta.get("annotations", {})
    model.annotations = dict(ann) if isinstance(ann, dict) else {}

    db = parse_database_file(definition_dir / "database.tmdl")
    if "compatibilityLevel" in db:
        model.compatibility_level = db["compatibilityLevel"]

    tables_dir = definition_dir / "tables"
    if tables_dir.exists():
        for tfile in sorted(tables_dir.glob("*.tmdl")):
            model.tables.append(parse_table_file(tfile))

    model.relationships = parse_relationships_file(
        definition_dir / "relationships.tmdl"
    )
    model.expressions = parse_expressions_file(
        definition_dir / "expressions.tmdl"
    )

    roles_dir = definition_dir / "roles"
    if roles_dir.exists():
        for rfile in sorted(roles_dir.glob("*.tmdl")):
            model.roles.append(parse_role_file(rfile))
    return model


def iter_measures(model: Model) -> Iterable[Measure]:
    for t in model.tables:
        yield from t.measures


__all__ = [
    "Model",
    "Table",
    "Column",
    "Measure",
    "Partition",
    "CalculationGroup",
    "CalculationItem",
    "Hierarchy",
    "HierarchyLevel",
    "Relationship",
    "Expression",
    "Role",
    "TablePermission",
    "load_model",
    "iter_measures",
    "parse_table_file",
    "parse_relationships_file",
    "parse_expressions_file",
    "parse_role_file",
    "parse_model_file",
    "parse_database_file",
]
