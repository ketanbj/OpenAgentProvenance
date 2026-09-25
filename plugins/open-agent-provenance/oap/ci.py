"""CI evidence collection and independent certificate verification.

The Lean executable owns the acceptance decision. Python observes bytes/processes,
checks the hook journal, and constructs the independent consumer policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from .store import Store, canonical, digest


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Artifact must be a regular file, not a symlink")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command_digest(command: str) -> str:
    if not command.strip():
        raise ValueError("Stage commands must not be empty")
    return hashlib.sha256(command.encode()).hexdigest()


def git(workspace: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(workspace), *args])


def source_snapshot(workspace: Path) -> str:
    """Hash tracked and non-ignored untracked files, including executable bits.

    Ignored files, Git metadata, and external dependencies are outside this scope.
    Reject symlinks/submodules rather than silently hashing an incomplete target.
    """
    paths = git(workspace, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    entries = []
    for raw in sorted(set(paths.split(b"\0")) - {b""}):
        name = os.fsdecode(raw)
        path = workspace / name
        if path.is_symlink():
            raise ValueError("Source snapshots do not support symlinks")
        if not path.exists():
            entries.append([name, "deleted"])
        elif path.is_file():
            entries.append([name, sha256_file(path), bool(path.stat().st_mode & stat.S_IXUSR)])
        else:
            raise ValueError("Source snapshots do not support submodules or special files")
    if not entries:
        raise ValueError("Source snapshot is empty")
    return digest(entries)


def hook_evidence(store: Store) -> tuple[list[dict], list[dict]]:
    """Export the complete fresh journal, preserving missing/conflicting call ends."""
    with closing(store.connect()) as db:
        session_ids = [row[0] for row in db.execute("SELECT DISTINCT session FROM events")]
    sessions, journals = [], []
    for session_id in sorted(session_ids):
        check = store.verify(session_id)
        if not check["valid"]:
            raise ValueError("Hook journal integrity check failed")
        events = store.events(session_id)
        observed = [e for e in events if e.get("evidence") == "hook-observed"]
        if not observed:
            continue  # Explicit MCP annotations cannot stand in for runtime hooks.
        calls: dict[str, dict] = {}
        for event in observed:
            kind = event["kind"]
            if kind not in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
                continue
            call_id = event["call_id"]
            call = calls.setdefault(
                call_id,
                {"id": call_id, "preCount": 0, "postCount": 0, "status": "", "tool": event["tool"]},
            )
            if call["tool"] != event["tool"]:
                raise ValueError("Conflicting hook tool names")
            if kind == "PreToolUse":
                call["preCount"] += 1
            else:
                call["postCount"] += 1
                call["status"] = event.get("status", "")
        # Ignore prompt/annotation observations after Stop, but require the last
        # lifecycle/tool observation to be a terminal event (no stale Stop).
        lifecycle = [
            e["kind"]
            for e in observed
            if e["kind"]
            in {
                "SessionStart",
                "SessionEnd",
                "Stop",
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
                "SubagentStart",
                "SubagentStop",
                "Interrupt",
            }
        ]
        sessions.append(
            {
                "id": session_id,
                "head": check["head"],
                "eventCount": check["event_count"],
                "started": bool(lifecycle) and lifecycle[0] == "SessionStart",
                "stopped": bool(lifecycle) and lifecycle[-1] in {"Stop", "SessionEnd"},
                "interrupted": "Interrupt" in lifecycle,
                "calls": [
                    {k: v for k, v in call.items() if k != "tool"}
                    for _, call in sorted(calls.items())
                ],
            }
        )
        journals.append({"session": session_id, "rows": store.rows(session_id)})
    return sessions, journals


def run_command(command: str, workspace: Path, env: dict, timeout: int) -> int:
    # Commands are intentional workflow configuration, passed via the environment
    # by Actions, never interpolated into generated shell source.
    process = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", command],
        cwd=workspace,
        env=env,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout)
    finally:
        # Stop background children in this stage's process group before taking
        # snapshots. Deliberately daemonized processes still require a sandbox.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_bundle_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8388608:
        raise ValueError("Bundle JSON must be a regular file of at most 8 MiB")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def verify_hook_export(journals: object) -> list[dict]:
    """Recheck chains and rederive the hook summary from exported rows.

    Only fixed, parameterized INSERT statements reconstruct a fresh journal.
    Never open a candidate-supplied SQLite database or execute its SQL.
    """
    if not isinstance(journals, list):
        raise ValueError("Invalid hook journal export")
    try:
        with TemporaryDirectory(prefix="oap-journal-check-") as directory:
            store = Store(Path(directory))
            session_ids = set()
            with closing(store.connect()) as db, db:
                for journal in journals:
                    session = journal["session"]
                    if session in session_ids or not journal["rows"]:
                        raise ValueError("Duplicate or empty exported session")
                    session_ids.add(session)
                    for row in journal["rows"]:
                        if row["session"] != session:
                            raise ValueError("Exported row belongs to another session")
                        db.execute(
                            "INSERT INTO events VALUES(?,?,?,?,?,?)",
                            (
                                row["seq"],
                                row["session"],
                                row["event_id"],
                                row["body"],
                                row["previous_hash"],
                                row["event_hash"],
                            ),
                        )
            sessions, _ = hook_evidence(store)
            return sessions
    except (KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError("Invalid hook journal export") from exc


def collect(
    *,
    workspace: Path,
    output: Path,
    artifact: Path,
    run_id: str,
    base_revision: str,
    agent_command: str,
    build_command: str,
    test_command: str,
    timeout: int = 1800,
) -> dict:
    workspace = workspace.resolve()
    output = output.resolve()
    if artifact.is_symlink():
        raise ValueError("Artifact path must not be a symlink")
    artifact = artifact.resolve()
    if output.is_relative_to(workspace) or artifact.is_relative_to(workspace):
        raise ValueError("CI output and artifact paths must be outside the source workspace")
    if output.exists() or artifact.exists() or artifact.is_symlink():
        raise ValueError("CI output and artifact paths must be fresh")
    if artifact.is_relative_to(output):
        raise ValueError("Build artifact must be outside the evidence bundle")
    if not run_id or not base_revision or timeout < 1:
        raise ValueError("Run ID, base revision, and positive timeout are required")
    for command in (agent_command, build_command, test_command):
        command_digest(command)
    if Path(git(workspace, "rev-parse", "--show-toplevel").decode().strip()).resolve() != workspace:
        raise ValueError("Workspace must be the root of a Git checkout")
    if git(workspace, "rev-parse", "HEAD").decode().strip() != base_revision:
        raise ValueError("Checkout does not match the expected base revision")
    output.mkdir(parents=True, mode=0o700)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    store = Store(output / "journal")
    env = {
        **os.environ,
        "OPENPROVENANCE_HOME": str(store.directory),
        "OPENPROVENANCE_DISABLED": "0",
        "OAP_ARTIFACT": str(artifact),
        "OAP_PLUGIN_ROOT": str(Path(__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    initial_source = source_snapshot(workspace)
    agent_exit = run_command(agent_command, workspace, env, timeout)
    # Freeze hook evidence at the end of the agent phase. Build/test commands
    # cannot supply hooks to compensate for an agent with capture disabled.
    sessions, journals = hook_evidence(store)
    if agent_exit != 0:
        raise ValueError(f"Agent command failed with exit code {agent_exit}")
    if not sessions:
        raise ValueError("No runtime hook evidence captured during the agent phase")
    source = source_snapshot(workspace)
    build_exit = run_command(build_command, workspace, env, timeout)
    if build_exit != 0:
        raise ValueError(f"Build command failed with exit code {build_exit}")
    build_after = source_snapshot(workspace)
    artifact_before = sha256_file(artifact)
    test_before = source_snapshot(workspace)
    test_exit = run_command(test_command, workspace, env, timeout)
    test_after = source_snapshot(workspace)
    artifact_after = sha256_file(artifact)
    if git(workspace, "rev-parse", "HEAD").decode().strip() != base_revision:
        raise ValueError("Agent/build/test changed HEAD; this policy requires an uncommitted patch")
    shutil.copyfile(artifact, output / "artifact")
    certificate = {
        "schemaVersion": 1,
        "runId": run_id,
        "baseRevision": base_revision,
        "agentCommandDigest": command_digest(agent_command),
        "agentExitCode": agent_exit,
        "sourceDigest": source,
        "artifactDigest": sha256_file(output / "artifact"),
        "build": {
            "commandDigest": command_digest(build_command),
            "exitCode": build_exit,
            "sourceBefore": source,
            "sourceAfter": build_after,
            "artifactDigest": artifact_before,
        },
        "test": {
            "commandDigest": command_digest(test_command),
            "exitCode": test_exit,
            "sourceBefore": test_before,
            "sourceAfter": test_after,
            "artifactBefore": artifact_before,
            "artifactAfter": artifact_after,
        },
        "sessions": sessions,
    }
    write_json(output / "certificate.json", certificate)
    write_json(output / "hook-journal.json", journals)
    write_json(
        output / "collection.json",
        {
            "initialSourceDigest": initial_source,
            "sourceScope": "git tracked and non-ignored untracked files; paths, bytes, executable bits",
            "evidence": "ci-runner-observed; hook observations are not authenticated",
        },
    )
    return certificate


def verify_bundle(
    *,
    bundle: Path,
    checker: Path,
    run_id: str,
    base_revision: str,
    agent_command: str,
    build_command: str,
    test_command: str,
    report: Path,
) -> dict:
    if report.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("Verification report must be outside the untrusted bundle")
    certificate = read_bundle_json(bundle / "certificate.json")
    if not isinstance(certificate, dict):
        raise ValueError("Certificate must be a JSON object")
    sessions = verify_hook_export(read_bundle_json(bundle / "hook-journal.json"))
    if certificate.get("sessions") != sessions:
        raise ValueError("Certificate hook summary does not match the exported journal")
    artifact_digest = sha256_file(bundle / "artifact")
    policy = {
        "runId": run_id,
        "baseRevision": base_revision,
        "agentCommandDigest": command_digest(agent_command),
        "buildCommandDigest": command_digest(build_command),
        "testCommandDigest": command_digest(test_command),
        "artifactDigest": artifact_digest,
    }
    if not run_id or not base_revision:
        raise ValueError("Consumer run ID and base revision are required")
    report.mkdir(parents=True, exist_ok=False)
    policy_path = report / "policy.json"
    write_json(policy_path, policy)
    # Feed the exact decoded object checked above to Lean, avoiding divergent
    # interpretations of duplicate JSON keys by independent parsers.
    certificate_path = report / "certificate.json"
    write_json(certificate_path, certificate)
    result = subprocess.run(
        [str(checker.resolve()), str(certificate_path.resolve()), str(policy_path.resolve())],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Lean checker rejected the certificate (see policy.json for expectations)")
    verdict = json.loads(result.stdout)
    if verdict != {"accepted": True, "policy": "agent-release-v1"}:
        raise ValueError("Unexpected checker response")
    # Detect accidental changes while verification ran. This is not isolation
    # against a malicious concurrent writer; use a separate clean Actions job.
    if sha256_file(bundle / "artifact") != artifact_digest:
        raise ValueError("Artifact changed during verification")
    verdict.update(
        {"artifactDigest": artifact_digest, "runId": run_id, "baseRevision": base_revision}
    )
    write_json(report / "verification.json", verdict)
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run agent, build, tests and collect a candidate bundle")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--artifact", type=Path, required=True)
    run.add_argument("--timeout", type=int, default=1800)
    verify = sub.add_parser("verify", help="Check a downloaded bundle with Lean")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--checker", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    for command in (run, verify):
        command.add_argument("--run-id", required=True)
        command.add_argument("--base-revision", required=True)
        for stage in ("agent", "build", "test"):
            command.add_argument(
                f"--{stage}-command", default=os.environ.get(f"OAP_{stage.upper()}_COMMAND")
            )
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        if any(args[f"{stage}_command"] is None for stage in ("agent", "build", "test")):
            raise ValueError("Agent, build, and test commands are required")
        result = collect(**args) if command == "run" else verify_bundle(**args)
        print(canonical(result))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"open-agent-provenance CI: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
