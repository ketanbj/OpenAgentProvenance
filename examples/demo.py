"""Run through uv with the plugin project. All journal writes use a temporary directory."""

from pathlib import Path
from tempfile import TemporaryDirectory

from oap.graph import export
from oap.service import add_source, begin_session, record_activity
from oap.store import Store

with TemporaryDirectory(prefix="oap-demo-") as directory:
    store = Store(Path(directory))
    session = begin_session(store, "codex", "demo-session")["session"]
    source = add_source(store, session, "Dataset", "urn:example:dataset:v1")
    analysis = record_activity(
        store, session, "Analyze dataset", [source["entity_id"]],
        [{"label": "Analysis", "uri": "urn:example:analysis:v1"}],
    )
    record_activity(
        store, session, "Write report", analysis["output_entity_ids"],
        [{"label": "Report", "uri": "urn:example:report:v1"}],
    )
    assert store.verify(session)["valid"]
    print(export(store, session))
