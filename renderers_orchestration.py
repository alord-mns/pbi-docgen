"""Render `docs/orchestration/<FlowName>.md` per documentation_req.md §2.14.

The renderer is model-agnostic: friendly-name resolution against
dataflow / dataset metadata is provided by the lineage object.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import md
from .config import Config
from .lineage import Lineage
from . import orchestration as orcmod


# A small helper for redacting items that §5 of documentation_req.md flags.
def _redact_recipient(raw: str) -> str:
    if not raw:
        return md.PLACEHOLDER
    # Teams thread IDs look like '19:xxxx@thread.v2'
    if raw.startswith("19:"):
        return md.PLACEHOLDER + " _(Teams thread ID redacted)_"
    if "@" in raw:  # email
        return md.PLACEHOLDER + " _(recipient email redacted)_"
    return md.md_escape_pipe(raw)


def _redact_site(raw: str) -> str:
    if not raw:
        return ""
    # SharePoint URLs are kept (they are not credentials) but path-only.
    return f"`{raw}`"


def _resolve_dataflow_name(
    flow: orcmod.Flow, target: orcmod.RefreshTarget, lin: Lineage
) -> tuple[str, str]:
    """Return (friendly_name, link) or ('', '') if unresolved."""
    if target.kind != "dataflow":
        return "", ""
    name = lin.short_id_to_name.get(target.object_id)
    if not name:
        # Try matching by ID prefix against any df ref
        prefix = target.object_id[:8]
        for ref in lin.dataflow_refs.values():
            if ref.dataflow_id.startswith(prefix):
                name = lin.short_id_to_name.get(ref.dataflow_id, "")
                if name:
                    break
    if name:
        return name, "../dataflows/" + md.safe_filename(name) + ".md"
    return "", ""


def _resolve_dataset_name(
    target: orcmod.RefreshTarget, lin: Lineage
) -> tuple[str, str]:
    if target.kind != "dataset":
        return "", ""
    # The semantic model's name matches the published dataset name.
    name = lin.model.name
    return name, "../model/" + md.safe_filename(name) + ".md"


def _action_id(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_]", "_", name)[:60] or "n"


def _render_sequence_diagram(flow: orcmod.Flow) -> list[str]:
    """A Mermaid `flowchart TD` derived from `runAfter`."""
    out: list[str] = ["```mermaid", "flowchart TD"]
    if flow.trigger:
        out.append(f"  TRIGGER([\"Trigger: {flow.trigger.type} — {flow.trigger.frequency} every {flow.trigger.interval}\"])")
    # Group by parent scope
    grouped: dict[str, list[orcmod.Action]] = defaultdict(list)
    for a in flow.actions:
        grouped[a.parent].append(a)
    # Top-level subgraph
    for parent, actions in grouped.items():
        if parent == "":
            for a in actions:
                shape = "{{" + a.type + "}}" if a.type in ("If", "Switch") else "[" + a.type + "]"
                label = f"{a.name}<br/>{shape}"
                out.append(f"  {_action_id(a.name)}[\"{md.md_escape_pipe(a.name)}<br/>({a.type})\"]")
        else:
            out.append(f"  subgraph {_action_id(parent)}_box [\"{md.md_escape_pipe(parent)}\"]")
            for a in actions:
                out.append(f"    {_action_id(a.name)}[\"{md.md_escape_pipe(a.name)}<br/>({a.type})\"]")
            out.append("  end")
    # Edges from runAfter
    for a in flow.actions:
        if not a.run_after and a.parent == "" and flow.trigger:
            out.append(f"  TRIGGER --> {_action_id(a.name)}")
        for pred in a.run_after:
            out.append(f"  {_action_id(pred)} --> {_action_id(a.name)}")
    out.append("```")
    return out


def render_orchestration(lin: Lineage, cfg: Config) -> dict[str, str]:
    """Return ``{filename: markdown}`` — one per flow plus a README index."""
    files: dict[str, str] = {}
    flows: list[orcmod.Flow] = list(lin.orchestration_flows or [])

    # ---- Index ----
    idx = [md.HEADER, "# Orchestration Flows\n"]
    idx.append(md.section_purpose(
        "Per-flow documentation for the workflows that orchestrate refreshes, notifications, and downstream automation.",
        "Operations/Support", "Data Engineers", "Power BI Developers",
    ))
    idx.append("\n| Flow | Trigger | Refreshes | Notifications | Workspaces touched |")
    idx.append("| --- | --- | --- | --- | --- |")
    for f in flows:
        fn = md.safe_filename(f.name) + ".md"
        trig = "—"
        if f.trigger:
            sched_bits = [f.trigger.frequency or "?"]
            if f.trigger.week_days:
                sched_bits.append("/".join(f.trigger.week_days))
            if f.trigger.hours:
                sched_bits.append(":".join(f.trigger.hours) + "h")
            trig = " ".join(sched_bits)
        idx.append(
            f"| {md.link(f.name, fn)} | {md.md_escape_pipe(trig)} | "
            f"{len(f.refresh_targets)} | {len(f.notifications)} | "
            f"{md.md_escape_pipe(', '.join(sorted(f.workspace_ids)) or '-')} |"
        )
    idx.append(f"\n_Total: {len(flows)} orchestration flow(s) under [`orchestration/`](../../orchestration/)._\n")
    files["README.md"] = "\n".join(idx)

    # ---- One file per flow ----
    primary_ws = (cfg.workspaces.primary or lin.primary_workspace_id or "").lower()
    for f in flows:
        body: list[str] = [md.HEADER, f"# Orchestration — {f.name}\n"]
        body.append(md.section_purpose(
            f"Refresh / alerting workflow `{f.name}` — trigger, action sequence, refreshed artefacts, notifications, and dependencies.",
            "Operations/Support", "Data Engineers", "Power BI Developers",
        ))

        body.append("\n## Flow Overview\n")
        body.append(f"- **Display name:** `{f.name}`")
        body.append(f"- **Source:** `{Path(f.source_file).relative_to(md.REPO_ROOT) if Path(f.source_file).is_absolute() else f.source_file}`")
        body.append(f"- **Platform:** Power Automate cloud flow (Logic App workflow schema)")
        body.append(f"- **Owner / team:** {md.PLACEHOLDER}")
        body.append(f"- **Purpose:** {md.PLACEHOLDER}  _(populate with the business intent — what this flow exists to achieve)_")
        body.append("")

        body.append("\n## Trigger\n")
        if f.trigger:
            body.append(f"- **Type:** `{f.trigger.type}`")
            if f.trigger.frequency:
                body.append(f"- **Frequency:** `{f.trigger.frequency}` (interval `{f.trigger.interval}`)")
            if f.trigger.week_days:
                body.append(f"- **Week days:** {', '.join(f.trigger.week_days)}")
            if f.trigger.hours:
                body.append(f"- **Hours:** {', '.join(f.trigger.hours)}")
            if f.trigger.time_zone:
                body.append(f"- **Time zone:** `{f.trigger.time_zone}`")
        else:
            body.append("- _No trigger declared in the workflow definition._")
        body.append("")

        body.append("\n## Refresh Sequence Diagram\n")
        body.extend(_render_sequence_diagram(f))
        body.append("")

        body.append("\n## Refreshed Artefacts\n")
        if f.refresh_targets:
            body.append("| Action | Type | Workspace ID | Object ID | Resolved name |")
            body.append("| --- | --- | --- | --- | --- |")
            for t in f.refresh_targets:
                if t.kind == "dataflow":
                    name, link_target = _resolve_dataflow_name(f, t, lin)
                else:
                    name, link_target = _resolve_dataset_name(t, lin)
                if name and link_target:
                    resolved = md.link(name, link_target)
                else:
                    resolved = md.UNKNOWN.replace(
                        "needs business input",
                        "not present in repo metadata",
                    )
                body.append(
                    f"| `{md.md_escape_pipe(t.action_name)}` | {t.kind} | "
                    f"`{t.workspace_id}` | `{t.object_id}` | {resolved} |"
                )
        else:
            body.append("_No dataflow / dataset refresh actions detected._")
        body.append("")

        body.append("\n## Cross-Workspace References\n")
        if f.workspace_ids:
            body.append("| Workspace ID | Role | Notes |")
            body.append("| --- | --- | --- |")
            for ws in sorted(f.workspace_ids):
                role = "primary (dataflows)" if ws.lower() == primary_ws else (
                    "dataset" if ws.lower() == (cfg.workspaces.dataset or "").lower()
                    else "secondary"
                )
                flag = ""
                if ws.lower() != primary_ws and role != "dataset":
                    flag = " ⚠️ does not match primary workspace"
                if ws.lower() == (cfg.workspaces.dataset or "").lower() and ws.lower() != primary_ws:
                    flag = " (dataset workspace differs from dataflow workspace)"
                body.append(f"| `{ws}` | {role} | {md.md_escape_pipe(flag) or '-'} |")
        else:
            body.append("_No workspace references detected._")
        body.append("")

        body.append("\n## Conditional Branches & Scopes\n")
        cond_actions = [a for a in f.actions if a.type in ("If", "Switch", "Scope", "Foreach")]
        if cond_actions:
            body.append("| Action | Type | Branches | Expression / scope name |")
            body.append("| --- | --- | --- | --- |")
            for a in cond_actions:
                children = [c.name for c in f.actions if c.parent == a.name]
                expr = a.expression[:120] if a.expression else "-"
                body.append(
                    f"| `{md.md_escape_pipe(a.name)}` | {a.type} | "
                    f"{md.md_escape_pipe(', '.join(children) or '_(none)_')} | "
                    f"`{md.md_escape_pipe(expr)}` |"
                )
        else:
            body.append("_No If / Switch / Scope / Foreach actions detected._")
        body.append("")

        body.append("\n## Notifications & Logging\n")
        if f.notifications:
            body.append("| Action | Channel | Mechanism | Recipient / target |")
            body.append("| --- | --- | --- | --- |")
            for n in f.notifications:
                if n.channel == "sharepoint-list":
                    target = _redact_site(n.site) or md.PLACEHOLDER
                else:
                    target = _redact_recipient(n.recipient)
                body.append(
                    f"| `{md.md_escape_pipe(n.action_name)}` | {n.channel} | "
                    f"`{n.mechanism}` | {target} |"
                )
        else:
            body.append("_No notification actions detected._")
        body.append(f"\n_Recipient PII (email addresses, Teams chat thread IDs) is redacted to `{md.PLACEHOLDER}` per documentation_req.md §5._")
        body.append("")

        body.append("\n## Connections Used\n")
        if f.connections:
            body.append("| Connector API | Reference | Purpose |")
            body.append("| --- | --- | --- |")
            seen_apis: set[str] = set()
            for c in f.connections:
                if c.api_name in seen_apis:
                    continue
                seen_apis.add(c.api_name)
                purpose = {
                    "dataflows": "Refresh Power BI dataflows.",
                    "powerbi": "Refresh Power BI datasets.",
                    "sharepointonline": "Read / write SharePoint list items (refresh log).",
                    "teams": "Post Adaptive Cards to a Teams chat / channel.",
                    "office365": "Send email notifications.",
                }.get(c.api_name, md.PLACEHOLDER)
                body.append(
                    f"| `{c.api_name}` | `{c.ref}` | {purpose} |"
                )
            body.append(f"\n_Connector connection GUIDs from `apisMap.json` / `connectionsMap.json` are intentionally **not** published in this document. Replace with `{{{{CONNECTION_PLACEHOLDER}}}}` if quoting them inside the docs._")
        else:
            body.append("_No connections declared._")
        body.append("")

        body.append("\n## Variables & Parameters\n")
        if f.variables:
            body.append("| Name | Type | Default |")
            body.append("| --- | --- | --- |")
            for v in f.variables:
                body.append(
                    f"| `{md.md_escape_pipe(v.name)}` | `{v.type}` | "
                    f"`{md.md_escape_pipe(v.default)[:60] or '-'}` |"
                )
        else:
            body.append("_No initialised variables detected._")
        body.append("")

        body.append("\n## Failure Handling\n")
        # Heuristic: any action whose name contains 'Fail' or whose parent's
        # `else` branch posts a card / SharePoint Failed item.
        failure_actions = [
            a.name for a in f.actions
            if "fail" in a.name.lower() or a.branch == "else"
        ]
        if failure_actions:
            body.append("Detected failure-path actions (typically run via `else` branches or `runAfter` succeeded/failed conditions):")
            for n in failure_actions[:20]:
                body.append(f"- `{md.md_escape_pipe(n)}`")
        else:
            body.append("_No explicit failure-path actions detected._")
        body.append(f"\n_Document the operational response to a failure (retry expectations, escalation): {md.PLACEHOLDER}_")
        body.append("")

        body.append("\n## Run-As Identity & Permissions\n")
        body.append(f"- **Owner / service principal:** {md.PLACEHOLDER}")
        body.append(f"- **Required permissions:** Power BI dataflow / dataset refresh, SharePoint list write, Teams chat post — see [Connections Used](#connections-used).")
        body.append("")

        body.append("\n## Dependencies\n")
        body.append("**Upstream (must succeed before this flow runs):**")
        body.append(f"- {md.PLACEHOLDER} _(e.g. upstream Databricks job)_")
        body.append("\n**Downstream consumers:**")
        body.append("- Refreshed dataflows / datasets above feed the documented semantic model and reports.")
        body.append("- Notifications post to SharePoint refresh logs and Teams chats.")
        body.append("")

        body.append("\n## Known Issues & Maintenance Notes\n")
        body.append(f"- {md.PLACEHOLDER}  _(record any known quirks — e.g. flaky steps, manual reruns, time-window constraints)_")
        body.append("")

        files[md.safe_filename(f.name) + ".md"] = "\n".join(body)

    return files


__all__ = ["render_orchestration"]
