"""Local checkpoints, mocked publishing, and truthful PR summaries."""

import ast
import inspect
import subprocess

import pytest

from merit import git_ops
from merit.baseline import baseline
from merit.cli import main
from merit.errors import EnvironmentError_, UsageError, ValidationFailed
from merit.handoff import handoff_block
from merit.model import Candidate, save_artefact
from test_checks_issues import project, save
from test_git_ops import git, repo

ACCENTED_E = chr(0x00E9)


def operation(name):
    result = getattr(git_ops, name, None)
    assert callable(result), f"Task21 must implement {name}"
    return result


def test_start_draft_creates_and_checks_out_branch(repo):
    operation("start_draft")(repo, "draft/audit")
    assert git(repo, "branch", "--show-current") == "draft/audit"


def test_save_excludes_unrelated_staged_and_untracked_files(repo):
    selected = f"space {ACCENTED_E} [1].txt"
    for name in [selected, "other.txt", "untracked.txt"]:
        (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "other.txt")
    operation("save")(repo, [selected], f"Save caf{ACCENTED_E}; $(nothing)")
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == '"space \\303\\251 [1].txt"'
    assert git(repo, "diff", "--cached", "--name-only") == "other.txt"
    assert git(repo, "log", "-1", "--format=%s") == f"Save caf{ACCENTED_E}; $(nothing)"


def test_save_handles_tracked_deletion_and_literal_magic(repo):
    names = ["delete.txt", "[ab].txt", "a.txt"]
    for name in names:
        (repo / name).write_text("before", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "files")
    (repo / names[0]).unlink()
    for name in names[1:]:
        (repo / name).write_text("after", encoding="utf-8")
    operation("save")(repo, names[:2], "Delete and edit")
    assert git(repo, "show", "HEAD:a.txt") == "before"
    assert git(repo, "show", "HEAD:[ab].txt") == "after"
    assert git_ops.file_at(repo, "HEAD", "delete.txt") is None


def test_cli_draft_and_save_resolve_files_from_invocation_directory(repo, monkeypatch, capsys):
    (repo / "merit.toml").write_text("", encoding="utf-8")
    directory = repo / "nested"
    directory.mkdir()
    (directory / "chosen.txt").write_text("chosen", encoding="utf-8")
    monkeypatch.chdir(directory)
    assert main(["start-draft", "draft/cli"]) == 0
    assert main(["save", "chosen.txt", "--message", "CLI checkpoint"]) == 0
    assert git(repo, "branch", "--show-current") == "draft/cli"
    assert git(repo, "show", "HEAD:nested/chosen.txt") == "chosen"
    assert "CLI checkpoint" in capsys.readouterr().out


@pytest.mark.parametrize("paths", [[], ["."], ["../outside.txt"], [".git/config"], [":(glob)*"]])
def test_save_rejects_broad_or_external_paths_before_staging(repo, paths):
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises((UsageError, EnvironmentError_)):
        operation("save")(repo, paths, "Unsafe")
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("name", ["-bad", "@{-1}", "bad name"])
def test_start_draft_rejects_unsafe_branch(repo, name):
    with pytest.raises((UsageError, EnvironmentError_)):
        operation("start_draft")(repo, name)
    assert git(repo, "branch", "--show-current") == "main"


def test_push_uses_current_branch_and_argv(repo, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "pushed\n", "")

    monkeypatch.setattr(git_ops.subprocess, "run", run)
    operation("push")(repo)
    assert calls[0][0] == ["git", "push", "--set-upstream", "--", "origin", "HEAD"]
    assert calls[0][1]["cwd"] == repo
    assert not calls[0][1].get("shell", False)


def test_open_pr_passes_body_via_utf8_stdin(repo, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "https://github.com/org/repo/pull/1\n", "")

    monkeypatch.setattr(git_ops.subprocess, "run", run)
    monkeypatch.setattr(git_ops, "pr_body", lambda root: f"caf{ACCENTED_E}\n$(echo secret)\n")
    assert operation("open_pr")(repo, "--web; $(bad)", base="main") == "https://github.com/org/repo/pull/1"
    args, kwargs = calls[0]
    assert args == ["gh", "pr", "create", "--title=--web; $(bad)", "--body-file", "-", "--base=main"]
    assert kwargs["input"] == f"caf{ACCENTED_E}\n$(echo secret)\n"
    assert kwargs["encoding"] == "utf-8" and not kwargs.get("shell", False)


