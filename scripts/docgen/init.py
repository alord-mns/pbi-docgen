"""Scaffold a repository so the docgen engine can run in it.

Run once after installing the engine:

    python -m scripts.docgen.init

Detects an existing Power BI layout before writing anything and adapts to it:
if the PBIP source is already present at non-default paths, the generated
``.docgen.toml`` points at where it actually is rather than imposing this
engine's default folder names. Folders are only scaffolded for a genuinely
empty repository.

Strictly **non-destructive** — nothing that already exists is modified, above
all an existing ``.docgen.toml``, which is the user's hand-written config. Safe
to re-run; every item is reported as created or skipped.

Requires Python 3.11+ and nothing else.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import md

# Directories never worth walking when sniffing for PBIP source.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".vscode",
    "model-docs", "model-docs-txt", "scripts",
}

_SCAFFOLD_DIRS = [
    "pbi/semantic-model",
    "pbi/thin-reports",
    "dataflows",
    "sql",
    "orchestration",
    "power-apps",
    "model-docs",
]

_GITIGNORE_ENTRIES = ["__pycache__/", "*.msapp"]


def _walk_dirs(root: Path):
    """Yield directories under ``root``, skipping noise."""
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _detect(root: Path) -> dict[str, object]:
    """Find PBIP and companion artefacts wherever they already live."""
    found: dict[str, object] = {}
    model_parents: list[str] = []
    report_parents: list[str] = []

    for d in _walk_dirs(root):
        if not (d / "definition").is_dir():
            continue
        if d.name.endswith(".SemanticModel"):
            model_parents.append(_rel(root, d.parent))
        elif d.name.endswith(".Report"):
            report_parents.append(_rel(root, d.parent))

    if model_parents:
        first = sorted(set(model_parents))[0]
        found["semantic_model_definition"] = f"{first}/*.SemanticModel/definition"
        # The exclusion glob must track wherever the model actually lives, or the
        # PBIP's embedded development report gets documented as a thin report.
        found["excluded_report_definitions"] = [f"{first}/*.Report/definition"]

    # Two solution shapes. Where reports sit *outside* the semantic-model folder
    # the model-attached one is usually a development artefact; where it is the
    # only report it must be the deliverable. Both are only a starting guess —
    # `[report_scope]` in the generated config is where the user states the truth.
    thin = sorted({p for p in report_parents if p not in set(model_parents)})
    if thin:
        found["thin_report_definitions"] = [f"{p}/*.Report/definition" for p in thin]
        found["_include_model_attached_report"] = False
    elif report_parents:
        found["thin_report_definitions"] = [
            f"{p}/*.Report/definition" for p in sorted(set(report_parents))
        ]
        found["excluded_report_definitions"] = []
        found["_include_model_attached_report"] = True

    # Distinctive filenames only — never guess which loose *.json are dataflows.
    for key, pattern, builder in (
        ("sql_exports", "*.sql", lambda p: f"{p}/*.sql"),
        ("orchestration_definitions", "definition.json", lambda p: f"{p}/**/definition.json"),
        ("power_apps_definitions", "CanvasManifest.json", lambda p: f"{p}/**/CanvasManifest.json"),
    ):
        hits = [
            f for f in root.rglob(pattern)
            if f.is_file()
            and not any(part in _SKIP_DIRS for part in f.relative_to(root).parts)
        ]
        if not hits:
            continue
        if key == "sql_exports":
            found[key] = builder(_rel(root, hits[0].parent))
        else:
            tops = sorted({_rel(root, h).split("/")[0] for h in hits})
            found[key] = [builder(t) for t in tops]

    return found


def _paths_block(detected: dict[str, object]) -> str:
    """Emit a fully-populated [paths] block.

    Written out in full even when it matches the engine defaults, so a repo
    never silently depends on a default that a later engine release might change.
    """
    from .config import _DEFAULT_PATHS

    lines: list[str] = []
    for key in (
        "semantic_model_definition",
        "thin_report_definitions",
        "excluded_report_definitions",
        "dataflow_exports",
        "sql_exports",
        "orchestration_definitions",
        "power_apps_definitions",
    ):
        value = detected.get(key, _DEFAULT_PATHS.get(key))
        rendered = (
            '"' + str(value) + '"' if isinstance(value, str)
            else "[" + ", ".join(f'"{v}"' for v in value) + "]"
        )
        lines.append(f"{key} = {rendered}")

    body = "\n".join(lines)
    return (
        "[paths]\n"
        "# Where your artefacts live. Detected from this repo where possible,\n"
        "# otherwise the engine defaults. Every glob is relative to the repo root.\n"
        "# These are written out in full deliberately: pinning them here means a\n"
        "# future engine release cannot move your layout out from under you.\n"
        f"{body}\n"
    )


def _starter_toml(detected: dict[str, object], display_name: str) -> str:
    return f'''# =============================================================================
# Per-repo configuration for the docgen engine.
#
# This file is the ONLY place repo-specific facts belong. The engine under
# `scripts/docgen/` is model-agnostic: it discovers files by glob and renders
# generic templates. Names, narratives, acronyms and workspace IDs come from
# here.
#
# Anything left empty renders a {{{{PLACEHOLDER}}}} rather than a guess.
# Run `python -m scripts.docgen.doctor` to see what is still missing.
# =============================================================================

[solution]
display_name = "{display_name}"
short_name = ""
purpose = """
One paragraph on what this solution does, who uses it, and on what cadence.
"""
calendar_summary = ""
business_domains = ""

[workspaces]
# Fabric / Power BI workspace GUIDs. Safe to commit; they are not credentials.
primary = ""   # where the dataflows live
dataset = ""   # where the semantic model lives, if different
secondary = []

{_paths_block(detected)}
[report_scope]
# Is the report attached to the semantic model a real, user-facing report, or a
# development artefact you don't want documented? Only you know — the value
# below is a guess based on your layout, so correct it if it is wrong.
#   true  - document it alongside any thin reports
#   false - skip it
# This overrides `excluded_report_definitions` for that report only.
include_model_attached_report = {str(detected.get("_include_model_attached_report", False)).lower()}

[measures]
# Naming conventions used to classify measures. BOTH ARE OPTIONAL.
# selector_prefixes: picker / slicer-driver measures. Leave empty if this model
#   has no slicer-driven SWITCH pattern — you lose nothing, there are simply no
#   metric-concept cards to produce.
# base_prefixes: atomic SUM/COUNT wrappers, folded into their table card.
#   Leave empty and every leaf measure gets a full card instead.
selector_prefixes = []
base_prefixes = []

[narratives]
# Prose the engine cannot derive from source. Each renders a placeholder if empty.
upstream_platforms = ""
lineage_narrative = []
change_impact_notes = []

[acronyms]
# "ABC" = "Expanded meaning". Every entry must appear in the overview or the
# acronym quality gate fails, so remove ones you stop using.

[headline_metrics]
names = []

[reports]
# "Report Name" = "One-line purpose." Keys may omit the .Report suffix.

[powerbi_app]
# The Power BI *distribution* App that publishes the reports.
# NOT a Power Platform canvas Power App (those are found via [paths]).
name = ""
purpose = ""
audience = ""

# [[data_sources]]
# name = "Upstream system"
# purpose = "What it provides."
# mechanism = "Connector / method used."
# host = ""
# freshness = ""
# connector_match = []

# [source_code]
# Source-code cards are emitted automatically when dataflow or SQL source is
# present. Uncomment to suppress them if raw SQL is sensitive in this repo.
# enabled = false
'''


def main(argv: list[str] | None = None) -> int:
    md.take_repo_root_arg(argv if argv is not None else sys.argv[1:])
    root = md.REPO_ROOT
    log: list[tuple[str, str]] = []

    print(f"[init] scaffolding `{root}`")
    detected = _detect(root)

    display_name = ""
    model_glob = detected.get("semantic_model_definition")
    if isinstance(model_glob, str):
        for d in root.glob(model_glob.replace("/definition", "")):
            display_name = d.name.removesuffix(".SemanticModel")
            break

    if detected:
        print("[init] detected existing layout:")
        for key in sorted(detected):
            print(f"[init]   {key} = {detected[key]}")
    else:
        print("[init] no existing Power BI source found — scaffolding the default layout")
        for rel in _SCAFFOLD_DIRS:
            target = root / rel
            if target.exists():
                log.append(("skipped", rel + "/"))
            else:
                target.mkdir(parents=True, exist_ok=True)
                (target / ".gitkeep").touch()
                log.append(("created", rel + "/.gitkeep"))

    docs_dir = root / "model-docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = docs_dir / ".docgen.toml"
    if cfg_file.exists():
        log.append(("skipped", "model-docs/.docgen.toml"))
    else:
        cfg_file.write_text(_starter_toml(detected, display_name), encoding="utf-8")
        log.append(("created", "model-docs/.docgen.toml"))

    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    missing = [e for e in _GITIGNORE_ENTRIES if e not in existing]
    if missing:
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        gitignore.write_text(existing + prefix + "\n".join(missing) + "\n", encoding="utf-8")
        log.append(("appended", f".gitignore ({', '.join(missing)})"))
    else:
        log.append(("skipped", ".gitignore"))

    print()
    for action, item in log:
        print(f"[init] {action:9} {item}")

    created = sum(1 for a, _ in log if a != "skipped")
    print(f"\n[init] {created} item(s) written, {len(log) - created} left untouched.")
    print("[init] next: fill in `model-docs/.docgen.toml`, then run "
          "`python -m scripts.docgen.doctor`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
