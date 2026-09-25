"""Deterministic host simulator for pipeline smoke tests; not a real AI agent.

It exercises the actual hook entry point around a real file-writing operation.
Replace the workflow agent-command with your configured Claude/Codex invocation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

session = uuid4().hex
hook = Path(os.environ["OAP_PLUGIN_ROOT"]) / "scripts/hook.py"


def emit(kind, **extra):
    subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "hook_event_name": kind,
                "session_id": session,
                "cwd": str(Path.cwd()),
                **extra,
            }
        ),
        text=True,
        check=True,
    )


emit("SessionStart")
emit(
    "PreToolUse",
    tool_name="Write",
    tool_use_id="demo-write",
    tool_input={"file": "agent-output.txt"},
)
Path("agent-output.txt").write_text("Built by the provenance CI smoke test.\n")
emit(
    "PostToolUse",
    tool_name="Write",
    tool_use_id="demo-write",
    tool_response={"exit_code": 0},
)
emit("Stop")
emit("SessionEnd")
