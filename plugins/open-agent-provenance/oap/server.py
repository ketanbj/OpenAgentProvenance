"""MCP stdio server using the official Python SDK."""

from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import graph, service
from .store import Store

mcp = FastMCP("open-agent-provenance")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=1024)
    uri: str | None = None
    sha256: str | None = None


@mcp.tool(annotations=READ)
def provenance_sessions(limit: int = 20) -> list[dict]:
    """List locally captured sessions, newest first. Use returned session keys in other tools."""
    return Store().sessions(limit)


@mcp.tool(annotations=WRITE)
def provenance_begin_session(
    host: Literal["claude", "codex"], session_id: str | None = None
) -> dict:
    """Begin explicit capture when hooks are unavailable. Records are marked agent-asserted.

    For an existing hooked session, use provenance_sessions instead. If the host session ID
    is known, pass it to join the same journal. Do not invent observed events.
    """
    return service.begin_session(Store(), host, session_id)


@mcp.tool(annotations=WRITE)
def provenance_add_source(
    session: str, label: str, uri: str | None = None, sha256: str | None = None
) -> dict:
    """Register an input artifact; returns an entity_id for record_activity inputs.

    Labels and URIs are saved verbatim: omit credentials, private query strings, and secrets.
    Optional sha256 describes raw artifact bytes. No URL is fetched and no file is read.
    """
    return service.add_source(Store(), session, label, uri, sha256)


@mcp.tool(annotations=WRITE)
def provenance_record_activity(
    session: str, name: str, inputs: list[str], outputs: list[Artifact]
) -> dict:
    """Assert an activity and artifact lineage. Each output is declared derived from every input.

    Supply only entities actually used to produce these outputs; split unrelated derivations
    into separate calls. Input IDs must already exist in the session. This is agent-asserted
    evidence, not automatic verification. Labels and URIs are stored verbatim.
    """
    return service.record_activity(
        Store(), session, name, inputs, [artifact.model_dump() for artifact in outputs]
    )


@mcp.tool(annotations=READ)
def provenance_export(session: str, format: Literal["json", "provn", "turtle"] = "json") -> str:
    """Return W3C PROV lineage for one session. Nothing is uploaded or written to an export path."""
    return graph.export(Store(), session, format)


@mcp.tool(annotations=READ)
def provenance_verify(session: str) -> dict:
    """Check local journal hash-chain consistency. This is not proof of authenticity or completeness."""
    return Store().verify(session)


def main() -> None:
    mcp.run(transport="stdio")
