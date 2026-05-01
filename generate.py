"""Orchestrator: load model, reports, dataflows, orchestration flows; render docs.

Run:

    python -m scripts.docgen.generate

The engine is model-agnostic. All repo-specific data (workspace IDs,
narratives, data-source descriptions, acronyms) is loaded from
``docs/.docgen.toml`` via :mod:`scripts.docgen.config`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import config as configmod
from . import dataflow as dfmod
from . import lineage as lineagemod
from . import md
from . import orchestration as orcmod
from . import pbir as pbirmod
from . import tmdl
from . import renderers_extras as rx
from . import renderers_model as rm
from . import renderers_orchestration as ro_orc
from . import renderers_overview as ro

REPO_ROOT = md.REPO_ROOT
DOCS = md.DOCS

# Files at the docs root that must be preserved when re-generating.
PROTECTED = {"documentation_req.md", "dataflow-references.md", ".docgen.toml"}


def _clean_dir(path: Path) -> None:
    """Remove generated subfolders without touching protected files at the docs root."""
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.name not in PROTECTED:
            entry.unlink()


def _resolve_semantic_model(cfg: configmod.Config) -> Path:
    matches = cfg.resolve(cfg.paths.semantic_model_definition)
    if not matches:
        raise SystemExit(
            f"[docgen] no semantic-model definition matched "
            f"`{cfg.paths.semantic_model_definition}` — set `paths.semantic_model_definition` in docs/.docgen.toml"
        )
    if len(matches) > 1:
        print(
            f"[docgen] WARNING: {len(matches)} TMDL definitions matched; using {matches[0]}",
            file=sys.stderr,
        )
    return matches[0]


def _resolve_report_definitions(cfg: configmod.Config) -> list[Path]:
    included = cfg.resolve_many(cfg.paths.thin_report_definitions)
    excluded = {p.resolve() for p in cfg.resolve_many(cfg.paths.excluded_report_definitions)}
    return [p for p in included if p.resolve() not in excluded]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    cfg = configmod.load()

    # ---- Semantic model ----
    semantic_def = _resolve_semantic_model(cfg)
    print(f"[docgen] loading semantic model from {semantic_def.relative_to(REPO_ROOT)}")
    model = tmdl.load_model(semantic_def)
    print(
        f"[docgen]   {len(model.tables)} tables · "
        f"{sum(len(t.measures) for t in model.tables):,} measures · "
        f"{len(model.relationships)} relationships · "
        f"{len(model.expressions)} expressions · "
        f"{len(model.roles)} roles"
    )

    # ---- Reports (one or many) ----
    report_defs = _resolve_report_definitions(cfg)
    if not report_defs:
        raise SystemExit(
            "[docgen] no report definitions matched — check `paths.thin_report_definitions` in docs/.docgen.toml"
        )
    reports = []
    for rd in report_defs:
        print(f"[docgen] loading report from {rd.relative_to(REPO_ROOT)}")
        rep = pbirmod.load_report(rd)
        reports.append(rep)
        print(
            f"[docgen]   {len(rep.pages)} pages · "
            f"{sum(len(p.visuals) for p in rep.pages)} visuals"
        )
    primary_report = reports[0]

    # ---- Dataflows ----
    dataflows_glob = cfg.paths.dataflow_exports
    dataflows_dir = (REPO_ROOT / dataflows_glob).parent if "*" in dataflows_glob else REPO_ROOT / dataflows_glob
    print(f"[docgen] loading dataflows from {dataflows_dir.relative_to(REPO_ROOT)}")
    dataflows = dfmod.load_dataflows(dataflows_dir)
    print(f"[docgen]   {len(dataflows)} dataflow(s)")

    # ---- Orchestration flows ----
    orchestration_paths = cfg.resolve_many(cfg.paths.orchestration_definitions)
    print(f"[docgen] loading {len(orchestration_paths)} orchestration flow definition(s)")
    flows = orcmod.load_flows(orchestration_paths)
    for f in flows:
        print(
            f"[docgen]   `{f.name}` — {len(f.refresh_targets)} refresh target(s), "
            f"{len(f.notifications)} notification(s), "
            f"workspaces: {sorted(f.workspace_ids)}"
        )

    # ---- Lineage ----
    print("[docgen] building lineage graph")
    lin = lineagemod.build(
        model,
        primary_report,
        dataflows,
        reports=reports,
        orchestration_flows=flows,
    )
    if cfg.workspaces.primary:
        lin.primary_workspace_id = cfg.workspaces.primary
    if cfg.workspaces.dataset:
        lin.dataset_workspace_id = cfg.workspaces.dataset
    if cfg.workspaces.secondary:
        lin.secondary_workspace_ids = list(cfg.workspaces.secondary)
    print(
        f"[docgen]   {len(lin.dataflow_refs)} dataflow refs · "
        f"{sum(len(v) for v in lin.measure_to_pages.values())} measure→page edges · "
        f"{len(lin.short_id_to_name)} dataflow name resolutions"
    )

    # ---- Wipe previous generation (keep protected requirements files) ----
    print(f"[docgen] cleaning {DOCS}")
    _clean_dir(DOCS)

    # ---- Phase 1 — inventory ----
    md.write(DOCS / "README.md", ro.render_readme(lin, cfg))
    md.write(DOCS / "architecture" / "overview.md", ro.render_architecture(lin, cfg))
    md.write(DOCS / "lineage" / "lineage.md", ro.render_lineage(lin, cfg))
    md.write(DOCS / "CHANGELOG.md", ro.render_changelog(cfg))

    # ---- Phase 2 — model + measures + glossary ----
    model_filename = md.safe_filename(model.name) + ".md"
    md.write(DOCS / "model" / model_filename, rm.render_model(lin))
    measures_files = rm.render_measures(lin)
    for fname, content in measures_files.items():
        md.write(DOCS / "measures" / fname, content)
    md.write(DOCS / "glossary.md", rx.render_glossary(lin, cfg))

    # ---- Phase 3 — sources, dataflows, orchestration ----
    for fname, content in rx.render_data_sources(lin, cfg).items():
        md.write(DOCS / "data-sources" / fname, content)
    for fname, content in rx.render_dataflows(lin).items():
        md.write(DOCS / "dataflows" / fname, content)
    for fname, content in ro_orc.render_orchestration(lin, cfg).items():
        md.write(DOCS / "orchestration" / fname, content)

    # ---- Phase 4 — reports & app ----
    for fname, content in rx.render_reports(lin).items():
        md.write(DOCS / "reports" / fname, content)
    for fname, content in rx.render_app(lin, cfg).items():
        md.write(DOCS / "app" / fname, content)

    # ---- Phase 5 — runbook + release notes ----
    md.write(DOCS / "ops" / "runbook.md", rx.render_runbook(lin, cfg))
    md.write(DOCS / "ReleaseNotes.md", rx.render_release_notes())

    print("[docgen] done")
    print("[docgen] run `python -m scripts.docgen.validate` to enforce quality gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
