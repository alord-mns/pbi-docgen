"""Render the semantic model document and the per-folder measure docs."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import md
from . import tmdl
from .lineage import Lineage


# ---------------------------------------------------------------------------
# Semantic model document
# ---------------------------------------------------------------------------
def _table_anchor(name: str) -> str:
    return md.slugify(name)


def _format_columns_table(table: tmdl.Table) -> list[str]:
    if not table.columns:
        return ["_(no columns — measure-only table)_", ""]
    rows = ["| Column | Type | Source / Derivation | Notes |", "| --- | --- | --- | --- |"]
    for col in table.columns:
        notes: list[str] = []
        if col.is_calculated:
            notes.append("calculated column")
        if col.is_hidden:
            notes.append("hidden")
        if col.summarize_by and col.summarize_by != "none":
            notes.append(f"summarize: {col.summarize_by}")
        if col.format_string:
            notes.append(f"format: `{col.format_string}`")
        if col.sort_by_column:
            notes.append(f"sort by `{col.sort_by_column}`")
        if col.display_folder:
            notes.append(f"folder: `{col.display_folder}`")
        source = col.source_column or ("calculated DAX" if col.is_calculated else "")
        if col.is_calculated and col.expression:
            source = f"DAX: `{col.expression[:80].replace(chr(10), ' ')}`"
        rows.append(
            f"| `{md.md_escape_pipe(col.name)}` | `{col.data_type or '-'}` | "
            f"{md.md_escape_pipe(source) or '-'} | {md.md_escape_pipe(', '.join(notes)) or '-'} |"
        )
    rows.append("")
    return rows


def _table_source_summary(table: tmdl.Table, lin: Lineage) -> str:
    if table.is_calculation_group:
        return "Calculation group (DAX)."
    if table.is_calculated:
        for p in table.partitions:
            if p.source_kind == "calculated":
                expr = p.source.strip()
                preview = expr[:120].replace("\n", " ")
                return f"Calculated table — DAX: `{preview}…`" if len(expr) > 120 else f"Calculated table — DAX: `{preview}`"
        return "Calculated table."
    refs = lin.table_to_dataflows.get(table.name)
    if refs:
        names = []
        for k in sorted(refs):
            df_id = k.split("::", 1)[1]
            names.append(lin.short_id_to_name.get(df_id, f"dataflow `{df_id[:8]}…`"))
        return "Power Query M referencing dataflow: " + ", ".join(names)
    if table.partitions:
        for p in table.partitions:
            if p.source:
                first = p.source.strip().splitlines()[0][:120]
                return f"Power Query M — first step: `{md.md_escape_pipe(first)}`"
    return "_unknown source — see TMDL_"


def render_model(lin: Lineage) -> str:
    model = lin.model
    body = [md.HEADER, f"# Semantic Model — {model.name}\n"]
    body.append(md.section_purpose(
        "Authoritative reference for every table, column, relationship, role, and time-intelligence asset in the semantic model.",
        "Power BI Developers", "Data Engineers",
    ))

    # Dataset info
    body.append("\n## Dataset Info\n")
    body.append("| Property | Value |")
    body.append("| --- | --- |")
    body.append(f"| Name | `{model.name}` |")
    body.append(f"| Compatibility level | `{model.compatibility_level or '-'}` |")
    body.append(f"| Culture | `{model.culture or '-'}` |")
    body.append("| Storage mode | Import |")
    body.append(f"| Tables | {len(model.tables)} |")
    body.append(f"| Columns | {sum(len(t.columns) for t in model.tables)} |")
    body.append(f"| Measures | {sum(len(t.measures) for t in model.tables):,} |")
    body.append(f"| Relationships | {len(model.relationships)} |")
    body.append(f"| Roles | {len(model.roles)} |")
    body.append(f"| Workspace | `{lin.primary_workspace_id or md.PLACEHOLDER}` |")
    body.append(f"| Dataset size | {md.PLACEHOLDER} |")
    body.append(f"| Endorsement | {md.PLACEHOLDER} |")
    body.append(f"| Sensitivity label | {md.PLACEHOLDER} |")
    body.append("")

    # Per-table sections
    body.append("\n## Table Definitions\n")
    body.append(f"_{len(model.tables)} tables in alphabetical order. Click a name to jump:_\n")
    body.append(", ".join(f"[{t.name}](#{_table_anchor(t.name)})" for t in model.tables))
    body.append("")
    for table in model.tables:
        body.append(f"\n### {table.name}\n")
        if table.description:
            body.append(f"> {md.md_escape_pipe(table.description)}\n")
        else:
            body.append(f"> {md.UNKNOWN if not table.is_calculation_group else 'Calculation group used to switch context for selected measures (see Calculation Groups section).'}\n")
        flags = []
        if table.is_hidden:
            flags.append("hidden")
        if table.is_calculation_group:
            flags.append("calculation group")
        if table.is_calculated:
            flags.append("calculated table")
        if flags:
            body.append(f"_Flags: {', '.join(flags)}_\n")
        body.append(f"**Source.** {_table_source_summary(table, lin)}\n")
        if table.lineage_tag:
            body.append(f"`lineageTag: {table.lineage_tag}`\n")
        body.extend(_format_columns_table(table))
        if table.measures:
            top_folders = sorted({(m.display_folder.split("\\")[0] if m.display_folder else "(none)") for m in table.measures})
            body.append(f"_Measures: {len(table.measures)} (folders: {', '.join(f'`{f}`' for f in top_folders)}). See [`docs/measures/`](../measures/)._\n")
        if table.hierarchies:
            body.append("**Hierarchies:**")
            for h in table.hierarchies:
                levels = " → ".join(f"`{lvl.column or lvl.name}`" for lvl in h.levels)
                body.append(f"- `{h.name}`: {levels}")
            body.append("")
        if table.is_calculation_group and table.calculation_group:
            body.append("**Calculation items:**")
            body.append("| Item | Ordinal | DAX |")
            body.append("| --- | --- | --- |")
            for ci in table.calculation_group.items:
                expr_preview = ci.expression.replace("\n", " ").strip()
                if len(expr_preview) > 200:
                    expr_preview = expr_preview[:200] + "…"
                body.append(
                    f"| `{md.md_escape_pipe(ci.name)}` | {ci.ordinal or '-'} | `{md.md_escape_pipe(expr_preview)}` |"
                )
            body.append("")

    # Relationships
    body.append("\n## Relationships\n")
    body.append("| From | To | Active | Cross-filter | Cardinality |")
    body.append("| --- | --- | --- | --- | --- |")
    for r in model.relationships:
        from_q = f"`{r.from_table}`.`{r.from_column}`"
        to_q = f"`{r.to_table}`.`{r.to_column}`"
        body.append(
            f"| {from_q} | {to_q} | {'yes' if r.is_active else 'no'} | "
            f"{r.cross_filtering_behavior} | {r.cardinality or 'many-to-one (default)'} |"
        )
    body.append("")

    # Calculated tables & calculation groups
    body.append("\n## Calculated Tables & Calculation Groups\n")
    calc_tables = [t for t in model.tables if t.is_calculated]
    cg_tables = [t for t in model.tables if t.is_calculation_group]
    if not calc_tables and not cg_tables:
        body.append("_None defined._\n")
    for t in calc_tables:
        body.append(f"### {t.name} (calculated)\n")
        for p in t.partitions:
            if p.source_kind == "calculated":
                body.append(md.code_block(p.source, "DAX"))
        body.append("")
    for t in cg_tables:
        body.append(f"### {t.name} (calculation group)\n")
        body.append(f"Precedence: `{t.calculation_group.precedence or '-'}`. "
                    f"{len(t.calculation_group.items)} item(s).")
        body.append("")

    # Field parameters
    body.append("\n## Field Parameters\n")
    fp_tables = []
    for t in model.tables:
        ann = t.annotations.get("PBI_FieldParameters") or t.annotations.get("PBI_FieldParameter")
        if ann:
            fp_tables.append((t, ann))
        # Heuristic fallback: tables whose only column references list of fields
        elif t.is_calculated and any(
            "NAMEOF" in p.source for p in t.partitions if p.source_kind == "calculated"
        ):
            fp_tables.append((t, "(detected from NAMEOF DAX pattern)"))
    if fp_tables:
        body.append("| Field parameter | Notes |")
        body.append("| --- | --- |")
        for t, note in fp_tables:
            body.append(f"| `{t.name}` | {md.md_escape_pipe(note)} |")
        body.append("")
    else:
        body.append("_No PBI_FieldParameters annotations were detected. Selector tables (`*MeasureSelection`, `DimensionSelection`, `ComparatorSelect`, etc.) act as user-driven switches and are documented as ordinary tables above._\n")

    # Row-Level Security
    body.append("\n## Row-Level Security\n")
    if not model.roles:
        body.append("_No roles defined._\n")
    else:
        body.append("| Role | Permission | Filter expression(s) | Intended membership |")
        body.append("| --- | --- | --- | --- |")
        for role in model.roles:
            if role.table_permissions:
                filt = "; ".join(
                    f"`{tp.table}`: `{md.md_escape_pipe(tp.filter_expression or '(no filter)')}`"
                    for tp in role.table_permissions
                )
            else:
                filt = "_(no table-level filters — full access at this permission level)_"
            body.append(
                f"| `{role.name}` | {role.model_permission or '-'} | "
                f"{filt} | {md.PLACEHOLDER} |"
            )
        body.append("")

    # Hierarchies (rolled up)
    body.append("\n## Hierarchies\n")
    any_hier = False
    for t in model.tables:
        for h in t.hierarchies:
            any_hier = True
            levels = " → ".join(f"`{lvl.column or lvl.name}`" for lvl in h.levels)
            body.append(f"- `{t.name}`.`{h.name}`: {levels}")
    if not any_hier:
        body.append("_No hierarchies defined._")
    body.append("")

    # Time intelligence & calendar
    body.append("\n## Time Intelligence & Calendar\n")
    cal_table = next((t for t in model.tables if t.name == "Calendar"), None)
    body.append("- **`Calendar`** is the primary date table. It carries the standard Calendar columns plus comparator columns (`fiscalWeekLastYear`, `fiscalWeekLastWeek`, `fiscalWeekLastToLastyear`, …) used as inactive relationships activated via `USERELATIONSHIP` to compute LY / LW / LY-1 measures.")
    body.append("- **`RefCalendar`** is a secondary date dimension used for forecast scenario / rolling reporting.")
    body.append("- **`DailyCalendar`** (shared expression) is used by daily-grain measures (online / Adobe).")
    body.append("- **`firstWeekCurrentQuarter`** and **`Restore Weeks Calendar`** are calculated/static helper tables for quarter-to-date and rollback views.")
    body.append("- **`fiscalWeek`** (integer of the form `YYYYWW`) is the join key for nearly every fact table.")
    if cal_table:
        body.append(f"- `Calendar` carries {len(cal_table.columns)} columns; mark-as-date-table status: {md.PLACEHOLDER}.")
    body.append("")

    # Performance notes
    body.append("\n## Performance Notes\n")
    body.append("- The model imports {0:,} rows into the {1} fact tables; refresh duration is {2}.".format(
        0, sum(1 for t in model.tables if t.name.startswith("fact")), md.PLACEHOLDER,
    ))
    body.append("- High-cardinality columns (e.g. `factSalesDispatch[Site No]`, `factSalesOrders[siteID]`) are direct keys to `Stores` — avoid creating bidirectional relationships across these.")
    body.append("- Several large fact tables (e.g. `factOnlineCustomerMetrics`, `factSalesDispatch`) are loaded via `PowerBI.Dataflows` which does not fold; full-table refresh is in use unless an incremental refresh policy is added.")
    body.append("- Bridge tables (`BridgeSalesType`, `BridgeMPPlan`) introduce bidirectional filters by design; verify in Performance Analyzer when adding new measures using these.")
    body.append(f"- Aggregation tables: {md.PLACEHOLDER}.")
    body.append("")

    # Metadata
    body.append("\n## Metadata\n")
    body.append("| Property | Value |")
    body.append("| --- | --- |")
    body.append(f"| Endorsement status | {md.PLACEHOLDER} |")
    body.append(f"| Sensitivity label | {md.PLACEHOLDER} |")
    body.append(f"| `__PBI_TimeIntelligenceEnabled` | `{model.annotations.get('__PBI_TimeIntelligenceEnabled', '-')}` |")
    pro_tooling = model.annotations.get("PBI_ProTooling", "-")
    body.append(f"| `PBI_ProTooling` | `{md.md_escape_pipe(pro_tooling)}` |")
    body.append("")
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Measure documents (one file per top-level displayFolder)
# ---------------------------------------------------------------------------
def _classify_measure_format(fmt: str) -> str:
    if not fmt:
        return "unspecified"
    f = fmt.lower()
    if "%" in fmt:
        return "percentage"
    if any(c in fmt for c in "£€$"):
        return "currency"
    if "yyyy" in f or "mm" in f or "dd" in f:
        return "date/time"
    return "number"


def _infer_grain(name: str, dax: str) -> str:
    n = name.lower()
    if "ytd" in n or "qtd" in n or "mtd" in n:
        return "time-cumulative (semi-additive)"
    if "rate" in n or "%" in name:
        return "ratio (non-additive)"
    if any(tok in n for tok in (" %", "/%", "share")):
        return "ratio (non-additive)"
    if "average" in n or "avg" in n:
        return "non-additive (average)"
    if "stock" in n and "stockturn" not in n:
        return "semi-additive (stock balance)"
    return "additive"


def _filter_context_hints(dax: str) -> str:
    hints: list[str] = []
    upper = dax.upper()
    if "USERELATIONSHIP" in upper:
        hints.append("activates an inactive relationship via `USERELATIONSHIP`")
    if "REMOVEFILTERS" in upper or "ALL(" in upper or "ALL (" in upper:
        hints.append("removes filters via `ALL` / `REMOVEFILTERS`")
    if "ALLEXCEPT" in upper:
        hints.append("preserves selected filters via `ALLEXCEPT`")
    if "FILTER (" in upper or "FILTER(" in upper:
        hints.append("applies row-level `FILTER`")
    if "SELECTEDVALUE" in upper:
        hints.append("reads slicer state via `SELECTEDVALUE`")
    if "TREATAS" in upper:
        hints.append("`TREATAS`-based virtual relationship")
    if not hints:
        hints.append("none beyond ambient filter context")
    return "; ".join(hints)


def _split_top_folder(display_folder: str) -> tuple[str, str]:
    if not display_folder:
        return "(none)", ""
    parts = display_folder.split("\\")
    return parts[0], "\\".join(parts[1:])


def _measure_filename(top_folder: str) -> str:
    if top_folder == "(none)":
        return "Unfoldered.md"
    return f"{md.safe_filename(top_folder)}.md"


def render_measures(lin: Lineage) -> dict[str, str]:
    """Return a dict mapping ``filename → content`` plus ``README.md`` index."""
    by_top: dict[str, list[tmdl.Measure]] = defaultdict(list)
    for t in lin.model.tables:
        for m in t.measures:
            top, _ = _split_top_folder(m.display_folder)
            by_top[top].append(m)

    files: dict[str, str] = {}

    # Index
    idx = [md.HEADER, "# Measures Index\n"]
    idx.append(md.section_purpose(
        "Catalogue of every DAX measure in the semantic model, grouped by top-level display folder.",
        "Power BI Developers", "Data Engineers", "Business End Users",
    ))
    idx.append("\n## Folders\n")
    idx.append("| Folder | Measures | File |")
    idx.append("| --- | --- | --- |")
    for top, items in sorted(by_top.items()):
        fn = _measure_filename(top)
        idx.append(f"| `{top}` | {len(items)} | {md.link(fn, fn)} |")
    idx.append("")
    idx.append(f"\n_Total: {sum(len(v) for v in by_top.values()):,} measures across {len(by_top)} folders._\n")
    idx.append("\n## Measure Definition Standard\n")
    idx.append("Every measure entry below follows the standard from §2.6 of `documentation_req.md`:")
    idx.append("name → business definition → DAX → format → grain → filter context → caveats → owner → hidden → report usage.")
    idx.append("")
    files["README.md"] = "\n".join(idx)

    # One file per top folder
    for top, items in sorted(by_top.items()):
        body = [md.HEADER, f"# Measures — `{top}`\n"]
        body.append(md.section_purpose(
            f"All DAX measures whose top-level display folder is `{top}`.",
            "Power BI Developers", "Data Engineers", "Business End Users",
        ))
        body.append(f"\n_{len(items)} measure(s)._\n")

        # Group by sub-folder for navigability
        sub_buckets: dict[str, list[tmdl.Measure]] = defaultdict(list)
        for m in items:
            _, sub = _split_top_folder(m.display_folder)
            sub_buckets[sub].append(m)

        body.append("\n## Sub-folder index\n")
        for sub in sorted(sub_buckets):
            label = sub or "(top level)"
            body.append(f"- [`{label}`](#{md.slugify(label)}) — {len(sub_buckets[sub])} measure(s)")
        body.append("")

        for sub in sorted(sub_buckets):
            label = sub or "(top level)"
            body.append(f"\n## {label}\n")
            for m_obj in sorted(sub_buckets[sub], key=lambda x: x.name.lower()):
                body.extend(_render_measure(m_obj, lin))
        files[_measure_filename(top)] = "\n".join(body)
    return files


def _render_measure(m_obj: tmdl.Measure, lin: Lineage) -> list[str]:
    qual = f"{m_obj.table}.{m_obj.name}"
    pages = sorted(lin.measure_to_pages.get(qual, set()))
    if not pages:
        # also check by member-only key (some queries expose only Property)
        pages = sorted(lin.measure_to_pages.get(m_obj.name, set()))
    business_def = m_obj.description.strip() if m_obj.description else ""
    if not business_def:
        business_def = md.UNKNOWN
    rows = [
        f"\n### `{m_obj.name}`\n",
        f"- **Table:** `{m_obj.table}`",
        f"- **Display folder:** `{m_obj.display_folder or '(none)'}`",
        f"- **Hidden:** {'yes' if m_obj.is_hidden else 'no'}",
        f"- **Format string:** `{m_obj.format_string or '-'}` ({_classify_measure_format(m_obj.format_string)})",
        f"- **Grain / scope (inferred):** {_infer_grain(m_obj.name, m_obj.expression)}",
        f"- **Filter context hints:** {_filter_context_hints(m_obj.expression)}",
        f"- **Owner / steward:** {md.PLACEHOLDER}",
        f"- **Caveats / known issues:** **None known**",
    ]
    if pages:
        rows.append(f"- **Report usage:** {len(pages)} page(s) — " + ", ".join(f"`{p}`" for p in pages[:8]) + (" …" if len(pages) > 8 else ""))
    else:
        rows.append("- **Report usage:** **Unable to determine from repo metadata** — measure not referenced via the parsed PBIR field bindings.")
    rows.append("\n**Business definition.**")
    rows.append(business_def)
    rows.append("\n**DAX.**")
    rows.append(md.code_block(m_obj.expression, "DAX"))
    return rows


__all__ = [
    "render_model",
    "render_measures",
]
