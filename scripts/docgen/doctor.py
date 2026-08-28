"""Preflight check for running the docgen engine in a new repository.

Run:

    python -m scripts.docgen.doctor

Answers the question a first-time user of a freshly-ported engine actually
has: *"will this run here, and what will it produce?"* It resolves every
configured glob, reports what matched, states which knowledge-base files will
be emitted, and flags config fields that would render as ``{{PLACEHOLDER}}``.

Strictly read-only: it writes nothing and never touches ``model-docs/``.
Exit code 0 means ``generate`` should run; 1 means a blocker must be fixed
first.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config as configmod
from . import md
from . import __version__
from .generate import _resolve_report_definitions

OK = "OK"
BLOCK = "BLOCKER"
NONE = "none"
WARN = "WARN"


def _rows_for_paths(cfg: configmod.Config) -> tuple[list[tuple[str, str, str]], bool]:
    """Resolve every source glob. Returns (rows, blocked)."""
    rows: list[tuple[str, str, str]] = []
    blocked = False

    model_matches = cfg.resolve(cfg.paths.semantic_model_definition)
    if model_matches:
        detail = f"`{cfg.paths.semantic_model_definition}` -> {model_matches[0].relative_to(md.REPO_ROOT)}"
        if len(model_matches) > 1:
            detail += f" (+{len(model_matches) - 1} more; first wins)"
        rows.append(("Semantic model (required)", OK, detail))
    else:
        blocked = True
        rows.append((
            "Semantic model (required)",
            BLOCK,
            f"nothing matched `{cfg.paths.semantic_model_definition}` "
            "- set `[paths] semantic_model_definition`",
        ))

    report_defs = _resolve_report_definitions(cfg)
    if report_defs:
        rows.append((
            "Thin reports (required)",
            OK,
            f"{len(report_defs)} report definition(s) after exclusions",
        ))
    else:
        blocked = True
        # "Matched but all excluded" and "matched nothing" need different fixes.
        matched = cfg.resolve_many(cfg.paths.thin_report_definitions)
        if matched:
            detail = (
                f"{len(matched)} report(s) matched but ALL were removed by "
                "`[paths] excluded_report_definitions`. If the report attached to "
                "the semantic model is your deliverable rather than a development "
                "artefact, clear that exclusion."
            )
        else:
            detail = (
                f"nothing matched `{', '.join(cfg.paths.thin_report_definitions)}` "
                "- set `[paths] thin_report_definitions`"
            )
        rows.append(("Thin reports (required)", BLOCK, detail))

    optional: list[tuple[str, list[Path], str]] = [
        ("Dataflow exports", cfg.resolve(cfg.paths.dataflow_exports), cfg.paths.dataflow_exports),
        ("SQL exports", cfg.resolve(cfg.paths.sql_exports), cfg.paths.sql_exports),
        (
            "Orchestration flows",
            cfg.resolve_many(cfg.paths.orchestration_definitions),
            ", ".join(cfg.paths.orchestration_definitions),
        ),
        (
            "Canvas Power Apps",
            cfg.resolve_many(cfg.paths.power_apps_definitions),
            ", ".join(cfg.paths.power_apps_definitions),
        ),
    ]
    for label, matches, pattern in optional:
        if matches:
            rows.append((f"{label} (optional)", OK, f"{len(matches)} file(s) via `{pattern}`"))
        else:
            rows.append((
                f"{label} (optional)",
                NONE,
                f"nothing matched `{pattern}` - section will be omitted",
            ))
    return rows, blocked


def _rows_for_content(cfg: configmod.Config) -> list[tuple[str, str, str]]:
    """Flag config fields that drive prose quality but are empty."""
    checks: list[tuple[str, object, str]] = [
        ("Solution display name", cfg.solution.display_name, "[solution] display_name"),
        ("Solution purpose", cfg.solution.purpose, "[solution] purpose"),
        ("Upstream-platform narrative", cfg.narratives.upstream_platforms, "[narratives] upstream_platforms"),
        ("Lineage narrative", cfg.narratives.lineage_narrative, "[narratives] lineage_narrative"),
        ("Change-impact notes", cfg.narratives.change_impact_notes, "[narratives] change_impact_notes"),
        ("Acronyms", cfg.acronyms, "[acronyms]"),
        ("Headline metrics", cfg.headline_metrics, "[headline_metrics] names"),
        ("Data-source descriptions", cfg.data_sources, "[[data_sources]]"),
        ("Report purposes", cfg.report_purposes, "[reports]"),
        ("Primary workspace ID", cfg.workspaces.primary, "[workspaces] primary"),
    ]
    rows = [
        (label, OK if value else WARN, f"`{key}`" + ("" if value else " is empty - renders a placeholder"))
        for label, value, key in checks
    ]

    # Unset prefixes are a legitimate choice, not a defect - report, don't warn.
    rows.append((
        "Selector / router pattern",
        OK if cfg.measures.selector_prefixes else NONE,
        "`[measures] selector_prefixes`"
        + ("" if cfg.measures.selector_prefixes else
           " not set - no metric concept cards. Correct if this model has no"
           " slicer-driven SWITCH pattern; set it if it does."),
    ))
    rows.append((
        "Base-measure folding",
        OK if cfg.measures.base_prefixes else NONE,
        "`[measures] base_prefixes`"
        + ("" if cfg.measures.base_prefixes else
           " not set - leaf measures get full cards instead of folding into"
           " their table card. Nothing is lost, but 01 grows."),
    ))
    return rows


def main(argv: list[str] | None = None) -> int:
    md.take_repo_root_arg(argv if argv is not None else sys.argv[1:])
    cfg = configmod.load()

    rows: list[tuple[str, str, str]] = []

    py_ok = sys.version_info >= (3, 11)
    rows.append((
        "Python 3.11+",
        OK if py_ok else BLOCK,
        f"running {sys.version_info.major}.{sys.version_info.minor}"
        + ("" if py_ok else " - `tomllib` requires 3.11"),
    ))

    rows.append(("Engine version", OK, f"docgen {__version__}"))
    rows.append((
        "Repo root",
        OK,
        f"`{md.REPO_ROOT}` (resolved via {md.repo_root_source()})",
    ))

    cfg_file = configmod.config_path()
    cfg_exists = cfg_file.exists()
    rows.append((
        "Config file",
        OK if cfg_exists else WARN,
        f"`{cfg_file.relative_to(md.REPO_ROOT)}`"
        + ("" if cfg_exists else " missing - engine would run on defaults with placeholder prose"),
    ))

    path_rows, blocked = _rows_for_paths(cfg)
    rows.extend(path_rows)
    blocked = blocked or not py_ok

    rows.extend(_rows_for_content(cfg))

    print(f"\n_Preflight: {md.TODAY} - "
          + ("READY" if not blocked else "BLOCKED")
          + "_\n")
    table = ["| Check | Status | Detail |", "| --- | --- | --- |"]
    for name, status, detail in rows:
        table.append(f"| {name} | {status} | {md.md_escape_pipe(detail)} |")
    print("\n".join(table))

    planned = ["00-overview.md", "01-model-and-metrics.md", "02-data-pipeline.md", "03-reports.md"]
    if cfg.source_code_enabled:
        planned.append("04-source-code.md")
    if cfg.resolve_many(cfg.paths.power_apps_definitions):
        planned.append("05-power-apps.md")
    print(f"\nPlanned output under `model-docs/`: {', '.join(planned)}")
    print("(`01-model-and-metrics.md` may split into numbered parts if oversized.)")

    if blocked:
        print("\n[doctor] BLOCKED - fix the blocker(s) above before running generate.")
        return 1
    print("\n[doctor] READY - run `python -m scripts.docgen.generate`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
