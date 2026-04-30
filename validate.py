"""Documentation quality-gate validator (per documentation_req.md §4).

Run:

    python -m scripts.docgen.validate

Outputs a pass/fail report and exits non-zero if any gate fails.  The
result is also embedded into ``docs/README.md`` between the
``<!-- VALIDATION:START -->`` / ``<!-- VALIDATION:END -->`` markers so
that the home document always reflects the latest run.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from . import dataflow as dfmod
from . import lineage as lineagemod
from . import md
from . import pbir as pbirmod
from . import tmdl
from .generate import (
    DATAFLOWS_DIR,
    DOCS,
    REPORT_DEFINITION,
    SEMANTIC_DEFINITION,
)


SECRET_PATTERNS = [
    re.compile(r"\bpassword\s*[:=]\s*['\"][^'\"\n]{1,}['\"]", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9+/_=-]{8,}['\"]", re.IGNORECASE),
    re.compile(r"\bAccountKey\s*=\s*[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\b(?:eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{5,})\b"),  # JWT
]


def _gather_md_files(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*.md")
        if p.name not in {"documentation_req.md"}
    ]


def _check_measure_coverage(model: tmdl.Model, files: list[Path]) -> tuple[bool, str]:
    measures = [(m.table, m.name) for t in model.tables for m in t.measures]
    measure_blob = "\n".join(p.read_text(encoding="utf-8") for p in files if "measures" in str(p))
    missing: list[str] = []
    for _table, name in measures:
        # The measure docs render names as ### `Name`.
        needle = f"### `{name}`"
        if needle not in measure_blob:
            missing.append(name)
    if not missing:
        return True, f"All {len(measures):,} measures documented."
    return False, f"{len(missing)}/{len(measures):,} measures missing — sample: {missing[:5]}"


def _check_unresolved_unknowns(files: list[Path]) -> tuple[bool, str]:
    bad: list[tuple[Path, int, str]] = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            # TODO without flag, or empty placeholder bullets
            if re.search(r"\bTODO\b", stripped) and "{{PLACEHOLDER}}" not in stripped:
                bad.append((p, i, stripped))
            if re.match(r"-\s*$", stripped):
                bad.append((p, i, "(empty bullet)"))
    if not bad:
        return True, "No unresolved TODOs or empty bullets."
    return False, f"{len(bad)} issues — sample: {bad[:3]}"


def _check_name_consistency(model: tmdl.Model, files: list[Path]) -> tuple[bool, str]:
    blob = "\n".join(p.read_text(encoding="utf-8") for p in files)
    # Sample 10 random table names — they should all appear at least once
    missing = [t.name for t in model.tables if t.name not in blob][:5]
    if not missing:
        return True, f"All {len(model.tables)} table names appear in the documentation."
    return False, f"Tables missing from docs: {missing}"


def _check_lineage_completeness(model: tmdl.Model, lineage_doc: Path) -> tuple[bool, str]:
    if not lineage_doc.exists():
        return False, "lineage.md does not exist"
    text = lineage_doc.read_text(encoding="utf-8")
    # Lineage diagram should mention every model fact table at least
    fact_tables = [t.name for t in model.tables if t.name.startswith("fact")]
    missing = [f for f in fact_tables if f not in text]
    if not missing:
        return True, f"All {len(fact_tables)} fact tables present in lineage."
    return False, f"Fact tables absent from lineage: {missing[:5]}"


def _check_secrets(files: list[Path]) -> tuple[bool, str]:
    hits: list[str] = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{p.relative_to(md.REPO_ROOT)}: {m.group(0)[:60]}")
    if not hits:
        return True, "No secret patterns found in any documentation file."
    return False, f"Possible secrets: {hits[:5]}"


def _check_audience_lines(files: list[Path]) -> tuple[bool, str]:
    missing: list[str] = []
    excluded = {"CHANGELOG.md", "ReleaseNotes.md", "dataflow-references.md"}
    for p in files:
        if p.name in excluded:
            continue
        text = p.read_text(encoding="utf-8")
        if "**Audience:**" not in text:
            missing.append(str(p.relative_to(md.REPO_ROOT)))
    if not missing:
        return True, "Every relevant doc carries an Audience line."
    return False, f"Missing audience lines: {missing[:5]}"


def _check_internal_links(files: list[Path]) -> tuple[bool, str]:
    bad: list[str] = []
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for p in files:
        text = p.read_text(encoding="utf-8")
        for m in link_re.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # Trim anchor
            base = target.split("#", 1)[0]
            if not base:
                continue
            decoded = base.replace("%20", " ")
            target_path = (p.parent / decoded).resolve()
            if not target_path.exists():
                bad.append(f"{p.relative_to(md.REPO_ROOT)} -> {target}")
    if not bad:
        return True, "All internal links resolve."
    return False, f"{len(bad)} broken link(s) — sample: {bad[:5]}"


def _check_changelog(docs: Path) -> tuple[bool, str]:
    cl = docs / "CHANGELOG.md"
    if not cl.exists():
        return False, "CHANGELOG.md missing"
    txt = cl.read_text(encoding="utf-8")
    if re.search(r"##\s*\[\d{4}-\d{2}-\d{2}\]", txt):
        return True, "CHANGELOG.md has at least one dated entry."
    return False, "CHANGELOG.md has no dated entry."


def _check_release_notes(docs: Path) -> tuple[bool, str]:
    rn = docs / "ReleaseNotes.md"
    if not rn.exists():
        return False, "ReleaseNotes.md missing"
    return True, "ReleaseNotes.md template present."


def main(argv: list[str] | None = None) -> int:
    print("[validate] loading sources for measure-coverage check…")
    model = tmdl.load_model(SEMANTIC_DEFINITION)
    report = pbirmod.load_report(REPORT_DEFINITION)
    dataflows = dfmod.load_dataflows(DATAFLOWS_DIR)
    lin = lineagemod.build(model, report, dataflows)
    files = _gather_md_files(DOCS)
    print(f"[validate] {len(files)} markdown files under {DOCS}")

    gates: list[tuple[str, bool, str]] = []
    gates.append(("100% measure coverage", *_check_measure_coverage(model, files)))
    gates.append(("No unresolved unknowns", *_check_unresolved_unknowns(files)))
    gates.append(("Name consistency (tables)", *_check_name_consistency(model, files)))
    gates.append(("Lineage completeness", *_check_lineage_completeness(model, DOCS / "lineage" / "lineage.md")))
    gates.append(("No sensitive data", *_check_secrets(files)))
    gates.append(("Audience labels present", *_check_audience_lines(files)))
    gates.append(("Cross-reference integrity", *_check_internal_links(files)))
    gates.append(("Change log initialised", *_check_changelog(DOCS)))
    gates.append(("Release notes template ready", *_check_release_notes(DOCS)))

    overall = all(ok for _name, ok, _msg in gates)
    rows = ["| Gate | Status | Detail |", "| --- | --- | --- |"]
    for name, ok, msg in gates:
        icon = "✅ pass" if ok else "❌ fail"
        rows.append(f"| {name} | {icon} | {md.md_escape_pipe(msg)} |")
    summary = f"_Last validated: {md.TODAY} — overall: " + ("**PASS**" if overall else "**FAIL**") + "_\n\n" + "\n".join(rows)

    # Inject into README
    readme = DOCS / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        text = re.sub(
            r"<!-- VALIDATION:START -->.*?<!-- VALIDATION:END -->",
            "<!-- VALIDATION:START -->\n" + summary + "\n<!-- VALIDATION:END -->",
            text,
            flags=re.DOTALL,
        )
        readme.write_text(text, encoding="utf-8")

    print(summary)
    print()
    if overall:
        print("[validate] PASS — all quality gates met.")
    else:
        print("[validate] FAIL — see report above.")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
