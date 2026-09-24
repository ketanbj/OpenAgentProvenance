import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from oap.capture import capture
from oap.graph import build_graph
from oap.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def event(kind, **extra):
    return {
        "session_id": "session-1",
        "cwd": "/private/project",
        "hook_event_name": kind,
        "tool_name": "Bash",
        "tool_use_id": "call-1",
        **extra,
    }


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_private_capture_correlates_and_deduplicates(store, host):
    secret = "SECRET_TOKEN_never_write_this"
    before = event("PreToolUse", tool_input={"command": "echo " + secret})
    session = capture(before, host, store)["session"]
    assert capture(before, host, store)["duplicate"]
    capture(
        event(
            "PostToolUse",
            tool_input=before["tool_input"],
            tool_response={"stdout": secret, "exit_code": 0},
        ),
        host,
        store,
    )
    assert len(store.events(session)) == 2
    graph = build_graph(store, session)
    invocations = [a for a in graph["activity"].values() if a.get("prov:label") == "Bash"]
    assert len(invocations) == 1
    assert invocations[0]["oap:status"] == "succeeded"
    assert len(graph["used"]) == len(graph["wasGeneratedBy"]) == 1
    assert secret.encode() not in store.path.read_bytes()
    assert b"/private/project" not in store.path.read_bytes()
    assert secret not in json.dumps(graph)
    assert store.verify(session)["valid"]


def test_failures_and_unknown_status(store):
    session = capture(event("PostToolUseFailure", error="sensitive failure"), "claude", store)[
        "session"
    ]
    graph = build_graph(store, session)
    assert any(a.get("oap:status") == "failed" for a in graph["activity"].values())
    session2 = capture(event("PostToolUse", tool_response="Exit code: 1"), "codex", store)[
        "session"
    ]
    graph2 = build_graph(store, session2)
    assert any(a.get("oap:status") == "returned" for a in graph2["activity"].values())
    assert session != session2


def test_pre_without_post_does_not_invent_success_or_output(store):
    session = capture(event("PreToolUse"), "claude", store)["session"]
    graph = build_graph(store, session)
    assert not graph.get("wasGeneratedBy")
    assert all("oap:status" not in a for a in graph["activity"].values())


def test_post_before_pre_and_cwd_change(store):
    session = capture(event("PostToolUse", tool_response=None), "codex", store)["session"]
    assert session == capture(event("PreToolUse", cwd="/other/project"), "codex", store)["session"]
    graph = build_graph(store, session)
    tools = [a for a in graph["activity"].values() if a.get("prov:label") == "Bash"]
    assert len(tools) == 1
    assert tools[0]["oap:status"] == "returned"


def test_concurrent_writers_keep_all_events_and_valid_chain(store):
    def write(i):
        return capture(
            event("PreToolUse", tool_use_id=f"call-{i}"), "codex", Store(store.directory)
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(40)))
    check = store.verify(results[0]["session"])
    assert check["valid"] and check["event_count"] == 40


def test_reused_call_ids_in_subagents_are_distinct(store):
    session = capture(event("PreToolUse", agent_id="child-1"), "claude", store)["session"]
    capture(event("PreToolUse", agent_id="child-2"), "claude", store)
    assert len(store.events(session)) == 2
    graph = build_graph(store, session)
    assert len(graph["agent"]) == 3
    assert len(graph["actedOnBehalfOf"]) == 2


def test_prompt_and_stop_payloads_are_hashed(store):
    session = capture(event("UserPromptSubmit", prompt="PRIVATE PROMPT"), "claude", store)[
        "session"
    ]
    capture(event("Stop", last_assistant_message="PRIVATE ANSWER"), "claude", store)
    graph = build_graph(store, session)
    assert len(graph["entity"]) == 2
    assert "PRIVATE" not in json.dumps(store.events(session))


def test_tamper_detection_blocks_export(store):
    session = capture(event("SessionStart"), "codex", store)["session"]
    with store.connect() as db:
        db.execute("UPDATE events SET body='{}'")
    assert store.verify(session)["valid"] is False
    with pytest.raises(ValueError, match="integrity"):
        build_graph(store, session)


def test_hook_fails_open_without_leaking_raw_input(tmp_path):
    script = Path(__file__).parents[1] / "scripts/hook.py"
    env = {**os.environ, "OPENPROVENANCE_HOME": str(tmp_path / "state")}
    result = subprocess.run(
        [sys.executable, str(script)],
        input='{"secret":"DO_NOT_LEAK"',
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert "DO_NOT_LEAK" not in result.stderr
    assert "not recorded" in result.stderr


def test_disabled_hook_has_no_side_effects(tmp_path):
    script = Path(__file__).parents[1] / "scripts/hook.py"
    env = {
        **os.environ,
        "OPENPROVENANCE_HOME": str(tmp_path / "state"),
        "OPENPROVENANCE_DISABLED": "1",
    }
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(event("SessionStart")),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr
    assert not (tmp_path / "state").exists()


def test_invalid_ids_rejected_before_persistence(store):
    with pytest.raises(ValueError):
        capture(event("PreToolUse", session_id=""), "codex", store)
    with pytest.raises(ValueError):
        capture(event("PreToolUse", tool_use_id=None), "codex", store)
    assert store.sessions() == []


def test_conflicting_duplicate_is_rejected(store):
    session = capture(event("PreToolUse", tool_input="first"), "codex", store)["session"]
    with pytest.raises(ValueError, match="Conflicting retry"):
        capture(event("PreToolUse", tool_input="different"), "codex", store)
    assert len(store.events(session)) == 1
    assert store.verify(session)["valid"]
