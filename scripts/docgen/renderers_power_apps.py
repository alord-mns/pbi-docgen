"""Renderer for ``05-power-apps.md`` — the canvas Power App companion cards.

The four core knowledge-base files (and the optional ``04-source-code.md``)
describe the semantic model, its pipeline, and its read-only thin reports. This
file documents the **Power Platform canvas Power Apps** that sit alongside the
solution — the data-entry / workflow apps (access requests, approvals, manual
inputs) that *write into* the SharePoint lists and other stores which, in turn,
feed the model. It is emitted only when at least one unpacked canvas app is
present under ``power-apps/`` (presence-driven, like the orchestration cards).

This is deliberately NOT the Power BI *distribution* App (the audience app that
publishes the reports) — that is configured under ``[powerbi_app]`` in
``.docgen.toml``.

One card per app. Read-only over sources and idempotent: all iteration is
sorted, and per-app author metadata is not rendered (PII).
"""
from __future__ import annotations

from . import cards
from . import md
from .cards import DocContext
from .power_apps import CanvasApp


def _api_family(api_id: str) -> str:
    """Friendly connector family from a provider API id, e.g.

    ``/providers/microsoft.powerapps/apis/shared_sharepointonline`` ->
    ``sharepointonline``.
    """
    tail = api_id.rsplit("/", 1)[-1] if api_id else ""
    return tail[len("shared_"):] if tail.startswith("shared_") else tail


def _connectors_table(app: CanvasApp) -> list[str]:
    if not app.connectors:
        return ["_No connector references found in the unpacked source._"]
    rows = ["| Connector | Tier | API family |", "|---|---|---|"]
    for c in app.connectors:
        name = md.md_escape_pipe(c.display_name or "(unnamed)")
        tier = md.md_escape_pipe(c.tier or "—")
        fam = _api_family(c.api_id)
        fam_cell = f"`{md.md_escape_pipe(fam)}`" if fam else "—"
        rows.append(f"| {name} | {tier} | {fam_cell} |")
    return rows


def _data_sources_table(app: CanvasApp) -> list[str]:
    if not app.data_sources:
        return ["_No connected data sources found in the unpacked source._"]
    rows = ["| Data source | Type | Access | Backing |", "|---|---|---|---|"]
    for d in app.data_sources:
        name = md.md_escape_pipe(d.name)
        dtype = md.md_escape_pipe(d.type or "—")
        access = "Read/write" if d.writable else "Read"
        if d.dataset:
            backing = f"`{md.md_escape_pipe(d.dataset)}`"
        else:
            fam = _api_family(d.api_id)
            backing = f"`{md.md_escape_pipe(fam)}`" if fam else "—"
        rows.append(f"| {name} | {dtype} | {access} | {backing} |")
    return rows


def _app_card(app: CanvasApp, downstream: list[tuple[str, str]]) -> cards.Card:
    n_screens = len(app.screens)
    n_conn = len(app.connectors)
    n_ds = len(app.data_sources)
    writable = app.writable_sources()

    parts: list[str] = []
    if app.description:
        parts.append(f"> {md.md_escape_pipe(app.description)}")
    else:
        parts.append(
            f"Canvas Power App with {n_screens} screen(s), reading / writing "
            f"{n_ds} data source(s) via {n_conn} connector(s). "
            "_No app description was authored in the source._"
        )
    parts.append("")

    parts.append("### Overview")
    parts.append("")
    form = " · ".join(p for p in (app.app_type, app.orientation) if p) or "Unknown"
    parts.append(f"- **Form factor:** {md.md_escape_pipe(form)}")
    if app.screens:
        parts.append(
            f"- **Screens ({n_screens}):** "
            + ", ".join(f"`{md.md_escape_pipe(s)}`" for s in app.screens)
        )
    parts.append(f"- **Connectors:** {n_conn}")
    parts.append(f"- **Connected data sources:** {n_ds}")
    if app.source_dir:
        parts.append(f"- **Unpacked source:** `{md.md_escape_pipe(app.source_dir)}/`")
    parts.append("")

    parts.append("### Connectors")
    parts.append("")
    parts.extend(_connectors_table(app))
    parts.append("")

    parts.append("### Data sources")
    parts.append("")
    parts.extend(_data_sources_table(app))
    if writable:
        parts.append("")
        parts.append(
            "> **Write-back targets:** the app writes into "
            + ", ".join(f"`{md.md_escape_pipe(d.name)}`" for d in writable)
            + ". Changes made here flow upstream into any dataflow / model table "
            "sourced from the same store."
        )

    if downstream:
        parts.append("")
        parts.append("### Downstream (pipeline)")
        parts.append("")
        parts.append(
            "_Dataflows whose Power Query M reads a store this app writes into "
            "\u2014 matched by data-source name appearing verbatim in the M._"
        )
        parts.append("")
        parts.append("| Write-back target | Feeds dataflow |")
        parts.append("|---|---|")
        for df_name, src_name in downstream:
            link = f"[{df_name}](#{cards.card_anchor('dataflow', df_name)})"
            parts.append(f"| `{md.md_escape_pipe(src_name)}` | {link} |")

    keywords = ["power app", "canvas app", "data entry app", app.name]
    keywords.extend(c.display_name for c in app.connectors if c.display_name)
    keywords.extend(d.name for d in writable)

    subtitle_bits = [b for b in (app.app_type, app.orientation) if b]
    subtitle = "canvas Power App"
    if subtitle_bits:
        subtitle += " (" + ", ".join(subtitle_bits) + ")"

    return cards.Card(
        anchor=cards.card_anchor("power-app", app.name),
        title=app.name,
        kind="Power App (canvas)",
        subtitle=subtitle,
        keywords=tuple(dict.fromkeys(k for k in keywords if k)),
        body="\n".join(parts).rstrip(),
    )


def render_power_apps(ctx: DocContext) -> str:
    """Assemble one card per canvas Power App into ``05-power-apps.md``."""
    apps: list[CanvasApp] = list(ctx.power_apps)

    # Reverse the {dataflow: [(app, source)]} write-back map into per-app
    # {app: [(dataflow, source)]} downstream links (sorted for determinism).
    per_app: dict[str, list[tuple[str, str]]] = {}
    for df_name, edges in ctx.app_writeback.items():
        for app_name, src_name in edges:
            per_app.setdefault(app_name, []).append((df_name, src_name))
    for name in per_app:
        per_app[name] = sorted(dict.fromkeys(per_app[name]))

    cardlist = [_app_card(app, per_app.get(app.name, [])) for app in apps]

    intro = (
        f"**Canvas Power Apps:** {len(cardlist)}. Each card lists an app's "
        "screens, connectors, and connected data sources, flagging the "
        "read/write (write-back) targets. These are Power Platform apps that "
        "*feed* the solution — not the Power BI distribution App that publishes "
        "the reports."
    )

    return cards.render_bundle(
        file_title="Power Apps",
        purpose=(
            "The Power Platform canvas Power Apps alongside this solution — the "
            "data-entry / workflow apps that write into the stores which feed the "
            "model. Distinct from the Power BI distribution App."
        ),
        audiences=("Business users", "App makers", "Analytics engineers"),
        intro=intro,
        cards=cardlist,
    )


__all__ = ["render_power_apps"]
