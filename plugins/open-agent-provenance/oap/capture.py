"""Host adapters. Raw payloads are hashed in memory and never persisted."""

from __future__ import annotations

from .store import Store, digest

EVENTS = {
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "Interrupt",
}


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError(f"Invalid {field}")
    return value


def fingerprint(value: object) -> dict:
    return {"sha256": digest(value), "encoding": "canonical-json-v1"}


def capture(payload: dict, host: str, store: Store) -> dict:
    if host not in {"claude", "codex"}:
        raise ValueError("Unknown host")
    if not isinstance(payload, dict):
        raise ValueError("Expected an event object")
    event = payload.get("hook_event_name")
    if event not in EVENTS:
        raise ValueError("Unsupported hook event")
    sid = identifier(payload.get("session_id"), "session_id")
    # A session remains stable across cwd changes and resumes, but hosts are distinct.
    session = digest([host, sid])
    body = {"kind": event, "host": host, "evidence": "hook-observed"}
    if isinstance(payload.get("cwd"), str):
        body["project_digest"] = digest(payload["cwd"])
    for field in ("agent_id", "turn_id", "prompt_id"):
        if payload.get(field):
            body[field] = digest(identifier(payload[field], field))
    if isinstance(payload.get("model"), str):
        body["model"] = payload["model"][:200]
    event_id = None
    if event in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        tool = identifier(payload.get("tool_name"), "tool_name")
        call_id = identifier(payload.get("tool_use_id"), "tool_use_id")
        body["tool"] = tool
        body["call_id"] = digest([body.get("agent_id"), call_id])
        event_id = digest([event, body["call_id"]])
        if "tool_input" in payload:
            body["input"] = fingerprint(payload["tool_input"])
        if event != "PreToolUse":
            response = payload.get("tool_response")
            # PostToolUse means 'returned', not necessarily succeeded in Codex.
            body["status"] = "returned"
            if event == "PostToolUseFailure":
                body["status"] = "failed"
                if "error" in payload:
                    body["output"] = fingerprint(payload["error"])
            elif "tool_response" in payload:
                body["output"] = fingerprint(response)
                if isinstance(response, dict):
                    if response.get("isError") is True:
                        body["status"] = "failed"
                    exit_code = response.get("exit_code")
                    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                        body["exit_code"] = exit_code
                        body["status"] = "succeeded" if exit_code == 0 else "failed"
    elif event == "UserPromptSubmit" and "prompt" in payload:
        body["input"] = fingerprint(payload["prompt"])
    elif event == "Stop":
        for field in ("last_assistant_message", "last_agent_message"):
            if field in payload:
                body["output"] = fingerprint(payload[field])
                break
    return store.append(session, body, event_id)
