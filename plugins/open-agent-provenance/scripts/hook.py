#!/usr/bin/env python3
"""A silent, non-blocking hook entry point with no third-party dependencies."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oap.capture import capture  # noqa: E402
from oap.store import Store  # noqa: E402


def main() -> int:
    if os.environ.get("OPENPROVENANCE_DISABLED") == "1":
        return 0
    try:
        # Bound work even when a host returns a very large file or tool output.
        raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Event exceeds size limit")
        host = "codex" if os.environ.get("PLUGIN_ROOT") else "claude"
        capture(json.loads(raw), host, Store())
    except Exception:
        # Never echo exception text: it can contain paths or raw payload fragments.
        print(
            "open-agent-provenance: event was not recorded; check local storage and hook input.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
