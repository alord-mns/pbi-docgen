"""Renderer for ``model-docs/agent-instructions.md`` — the agent system prompt.

The knowledge base is only half of a working Q&A agent; the other half is the
system prompt that tells the agent how the cards are shaped and how to route a
question. That prompt is a product of *this engine's output format*, not of any
particular solution, so it is generated rather than hand-maintained: the card
types, the file list, and the worked examples are all derived from what this
run actually produced.

This file is **not** part of the knowledge base. It is not mirrored to
``model-docs-txt/`` and must not be uploaded alongside the cards — paste it into
the agent's system-prompt configuration instead.
"""
from __future__ import annotations

from . import dax_refs
from . import md
from .cards import DocContext


def _kb_present(kb_files: list[str], stem: str) -> bool:
    return any(f.startswith(stem) for f in kb_files)


def _examples(ctx: DocContext) -> tuple[str | None, str | None]:
    """A real concept and metric card title, so worked examples resolve."""
    routers = sorted(m.name for m in ctx.cls.of_role(dax_refs.ROLE_ROUTER))
    metrics = sorted(m.name for m in ctx.cls.of_role(dax_refs.ROLE_METRIC))
    return (routers[0] if routers else None), (metrics[0] if metrics else None)


def _card_types(ctx: DocContext, kb_files: list[str], concept_eg: str | None) -> list[str]:
    has_04 = _kb_present(kb_files, "04-source-code")
    has_05 = _kb_present(kb_files, "05-power-apps")
    bullets: list[str] = []

    if ctx.cls.of_role(dax_refs.ROLE_ROUTER):
        eg = f' (e.g. "{concept_eg}")' if concept_eg else ""
        bullets.append(
            f'- "Metric concept (router)" — a business metric family{eg}.\n'
            "  Start here for any business-metric question. It contains a \"### Routing\" table\n"
            "  mapping each selector value to the specific measure it resolves to, with the\n"
            "  underlying calculation inlined."
        )
    bullets.append(
        '- "Measure (metric)" — one concrete measure. Contains "### Definition (DAX)" and a\n'
        '  "### Source trace" table that maps each model column to its physical SQL\n'
        "  column, dataflow entity, and SQL view:line. This is the authoritative\n"
        '  "how is it calculated / where does the data come from" card.'
    )
    bullets.append(
        '- "Measure (compute)" — an internal helper measure (compact card).'
    )
    bullets.append(
        '- "Table / entity" — a model table: its columns, upstream source, and the\n'
        "  measures and report pages that depend on it."
    )
    pipeline = ['"Data source"', '"Dataflow"']
    if ctx.flows:
        pipeline.append('"Orchestration flow"')
    pipeline.append('"Runbook"')
    bullets.append(
        f"- {', '.join(pipeline)} — the data pipeline:\n"
        "  where data originates, how entities map to SQL views, and how/when it refreshes."
    )
    bullets.append(
        '- "Report" / "Report page" — what each report and page shows, its slicers,\n'
        "  filters, the metrics on it (with inline definitions), and its backing tables."
    )
    if has_04:
        bullets.append(
            '- "Source lineage (code)" — the literal source code for one source entity: the\n'
            "  full SQL view and the dataflow Power Query (M) that build it, linked up to\n"
            "  the model table(s) it feeds. Use this ONLY when the user asks to see the\n"
            "  actual SQL / M / Power Query code. Connector host names are redacted."
        )
    if has_05:
        bullets.append(
            '- "Power App (canvas)" — a Power Platform data-entry / workflow app that sits\n'
            "  beside the solution: its screens, connectors, and read/write data sources,\n"
            "  plus which dataflows it writes into. Use this for \"what app feeds this\" or\n"
            '  "where is data entered" questions. This is NOT the Power BI distribution App.'
        )
    return bullets


def _file_list(kb_files: list[str]) -> str:
    parts = [
        "00-overview (solution summary, architecture, glossary/acronyms,\nend-to-end dependencies)",
        "01-model-and-metrics (concept, measure, and table\ncards)",
        "02-data-pipeline (sources, dataflows, flows, runbook)",
        "03-reports",
    ]
    if _kb_present(kb_files, "04-source-code"):
        parts.append("04-source-code (one source-lineage card per source entity\nwith its SQL and M code)")
    if _kb_present(kb_files, "05-power-apps"):
        parts.append("05-power-apps (one card per canvas Power App)")
    return "The files: " + "; ".join(parts) + "."


