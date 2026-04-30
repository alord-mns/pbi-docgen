"""Orchestrator: load model, report, dataflows; render every documentation file.

Run:

    python -m scripts.docgen.generate
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import dataflow as dfmod
from . import lineage as lineagemod
from . import md
from . import pbir as pbirmod
from . import tmdl
from . import renderers_extras as rx
from . import renderers_model as rm
from . import renderers_overview as ro

REPO_ROOT = md.REPO_ROOT
DOCS = md.DOCS
SEMANTIC_DEFINITION = REPO_ROOT / "src" / "semantic-model" / "FH&B Weekly Data Model.SemanticModel" / "definition"
REPORT_DEFINITION = REPO_ROOT / "src" / "semantic-model" / "FH&B Weekly Data Model.Report" / "definition"
DATAFLOWS_DIR = REPO_ROOT / "dataflows"


PROTECTED = {"documentation_req.md", "dataflow-references.md"}


def _clean_dir(path: Path) -> None:
    """Remove generated subfolders without touching protected files at the docs root."""
    if not path.exists():
        return
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.name not in PROTECTED:
            entry.unlink()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    print(f"[docgen] loading semantic model from {SEMANTIC_DEFINITION}")
    model = tmdl.load_model(SEMANTIC_DEFINITION)
    print(
        f"[docgen]   {len(model.tables)} tables · "
        f"{sum(len(t.measures) for t in model.tables):,} measures · "
        f"{len(model.relationships)} relationships · "
        f"{len(model.expressions)} expressions · "
        f"{len(model.roles)} roles"
    )
    print(f"[docgen] loading report from {REPORT_DEFINITION}")
    report = pbirmod.load_report(REPORT_DEFINITION)
    print(
        f"[docgen]   {len(report.pages)} pages · "
        f"{sum(len(p.visuals) for p in report.pages)} visuals"
    )
    print(f"[docgen] loading dataflows from {DATAFLOWS_DIR}")
    dataflows = dfmod.load_dataflows(DATAFLOWS_DIR)
    print(f"[docgen]   {len(dataflows)} dataflow(s)")

    print("[docgen] building lineage graph")
    lin = lineagemod.build(model, report, dataflows)
    print(
        f"[docgen]   {len(lin.dataflow_refs)} dataflow refs · "
        f"{sum(len(v) for v in lin.measure_to_pages.values())} measure→page edges · "
        f"{len(lin.short_id_to_name)} dataflow name resolutions"
    )

    # ---- Wipe previous generation (keep protected requirements files) ----
    print(f"[docgen] cleaning {DOCS}")
    _clean_dir(DOCS)

    # ---- Phase 1 — inventory ----
    md.write(DOCS / "README.md", ro.render_readme(lin))
    md.write(DOCS / "architecture" / "overview.md", ro.render_architecture(lin))
    md.write(DOCS / "lineage" / "lineage.md", ro.render_lineage(lin))
    md.write(DOCS / "CHANGELOG.md", ro.render_changelog())

    # ---- Phase 2 — model + measures + glossary ----
    model_filename = md.safe_filename(model.name) + ".md"
    md.write(DOCS / "model" / model_filename, rm.render_model(lin))
    measures_files = rm.render_measures(lin)
    for fname, content in measures_files.items():
        md.write(DOCS / "measures" / fname, content)
    md.write(DOCS / "glossary.md", rx.render_glossary(lin))

    # ---- Phase 3 — sources & transforms ----
    for fname, content in rx.render_data_sources(lin).items():
        md.write(DOCS / "data-sources" / fname, content)
    for fname, content in rx.render_dataflows(lin).items():
        md.write(DOCS / "dataflows" / fname, content)

    # ---- Phase 4 — report & app ----
    for fname, content in rx.render_reports(lin).items():
        md.write(DOCS / "reports" / fname, content)
    for fname, content in rx.render_app(lin).items():
        md.write(DOCS / "app" / fname, content)

    # ---- Phase 5 — runbook + release notes ----
    md.write(DOCS / "ops" / "runbook.md", rx.render_runbook(lin))
    md.write(DOCS / "ReleaseNotes.md", rx.render_release_notes())

    print("[docgen] done")
    print(f"[docgen] run `python -m scripts.docgen.validate` to enforce quality gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
