"""Documentation quality-gate validator for the card-based agent knowledge base.

Run:

    python -m scripts.docgen.validate

The knowledge base is four flat files of self-sufficient cards
(``00-overview.md`` … ``03-reports.md``). This validator enforces that the
cards are addressable, cross-referenceable, complete, and leak-free. It is
read-only over the documentation — it prints a pass/fail report and exits
non-zero if any gate fails; it never mutates the generated files.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from . import config as configmod
from . import dataflow as dfmod
from . import dax_refs
from . import lineage as lineagemod
from . import md
from . import orchestration as orcmod
from . import pbir as pbirmod
from . import power_apps as pamod
from . import tmdl
from . import cards
from .generate import (
    _resolve_dataflow_files,
    _resolve_report_definitions,
    _resolve_semantic_model,
    _visual_usage,
)


KB_FILES = [
    "00-overview.md",
    "01-model-and-metrics.md",
    "02-data-pipeline.md",
    "03-reports.md",
]

SECRET_PATTERNS = [
    re.compile(r"\bpassword\s*[:=]\s*['\"][^'\"\n]{1,}['\"]", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9+/_=-]{8,}['\"]", re.IGNORECASE),
    re.compile(r"\bAccountKey\s*=\s*[A-Za-z0-9+/=]{20,}"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{5,}\b"),  # JWT
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TEAMS_THREAD_RE = re.compile(r"19:[A-Za-z0-9_-]+@thread\.v2")

_ANCHOR_RE = re.compile(r'<a id="([^"]+)"></a>')
_LINK_RE = re.compile(r"\]\(#([a-z0-9-]+)\)")


# ---------------------------------------------------------------------------
# Card-block model
# ---------------------------------------------------------------------------
class CardBlock:
    __slots__ = ("anchor", "title", "kind", "text")

    def __init__(self, anchor: str, title: str, kind: str, text: str) -> None:
        self.anchor = anchor
        self.title = title
        self.kind = kind
        self.text = text


def _split_cards(text: str) -> list[CardBlock]:
    """Slice a bundle into card blocks keyed by their anchor."""
    blocks: list[CardBlock] = []
    matches = list(_ANCHOR_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]
        anchor = m.group(1)
        title_m = re.search(r"^## (.+)$", chunk, flags=re.MULTILINE)
        kind_m = re.search(r"^\*\*Type:\*\* (.+)$", chunk, flags=re.MULTILINE)
        title = title_m.group(1).strip() if title_m else ""
        kind = kind_m.group(1).strip() if kind_m else ""
        blocks.append(CardBlock(anchor, title, kind, chunk))
    return blocks


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
def _check_files_present(docs: Path, extra: tuple[str, ...] = ()) -> tuple[bool, str]:
    expected = list(KB_FILES) + list(extra)
    missing = [f for f in expected if not (docs / f).exists()]
    if missing:
        return False, f"Missing knowledge-base file(s): {missing}"
    return True, f"All {len(expected)} knowledge-base files present."


def _check_anchor_uniqueness(blobs: dict[str, str]) -> tuple[bool, str]:
    seen: dict[str, str] = {}
    dupes: list[str] = []
    total = 0
    for name in sorted(blobs):
        for a in _ANCHOR_RE.findall(blobs[name]):
            total += 1
            if a in seen:
                dupes.append(f"{a} ({seen[a]} & {name})")
            else:
                seen[a] = name
    if dupes:
        return False, f"{len(dupes)} duplicate anchor(s) — sample: {dupes[:5]}"
    return True, f"All {total} card anchors are unique across the knowledge base."


def _check_link_integrity(blobs: dict[str, str]) -> tuple[bool, str]:
    anchors: set[str] = set()
    for text in blobs.values():
        anchors |= set(_ANCHOR_RE.findall(text))
    dangling: list[str] = []
    for name in sorted(blobs):
        for target in sorted(set(_LINK_RE.findall(blobs[name]))):
            if target not in anchors:
                dangling.append(f"{name} -> #{target}")
    if dangling:
        return False, f"{len(dangling)} dangling link(s) — sample: {dangling[:5]}"
    return True, "Every internal card link resolves to an existing anchor."


def _check_measure_coverage(cls, anchors: set[str], blob_metrics: str) -> tuple[bool, str]:
    missing: list[str] = []
    for mc in cls.of_role(dax_refs.ROLE_ROUTER):
        if cards.card_anchor("concept", mc.name) not in anchors:
            missing.append(f"router:{mc.name}")
    for role in (dax_refs.ROLE_METRIC, dax_refs.ROLE_COMPUTE):
        for mc in cls.of_role(role):
            if cards.card_anchor("measure", mc.name) not in anchors:
                missing.append(f"{role}:{mc.name}")
    for mc in cls.of_role(dax_refs.ROLE_BASE):
        if f"`{mc.name}`" not in blob_metrics:
            missing.append(f"base:{mc.name}")
    total = sum(
        len(cls.of_role(r))
        for r in (
            dax_refs.ROLE_ROUTER,
            dax_refs.ROLE_METRIC,
            dax_refs.ROLE_COMPUTE,
            dax_refs.ROLE_BASE,
        )
    )
    if missing:
        return False, f"{len(missing)}/{total} measures without a card/fold — sample: {missing[:5]}"
    return True, f"All {total} classified measures have a card or are folded into a table card."


def _check_concept_routing(metric_cards: list[CardBlock]) -> tuple[bool, str]:
    bad: list[str] = []
    n = 0
    for b in metric_cards:
        if b.kind.startswith("Metric concept"):
            n += 1
            if "### Routing" not in b.text:
                bad.append(b.title)
    if bad:
        return False, f"{len(bad)} concept card(s) missing a routing table — sample: {bad[:5]}"
    return True, f"All {n} concept (router) cards carry a routing table."


def _check_source_trace(metric_cards: list[CardBlock]) -> tuple[bool, str]:
    bad: list[str] = []
    n = 0
    for b in metric_cards:
        if "Measure (metric)" in b.kind:
            n += 1
            if "### Source trace" not in b.text:
                bad.append(b.title)
    if bad:
        return False, f"{len(bad)} metric card(s) missing a source-trace section — sample: {bad[:5]}"
    return True, f"All {n} metric cards carry a source-trace section."


def _check_downstream_impact(pipeline_cards: list[CardBlock]) -> tuple[bool, str]:
    bad: list[str] = []
    n = 0
    for b in pipeline_cards:
        if b.kind.startswith("Dataflow"):
            n += 1
            if "### Downstream impact" not in b.text:
                bad.append(b.title)
    if bad:
        return False, f"{len(bad)} dataflow card(s) missing a downstream-impact section — sample: {bad[:5]}"
    return True, f"All {n} dataflow cards carry a downstream-impact section."


def _check_acronyms(cfg: configmod.Config, blob_overview: str) -> tuple[bool, str]:
    if not cfg.acronyms:
        return True, "No acronyms configured — gate skipped."
    if "### Acronyms" not in blob_overview:
        return False, "Overview is missing the acronyms section."
    missing = [k for k in sorted(cfg.acronyms) if f"`{k}`" not in blob_overview]
    if missing:
        return False, f"{len(missing)} configured acronym(s) absent from overview — sample: {missing[:5]}"
    return True, f"All {len(cfg.acronyms)} configured acronyms appear in the overview."


def _check_secrets(blobs: dict[str, str]) -> tuple[bool, str]:
    hits: list[str] = []
    for name in sorted(blobs):
        text = blobs[name]
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{name}: {m.group(0)[:50]}")
        for m in EMAIL_RE.finditer(text):
            hits.append(f"{name}: email {m.group(0)}")
        for m in TEAMS_THREAD_RE.finditer(text):
            hits.append(f"{name}: teams-thread {m.group(0)[:30]}")
    if hits:
        return False, f"{len(hits)} possible leak(s) — sample: {hits[:5]}"
    return True, "No secrets, recipient emails, or Teams thread IDs found."


def _check_source_code(blob: str) -> tuple[bool, str]:
    """Every source-lineage card must carry code or an explicit absence note."""
    cards_ = [c for c in _split_cards(blob) if c.kind.startswith("Source lineage (code)")]
    if not cards_:
        return False, "04-source-code.md emitted but contains no source-lineage cards."
    bad: list[str] = []
    for c in cards_:
        has_code = "```sql" in c.text or "```powerquery-m" in c.text
        has_note = "_No SQL export" in c.text or "_No dataflow M query" in c.text
        is_connection = c.title == "Connection parameters"
        if not (has_code or has_note or is_connection):
            bad.append(c.title)
    if bad:
        return False, f"{len(bad)} card(s) missing code / absence note: {bad[:5]}"
    return True, f"All {len(cards_)} source-lineage card(s) carry code or a documented absence."


def _check_power_apps(blob: str) -> tuple[bool, str]:
    """Each canvas Power App card must carry connector and data-source sections."""
    cards_ = [c for c in _split_cards(blob) if c.kind.startswith("Power App")]
    if not cards_:
        return True, "No Power App cards to check."
    bad: list[str] = []
    for c in cards_:
        if "### Connectors" not in c.text or "### Data sources" not in c.text:
            bad.append(c.title or c.anchor)
    if bad:
        return False, f"{len(bad)} Power App card(s) missing connector/data-source section: {bad[:5]}"
    return True, f"All {len(cards_)} Power App card(s) carry connector and data-source sections."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    md.take_repo_root_arg(argv if argv is not None else sys.argv[1:])
    DOCS = md.DOCS
    cfg = configmod.load()
    print("[validate] loading sources for quality-gate checks…")
    model = tmdl.load_model(_resolve_semantic_model(cfg))
    reports = [pbirmod.load_report(rd) for rd in _resolve_report_definitions(cfg)]
    dataflows = dfmod.load_dataflow_files(_resolve_dataflow_files(cfg))
    flows = orcmod.load_flows(cfg.resolve_many(cfg.paths.orchestration_definitions))
    lin = lineagemod.build(
        model, reports[0], dataflows, reports=reports, orchestration_flows=flows
    )
    cls = dax_refs.classify_measures(
        model,
        visual_usage=_visual_usage(reports),
        selector_prefixes=tuple(cfg.measures.selector_prefixes),
        base_prefixes=tuple(cfg.measures.base_prefixes),
    )

    # Canvas Power Apps are presence-driven: the 05 file is required only when
    # at least one unpacked app is discovered under the configured glob.
    power_apps = pamod.load_power_apps(
        cfg.resolve_many(cfg.paths.power_apps_definitions), repo_root=md.REPO_ROOT
    )

    extra_files: list[str] = []
    if cfg.source_code_enabled:
        extra_files.append("04-source-code.md")
    if power_apps:
        extra_files.append("05-power-apps.md")
    files_ok, files_msg = _check_files_present(DOCS, extra=tuple(extra_files))

    # Discover the full knowledge base, including any size-split 01 parts.
    kb_files = list(KB_FILES)
    for p in sorted(DOCS.glob("01-model-and-metrics-*.md")):
        if p.name not in kb_files:
            kb_files.append(p.name)
    if (DOCS / "04-source-code.md").exists() and "04-source-code.md" not in kb_files:
        kb_files.append("04-source-code.md")
    if (DOCS / "05-power-apps.md").exists() and "05-power-apps.md" not in kb_files:
        kb_files.append("05-power-apps.md")
    blobs: dict[str, str] = {}
    for f in kb_files:
        p = DOCS / f
        blobs[f] = p.read_text(encoding="utf-8") if p.exists() else ""

    all_anchors: set[str] = set()
    for text in blobs.values():
        all_anchors |= set(_ANCHOR_RE.findall(text))

    # The model-and-metrics bundle may span several files; treat them as one.
    model_metric_blob = "\n".join(
        blobs[f] for f in kb_files if f.startswith("01-model-and-metrics")
    )
    metric_cards = _split_cards(model_metric_blob)
    pipeline_cards = _split_cards(blobs["02-data-pipeline.md"])

    gates: list[tuple[str, bool, str]] = []
    gates.append(("Knowledge-base files present", files_ok, files_msg))
    gates.append(("Card anchors unique", *_check_anchor_uniqueness(blobs)))
    gates.append(("Cross-reference integrity", *_check_link_integrity(blobs)))
    gates.append(
        ("Measure coverage", *_check_measure_coverage(cls, all_anchors, model_metric_blob))
    )
    gates.append(("Concept routing tables", *_check_concept_routing(metric_cards)))
    gates.append(("Metric source-trace sections", *_check_source_trace(metric_cards)))
    gates.append(("Dataflow downstream impact", *_check_downstream_impact(pipeline_cards)))
    gates.append(("Acronyms documented", *_check_acronyms(cfg, blobs["00-overview.md"])))
    gates.append(("No sensitive data", *_check_secrets(blobs)))
    if cfg.source_code_enabled and blobs.get("04-source-code.md"):
        gates.append(
            ("Source-lineage cards carry code", *_check_source_code(blobs["04-source-code.md"]))
        )
    if power_apps and blobs.get("05-power-apps.md"):
        gates.append(
            ("Power App cards complete", *_check_power_apps(blobs["05-power-apps.md"]))
        )

    overall = all(ok for _name, ok, _msg in gates)
    rows = ["| Gate | Status | Detail |", "| --- | --- | --- |"]
    for name, ok, msg in gates:
        icon = "PASS" if ok else "FAIL"
        rows.append(f"| {name} | {icon} | {md.md_escape_pipe(msg)} |")
    print(f"\n_Validated: {md.TODAY} — overall: " + ("PASS" if overall else "FAIL") + "_\n")
    print("\n".join(rows))
    print()
    if overall:
        print("[validate] PASS — all quality gates met.")
    else:
        print("[validate] FAIL — see report above.")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
