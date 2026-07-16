"""Orchestrator: load model, reports, dataflows, orchestration flows; render docs.

Run:

    python -m scripts.docgen.generate

The engine is model-agnostic. All repo-specific data (workspace IDs,
narratives, data-source descriptions, acronyms) is loaded from
``docs/.docgen.toml`` via :mod:`scripts.docgen.config`.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from . import config as configmod
from . import dataflow as dfmod
from . import dax_refs
from . import lineage as lineagemod
from . import md
from . import orchestration as orcmod
from . import pbir as pbirmod
from . import sourcetrace
from . import sqlsource
from . import tmdl
from . import cards
from . import renderers_model_metrics as rmm
from . import renderers_overview as ro
from . import renderers_pipeline as rp
from . import renderers_reports as rr
from . import renderers_source_code as rsc

REPO_ROOT = md.REPO_ROOT
DOCS = md.DOCS
# Plain-text mirror of the agent knowledge base, for agents that cannot ingest
# Markdown. Kept wholly separate from model-docs/ so it never participates in
# the docgen validation gates.
DOCS_TXT = REPO_ROOT / "model-docs-txt"

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


def _visual_usage(reports: list) -> dict[str, int]:
    """Count how many visuals reference each measure (by member name)."""
    usage: dict[str, int] = {}
    for rep in reports:
        for page in rep.pages:
            for visual in page.visuals:
                for fr in visual.fields:
                    if fr.kind == "Measure" and fr.member:
                        usage[fr.member] = usage.get(fr.member, 0) + 1
    return usage


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

    # ---- Measure classification (selector/router/metric/compute/base) ----
    print("[docgen] classifying measures")
    cls = dax_refs.classify_measures(
        model,
        visual_usage=_visual_usage(reports),
        selector_prefixes=tuple(cfg.measures.selector_prefixes),
        base_prefixes=tuple(cfg.measures.base_prefixes),
    )
    counts = cls.counts()
    print(
        "[docgen]   "
        + " · ".join(f"{role}: {counts.get(role, 0)}" for role in sorted(counts))
    )

    # ---- SQL source catalog + two-hop source trace ----
    print("[docgen] loading SQL export catalog")
    sql_catalog = sqlsource.load_sql_catalog(
        cfg.resolve(cfg.paths.sql_exports), repo_root=REPO_ROOT
    )
    print(f"[docgen]   {len(sql_catalog.views_by_entity)} SQL view(s)")
    print("[docgen] building two-hop source trace")
    trace = sourcetrace.build_source_trace(model, cls, sql_catalog, lin)

    # ---- Card context (shared by all four renderers) ----
    ctx = cards.build_context(
        cfg=cfg,
        model=model,
        reports=reports,
        dataflows=dataflows,
        flows=flows,
        lin=lin,
        cls=cls,
        trace=trace,
        sql_catalog=sql_catalog,
    )

    # ---- Write phase (skip files whose content is unchanged) ----
    generated: set[Path] = set()
    changed: list[Path] = []

    def emit(path: Path, content: str) -> None:
        generated.add(path.resolve())
        if md.write(path, content):
            changed.append(path)

    # ---- Four flat card bundles (agent knowledge base) ----
    kb_outputs: list[tuple[str, str]] = [("00-overview.md", ro.render_overview(ctx))]
    kb_outputs.extend(rmm.render_model_and_metrics(ctx))
    kb_outputs.append(("02-data-pipeline.md", rp.render_data_pipeline(ctx)))
    kb_outputs.append(("03-reports.md", rr.render_reports(ctx)))
    if cfg.source_code_enabled:
        kb_outputs.append(("04-source-code.md", rsc.render_source_code(ctx)))
    for filename, text in kb_outputs:
        emit(DOCS / filename, text)

    # ---- Plain-text mirror (for agents that reject Markdown uploads) ----
    # Each KB file is saved verbatim as a .txt under model-docs-txt/, a simple
    # "save as" with identical content. This folder is independent of the
    # validation gates that run over model-docs/.
    txt_generated: set[Path] = set()

    def emit_txt(path: Path, content: str) -> None:
        txt_generated.add(path.resolve())
        if md.write(path, content):
            changed.append(path)

    for filename, text in kb_outputs:
        emit_txt(DOCS_TXT / (Path(filename).stem + ".txt"), text)

    # ---- Sweep orphans (files that existed but were not produced this run) ----
    removed = _sweep_orphans(DOCS, generated)
    removed += _sweep_orphans(DOCS_TXT, txt_generated)
    unchanged = len(generated) + len(txt_generated) - len(changed)

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
