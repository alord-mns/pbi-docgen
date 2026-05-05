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


def _format_trigger_summary(flow: orcmod.Flow) -> str:
    if not flow.trigger:
        return "The flow is started manually or by an external trigger not described in the workflow metadata."
    parts: list[str] = [f"This flow starts automatically via a `{flow.trigger.type}` trigger"]
    if flow.trigger.frequency:
        parts.append(f"running every `{flow.trigger.interval}` `{flow.trigger.frequency}`")
    if flow.trigger.week_days:
        parts.append(f"on {', '.join(flow.trigger.week_days)}")
    if flow.trigger.hours:
        parts.append(f"at {', '.join(flow.trigger.hours)}")
    if flow.trigger.time_zone:
        parts.append(f"(`{flow.trigger.time_zone}`)")
    return " ".join(parts) + "."


def _classify_actions(flow: orcmod.Flow) -> dict[str, list[orcmod.Action]]:
    categories: dict[str, list[orcmod.Action]] = defaultdict(list)
    for action in flow.actions:
        lowered = action.name.lower()
        if action.type == "InitializeVariable":
            categories["setup"].append(action)
        elif action.type in ("ParseJson", "Foreach") or "parse" in lowered or "filter" in lowered:
            categories["prep"].append(action)
        elif action.type == "OpenApiConnection" and action.operation_id == "RefreshDataflow":
            categories["dataflow_refresh"].append(action)
        elif action.type == "OpenApiConnection" and action.operation_id == "RefreshDataset":
            categories["dataset_refresh"].append(action)
        elif action.type in ("If", "Scope", "Foreach", "Switch"):
            categories["decision"].append(action)
        elif action.type == "OpenApiConnection" and action.operation_id in ("PostCardToConversation", "PostItem", "PatchItem", "SendEmailV2"):
            categories["notify"].append(action)
    return categories


def _refresh_target_label(target: orcmod.RefreshTarget, lin: Lineage) -> str:
    if target.kind == "dataflow":
        name, _ = _resolve_dataflow_name(None, target, lin)
    else:
        name, _ = _resolve_dataset_name(target, lin)
    if name:
        return name
    return target.object_id[:8] + ("…" if len(target.object_id) > 8 else "")