@pytest.mark.parametrize("action", ["start-draft", "save", "push", "open-pr"])
@pytest.mark.parametrize("failure", ["missing", "failed", "os"])
def test_command_environment_failures_exit4(repo, monkeypatch, capsys, action, failure):
    # A marker lets the CLI locate this test repo without needing governed data.
    (repo / "merit.toml").write_text("", encoding="utf-8")
    (repo / "chosen.txt").write_text("chosen", encoding="utf-8")
    monkeypatch.chdir(repo)
    if action == "open-pr":
        monkeypatch.setattr(git_ops, "pr_body", lambda root: "body")

    def run(args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError(args[0])
        if failure == "os":
            raise OSError("forced process failure")
        return subprocess.CompletedProcess(args, 1, "", "forced command failure")

    monkeypatch.setattr(git_ops.subprocess, "run", run)
    argv = {"start-draft": ["draft/test"], "save": ["chosen.txt", "-m", "checkpoint"],
            "push": [], "open-pr": ["--title", "review"]}[action]
    assert main([action, *argv]) == 4
    error = capsys.readouterr().err
    assert "error:" in error and "fix:" in error
    if failure == "missing":
        hint = "winget install --id GitHub.cli -e" if action == "open-pr" else "winget install --id Git.Git -e"
        assert f"fix: {hint}\n" in error


def test_pr_body_status_provenance_and_separate_candidates(project, capsys):
    root, req, issue, decision, _ = project
    for number, status in enumerate(["draft", "clarifying", "baselined", "deferred", "withdrawn"], 1):
        data = {**req, "id": f"REQ-SMP-{number:04d}", "status": status}
        if status == "deferred":
            data["deferred_reason"] = "Later"
        if status == "withdrawn":
            data["withdrawn_reason"] = "Replaced"
        if number % 2 == 0:
            data.update(provenance="inferred", rationale="Inferred from evidence")
        save(root, "requirements", data)
    save_artefact(Candidate(schema_version=1, id="CAN-0001", statement="Idea", rationale="Ungrounded",
                            raised_by="Alex", raised_on="2026-10-18", status="open"),
                  root / "candidates" / "CAN-0001.yaml")
    issue.update(status="resolved", resolved_by=decision["id"])
    save(root, "issues", issue)
    save(root, "decisions", decision)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    text = operation("pr_body")(root)
    assert "Requirements: 5" in text
    for status in ["draft", "clarifying", "baselined", "deferred", "withdrawn"]:
        assert f"| {status} | 1 |" in text
    assert "| stated | 3 |" in text and "| inferred | 2 |" in text
    assert "Candidates (not requirements): 1" in text
    assert "| awaiting_confirmation | 1 |" in text and "| resolved | 0 |" in text
    assert "Baseline tag:" not in text
    assert main(["pr-body", str(root)]) == 0
    assert capsys.readouterr().out == text
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_pr_body_refuses_structural_errors_without_false_counts(project, capsys):
    root, *_ = project
    (root / "issues" / "bad.yaml").write_text("[", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        operation("pr_body")(root)
    assert main(["pr-body", str(root)]) == 1
    output = capsys.readouterr()
    assert "structurally invalid" in output.err and "fix: merit validate" in output.err
    assert output.out == ""


def test_pr_body_handoff_requires_prepared_current_ready_manifest(project):
    root, req, issue, decision, confirmation = project
    body = operation("pr_body")
    req["status"] = "baselined"
    save(root, "requirements", req)
    assert "Baseline tag:" not in body(root)
    manifest = baseline(root, prepared_by="Alex").manifest
    assert handoff_block(manifest.id) in body(root)
    issue.update(status="resolved", resolved_by=decision["id"])
    save(root, "issues", issue)
    save(root, "decisions", decision)
    assert "Baseline tag:" not in body(root)
    decision.update(confirmed_by="Jordan", confirmation_record=confirmation)
    save(root, "decisions", decision)
    # Readiness alone cannot advertise the earlier manifest's stale snapshot.
    assert "Baseline tag:" not in body(root)
    latest = baseline(root, prepared_by="Alex").manifest
    assert handoff_block(latest.id) in body(root)
    req["status"] = "draft"
    save(root, "requirements", req)
    assert "Baseline tag:" not in body(root)


def test_module_never_invokes_forbidden_git_actions():
    tree = ast.parse(inspect.getsource(git_ops))
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            words = [item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if words and words[0] in ("git", "gh"):
                assert "merge" not in words and "tag" not in words
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_git", "_run"):
            words = [item.value for item in node.args if isinstance(item, ast.Constant)]
            assert "merge" not in words and "tag" not in words
