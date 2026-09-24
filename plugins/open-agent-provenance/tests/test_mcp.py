import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_tools_lineage_and_errors(tmp_path):
    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "oap", "serve"],
            cwd=str(Path(__file__).parents[1]),
            env={**os.environ, "OPENPROVENANCE_HOME": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                assert len(tools) == 6
                assert next(
                    t for t in tools if t.name == "provenance_export"
                ).annotations.readOnlyHint

                async def call(name, arguments):
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)

                result = await call("provenance_begin_session", {"host": "codex"})
                session = result["session"]
                source = await call("provenance_add_source", {"session": session, "label": "input"})
                await call(
                    "provenance_record_activity",
                    {
                        "session": session,
                        "name": "transform",
                        "inputs": [source["entity_id"]],
                        "outputs": [{"label": "output"}],
                    },
                )
                graph = await call("provenance_export", {"session": session})
                assert graph["wasDerivedFrom"]
                assert (await call("provenance_verify", {"session": session}))["valid"]
                invalid = await client.call_tool("provenance_export", {"session": "missing"})
                assert invalid.isError

    asyncio.run(exercise())


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_packaged_launcher_from_unrelated_working_directory(tmp_path, host):
    root = Path(__file__).parents[1].resolve()
    if host == "claude":
        config = json.loads((root / ".mcp.json").read_text())["mcpServers"]["open-agent-provenance"]
        # Claude performs this expansion; Codex uses its manifest's cwd instead.
        args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(root)) for arg in config["args"]]
        cwd = tmp_path
    else:
        config = json.loads((root / ".codex-plugin/plugin.json").read_text())["mcpServers"][
            "open-agent-provenance"
        ]
        args = config["args"]
        cwd = root / config["cwd"]

    async def exercise():
        params = StdioServerParameters(
            command=config["command"],
            args=args,
            cwd=str(cwd),
            env={**os.environ, "OPENPROVENANCE_HOME": str(tmp_path / "state")},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                result = await client.call_tool("provenance_begin_session", {"host": host})
                assert not result.isError
                assert (tmp_path / "state/events.sqlite3").exists()

    asyncio.run(exercise())
