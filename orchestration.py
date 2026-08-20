"""Power Automate / Logic App workflow JSON parser.

Each workflow lives under ``orchestration/<flowFolder>/`` with a
``definition.json`` (Logic App workflow schema), an ``apisMap.json``
mapping connector references to API IDs, and a ``connectionsMap.json``
mapping connector references to connection GUIDs.

The parser extracts:

* trigger metadata (recurrence schedule)
* every action and its ``runAfter`` predecessors (the action DAG)
* every dataflow refresh action (workspace ID + dataflow ID)
* every dataset refresh action (workspace ID + dataset ID)
* every notification channel (Teams, SharePoint list write, email)
* every connector reference

The model is deliberately solution-agnostic: it does not know about any
specific solution or dataflow names. Friendly-name resolution against the
TMDL / dataflow exports is the responsibility of the lineage builder.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Trigger:
    name: str = ""
    type: str = ""  # Recurrence, Manual, OnEvent, …
    frequency: str = ""
    interval: int = 0
    time_zone: str = ""
    week_days: list[str] = field(default_factory=list)
    hours: list[str] = field(default_factory=list)


@dataclass
class Action:
    name: str  # internal action key
    type: str  # action type (OpenApiConnection, If, Scope, Foreach, InitializeVariable, …)
    api_id: str = ""  # for OpenApiConnection actions
    operation_id: str = ""
    connection_name: str = ""
    parameters: dict = field(default_factory=dict)
    expression: str = ""  # for If/Switch
    run_after: list[str] = field(default_factory=list)  # predecessor names
    parent: str = ""  # parent scope/foreach/if name (empty = top level)
    branch: str = "actions"  # 'actions' or 'else' (for If) or 'foreach'
    children: list[str] = field(default_factory=list)


@dataclass
class RefreshTarget:
    """A dataflow- or dataset-refresh action."""
    action_name: str
    kind: str  # 'dataflow' or 'dataset'
    workspace_id: str
    object_id: str  # dataflow or dataset GUID


@dataclass
class Notification:
    action_name: str
    channel: str  # 'teams', 'sharepoint-list', 'email'
    mechanism: str  # operationId
    trigger_condition: str = ""  # parent if-expression if any
    recipient: str = ""  # raw recipient (engine redacts in renderer)
    raw_recipient: str = ""  # kept verbatim for redaction-aware logging
    site: str = ""  # for sharepoint-list


@dataclass
class Variable:
    name: str
    type: str
    default: str = ""


@dataclass
class ConnectionRef:
    ref: str  # key in connectionReferences
    api_id: str  # /providers/Microsoft.PowerApps/apis/shared_xxx
    api_name: str  # short name e.g. 'sharepointonline', 'dataflows'
    connection_name: str  # opaque name used by the runtime
    connection_guid: str = ""  # from connectionsMap.json (sensitive)
    api_guid: str = ""  # from apisMap.json


@dataclass
class Flow:
    name: str  # display name from definition.json
    folder: str  # folder name on disk
    source_file: str  # relative path
    flow_id: str = ""  # GUID from `id` field
    creator_id: str = ""
    description: str = ""
    trigger: Trigger | None = None
    actions: list[Action] = field(default_factory=list)
    refresh_targets: list[RefreshTarget] = field(default_factory=list)
    notifications: list[Notification] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    connections: list[ConnectionRef] = field(default_factory=list)
    workspace_ids: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_CONNECTOR_API_RE = re.compile(r"/providers/Microsoft\.PowerApps/apis/(shared_[A-Za-z0-9_]+)")


def _walk_actions(
    actions_obj: dict,
    parent: str,
    branch: str,
    flow: Flow,
) -> None:
    """Recursively walk an `actions` object, populating `flow.actions`."""
    if not isinstance(actions_obj, dict):
        return
    for name, body in actions_obj.items():
        if not isinstance(body, dict):
            continue
        atype = body.get("type", "")
        run_after = list((body.get("runAfter") or {}).keys())

        host = (body.get("inputs") or {}).get("host") or {}
        api_id = ""
        if isinstance(host, dict):
            api_id_full = host.get("apiId", "") or ""
            m = _CONNECTOR_API_RE.search(api_id_full)
            api_id = m.group(1) if m else api_id_full
            operation_id = host.get("operationId", "") or ""
            connection_name = host.get("connectionName", "") or ""
        else:
            operation_id = ""
            connection_name = ""

        params = (body.get("inputs") or {}).get("parameters") or {}
        if not isinstance(params, dict):
            params = {}

        expression = ""
        if atype in ("If",):
            expr_obj = body.get("expression") or {}
            expression = json.dumps(expr_obj, separators=(",", ":"))[:400]

        action = Action(
            name=name,
            type=atype,
            api_id=api_id,
            operation_id=operation_id,
            connection_name=connection_name,
            parameters=params,
            expression=expression,
            run_after=run_after,
            parent=parent,
            branch=branch,
        )
        flow.actions.append(action)

        # ----- Dataflow refresh -----
        if api_id == "shared_dataflows" and operation_id == "RefreshDataflow":
            ws = params.get("groupIdForRefreshDataflow", "")
            df = params.get("dataflowIdForRefreshDataflow", "")
            # IDs may include a `-wshost-PowerBi` suffix; strip for matching.
            df_clean = df.split("-wshost", 1)[0] if df else ""
            flow.refresh_targets.append(
                RefreshTarget(
                    action_name=name,
                    kind="dataflow",
                    workspace_id=ws,
                    object_id=df_clean,
                )
            )
            if ws:
                flow.workspace_ids.add(ws)

        # ----- Dataset refresh -----
        if api_id == "shared_powerbi" and operation_id == "RefreshDataset":
            ws = params.get("groupid", "")
            ds = params.get("datasetid", "")
            flow.refresh_targets.append(
                RefreshTarget(
                    action_name=name,
                    kind="dataset",
                    workspace_id=ws,
                    object_id=ds,
                )
            )
            if ws:
                flow.workspace_ids.add(ws)

        # ----- Notifications -----
        if api_id == "shared_teams" and operation_id == "PostCardToConversation":
            recipient = params.get("body/recipient", "")
            flow.notifications.append(
                Notification(
                    action_name=name,
                    channel="teams",
                    mechanism="PostCardToConversation",
                    recipient=recipient,
                    raw_recipient=recipient,
                )
            )
        elif api_id == "shared_sharepointonline" and operation_id in (
            "PostItem",
            "PatchItem",
        ):
            site = params.get("dataset", "")
            flow.notifications.append(
                Notification(
                    action_name=name,
                    channel="sharepoint-list",
                    mechanism=operation_id,
                    site=site,
                )
            )
        elif api_id == "shared_office365" and operation_id == "SendEmailV2":
            to = params.get("emailMessage/To", "")
            flow.notifications.append(
                Notification(
                    action_name=name,
                    channel="email",
                    mechanism="SendEmailV2",
                    recipient=to,
                    raw_recipient=to,
                )
            )

        # ----- Variables -----
        if atype == "InitializeVariable":
            for var in (body.get("inputs") or {}).get("variables", []) or []:
                flow.variables.append(
                    Variable(
                        name=var.get("name", ""),
                        type=var.get("type", ""),
                        default=str(var.get("value", "")),
                    )
                )

        # ----- Recurse into nested actions -----
        if "actions" in body:
            _walk_actions(body["actions"], name, "actions", flow)
        if "else" in body and isinstance(body["else"], dict):
            _walk_actions(body["else"].get("actions") or {}, name, "else", flow)


def parse_flow(definition_path: Path) -> Flow:
    """Parse a single Logic-App workflow JSON and its sibling maps."""
    data = json.loads(definition_path.read_text(encoding="utf-8"))

    folder = definition_path.parent.name
    props = data.get("properties", {}) or {}
    display_name = props.get("displayName") or data.get("name") or folder
    definition = props.get("definition", {}) or {}
    metadata = definition.get("metadata", {}) or {}

    flow = Flow(
        name=display_name,
        folder=folder,
        source_file=str(definition_path),
        flow_id=str(data.get("name") or "")[:60],
        creator_id=str((metadata.get("creator") or {}).get("id", "")),
    )

    # ---- Trigger ----
    triggers = definition.get("triggers", {}) or {}
    if triggers:
        first_name, first_body = next(iter(triggers.items()))
        if isinstance(first_body, dict):
            rec = first_body.get("recurrence", {}) or {}
            sched = rec.get("schedule", {}) or {}
            flow.trigger = Trigger(
                name=first_name,
                type=first_body.get("type", ""),
                frequency=rec.get("frequency", ""),
                interval=rec.get("interval", 0) or 0,
                time_zone=rec.get("timeZone", ""),
                week_days=list(sched.get("weekDays", []) or []),
                hours=list(sched.get("hours", []) or []),
            )

    # ---- Actions ----
    _walk_actions(definition.get("actions", {}) or {}, "", "actions", flow)
    # Backfill children
    by_name = {a.name: a for a in flow.actions}
    for a in flow.actions:
        if a.parent and a.parent in by_name:
            by_name[a.parent].children.append(a.name)

    # ---- Connection references ----
    api_map_path = definition_path.parent / "apisMap.json"
    conn_map_path = definition_path.parent / "connectionsMap.json"
    api_map = (
        json.loads(api_map_path.read_text(encoding="utf-8"))
        if api_map_path.exists()
        else {}
    )
    conn_map = (
        json.loads(conn_map_path.read_text(encoding="utf-8"))
        if conn_map_path.exists()
        else {}
    )
    refs = props.get("connectionReferences", {}) or {}
    for ref_key, ref_body in refs.items():
        if not isinstance(ref_body, dict):
            continue
        api_id_full = ref_body.get("id", "") or ""
        m = _CONNECTOR_API_RE.search(api_id_full)
        api_short = m.group(1) if m else api_id_full
        flow.connections.append(
            ConnectionRef(
                ref=ref_key,
                api_id=api_id_full,
                api_name=ref_body.get("apiName", "") or api_short.replace("shared_", ""),
                connection_name=ref_body.get("connectionName", "") or "",
                connection_guid=conn_map.get(ref_key, ""),
                api_guid=api_map.get(ref_key, ""),
            )
        )

    return flow


def load_flows(definition_paths: list[Path]) -> list[Flow]:
    """Parse every flow definition path; return them in input order."""
    return [parse_flow(p) for p in definition_paths]


__all__ = [
    "Flow",
    "Trigger",
    "Action",
    "RefreshTarget",
    "Notification",
    "Variable",
    "ConnectionRef",
    "parse_flow",
    "load_flows",
]
