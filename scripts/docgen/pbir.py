"""PBIR (Power BI Report definition) parser.

The new PBIR format stores each report page as a folder under
``<Report>.Report/definition/pages/``.  Each page folder contains a
``page.json`` (page-level metadata + filters) and a ``visuals/`` folder
with one sub-folder per visual containing ``visual.json``.

This module returns lightweight dataclasses describing pages and the
visuals/measures they reference, which is enough for the documentation
templates and for the lineage Page → Measure traceability.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FieldRef:
    entity: str = ""  # table name (TMDL)
    member: str = ""  # column or measure name
    kind: str = ""  # "Measure" / "Column" / "HierarchyLevel" / "Other"

    @property
    def qualified(self) -> str:
        if self.entity and self.member:
            return f"{self.entity}.{self.member}"
        return self.entity or self.member


@dataclass
class Visual:
    folder: str
    visual_type: str = ""
    title: str = ""
    fields: list[FieldRef] = field(default_factory=list)
    raw_kind: str = "visual"  # visual / group / textbox / pageNavigator etc.


@dataclass
class Filter:
    name: str = ""
    field: FieldRef = field(default_factory=FieldRef)
    type: str = ""
    how_created: str = ""


@dataclass
class Page:
    folder: str
    name: str = ""  # internal name e.g. ReportSection...
    display_name: str = ""
    width: int = 0
    height: int = 0
    display_option: str = ""
    visuals: list[Visual] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)


@dataclass
class Report:
    name: str = ""  # report folder name
    pages: list[Page] = field(default_factory=list)
    page_order: list[str] = field(default_factory=list)


def _walk_field_refs(node, out: list[FieldRef]) -> None:
    """Recursively pull Measure / Column / Hierarchy field references."""
    if isinstance(node, dict):
        for kind in ("Measure", "Column", "HierarchyLevel", "Aggregation"):
            if kind in node and isinstance(node[kind], dict):
                fr = FieldRef(kind=kind)
                expr = node[kind].get("Expression", {})
                if isinstance(expr, dict):
                    src = expr.get("SourceRef", {})
                    if isinstance(src, dict):
                        fr.entity = src.get("Entity", "") or src.get("Source", "")
                fr.member = node[kind].get("Property", "") or node[kind].get("Hierarchy", "")
                if fr.entity or fr.member:
                    out.append(fr)
        for v in node.values():
            _walk_field_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_field_refs(v, out)


def _extract_title(visual_obj: dict) -> str:
    objects = visual_obj.get("objects") or {}
    title_blocks = objects.get("title") or []
    for blk in title_blocks:
        props = (blk or {}).get("properties") or {}
        text = props.get("text") or {}
        expr = text.get("expr") or {}
        lit = expr.get("Literal") or {}
        val = lit.get("Value")
        if isinstance(val, str):
            v = val.strip().strip("'").strip()
            if v:
                return v
    return ""


def parse_visual(path: Path) -> Visual:
    data = json.loads(path.read_text(encoding="utf-8"))
    vis = Visual(folder=path.parent.name)
    visual_obj = data.get("visual") or {}
    if visual_obj:
        vis.visual_type = visual_obj.get("visualType", "")
        vis.title = _extract_title(visual_obj)
        # Pull field refs out of query.queryState.*.projections[*].field
        query = visual_obj.get("query") or {}
        qs = query.get("queryState") or {}
        for role_name, role in qs.items():
            for proj in (role or {}).get("projections", []) or []:
                _walk_field_refs(proj.get("field"), vis.fields)
    elif data.get("visualGroup"):
        vis.raw_kind = "group"
        vis.visual_type = "group"
        vis.title = (data["visualGroup"].get("displayName") or "").strip()
    elif data.get("visualContainerObjects") or data.get("textbox"):
        vis.raw_kind = "textbox"
        vis.visual_type = "textbox"
    else:
        vis.visual_type = "unknown"
    # De-duplicate fields by qualified name
    seen: set[str] = set()
    deduped: list[FieldRef] = []
    for fr in vis.fields:
        q = fr.qualified
        if q and q not in seen:
            seen.add(q)
            deduped.append(fr)
    vis.fields = deduped
    return vis


def parse_page(folder: Path) -> Page:
    page_json = folder / "page.json"
    data = json.loads(page_json.read_text(encoding="utf-8"))
    page = Page(folder=folder.name)
    page.name = data.get("name", folder.name)
    page.display_name = data.get("displayName", "")
    page.display_option = data.get("displayOption", "")
    page.width = int(data.get("width", 0) or 0)
    page.height = int(data.get("height", 0) or 0)

    fc = data.get("filterConfig") or {}
    for f in fc.get("filters", []) or []:
        flt = Filter(
            name=f.get("name", ""),
            type=f.get("type", ""),
            how_created=f.get("howCreated", ""),
        )
        fr_list: list[FieldRef] = []
        _walk_field_refs(f.get("field"), fr_list)
        if fr_list:
            flt.field = fr_list[0]
        page.filters.append(flt)

    visuals_dir = folder / "visuals"
    if visuals_dir.exists():
        for vfolder in sorted(visuals_dir.iterdir()):
            vfile = vfolder / "visual.json"
            if vfile.exists():
                try:
                    page.visuals.append(parse_visual(vfile))
                except json.JSONDecodeError:
                    page.visuals.append(
                        Visual(folder=vfolder.name, visual_type="unparseable")
                    )
    return page


def load_report(report_definition_dir: Path) -> Report:
    report = Report(name=report_definition_dir.parent.name)
    pages_dir = report_definition_dir / "pages"
    pages_meta = pages_dir / "pages.json"
    if pages_meta.exists():
        try:
            meta = json.loads(pages_meta.read_text(encoding="utf-8"))
            report.page_order = list(meta.get("pageOrder", []))
        except json.JSONDecodeError:
            pass
    if pages_dir.exists():
        folders = {f.name: f for f in pages_dir.iterdir() if f.is_dir()}
        ordered = [folders[n] for n in report.page_order if n in folders]
        leftover = [folders[n] for n in folders if n not in report.page_order]
        for folder in ordered + sorted(leftover):
            try:
                report.pages.append(parse_page(folder))
            except Exception as exc:  # noqa: BLE001 — keep generation flowing
                report.pages.append(
                    Page(folder=folder.name, display_name=f"<error: {exc}>")
                )
    return report


__all__ = [
    "FieldRef",
    "Visual",
    "Filter",
    "Page",
    "Report",
    "parse_visual",
    "parse_page",
    "load_report",
]
