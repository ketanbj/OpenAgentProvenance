# Open Agent Provenance

A local plugin for **Claude Code and Codex** that records agent activity using the
[W3C PROV family](https://www.w3.org/TR/prov-overview/) linked from
[OpenProvenance](https://openprovenance.org/). Version 0.1.0 is a working local
implementation, with automatic lifecycle capture and explicit artifact lineage.

## What it does

- Captures session, prompt, tool, response, and subagent lifecycle events exposed
  by each host's hooks. Captures Claude's `PostToolUseFailure` separately.
- Stores an append-only SQLite event journal, with transaction-safe parallel
  writes, tool-event deduplication, and a per-session SHA-256 integrity chain.
- Represents agents, activities, entities, usage, generation, attribution,
  delegation, and explicitly asserted derivation in PROV.
- Provides six MCP tools for listing sessions, registering sources, recording
  artifact lineage, checking integrity, and exporting records.
- Exports **PROV-JSON**, **PROV-N**, and **PROV-O Turtle**. The Python `prov` library
  handles PROV-N and RDF serialization.

There is no hosted backend or telemetry. The plugin does not contact
openprovenance.org or upload records to its services. Installation can download
Python dependencies through `uv`; capture itself uses only the standard library.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a host version
supporting local plugins, stdio MCP, and command hooks. The hook command uses
`python3`; both `python3` and `uv` must be on the host application's PATH.

From the repository root:

```sh
uv sync --frozen --project plugins/open-agent-provenance
uv run --frozen --project plugins/open-agent-provenance pytest -q plugins/open-agent-provenance/tests
uv run --frozen --project plugins/open-agent-provenance python examples/demo.py
```

The example uses a temporary database, asserts a two-step artifact lineage, checks
its integrity, and prints PROV-JSON. It does not modify your actual journal.

### Claude Code

Launch a new session from the repository root:

```sh
claude --plugin-dir ./plugins/open-agent-provenance
```

For a different working project, pass the absolute path to the plugin instead.
The shared `hooks/hooks.json` loads automatically; the Claude manifest adds only
the Claude-specific failure hook. `.mcp.json` starts the local MCP server.

Invoke `/open-agent-provenance:provenance` or ask:

> Show the recorded provenance for this session, then export it as PROV-JSON.

### Codex

The plugin includes a validated `.codex-plugin/plugin.json` compatibility
manifest, a skill, command hooks, and an inline stdio MCP definition. Its MCP
working directory resolves relative to the installed plugin root; it does not
rely on Claude's variable substitution.

Use Codex's built-in `$plugin-creator` to register the existing
`plugins/open-agent-provenance` folder in your **personal marketplace**. For example:

> Register this existing plugin in my personal marketplace:
> /absolute/path/to/OpenAgentProvenance/plugins/open-agent-provenance

Then install **Open Agent Provenance** from that marketplace in the app, review
and trust its hook definitions through the host's hook controls, and start a new
session. Installation alone does not trust hooks. Invoke the `provenance` skill
or ask to inspect this session's provenance. See the official
[plugin packaging guide](https://developers.openai.com/plugins/build/plugins)
and [hook reference](https://learn.chatgpt.com/docs/hooks).

If your Codex version lacks plugin hooks, you can still use the MCP server:

```sh
codex mcp add open-agent-provenance -- uv run --frozen --directory /absolute/path/to/OpenAgentProvenance/plugins/open-agent-provenance open-agent-provenance serve
```

MCP-only use records **explicit annotations**; it cannot intercept other tools.
Use `provenance_begin_session` first in that mode. This fallback does not install
the skill or enable automatic capture.

## Storage and controls

By default, all local hosts share:

```text
~/.local/state/open-agent-provenance/events.sqlite3
```

Set `OPENPROVENANCE_HOME` to an **absolute directory** before launching the host
to choose a different journal. Use the same value for hooks and the MCP process.
Codex's MCP definition forwards this variable. New directories and databases are
created with owner-only permissions (0700 and 0600 on POSIX); existing directories
are not chmod-ed. Do not point this at a shared or untrusted directory.

Set `OPENPROVENANCE_DISABLED=1` before launching a host to turn off automatic hook
capture. Explicit MCP recording remains available. Disable the plugin in the host
to disable both. Records persist until you remove the database; there is no
automatic retention limit. Stop hosts/server processes before deleting it.

Inspect and export from a terminal:

```sh
uv run --frozen --project plugins/open-agent-provenance open-agent-provenance sessions
uv run --frozen --project plugins/open-agent-provenance open-agent-provenance verify SESSION_KEY
uv run --frozen --project plugins/open-agent-provenance open-agent-provenance export SESSION_KEY > session.prov.json
uv run --frozen --project plugins/open-agent-provenance open-agent-provenance export SESSION_KEY --format provn > session.provn
uv run --frozen --project plugins/open-agent-provenance open-agent-provenance export SESSION_KEY --format turtle > session.ttl
```

Use a session key returned by `sessions`, not the raw host session ID.

## Provenance model

| Observed or asserted fact | PROV representation |
| --- | --- |
| Host session / subagent | `prov:SoftwareAgent` and session `prov:Activity` |
| Correlated pre/post tool events | One `prov:Activity` per host/session/subagent/tool-call ID |
| Prompt or tool input fingerprint | `prov:Entity`, connected with `used` |
| Tool output or final response fingerprint | `prov:Entity`, connected with `wasGeneratedBy` and `wasAttributedTo` |
| Explicit input artifact | Entity with optional URI and raw-byte SHA-256 |
| Explicit transformation | Activity with `used`, `wasGeneratedBy`, `wasDerivedFrom`, and attribution |

`oap:` is an extension namespace (`urn:open-agent-provenance:v1:`); `id:` provides
stable identifiers within a journal. Hook records carry `oap:evidence =
hook-observed`; explicit annotations carry `agent-asserted`. Hook timestamps are
observation times, not exact execution start/end times. Repeated session starts
and stops remain lifecycle observations, allowing resume without inventing a
single uninterrupted execution interval.

Automatic capture fingerprints the **host's JSON payload**, not the bytes of
every file a shell command touches. To describe actual artifact lineage, register
sources using `provenance_add_source`, then pass their returned IDs into
`provenance_record_activity`. Returned output IDs can feed later activities.
Each call asserts that every output derives from every listed input; split
independent transformations. URI/digest annotations do not read or fetch files.

## Privacy, integrity, and coverage

- Raw prompts, commands, tool responses, errors, and final messages are hashed in
  memory and discarded. Transcripts and arbitrary tool-named files are never read.
  Tool names, optional model identifiers, event types, timestamps, and execution
  status remain visible. Host/session/call IDs and project paths are hashed.
- Explicit artifact labels and URIs are stored verbatim. Omit secrets and sensitive
  query strings. Payload hashes are not encryption: low-entropy values can be
  guessed. The database is local and unencrypted.
- Fingerprints use SHA-256 over sorted, compact, ASCII-escaped JSON, with NaN and
  Infinity rejected (`canonical-json-v1`). Artifact hashes supplied through MCP
  describe raw bytes. These encodings are different and not interchangeable.
- The chain detects local inconsistency. It is **not signed or anchored**: a writer
  with database access can recompute it, and deleting a suffix is not detectable
  without an externally saved head. It does not prove completeness or authenticity.
- Hooks fail open: malformed, oversized (>8 MiB), unsupported, or unwritable
  events emit a generic stderr diagnostic and do not block agent work. Inspect
  host hook/debug logs for capture errors. Crashes, missing trust, host timeouts,
  and unsupported paths can leave gaps. A pre-event without a post-event remains
  incomplete; it is never marked successful.
- Codex `PostToolUse` includes unsuccessful command returns. Structured `exit_code`
  and MCP `isError` determine status when present; otherwise status is `returned`.
  No success is guessed from arbitrary response text.
- Codex hosted tools such as web search are outside local tool-hook coverage.
  This plugin does not observe hidden reasoning, remote side effects, all file
  changes, or model internals. Event delivery and subagent fields vary by host.
- `verify` checks the journal, **not the full W3C PROV Constraints specification**.
  Tests check library interoperability and graph relationships; they are not
  standards certification. No public validation service receives your data.

## Development

The entire installable runtime lives in `plugins/open-agent-provenance`; it has no
relative dependencies outside that folder. Hooks use Python's standard library;
the MCP server uses the official SDK. Dependencies are pinned in `uv.lock`.

```sh
uv run --frozen --project plugins/open-agent-provenance ruff check plugins/open-agent-provenance
uv run --frozen --project plugins/open-agent-provenance ruff format --check plugins/open-agent-provenance
claude plugin validate plugins/open-agent-provenance
python3 scripts/build_plugin.py
```

The build produces `dist/open-agent-provenance-0.1.0.zip`, excluding virtualenvs,
tests, caches, and local journals. The source folder can be used directly with
Claude; Codex uses a marketplace entry pointing to the unpacked folder.

Test coverage includes real MCP stdio negotiation, tool calls and errors,
concurrent writers, retry deduplication, missing/out-of-order events, host and
subagent isolation, hash-chain tampering, payload privacy, and PROV/RDF parsing.
Native Claude manifest validation is exercised locally. Native Codex activation
must be checked with a working Codex installation; a passing MCP test alone does
not establish that the host has enabled and trusted hooks.

References: [Claude plugins](https://code.claude.com/docs/en/plugins-reference),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Codex hooks](https://learn.chatgpt.com/docs/hooks),
[PROV-JSON](https://www.w3.org/submissions/prov-json/),
[PROV-O](https://www.w3.org/TR/prov-o/).
