"""PROV-JSON projection. Observation timestamps are not claimed as execution times."""

from __future__ import annotations

import json

from .store import Store, digest

PREFIXES = {
    "prov": "http://www.w3.org/ns/prov#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "oap": "urn:open-agent-provenance:v1:",
    "id": "urn:open-agent-provenance:id:",
}


def qtype(name: str) -> dict:
    return {"$": name, "type": "prov:QUALIFIED_NAME"}


def ref(*parts: str) -> str:
    return "id:" + digest(parts)


def build_graph(store: Store, session: str) -> dict:
    check = store.verify(session)
    if not check["valid"]:
        raise ValueError("Journal integrity check failed")
    events = store.events(session)
    graph: dict = {"prefix": PREFIXES.copy()}

    def add(kind: str, key: str, attrs: dict) -> str:
        graph.setdefault(kind, {}).setdefault(key, {}).update(attrs)
        return key

    def edge(kind: str, **attrs: str) -> None:
        add(kind, ref(kind, json.dumps(attrs, sort_keys=True)), attrs)

    host = events[0]["host"]
    agent = add(
        "agent",
        ref(session, "agent"),
        {
            "prov:type": qtype("prov:SoftwareAgent"),
            "prov:label": host,
        },
    )
    session_activity = add(
        "activity",
        ref(session, "session"),
        {
            "prov:type": qtype("oap:Session"),
            "oap:session": session,
            "oap:firstObservedAt": events[0]["observed_at"],
            "oap:lastObservedAt": events[-1]["observed_at"],
        },
    )
    edge("wasAssociatedWith", **{"prov:activity": session_activity, "prov:agent": agent})

    for event in events:
        kind, event_id = event["kind"], event["event_id"]
        event_agent = agent
        if event.get("agent_id"):
            event_agent = add(
                "agent",
                ref(session, "agent", event["agent_id"]),
                {
                    "prov:type": qtype("prov:SoftwareAgent"),
                    "prov:label": host + " subagent",
                },
            )
            edge("actedOnBehalfOf", **{"prov:delegate": event_agent, "prov:responsible": agent})
        if kind == "Source":
            add("entity", event["entity_id"], event["attributes"])
            continue
        if kind == "Activity":
            activity = add(
                "activity",
                event["activity_id"],
                {
                    "prov:label": event["name"],
                    "oap:evidence": "agent-asserted",
                    "oap:session": session,
                    "oap:observedAt": event["observed_at"],
                },
            )
            edge("wasAssociatedWith", **{"prov:activity": activity, "prov:agent": event_agent})
            for entity in event["inputs"]:
                edge("used", **{"prov:activity": activity, "prov:entity": entity})
            for output in event["outputs"]:
                entity = add("entity", output["entity_id"], output["attributes"])
                edge("wasGeneratedBy", **{"prov:entity": entity, "prov:activity": activity})
                edge("wasAttributedTo", **{"prov:entity": entity, "prov:agent": event_agent})
                for source in event["inputs"]:
                    edge(
                        "wasDerivedFrom",
                        **{
                            "prov:generatedEntity": entity,
                            "prov:usedEntity": source,
                            "prov:activity": activity,
                        },
                    )
            continue

        is_tool = kind in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
        activity = ref(session, "tool", event["call_id"]) if is_tool else ref(session, event_id)
        attrs = {
            "prov:type": qtype("oap:ToolInvocation" if is_tool else "oap:LifecycleEvent"),
            "prov:label": event.get("tool", kind),
            "oap:session": session,
            "oap:evidence": event["evidence"],
        }
        if is_tool:
            if kind == "PreToolUse":
                attrs["oap:preObservedAt"] = event["observed_at"]
            else:
                attrs["oap:postObservedAt"] = event["observed_at"]
                attrs["oap:status"] = event["status"]
            if "exit_code" in event:
                attrs["oap:exitCode"] = event["exit_code"]
        else:
            attrs["oap:observedAt"] = event["observed_at"]
        for field in ("model", "turn_id", "prompt_id", "project_digest"):
            if field in event:
                attrs["oap:" + field] = event[field]
        add("activity", activity, attrs)
        edge("wasAssociatedWith", **{"prov:activity": activity, "prov:agent": event_agent})
        for role in ("input", "output"):
            if role not in event:
                continue
            payload = event[role]
            entity = add(
                "entity",
                ref(activity, role, payload["sha256"]),
                {
                    "prov:type": qtype("oap:PayloadDigest"),
                    "oap:sha256": payload["sha256"],
                    "oap:encoding": payload["encoding"],
                    "oap:role": role,
                },
            )
            edge(
                "used" if role == "input" else "wasGeneratedBy",
                **{"prov:activity": activity, "prov:entity": entity},
            )
            if role == "output":
                edge("wasAttributedTo", **{"prov:entity": entity, "prov:agent": event_agent})
        # No inferred derivation from mere temporal ordering or same-session membership.
    return graph


def export(store: Store, session: str, format: str = "json") -> str:
    graph = build_graph(store, session)
    if format == "json":
        return json.dumps(graph, indent=2, sort_keys=True)
    from prov.model import ProvDocument

    doc = ProvDocument.deserialize(content=json.dumps(graph), format="json")
    if format == "provn":
        return doc.get_provn()
    if format == "turtle":
        return doc.serialize(format="rdf", rdf_format="turtle")
    raise ValueError("format must be json, provn, or turtle")
