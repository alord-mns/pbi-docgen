"""Lineage graph for the FH&B Weekly solution.

Pulls together TMDL / PBIR / dataflow data and produces:

* node and edge lists suitable for a Mermaid flowchart
* a measure-to-pages lookup used by the measures docs
* a table-to-pages lookup used by the model doc
* a workspace dataflow inventory keyed by dataflow ID
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import dataflow as dfmod
from . import pbir as pbirmod
from . import tmdl


# Regex extracting workspaceId / dataflowId / entity from M expressions
_WORKSPACE_RE = re.compile(r'workspaceId\s*=\s*"([^"]+)"')
_DATAFLOW_RE = re.compile(r'dataflowId\s*=\s*"([^"]+)"')
_ENTITY_RE = re.compile(r'\[entity\s*=\s*"([^"]+)"')


@dataclass
class DataflowRef:
    """A dataflow reference discovered in the semantic model M code."""

    workspace_id: str
    dataflow_id: str
    entities: set[str] = field(default_factory=set)
    consumers: set[str] = field(default_factory=set)  # table or expression name

    @property
    def short_id(self) -> str:
        return self.dataflow_id[:8]


@dataclass
class Lineage:
    model: tmdl.Model
    report: pbirmod.Report
    dataflows: list[dfmod.Dataflow]
    dataflow_refs: dict[str, DataflowRef] = field(default_factory=dict)
    table_to_dataflows: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    measure_to_pages: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    column_to_pages: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    table_to_pages: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    primary_workspace_id: str = ""

    def dataflow_name_for_id(self, dataflow_id: str) -> str:
        """Best-effort lookup: match exported dataflow JSON by name == any consumer's
        entity, otherwise return the short ID."""
        # Direct ID match against the JSON files isn't possible — JSONs don't carry
        # the dataflow ID — so we expose just the short ID; named mapping can be
        # added later via docs/dataflow-references.md.
        return self.short_id_to_name.get(dataflow_id, dataflow_id[:8])

    short_id_to_name: dict[str, str] = field(default_factory=dict)


def _scan_m_for_dataflows(m_code: str) -> list[tuple[str, str, set[str]]]:
    """Return list of ``(workspace_id, dataflow_id, entity_names)`` tuples
    found in a Power Query M expression.  Each combination of workspace
    and dataflow gets one tuple with all distinct entities."""
    if not m_code:
        return []
    workspaces = _WORKSPACE_RE.findall(m_code)
    dataflows = _DATAFLOW_RE.findall(m_code)
    entities = set(_ENTITY_RE.findall(m_code))
    if not workspaces or not dataflows:
        return []
    # The simple case: a single workspace + single dataflow are referenced.
    out: list[tuple[str, str, set[str]]] = []
    pairs = set(zip(workspaces, dataflows)) if len(workspaces) == len(dataflows) else {
        (workspaces[0], df) for df in dataflows
    }
    for ws, df in pairs:
        out.append((ws, df, entities))
    return out


def build(
    model: tmdl.Model, report: pbirmod.Report, dataflows: list[dfmod.Dataflow]
) -> Lineage:
    lineage = Lineage(model=model, report=report, dataflows=dataflows)

    # ---- Dataflow references in TMDL partitions and shared expressions ----
    def _collect(consumer: str, m_code: str) -> None:
        for ws, df, entities in _scan_m_for_dataflows(m_code):
            key = f"{ws}::{df}"
            ref = lineage.dataflow_refs.get(key)
            if ref is None:
                ref = DataflowRef(workspace_id=ws, dataflow_id=df)
                lineage.dataflow_refs[key] = ref
            ref.entities.update(entities)
            ref.consumers.add(consumer)
            lineage.table_to_dataflows[consumer].add(key)

    for t in model.tables:
        for p in t.partitions:
            if p.source_kind in ("m", ""):
                _collect(t.name, p.source)
    for e in model.expressions:
        _collect(e.name, e.expression)

    # Detect primary workspace = the most frequently referenced one
    ws_counts: dict[str, int] = defaultdict(int)
    for ref in lineage.dataflow_refs.values():
        ws_counts[ref.workspace_id] += len(ref.consumers)
    if ws_counts:
        lineage.primary_workspace_id = max(ws_counts.items(), key=lambda kv: kv[1])[0]

    # ---- Map dataflow IDs to friendly names where possible ----
    # The exported JSON files carry only the dataflow display name, not the ID.
    # We build a lookup by searching for entity-name overlaps with the M code:
    # each TMDL entity reference must correspond to an entity declared in some
    # exported dataflow JSON.  A dataflow whose entities cover every referenced
    # entity is a strong candidate.
    df_by_entities: dict[str, set[str]] = {
        df.name: {e.name for e in df.entities} for df in dataflows
    }
    for ref in lineage.dataflow_refs.values():
        best_match = ""
        for df_name, entset in df_by_entities.items():
            if ref.entities and ref.entities <= entset:
                best_match = df_name
                break
        if best_match:
            lineage.short_id_to_name[ref.dataflow_id] = best_match

    # ---- Visual / page → measure & column → table back-references ----
    for page in report.pages:
        page_label = page.display_name or page.folder
        for visual in page.visuals:
            for fr in visual.fields:
                if not fr.entity:
                    continue
                qual = fr.qualified
                if fr.kind == "Measure":
                    lineage.measure_to_pages[qual].add(page_label)
                elif fr.kind in ("Column", "HierarchyLevel"):
                    lineage.column_to_pages[qual].add(page_label)
                lineage.table_to_pages[fr.entity].add(page_label)
        for flt in page.filters:
            if flt.field.entity:
                lineage.table_to_pages[flt.field.entity].add(page_label)

    return lineage


__all__ = ["DataflowRef", "Lineage", "build"]
