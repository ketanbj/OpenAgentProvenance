"""Build a self-contained plugin archive from an explicit runtime allowlist."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
plugin = root / "plugins" / "open-agent-provenance"
version = json.loads((plugin / ".codex-plugin/plugin.json").read_text())["version"]
destination = root / "dist" / f"open-agent-provenance-{version}.zip"
destination.parent.mkdir(exist_ok=True)
directories = {".codex-plugin", ".claude-plugin", "hooks", "oap", "scripts", "skills"}
files = {".mcp.json", "pyproject.toml", "uv.lock"}
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(plugin.rglob("*")):
        relative = path.relative_to(plugin)
        if path.is_file() and "__pycache__" not in relative.parts:
            if relative.parts[0] in directories or relative.as_posix() in files:
                archive.write(path, Path(plugin.name) / relative)
    archive.write(root / "README.md", f"{plugin.name}/README.md")
print(destination)
