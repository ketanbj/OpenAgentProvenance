import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oap.capture import capture
from oap.ci import (
    collect,
    command_digest,
    hook_evidence,
    sha256_file,
    source_snapshot,
    verify_bundle,
)
from oap.service import begin_session
from oap.store import Store

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = {
    "agent_command": 'python3 "$OAP_ACTION_ROOT/examples/ci_agent.py"',
    "build_command": 'python3 "$OAP_ACTION_ROOT/examples/ci_build.py"',
    "test_command": 'python3 "$OAP_ACTION_ROOT/examples/ci_test.py"',
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("approved starting source\n")
    (workspace / ".gitignore").write_text("__pycache__/\n*.pyc\nignored/\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    monkeypatch.setenv("OAP_ACTION_ROOT", str(ROOT))
    return {
        "workspace": workspace,
        "output": tmp_path / "bundle",
        "artifact": tmp_path / "build" / "release.zip",
        "run_id": "123:1",
        "base_revision": revision,
        **COMMANDS,
    }


@pytest.fixture
def checker():
    configured = os.environ.get("OAP_LEAN_CHECKER")
    path = Path(configured) if configured else ROOT / "formal/.lake/build/bin/provenance-checker"
    if configured and not path.is_file():
        pytest.fail("OAP_LEAN_CHECKER points to a missing executable")
    if not path.is_file():
        pytest.skip("Build formal/ with lake, or set OAP_LEAN_CHECKER")
    return path


@pytest.fixture
def certificate(project):
    return collect(**project)


def policy_for(project, certificate):
    return {
        "runId": project["run_id"],
        "baseRevision": project["base_revision"],
        "agentCommandDigest": command_digest(project["agent_command"]),
        "buildCommandDigest": command_digest(project["build_command"]),
        "testCommandDigest": command_digest(project["test_command"]),
        "artifactDigest": certificate["artifactDigest"],
    }


def check_certificate(checker, tmp_path, certificate, policy):
    cert_file, policy_file = tmp_path / "cert.json", tmp_path / "policy.json"
    cert_file.write_text(json.dumps(certificate))
    policy_file.write_text(json.dumps(policy))
    return subprocess.run(
        [str(checker), str(cert_file), str(policy_file)], capture_output=True, text=True
    )


def test_real_hooks_feed_compiled_lean_and_independent_verifier(project, certificate, checker):
    assert certificate["sessions"][0]["calls"][0]["status"] == "succeeded"
    verdict = verify_bundle(
        bundle=project["output"],
        checker=checker,
        report=project["output"].parent / "report",
        run_id=project["run_id"],
        base_revision=project["base_revision"],
        **COMMANDS,
    )
    assert verdict["accepted"] is True
    assert verdict["artifactDigest"] == sha256_file(project["output"] / "artifact")


@pytest.mark.parametrize(
    "path,value",
    [
        (("schemaVersion",), 2),
        (("runId",), "another-run"),
        (("baseRevision",), "another-revision"),
        (("agentCommandDigest",), "another-command"),
        (("agentExitCode",), 1),
        (("sourceDigest",), "another-source"),
        (("artifactDigest",), "another-artifact"),
        (("build", "commandDigest"), "another-build"),
        (("build", "exitCode"), 1),
        (("build", "sourceBefore"), "old-source"),
        (("build", "sourceAfter"), "changed-source"),
        (("build", "artifactDigest"), "old-artifact"),
        (("test", "commandDigest"), "weaker-test-command"),
        (("test", "exitCode"), 1),
        (("test", "sourceBefore"), "old-source"),
        (("test", "sourceAfter"), "changed-source"),
        (("test", "artifactBefore"), "old-artifact"),
        (("test", "artifactAfter"), "changed-artifact"),
        (("sessions",), []),
        (("sessions", 0, "started"), False),
        (("sessions", 0, "stopped"), False),
        (("sessions", 0, "interrupted"), True),
        (("sessions", 0, "calls"), []),
        (("sessions", 0, "calls", 0, "preCount"), 0),
        (("sessions", 0, "calls", 0, "postCount"), 0),
        (("sessions", 0, "calls", 0, "postCount"), 2),
        (("sessions", 0, "calls", 0, "status"), "unknown"),
    ],
)
def test_lean_rejects_invalid_evidence(project, certificate, checker, tmp_path, path, value):
    policy = policy_for(project, certificate)
    altered = copy.deepcopy(certificate)
    cursor = altered
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    result = check_certificate(checker, tmp_path, altered, policy)
    assert result.returncode == 1, result.stderr


@pytest.mark.parametrize("status", ["failed", "returned"])
def test_tool_failure_can_be_recovered_without_inventing_success(
    project,
    certificate,
    checker,
    tmp_path,
    status,
):
    certificate["sessions"][0]["calls"][0]["status"] = status
    result = check_certificate(checker, tmp_path, certificate, policy_for(project, certificate))
    assert result.returncode == 0


def test_verifier_hashes_downloaded_bytes(project, certificate, checker):
    (project["output"] / "artifact").write_bytes(b"substituted release")
    with pytest.raises(ValueError, match="rejected"):
        verify_bundle(
            bundle=project["output"],
            checker=checker,
            report=project["output"].parent / "report",
            run_id=project["run_id"],
            base_revision=project["base_revision"],
            **COMMANDS,
        )


def test_verifier_rederives_hook_summary(project, certificate, checker):
    certificate["sessions"][0]["calls"][0]["status"] = "returned"
    (project["output"] / "certificate.json").write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match="does not match"):
        verify_bundle(
            bundle=project["output"],
            checker=checker,
            report=project["output"].parent / "report",
            run_id=project["run_id"],
            base_revision=project["base_revision"],
            **COMMANDS,
        )


def test_verifier_rechecks_exported_hash_chain(project, certificate, checker):
    journal_path = project["output"] / "hook-journal.json"
    journals = json.loads(journal_path.read_text())
    journals[0]["rows"][0]["event_hash"] = "tampered"
    journal_path.write_text(json.dumps(journals))
    with pytest.raises(ValueError, match="integrity"):
        verify_bundle(
            bundle=project["output"],
            checker=checker,
            report=project["output"].parent / "report",
            run_id=project["run_id"],
            base_revision=project["base_revision"],
            **COMMANDS,
        )


def test_verifier_rejects_duplicate_json_keys(project, certificate, checker):
    path = project["output"] / "certificate.json"
    path.write_text('{"schemaVersion": 99,' + json.dumps(certificate)[1:])
    with pytest.raises(ValueError, match="Duplicate JSON"):
        verify_bundle(
            bundle=project["output"],
            checker=checker,
            report=project["output"].parent / "report",
            run_id=project["run_id"],
            base_revision=project["base_revision"],
            **COMMANDS,
        )


@pytest.mark.parametrize("command", ["true", "exit 3"])
def test_runner_fails_without_runtime_evidence(project, command):
    with pytest.raises(ValueError, match="No runtime hook|Agent command failed"):
        collect(**{**project, "agent_command": command})


def test_manual_mcp_session_does_not_count_as_hooks(tmp_path):
    store = Store(tmp_path / "journal")
    begin_session(store, "codex")
    assert hook_evidence(store) == ([], [])


def test_corrupted_journal_is_not_exported(tmp_path):
    store = Store(tmp_path / "journal")
    capture({"session_id": "s", "hook_event_name": "SessionStart"}, "claude", store)
    with store.connect() as db:
        db.execute("UPDATE events SET event_hash='broken'")
    with pytest.raises(ValueError, match="integrity"):
        hook_evidence(store)


def test_stop_before_last_tool_does_not_close_session(tmp_path):
    store = Store(tmp_path / "journal")
    for kind in ("SessionStart", "Stop", "PreToolUse", "PostToolUse"):
        capture(
            {"session_id": "s", "hook_event_name": kind, "tool_name": "Bash", "tool_use_id": "c"},
            "claude",
            store,
        )
    sessions, _ = hook_evidence(store)
    assert sessions[0]["stopped"] is False


@pytest.mark.parametrize(
    "test_suffix",
    [
        '\nprintf changed >> "$OAP_ARTIFACT"',
        "\nprintf changed >> source.txt",
        "\nexit 1",
    ],
)
def test_actual_test_mutations_and_failures_reach_lean(project, checker, tmp_path, test_suffix):
    project["test_command"] += test_suffix
    certificate = collect(**project)
    result = check_certificate(checker, tmp_path, certificate, policy_for(project, certificate))
    assert result.returncode == 1


def test_snapshot_covers_untracked_deletion_and_executable_bits(project):
    workspace = project["workspace"]
    original = source_snapshot(workspace)
    extra = workspace / "new-input.txt"
    extra.write_text("new input")
    added = source_snapshot(workspace)
    assert added != original
    extra.chmod(0o755)
    assert source_snapshot(workspace) != added
    extra.unlink()
    assert source_snapshot(workspace) == original
    (workspace / "source.txt").unlink()
    assert source_snapshot(workspace) != original


def test_source_symlinks_are_rejected(project):
    (project["workspace"] / "link").symlink_to("source.txt")
    with pytest.raises(ValueError, match="symlinks"):
        source_snapshot(project["workspace"])


def test_reused_output_and_wrong_revision_rejected(project):
    with pytest.raises(ValueError, match="base revision"):
        collect(**{**project, "base_revision": "wrong"})
    project["output"].mkdir()
    with pytest.raises(ValueError, match="fresh"):
        collect(**project)


def test_lean_rejects_malformed_json(checker, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schemaVersion": "not a number"}')
    result = subprocess.run([str(checker), str(bad), str(bad)], capture_output=True)
    assert result.returncode == 2


def test_ci_cli_run_and_verify(project, checker, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "plugins/open-agent-provenance")}
    for stage in ("agent", "build", "test"):
        env[f"OAP_{stage.upper()}_COMMAND"] = project[f"{stage}_command"]
    common = ["--run-id", project["run_id"], "--base-revision", project["base_revision"]]
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "oap.ci",
            "run",
            "--workspace",
            str(project["workspace"]),
            "--output",
            str(project["output"]),
            "--artifact",
            str(project["artifact"]),
            *common,
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    downloaded = tmp_path / "downloaded"
    shutil.copytree(project["output"], downloaded)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "oap.ci",
            "verify",
            "--bundle",
            str(downloaded),
            "--checker",
            str(checker),
            "--report",
            str(tmp_path / "report"),
            *common,
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["accepted"] is True
