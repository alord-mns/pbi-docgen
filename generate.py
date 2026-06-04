"""Orchestrator: load model, reports, dataflows, orchestration flows; render docs.

Run:

    python -m scripts.docgen.generate

The engine is model-agnostic. All repo-specific data (workspace IDs,
narratives, data-source descriptions, acronyms) is loaded from
``docs/.docgen.toml`` via :mod:`scripts.docgen.config`.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
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

GENERATION_LOG = "generation-log.md"

# Files at the docs root that must be preserved when re-generating.
PROTECTED = {"documentation_req.md", "dataflow-references.md", ".docgen.toml", GENERATION_LOG}


def _sweep_orphans(root: Path, generated: set[Path]) -> list[Path]:
    """Delete files under ``root`` that were not written during this run.

    Protected files (PROTECTED set, matched by name) are always kept. Empty
    directories left behind by the sweep are removed.
    """
    removed: list[Path] = []
    if not root.exists():
        return removed
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name in PROTECTED:
            continue
        if p.resolve() not in generated:
            p.unlink()
            removed.append(p)
    # Prune empty directories (bottom-up).
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    return removed


def _append_generation_log(
    log_path: Path,
    changed: list[Path],
    removed: list[Path],
    unchanged: int,
) -> None:
    """Prepend a dated entry to the generation log. Creates the file if absent."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rel = lambda p: str(p.relative_to(REPO_ROOT)).replace("\\", "/")
    lines = [f"## {ts}\n"]
    lines.append(f"- changed: {len(changed)}")
    lines.append(f"- removed: {len(removed)}")
    lines.append(f"- unchanged: {unchanged}")
    if changed or removed:
        lines.append("")
        for p in sorted(changed):
            lines.append(f"- ~ `{rel(p)}`")
        for p in sorted(removed):
            lines.append(f"- \u2212 `{rel(p)}`")
    new_entry = "\n".join(lines) + "\n\n"

    header = (
        "# Docgen Generation Log\n\n"
        "Append-only record of `python -m scripts.docgen.generate` runs. "
        "This file is protected from the per-run sweep and persists across runs.\n\n"
        "Newest entries first. `changed` includes both newly created and modified "
        "files; `removed` are files that existed before this run but were not "
        "produced by it (typically the result of renamed or deleted source artefacts).\n\n"
    )
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        # Strip any prior header so we can re-prepend a single canonical one.
        body = existing.split("## ", 1)
        prior_entries = ("## " + body[1]) if len(body) == 2 else ""
    else:
        prior_entries = ""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(header + new_entry + prior_entries, encoding="utf-8")


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

    # ---- Write phase (skip files whose content is unchanged) ----
    generated: set[Path] = set()
    changed: list[Path] = []

    def emit(path: Path, content: str) -> None:
        generated.add(path.resolve())
        if md.write(path, content):
            changed.append(path)

    # ---- Phase 1 — inventory ----
    readme_content = ro.render_readme(lin, cfg)
    readme_path = DOCS / "README.md"
    # Preserve any VALIDATION block previously injected by `validate.py` so
    # successive generate / validate runs do not ping-pong the same file.
    if readme_path.exists():
        existing = readme_path.read_text(encoding="utf-8")
        m = re.search(
            r"<!-- VALIDATION:START -->.*?<!-- VALIDATION:END -->",
            existing,
            flags=re.DOTALL,
        )
        if m:
            readme_content = re.sub(
                r"<!-- VALIDATION:START -->.*?<!-- VALIDATION:END -->",
                m.group(0),
                readme_content,
                flags=re.DOTALL,
            )
    emit(readme_path, readme_content)
    emit(DOCS / "architecture" / "overview.md", ro.render_architecture(lin, cfg))
    emit(DOCS / "lineage" / "lineage.md", ro.render_lineage(lin, cfg))
    emit(DOCS / "CHANGELOG.md", ro.render_changelog(cfg))

    # ---- Phase 2 — model + measures + glossary ----
    model_filename = md.safe_filename(model.name) + ".md"
    emit(DOCS / "model" / model_filename, rm.render_model(lin))
    measures_files = rm.render_measures(lin)
    for fname, content in measures_files.items():
        emit(DOCS / "measures" / fname, content)
    emit(DOCS / "glossary.md", rx.render_glossary(lin, cfg))

    # ---- Phase 3 — sources, dataflows, orchestration ----
    for fname, content in rx.render_data_sources(lin, cfg).items():
        emit(DOCS / "data-sources" / fname, content)
    for fname, content in rx.render_dataflows(lin).items():
        emit(DOCS / "dataflows" / fname, content)
    for fname, content in ro_orc.render_orchestration(lin, cfg).items():
        emit(DOCS / "orchestration" / fname, content)

    # ---- Phase 4 — reports & app ----
    for fname, content in rx.render_reports(lin).items():
        emit(DOCS / "reports" / fname, content)
    for fname, content in rx.render_app(lin, cfg).items():
        emit(DOCS / "app" / fname, content)

    # ---- Phase 5 — runbook + release notes ----
    emit(DOCS / "ops" / "runbook.md", rx.render_runbook(lin, cfg))
    emit(DOCS / "ReleaseNotes.md", rx.render_release_notes())

    # ---- Sweep orphans (files that existed but were not produced this run) ----
    removed = _sweep_orphans(DOCS, generated)
    unchanged = len(generated) - len(changed)

    # ---- Append generation log (protected from sweep) ----
    _append_generation_log(DOCS / GENERATION_LOG, changed, removed, unchanged)

    print(
        f"[docgen] write summary: {len(changed)} changed \u00b7 "
        f"{len(removed)} removed \u00b7 {unchanged} unchanged"
    )
    if changed:
        for p in sorted(changed)[:10]:
            print(f"[docgen]   ~ {p.relative_to(REPO_ROOT)}")
        if len(changed) > 10:
            print(f"[docgen]   \u2026 and {len(changed) - 10} more")
    if removed:
        for p in sorted(removed)[:10]:
            print(f"[docgen]   \u2212 {p.relative_to(REPO_ROOT)}")

    print("[docgen] done")
    print("[docgen] run `python -m scripts.docgen.validate` to enforce quality gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
