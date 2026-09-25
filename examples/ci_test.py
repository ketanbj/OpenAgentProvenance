"""Test both source content and the exact packaged release file."""

import os
from pathlib import Path
from zipfile import ZipFile

expected = b"Built by the provenance CI smoke test.\n"
if Path("agent-output.txt").read_bytes() != expected:
    raise SystemExit("Unexpected generated source")
with ZipFile(os.environ["OAP_ARTIFACT"]) as archive:
    if (
        archive.namelist() != ["agent-output.txt"]
        or archive.read("agent-output.txt") != expected
    ):
        raise SystemExit("Unexpected release artifact")