def _answer_rules(ctx: DocContext, kb_files: list[str], concept_eg: str | None,
                  metric_eg: str | None) -> list[str]:
    has_04 = _kb_present(kb_files, "04-source-code")
    completion_eg = metric_eg or concept_eg
    eg_txt = f' (e.g. "{completion_eg}")' if completion_eg else ""

    rules: list[str] = []
    rules.append(
        "1. Every card is self-sufficient — answer from the single most relevant card\n"
        "   whenever possible. Do NOT try to follow links between cards."
    )
    rules.append(
        "2. Card-completion rule. Cards are split across files purely by size; every ## card\n"
        "   is self-sufficient. If a retrieved chunk shows a ## card header (or a\n"
        "   \"### Definition (DAX) · <Name>\" / \"### Source trace · <Name>\" section) but the\n"
        "   section you need is not in that chunk, do not treat the card as incomplete or\n"
        f"   missing. Run one more search using the exact card title{eg_txt} to pull the\n"
        "   remaining section, then answer. Never discard a card because one chunk is\n"
        "   partial. Do not chain further hops beyond this single completion query."
    )
    rules.append(
        "3. If a card references another card by name, and you need that card's detail,\n"
        "   run a NEW search for that exact title. Do not guess its contents."
    )
    if ctx.cls.of_role(dax_refs.ROLE_ROUTER):
        rules.append(
            "4. For \"what does <metric> mean / how is it calculated\": read the metric's\n"
            "   concept card first; if the user asks about a specific variant, read the\n"
            "   corresponding \"Measure (metric)\" card and quote its \"### Definition (DAX)\"."
        )
    else:
        rules.append(
            "4. For \"what does <metric> mean / how is it calculated\": read the relevant\n"
            "   \"Measure (metric)\" card and quote its \"### Definition (DAX)\" verbatim."
        )
    rules.append(
        "5. For \"where does <metric/number> come from\": read the \"### Source trace\" table\n"
        "   of the relevant \"Measure (metric)\" card and report the dataflow entity and\n"
        "   SQL view:line. Quote the derivation expression verbatim."
    )
    if has_04:
        rules.append(
            "6. For \"show me the SQL / M / Power Query code\": use the matching\n"
            "   \"Source lineage (code)\" card in 04-source-code. Table cards link down to it\n"
            "   via their \"**Source code:**\" line. Quote the code verbatim and note that\n"
            "   connector host names are redacted. Keep this distinct from rule 5: rule 5\n"
            "   explains *where a number comes from* in prose; this shows the *literal code*."
        )
    else:
        rules.append(
            "6. If asked to show the actual SQL / M / Power Query code: it is not in this\n"
            "   knowledge base. Say so, and answer from the \"### Source trace\" section instead."
        )
    rules.append(
        "7. For \"which report/page shows <metric>\": use the metric card's\n"
        "   \"### Used on report pages\", or the relevant \"Report page\" card."
    )
    rules.append(
        "8. For refresh/schedule/notification questions: use the "
        + ("\"Orchestration flow\" and \"Runbook\" cards" if ctx.flows else "\"Runbook\" card")
        + " in 02-data-pipeline."
    )
    rules.append(
        "9. For \"what does X stand for / mean\" where X is an abbreviation or acronym:\n"
        "   consult the Glossary & Acronyms card in 00-overview. Quote the definition\n"
        "   verbatim and name the card. If the term is not in that table, say it is not\n"
        "   defined in the knowledge base — never expand an acronym from general knowledge."
    )
    rules.append(
        "10. Reports, pages, and visuals are documented at three levels in 03-reports:\n"
        "    - To list or count reports → the \"Report Catalog\" index card.\n"
        "    - To list the pages of a specific report → that report's own card.\n"
        "    - To find visuals, slicers, filters, or metrics on a page → that page's card.\n"
        "    Don't stop at the catalog for page- or visual-level questions. When a page\n"
        "    name exists in more than one report, page cards are titled <Page> — <Report>;\n"
        "    name the report in your answer, and ask which one if unsure."
    )
    return rules


def render_agent_instructions(ctx: DocContext, kb_files: list[str]) -> str:
    name = ctx.cfg.solution.display_name or "Power BI"
    concept_eg, metric_eg = _examples(ctx)

    out: list[str] = [
        md.HEADER.rstrip(),
        "",
        "# Agent system prompt",
        "",
        "Paste everything below the line into your agent's system-prompt / instructions",
        "field. This file is **not** part of the knowledge base — do not upload it with",
        "the card files. It is regenerated on every run, so adapt it where you deploy it",
        "rather than editing it here.",
        "",
        "Some routing guidance below reflects how Microsoft 365 Copilot chunks and",
        "retrieves documents. Treat it as a sensible default and adjust for other",
        "platforms.",
        "",
        "---",
        "",
        f"You are the {name} Power BI assistant. You answer questions about a Power BI",
        "solution — its business metrics, semantic model, data pipeline, and reports —",
        "using ONLY the attached knowledge base. Never invent business meaning, numbers,",
        "DAX, SQL, or lineage. If the knowledge base does not contain the answer, say so.",
        "",
        "## How the knowledge base is organised",
        "",
        'The knowledge is a set of Markdown files made of self-contained "cards". Each card',
        'is one "## " section and fully answers one question on its own. A card begins with',
        'a "**Type:**" line telling you what it is:',
        "",
    ]
    out.extend(_card_types(ctx, kb_files, concept_eg))
    out.append("")
    out.append(_file_list(kb_files))
    out.append("")
    out.append("## How to answer")
    out.append("")
    out.extend(_answer_rules(ctx, kb_files, concept_eg, metric_eg))
    out.extend([
        "",
        "## Faithfulness rules",
        "",
        "- Quote DAX and SQL exactly as written; never paraphrase a formula into a new one.",
        '- If a "### Source trace" row says a column is unresolved, or a card shows',
        '  "{{PLACEHOLDER}}" or "(unresolved binding)", report that honestly — do not fill',
        "  the gap with an assumption.",
        "- Workspace and dataset IDs may be shared. Never reveal anything that looks like a",
        "  credential, email address, or Teams thread ID.",
        "- When you state a fact, name the card or file it came from so the user can verify.",
    ])
    return "\n".join(out) + "\n"


__all__ = ["render_agent_instructions"]
