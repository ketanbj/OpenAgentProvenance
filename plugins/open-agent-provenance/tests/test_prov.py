import json

import pytest
from prov.model import ProvDocument
from rdflib import RDF, Graph, Namespace

from oap.capture import capture
from oap.graph import build_graph, export
from oap.service import add_source, begin_session, record_activity
from oap.store import Store


@pytest.fixture
def lineage(tmp_path):
    store = Store(tmp_path)
    session = begin_session(store, "codex")["session"]
    source = add_source(store, session, "Input table", "file:///work/input.csv", "a" * 64)
    output = record_activity(
        store,
        session,
        "Summarize input",
        [source["entity_id"]],
        [{"label": "Report", "uri": "file:///work/report.md"}],
    )
    return store, session, source, output


def test_semantic_lineage_and_prov_roundtrip(lineage):
    store, session, source, output = lineage
    graph = build_graph(store, session)
    assert graph["wasDerivedFrom"]
    edge = next(iter(graph["wasDerivedFrom"].values()))
    assert edge["prov:usedEntity"] == source["entity_id"]
    assert edge["prov:generatedEntity"] == output["output_entity_ids"][0]
    doc = ProvDocument.deserialize(content=export(store, session), format="json")
    assert len(doc.get_records()) == sum(len(v) for k, v in graph.items() if k != "prefix")
    assert doc == ProvDocument.deserialize(content=doc.serialize(format="json"), format="json")
    assert "wasDerivedFrom(" in export(store, session, "provn")
    rdf = Graph().parse(data=export(store, session, "turtle"), format="turtle")
    prov = Namespace("http://www.w3.org/ns/prov#")
    # Named PROV relations serialize as qualified PROV-O nodes.
    derivations = list(rdf.objects(None, prov.qualifiedDerivation))
    assert derivations
    assert list(rdf.objects(derivations[0], prov.entity))
    assert list(rdf.triples((None, RDF.type, prov.SoftwareAgent)))


def test_output_can_feed_next_activity(lineage):
    store, session, _, output = lineage
    record_activity(
        store,
        session,
        "Revise report",
        output["output_entity_ids"],
        [{"label": "Report", "uri": "file:///work/report.md"}],
    )
    graph = build_graph(store, session)
    assert len(graph["wasDerivedFrom"]) == 2
    generated_ids = [e["prov:entity"] for e in graph["wasGeneratedBy"].values()]
    assert len(generated_ids) == len(set(generated_ids)) == 2


def test_invalid_source_reference_does_not_write(lineage):
    store, session, _, _ = lineage
    count = len(store.events(session))
    with pytest.raises(ValueError, match="does not exist"):
        record_activity(store, session, "Bad", ["id:missing"], [{"label": "report"}])
    assert len(store.events(session)) == count


@pytest.mark.parametrize("params", [{"sha256": "bad"}, {"uri": "relative/path"}])
def test_invalid_artifact_is_atomic(lineage, params):
    store, session, _, _ = lineage
    count = len(store.events(session))
    with pytest.raises(ValueError):
        add_source(store, session, "test", **params)
    assert len(store.events(session)) == count


def test_hook_graph_parses_and_does_not_invent_file_lineage(tmp_path):
    store = Store(tmp_path)
    payload = {
        "session_id": "s",
        "tool_name": "Write",
        "tool_use_id": "c",
        "tool_input": {"file_path": "a.txt", "content": "content"},
    }
    session = capture({**payload, "hook_event_name": "PreToolUse"}, "claude", store)["session"]
    capture({**payload, "hook_event_name": "PostToolUse", "tool_response": {}}, "claude", store)
    graph = build_graph(store, session)
    doc = ProvDocument.deserialize(content=json.dumps(graph), format="json")
    assert doc.get_records()
    assert "wasDerivedFrom" not in graph
    assert all("prov:startTime" not in a for a in graph["activity"].values())


def test_unknown_session_errors(tmp_path):
    with pytest.raises(ValueError, match="Unknown session"):
        export(Store(tmp_path), "does-not-exist")