def _unique_preserving_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _render_plain_english_walkthrough(flow: orcmod.Flow, lin: Lineage) -> list[str]:
    categories = _classify_actions(flow)
    dataflow_targets = [t for t in flow.refresh_targets if t.kind == "dataflow"]
    dataset_targets = [t for t in flow.refresh_targets if t.kind == "dataset"]
    teams_count = sum(1 for n in flow.notifications if n.channel == "teams")
    sharepoint_count = sum(1 for n in flow.notifications if n.channel == "sharepoint-list")
    email_count = sum(1 for n in flow.notifications if n.channel == "email")
    failure_actions = [a for a in flow.actions if "fail" in a.name.lower() or a.branch == "else"]

    out: list[str] = []
    out.append("\n## Plain-English Walkthrough\n")
    out.append("\n### Summary\n")
    out.append(_format_trigger_summary(flow))
    if dataflow_targets or dataset_targets:
        summary_bits: list[str] = []
        if dataflow_targets:
            summary_bits.append(
                f"refreshes {len(dataflow_targets)} dataflow target(s)"
            )
        if dataset_targets:
            summary_bits.append(
                f"refreshes {len(dataset_targets)} dataset target(s)"
            )
        out.append(
            "In the normal path, it " + " and ".join(summary_bits)
            + ", then posts status updates to the configured notification channels."
        )
    else:
        out.append("This workflow does not contain any detectable dataflow or dataset refresh actions.")

    out.append("\n### Step-by-Step Walkthrough\n")
    out.append("| Step | What happens in normal language | Technical mapping |")
    out.append("| --- | --- | --- |")

    step_num = 1
    out.append(
        f"| {step_num} | {_format_trigger_summary(flow)} | "
        + (f"`{flow.trigger.name}`" if flow.trigger else "workflow trigger metadata")
        + " |"
    )
    step_num += 1

    if categories["setup"] or categories["prep"]:
        setup_actions = categories["setup"] + categories["prep"]
        setup_names = ", ".join(f"`{a.name}`" for a in setup_actions[:6])
        out.append(
            f"| {step_num} | The flow prepares its working state by setting variables and reading any existing control or log records it needs before starting refreshes. | {setup_names or '-'} |"
        )
        step_num += 1

    if dataflow_targets:
        unique_labels = _unique_preserving_order([
            _refresh_target_label(t, lin) for t in dataflow_targets
        ])
        labels = ", ".join(unique_labels[:5])
        if len(unique_labels) > 5:
            labels += f" and {len(unique_labels) - 5} more"
        mappings = ", ".join(f"`{t.action_name}`" for t in dataflow_targets[:6])
        out.append(
            f"| {step_num} | The workflow starts the upstream dataflow refreshes needed for this reporting cycle: {md.md_escape_pipe(labels)}. | {mappings} |"
        )
        step_num += 1

    decision_actions = [a for a in categories["decision"] if a.type == "If"]
    if decision_actions:
        decision_names = ", ".join(f"`{a.name}`" for a in decision_actions[:6])
        out.append(
            f"| {step_num} | After the upstream refreshes start, the workflow checks whether each required branch completed successfully and decides whether to continue or record a failure. | {decision_names} |"
        )
        step_num += 1

    if dataset_targets:
        unique_labels = _unique_preserving_order([
            _refresh_target_label(t, lin) for t in dataset_targets
        ])
        labels = ", ".join(unique_labels[:5])
        if len(unique_labels) > 5:
            labels += f" and {len(unique_labels) - 5} more"
        mappings = ", ".join(f"`{t.action_name}`" for t in dataset_targets[:6])
        out.append(
            f"| {step_num} | Once the prerequisite upstream steps have passed, the workflow starts the downstream dataset refreshes: {md.md_escape_pipe(labels)}. | {mappings} |"
        )
        step_num += 1

    if teams_count or sharepoint_count or email_count:
        notification_parts: list[str] = []
        if sharepoint_count:
            notification_parts.append(f"logs to SharePoint ({sharepoint_count} action(s))")
        if teams_count:
            notification_parts.append(f"posts Teams updates ({teams_count} action(s))")
        if email_count:
            notification_parts.append(f"sends email ({email_count} action(s))")
        mapping_actions = ", ".join(f"`{a.name}`" for a in categories["notify"][:6])
        out.append(
            f"| {step_num} | Throughout the run, the workflow records progress and outcomes: it "
            + ", ".join(notification_parts)
            + f". | {mapping_actions or '-'} |"
        )
        step_num += 1

    out.append("\n### Happy Path\n")
    happy_bits: list[str] = ["the trigger starts on schedule"]
    if dataflow_targets:
        happy_bits.append("the required upstream dataflows refresh successfully")
    if dataset_targets:
        happy_bits.append("the downstream dataset refreshes are started")
    if teams_count or sharepoint_count or email_count:
        happy_bits.append("status notifications are sent to the configured channels")
    out.append("In the expected run, " + ", then ".join(happy_bits) + ".")

    out.append("\n### Failure Handling in Plain English\n")
    if failure_actions:
        failure_names = ", ".join(f"`{a.name}`" for a in failure_actions[:8])
        out.append(
            "If one of the monitored refresh steps fails, the workflow records that outcome and sends failure notifications instead of continuing down the normal success path."
        )
        out.append(f"The main failure-path actions detected in the definition are: {failure_names}.")
    else:
        out.append("No explicit failure branch could be detected from the parsed action graph.")

    return out


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

        body.extend(_render_plain_english_walkthrough(f, lin))
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
