"""Power BI Dataflow JSON parser (CDM model.json format).

Reads the JSON files exported under ``dataflows/`` and extracts the
high-level information needed for documentation: dataflow name, the
``section Section1`` Power Query (M) document, declared entities and
their attributes, and parameter queries.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DataflowAttribute:
    name: str
    data_type: str = ""


@dataclass
class DataflowEntity:
    name: str
    description: str = ""
    attributes: list[DataflowAttribute] = field(default_factory=list)
    refresh_policy: str = ""


@dataclass
class DataflowQuery:
    name: str
    expression: str = ""
    is_parameter: bool = False
    load_enabled: bool = True


@dataclass
class Dataflow:
    name: str
    description: str = ""
    modified_time: str = ""
    entities: list[DataflowEntity] = field(default_factory=list)
    queries: list[DataflowQuery] = field(default_factory=list)
    source_file: str = ""

    def primary_data_sources(self) -> list[str]:
        """Best-effort summary of the upstream systems referenced in M."""
        sources: set[str] = set()
        for q in self.queries:
            if q.is_parameter:
                continue
            for marker in (
                "Databricks.Catalogs",
                "Sql.Database",
                "Sql.Databases",
                "AzureDataLake.",
                "AzureBlobStorage.",
                "SharePoint.Files",
                "Excel.Workbook",
                "Csv.Document",
                "Web.Contents",
                "Odbc.DataSource",
            ):
                if marker in q.expression:
                    sources.add(marker.rstrip("."))
        return sorted(sources)


_SHARED_RE = re.compile(
    r"shared\s+(?:#\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))\s*=\s*",
    re.MULTILINE,
)


def _split_section(document: str) -> list[tuple[str, str]]:
    """Split a Power Query ``section Section1; ...`` document into
    ``(query_name, expression)`` tuples.

    Handles both ``shared name = ...;`` and ``shared #"quoted name" = ...;``.
    Each query body ends at a ``;`` followed by another ``shared`` or end of
    document.
    """
    matches = list(_SHARED_RE.finditer(document))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1) or m.group(2) or ""
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(document)
        body = document[start:end].rstrip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        out.append((name, body))
    return out


def parse_dataflow(path: Path) -> Dataflow:
    data = json.loads(path.read_text(encoding="utf-8"))
    df = Dataflow(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        modified_time=data.get("modifiedTime", ""),
        source_file=str(path),
    )
    mashup = data.get("pbi:mashup") or {}
    document = mashup.get("document", "")
    queries_meta = mashup.get("queriesMetadata", {}) or {}

    for qname, expr in _split_section(document):
        meta = queries_meta.get(qname, {}) or {}
        load_enabled = bool(meta.get("loadEnabled", True))
        is_param = bool(re.search(r"meta\s*\[\s*IsParameterQuery\s*=\s*true", expr))
        df.queries.append(
            DataflowQuery(
                name=qname,
                expression=expr.strip(),
                is_parameter=is_param,
                load_enabled=load_enabled,
            )
        )

    for ent in data.get("entities", []) or []:
        e = DataflowEntity(
            name=ent.get("name", ""),
            description=ent.get("description", ""),
        )
        rp = ent.get("pbi:refreshPolicy") or {}
        if rp:
            e.refresh_policy = rp.get("$type", "") or ""
        for attr in ent.get("attributes", []) or []:
            e.attributes.append(
                DataflowAttribute(
                    name=attr.get("name", ""),
                    data_type=attr.get("dataType", ""),
                )
            )
        df.entities.append(e)
    return df


def load_dataflows(folder: Path) -> list[Dataflow]:
    if not folder.exists():
        return []
    out: list[Dataflow] = []
    for f in sorted(folder.glob("*.json")):
        try:
            out.append(parse_dataflow(f))
        except Exception as exc:  # noqa: BLE001
            out.append(
                Dataflow(
                    name=f.stem,
                    description=f"<error parsing: {exc}>",
                    source_file=str(f),
                )
            )
    return out


__all__ = [
    "Dataflow",
    "DataflowEntity",
    "DataflowAttribute",
    "DataflowQuery",
    "parse_dataflow",
    "load_dataflows",
]
