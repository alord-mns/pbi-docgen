"""Parser for a Power BI **distribution App** metadata export.

A Power BI App is a service-side publishing construct, not a file artefact, so
it has no PBIP definition to read. Instead a small JSON is exported out of band
(a Power Automate flow or a REST call to the Power BI API) and committed to the
repo. This module reads that export.

It is distinct from :mod:`power_apps`, which parses Power Platform *canvas* apps.
The App parsed here is the audience app that publishes a solution's reports; it
is configured via ``[paths] powerbi_app_definition``.

Read-only and solution-agnostic. The publisher's name is deliberately dropped
(personal data); workspace / app / report GUIDs are resource identifiers and are
retained, but embed URLs and their base64 config blobs are not — they are noise
in documentation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PublishedReport:
    name: str
    report_type: str = ""


@dataclass
class PublishedApp:
    name: str
    description: str = ""
    workspace_name: str = ""
    workspace_id: str = ""
    last_updated: str = ""
    reports: list[PublishedReport] = field(default_factory=list)
    dashboard_count: int = 0
    source_file: str = ""


def _load_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_powerbi_app(path: Path, repo_root: Path | None = None) -> PublishedApp | None:
    data = _load_json(path)
    if data is None:
        return None
    app = data.get("app", {}) or {}
    ws = data.get("workspace", {}) or {}
    reports = [
        PublishedReport(
            name=(r.get("reportName") or "").strip(),
            report_type=(r.get("reportType") or "").strip(),
        )
        for r in (data.get("reports") or [])
        if (r.get("reportName") or "").strip()
    ]
    dashboards = data.get("dashboards") or []
    try:
        rel = str(path.resolve().relative_to(repo_root)).replace("\\", "/") if repo_root else path.name
    except (ValueError, OSError):
        rel = path.name
    return PublishedApp(
        name=(app.get("name") or path.stem).strip(),
        description=(app.get("description") or "").strip(),
        workspace_name=(ws.get("name") or "").strip(),
        workspace_id=(ws.get("id") or "").strip(),
        last_updated=(app.get("lastUpdated") or "").strip(),
        reports=reports,
        dashboard_count=len(dashboards) if isinstance(dashboards, list) else 0,
        source_file=rel,
    )


def load_powerbi_apps(paths: list[Path], repo_root: Path | None = None) -> list[PublishedApp]:
    """Parse every committed App export; sorted by name for deterministic output."""
    apps = [parse_powerbi_app(p, repo_root=repo_root) for p in paths]
    out = [a for a in apps if a is not None]
    out.sort(key=lambda a: a.name.lower())
    return out


__all__ = ["PublishedReport", "PublishedApp", "parse_powerbi_app", "load_powerbi_apps"]
