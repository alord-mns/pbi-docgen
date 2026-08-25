"""Power Platform canvas Power App parser.

Each canvas app is unpacked with ``pac canvas unpack`` into a folder per app.
Detection anchors on the ``CanvasManifest.json`` that the unpack emits at each
app root (see ``[paths] power_apps_definitions`` in ``.docgen.toml``). The
parser reads, from the sibling folders:

* app metadata (friendly name, description, form factor, orientation) from
  ``CanvasManifest.json``
* the screen list from ``Src/*.fx.yaml``
* the connector references from ``Connections/Connections.json``
* the connected data sources from ``DataSources/*.json`` (including the
  read/write flag that identifies write-back targets)

The model is deliberately solution-agnostic: it does not know about any
specific app, list, or connector. Friendly-name resolution against SharePoint
lists / dataflow entities is the responsibility of the lineage builder. The
parser is read-only and never runs ``pac`` itself.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class AppDataSource:
    """A connected data source referenced by the app (sample data excluded)."""

    name: str
    type: str = ""  # ServiceInfo, ConnectedDataSourceInfo, …
    api_id: str = ""  # /providers/microsoft.powerapps/apis/shared_<x>
    dataset: str = ""  # DatasetName, e.g. a SharePoint site URL
    writable: bool = False  # IsWritable — True marks a write-back target


@dataclass
class AppConnector:
    """A connector reference (one Connections.json entry)."""

    display_name: str
    api_id: str = ""  # /providers/microsoft.powerapps/apis/shared_<x>
    tier: str = ""  # apiTier, e.g. Standard / Premium
    data_sources: list[str] = field(default_factory=list)


@dataclass
class CanvasApp:
    name: str
    source_dir: str = ""  # repo-relative folder holding the unpacked source
    description: str = ""
    app_type: str = ""  # DocumentAppType, e.g. Phone / Tablet
    orientation: str = ""  # DocumentLayoutOrientation
    author: str = ""  # kept verbatim for redaction-aware rendering
    screens: list[str] = field(default_factory=list)
    connectors: list[AppConnector] = field(default_factory=list)
    data_sources: list[AppDataSource] = field(default_factory=list)

    def writable_sources(self) -> list[AppDataSource]:
        return [d for d in self.data_sources if d.writable]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _friendly_name(manifest: dict, encoded_name: str, fallback: str) -> str:
    """Resolve the app's display name.

    Priority: ``PublishInfo.AppName`` → base64-decoded ``Properties.Name``
    (``pac`` stores it as ``base64(<original file name>)`` + ``.msapp``) →
    the unpacked folder name.
    """
    publish = manifest.get("PublishInfo") or {}
    app_name = str(publish.get("AppName", "") or "").strip()
    if app_name:
        return app_name

    stem = encoded_name
    if stem.lower().endswith(".msapp"):
        stem = stem[: -len(".msapp")]
    if stem:
        try:
            pad = "=" * (-len(stem) % 4)
            decoded = base64.b64decode(stem + pad).decode("utf-8")
            if decoded and all(c.isprintable() or c.isspace() for c in decoded):
                return decoded.strip()
        except (ValueError, UnicodeDecodeError):
            pass
    return fallback


def _screens(app_root: Path) -> list[str]:
    src = app_root / "Src"
    if not src.is_dir():
        return []
    names: list[str] = []
    for p in src.glob("*.fx.yaml"):
        names.append(p.name[: -len(".fx.yaml")])
    return sorted(names)


def _connectors(app_root: Path) -> list[AppConnector]:
    data = _load_json(app_root / "Connections" / "Connections.json")
    if not isinstance(data, dict):
        return []
    out: list[AppConnector] = []
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        ref = entry.get("connectionRef") or {}
        out.append(
            AppConnector(
                display_name=str(ref.get("displayName", "") or "").strip(),
                api_id=str(ref.get("id", "") or "").strip(),
                tier=str(ref.get("apiTier", "") or "").strip(),
                data_sources=sorted(
                    str(d) for d in (entry.get("dataSources") or []) if d
                ),
            )
        )
    out.sort(key=lambda c: (c.display_name.lower(), c.api_id))
    return out


def _data_sources(app_root: Path) -> list[AppDataSource]:
    folder = app_root / "DataSources"
    if not folder.is_dir():
        return []
    out: list[AppDataSource] = []
    for path in folder.glob("*.json"):
        payload = _load_json(path)
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Skip blank-app template boilerplate (sample / static collections).
            if row.get("IsSampleData") or row.get("Type") == "StaticDataSourceInfo":
                continue
            name = str(row.get("Name", "") or "").strip()
            if not name:
                continue
            out.append(
                AppDataSource(
                    name=name,
                    type=str(row.get("Type", "") or "").strip(),
                    api_id=str(row.get("ApiId", "") or "").strip(),
                    dataset=str(row.get("DatasetName", "") or "").strip(),
                    writable=bool(row.get("IsWritable", False)),
                )
            )
    out.sort(key=lambda d: d.name.lower())
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_canvas_app(manifest_path: Path, repo_root: Path | None = None) -> CanvasApp:
    """Parse one unpacked canvas app given its ``CanvasManifest.json`` path."""
    app_root = manifest_path.parent
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        manifest = {}
    props = manifest.get("Properties") or {}

    if repo_root is not None:
        try:
            source_dir = app_root.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            source_dir = app_root.name
    else:
        source_dir = app_root.name

    return CanvasApp(
        name=_friendly_name(manifest, str(props.get("Name", "") or ""), app_root.name),
        source_dir=source_dir,
        description=str(props.get("AppDescription", "") or "").strip(),
        app_type=str(props.get("DocumentAppType", "") or "").strip(),
        orientation=str(props.get("DocumentLayoutOrientation", "") or "").strip(),
        author=str(props.get("Author", "") or "").strip(),
        screens=_screens(app_root),
        connectors=_connectors(app_root),
        data_sources=_data_sources(app_root),
    )


def load_power_apps(
    manifest_paths: list[Path], repo_root: Path | None = None
) -> list[CanvasApp]:
    """Parse every unpacked canvas app; sorted by name for deterministic output."""
    apps = [parse_canvas_app(p, repo_root=repo_root) for p in manifest_paths]
    apps.sort(key=lambda a: (a.name.lower(), a.source_dir))
    return apps


# ---------------------------------------------------------------------------
# Lineage overlay: app write-back -> dataflow
# ---------------------------------------------------------------------------
def writeback_links(apps, dataflows) -> dict[str, list[tuple[str, str]]]:
    """Match Power App write-back targets to the dataflows that read them.

    An edge is asserted only on hard evidence: a writable data-source name (a
    SharePoint list title, etc.) appearing as a **quoted literal** in a
    dataflow's Power Query M. This is deliberately conservative — sharing a
    SharePoint *site* is not enough; the specific list/table name must be
    referenced — so no false ``app -> dataflow`` edges are invented.

    Returns ``{dataflow_name: [(app_name, source_name), ...]}`` with each list
    de-duplicated and sorted.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    df_list = list(dataflows)
    for app in apps:
        for ds in app.writable_sources():
            name = (ds.name or "").strip()
            if not name:
                continue
            needles = (f'"{name}"', f"'{name}'")
            for df in df_list:
                blob = "\n".join(
                    q.expression
                    for q in getattr(df, "queries", [])
                    if getattr(q, "expression", "")
                )
                if any(n in blob for n in needles):
                    out.setdefault(df.name, []).append((app.name, name))
    for key in out:
        out[key] = sorted(dict.fromkeys(out[key]))
    return out


__all__ = [
    "AppDataSource",
    "AppConnector",
    "CanvasApp",
    "parse_canvas_app",
    "load_power_apps",
    "writeback_links",
]
