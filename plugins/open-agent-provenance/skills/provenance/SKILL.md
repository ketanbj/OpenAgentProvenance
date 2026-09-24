---
name: provenance
description: Inspect, export, and annotate provenance captured by Open Agent Provenance for Claude Code or Codex. Use when tracking artifact sources, explaining how an output was produced, or checking a session's recorded lineage.
---

Use the `open-agent-provenance` MCP server's `provenance_*` tools. Hooks record
supported lifecycle events automatically after the host enables and trusts them.
Do not duplicate tool-hook records manually.

1. Call `provenance_sessions` and select the session that matches the task. If
   several sessions are plausible, inspect their exports or ask which one is intended.
   Do not silently select the newest session from an unrelated project.
2. When hooks are unavailable, call `provenance_begin_session` with `claude` or
   `codex`. State that explicit records cover only the work you record. A known host
   session ID joins its journal; an omitted ID creates a separate manual session.
3. For semantic artifact lineage, register actual sources with
   `provenance_add_source`. Pass returned entity IDs to `provenance_record_activity`.
   Use that activity's output IDs as inputs to subsequent activities. Every output
   in one call is asserted derived from every input: split unrelated work into
   separate calls. Source URLs and artifact hashes must be observed, not guessed.
4. Use `provenance_verify` to check journal consistency and `provenance_export` for
   PROV-JSON (`json`), PROV-N (`provn`), or PROV-O Turtle (`turtle`). Saving the returned
   text is separate from exporting; no tool uploads the journal.

Automatic capture stores SHA-256 digests of canonical JSON payloads, not raw
prompts, commands, file contents, or tool results. Explicit labels and URIs are
stored verbatim: exclude credentials and sensitive URL parameters. An artifact
SHA-256 is of raw bytes, a different encoding from an automatic payload digest.

Distinguish `hook-observed` records from `agent-asserted` lineage. Hook observation
times are not exact tool execution times. A returned tool result does not alone
prove success. Missing post events can mean denial, interruption, or a coverage
gap. Hash-chain consistency does not establish authenticity, completeness, or
W3C PROV Constraints validation. Never claim capture of hidden reasoning.
