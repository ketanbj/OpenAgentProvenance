"""Explicit semantic lineage, separate from host-observed capture."""

from __future__ import annotations

import re
from uuid import uuid4

from .capture import identifier
from .graph import build_graph, ref
from .store import Store, digest


def begin_session(store: Store, host: str, session_id: str | None = None) -> dict:
    if host not in {"claude", "codex"}:
        raise ValueError("host must be claude or codex")
    session_id = identifier(session_id or uuid4().hex, "session_id")
    session = digest([host, session_id])
    return store.append(
        session,
        {"kind": "SessionStart", "host": host, "evidence": "agent-asserted"},
        "manual-session-start",
    )


def artifact_attributes(label: str, uri: str | None = None, sha256: str | None = None) -> dict:
    attrs = {"prov:label": identifier(label, "label"), "oap:evidence": "agent-asserted"}
    if uri is not None:
        identifier(uri, "uri")
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", uri):
            raise ValueError("uri must be absolute, such as https:, file:, or urn:")
        attrs["prov:location"] = {"$": uri, "type": "xsd:anyURI"}
    if sha256 is not None:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
            raise ValueError("sha256 must contain 64 hex characters")
        attrs["oap:sha256"] = sha256.lower()
        attrs["oap:encoding"] = "raw-bytes"
    return attrs


def add_source(
    store: Store, session: str, label: str, uri: str | None = None, sha256: str | None = None
) -> dict:
    host = store.events(session)[0]["host"]
    event_id = uuid4().hex
    entity_id = ref(session, event_id, "source")
    store.append(
        session,
        {
            "kind": "Source",
            "host": host,
            "entity_id": entity_id,
            "attributes": artifact_attributes(label, uri, sha256),
        },
        event_id,
    )
    return {"session": session, "entity_id": entity_id}


def record_activity(
    store: Store, session: str, name: str, inputs: list[str], outputs: list[dict]
) -> dict:
    if len(inputs) > 100 or len(outputs) > 100:
        raise ValueError("At most 100 inputs and outputs per activity")
    graph = build_graph(store, session)
    for entity_id in inputs:
        if entity_id not in graph.get("entity", {}):
            raise ValueError("Input entity does not exist in this session")
    event_id = uuid4().hex
    activity_id = ref(session, event_id, "activity")
    artifacts = [
        {
            "entity_id": ref(session, event_id, "output", str(index)),
            "attributes": artifact_attributes(**artifact),
        }
        for index, artifact in enumerate(outputs)
    ]
    host = store.events(session)[0]["host"]
    store.append(
        session,
        {
            "kind": "Activity",
            "host": host,
            "activity_id": activity_id,
            "name": identifier(name, "name"),
            "inputs": inputs,
            "outputs": artifacts,
        },
        event_id,
    )
    return {
        "session": session,
        "activity_id": activity_id,
        "output_entity_ids": [artifact["entity_id"] for artifact in artifacts],
    }
