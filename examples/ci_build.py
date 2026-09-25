"""Package the smoke-test agent's output at the runner-supplied artifact path."""

import os
from zipfile import ZipFile

with ZipFile(os.environ["OAP_ARTIFACT"], "w") as archive:
    archive.write("agent-output.txt")
