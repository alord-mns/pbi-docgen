"""Per-repo configuration loader for the docgen pipeline.

The engine reads a single `model-docs/.docgen.toml` from the repo root. Every
field is optional; sensible defaults are applied so the engine still runs
on a brand-new repo with no config file.

Design principle: anything that *names* the model lives here. Anything
that *interprets* the model lives in the engine modules.
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# --- Convention paths ------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "model-docs"
CONFIG_PATH = DOCS / ".docgen.toml"


# --- Defaults --------------------------------------------------------------
_DEFAULT_PATHS = {
    "semantic_model_definition": "src/semantic-model/*.SemanticModel/definition",
    "thin_report_definitions": ["src/thin-reports/*.Report/definition"],
    "excluded_report_definitions": ["src/semantic-model/*.Report/definition"],
    "dataflow_exports": "dataflows/*.json",
    "orchestration_definitions": ["orchestration/**/definition.json"],
}


# --- Dataclasses -----------------------------------------------------------
@dataclass
class DataSource:
    name: str
    purpose: str = ""
    mechanism: str = ""
    host: str = ""
    freshness: str = ""
    connector_match: list[str] = field(default_factory=list)
    objects: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class Solution:
    display_name: str = ""
    short_name: str = ""
    purpose: str = ""
    calendar_summary: str = ""
    business_domains: str = ""


@dataclass
class Workspaces:
    primary: str = ""
    dataset: str = ""
    secondary: list[str] = field(default_factory=list)


@dataclass
class Paths:
    semantic_model_definition: str = _DEFAULT_PATHS["semantic_model_definition"]
    thin_report_definitions: list[str] = field(
        default_factory=lambda: list(_DEFAULT_PATHS["thin_report_definitions"])
    )
    excluded_report_definitions: list[str] = field(
        default_factory=lambda: list(_DEFAULT_PATHS["excluded_report_definitions"])
    )
    dataflow_exports: str = _DEFAULT_PATHS["dataflow_exports"]
    orchestration_definitions: list[str] = field(
        default_factory=lambda: list(_DEFAULT_PATHS["orchestration_definitions"])
    )


@dataclass
class Narratives:
    upstream_platforms: str = ""
    lineage_narrative: list[str] = field(default_factory=list)
    change_impact_notes: list[str] = field(default_factory=list)


@dataclass
class App:
    name: str = ""
    purpose: str = ""
    audience: str = ""


@dataclass
class Config:
    solution: Solution = field(default_factory=Solution)
    workspaces: Workspaces = field(default_factory=Workspaces)
    paths: Paths = field(default_factory=Paths)
    narratives: Narratives = field(default_factory=Narratives)
    acronyms: dict[str, str] = field(default_factory=dict)
    headline_metrics: list[str] = field(default_factory=list)
    data_sources: list[DataSource] = field(default_factory=list)
    app: App = field(default_factory=App)
    raw: dict = field(default_factory=dict)

    # Convenience accessors -------------------------------------------------
    def resolve(self, pattern: str) -> list[Path]:
        """Resolve a glob pattern relative to repo root, sorted."""
        return sorted(REPO_ROOT.glob(pattern))

    def resolve_many(self, patterns: list[str]) -> list[Path]:
        out: list[Path] = []
        seen: set[Path] = set()
        for pat in patterns:
            for p in self.resolve(pat):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        return out

    def is_excluded_report(self, definition_dir: Path) -> bool:
        """Return True if a report definition path matches any excluded glob."""
        excluded = self.resolve_many(self.paths.excluded_report_definitions)
        return definition_dir.resolve() in {p.resolve() for p in excluded}


# --- Loader ----------------------------------------------------------------
def load(path: Path | None = None) -> Config:
    """Load `docs/.docgen.toml` (or a custom path) into a `Config` object.

    A missing file produces a default config with empty narratives and the
    convention paths above; the engine then renders neutral placeholders.
    """
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        print(f"[config] no .docgen.toml at {cfg_path} — using defaults",
              file=sys.stderr)
        return Config()

    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)

    sol = data.get("solution", {}) or {}
    ws = data.get("workspaces", {}) or {}
    paths_t = data.get("paths", {}) or {}
    nar = data.get("narratives", {}) or {}
    acro = data.get("acronyms", {}) or {}
    headline = (data.get("headline_metrics") or {}).get("names", []) or []
    app_t = data.get("app", {}) or {}
    sources_t = data.get("data_sources", []) or []

    cfg = Config(
        solution=Solution(
            display_name=sol.get("display_name", ""),
            short_name=sol.get("short_name", ""),
            purpose=(sol.get("purpose", "") or "").strip(),
            calendar_summary=sol.get("calendar_summary", ""),
            business_domains=sol.get("business_domains", ""),
        ),
        workspaces=Workspaces(
            primary=ws.get("primary", ""),
            dataset=ws.get("dataset", ""),
            secondary=list(ws.get("secondary", []) or []),
        ),
        paths=Paths(
            semantic_model_definition=paths_t.get(
                "semantic_model_definition",
                _DEFAULT_PATHS["semantic_model_definition"],
            ),
            thin_report_definitions=list(
                paths_t.get(
                    "thin_report_definitions",
                    _DEFAULT_PATHS["thin_report_definitions"],
                )
            ),
            excluded_report_definitions=list(
                paths_t.get(
                    "excluded_report_definitions",
                    _DEFAULT_PATHS["excluded_report_definitions"],
                )
            ),
            dataflow_exports=paths_t.get(
                "dataflow_exports", _DEFAULT_PATHS["dataflow_exports"]
            ),
            orchestration_definitions=list(
                paths_t.get(
                    "orchestration_definitions",
                    _DEFAULT_PATHS["orchestration_definitions"],
                )
            ),
        ),
        narratives=Narratives(
            upstream_platforms=(nar.get("upstream_platforms", "") or "").strip(),
            lineage_narrative=list(nar.get("lineage_narrative", []) or []),
            change_impact_notes=list(nar.get("change_impact_notes", []) or []),
        ),
        acronyms=dict(acro),
        headline_metrics=list(headline),
        app=App(
            name=app_t.get("name", ""),
            purpose=app_t.get("purpose", ""),
            audience=app_t.get("audience", ""),
        ),
        data_sources=[
            DataSource(
                name=ds.get("name", "Unnamed source"),
                purpose=ds.get("purpose", ""),
                mechanism=ds.get("mechanism", ""),
                host=ds.get("host", ""),
                freshness=ds.get("freshness", ""),
                connector_match=list(ds.get("connector_match", []) or []),
                objects=[tuple(o) + ("",) * (3 - len(o)) for o in ds.get("objects", [])],
            )
            for ds in sources_t
        ],
        raw=data,
    )
    return cfg


__all__ = [
    "Config",
    "Solution",
    "Workspaces",
    "Paths",
    "Narratives",
    "DataSource",
    "App",
    "load",
    "REPO_ROOT",
    "DOCS",
    "CONFIG_PATH",
]
