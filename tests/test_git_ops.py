import os
import subprocess

import pytest

from merit.git_ops import ComparisonPoint, comparison_point, file_at, list_tags


def git(root, *args):
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False,
        encoding="utf-8", env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "core.autocrlf", "false")
    git(tmp_path, "commit", "--allow-empty", "-m", "initial")
    return tmp_path


def test_nearest_reachable_baseline_beats_main_and_unreachable_tags(repo):
    git(repo, "tag", "baseline/old")
    git(repo, "commit", "--allow-empty", "-m", "second")
    git(repo, "tag", "-a", "baseline/new", "-m", "new")
    git(repo, "switch", "-c", "other")
    git(repo, "commit", "--allow-empty", "-m", "unreachable")
    git(repo, "tag", "baseline/unreachable")
    git(repo, "switch", "main")
    git(repo, "commit", "--allow-empty", "-m", "after baseline")
    git(repo, "tag", "release/newest")
    assert comparison_point(repo) == ComparisonPoint("baseline", "baseline/new")
    assert list_tags(repo) == [
        "baseline/new", "baseline/old", "baseline/unreachable", "release/newest"
    ]


def test_lightweight_baseline_is_eligible(repo):
    git(repo, "tag", "baseline/light")
    assert comparison_point(repo) == ComparisonPoint("baseline", "baseline/light")


def test_merge_base_is_shared_ancestor_not_main_tip(repo):
    shared = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "draft")
    git(repo, "commit", "--allow-empty", "-m", "draft")
    git(repo, "switch", "main")
    git(repo, "commit", "--allow-empty", "-m", "main advance")
    git(repo, "switch", "draft")
    assert comparison_point(repo) == ComparisonPoint("merge-base", shared)


def test_orphan_ignores_unreachable_baseline(repo):
    git(repo, "tag", "baseline/main")
    git(repo, "switch", "--orphan", "orphan")
    assert comparison_point(repo) == ComparisonPoint("none", None)
    git(repo, "commit", "--allow-empty", "-m", "orphan")
    assert comparison_point(repo) == ComparisonPoint("none", None)


def test_missing_main_and_unborn_repository(tmp_path):
    git(tmp_path, "init", "-b", "draft")
    assert comparison_point(tmp_path) == ComparisonPoint("none", None)
    assert list_tags(tmp_path) == []


def test_committed_branch_without_main_has_no_comparison(repo):
    git(repo, "branch", "-m", "draft")
    assert comparison_point(repo) == ComparisonPoint("none", None)
    git(repo, "tag", "baseline/draft")
    assert comparison_point(repo) == ComparisonPoint("baseline", "baseline/draft")


def test_failed_tag_lookup_still_attempts_merge_base(repo, monkeypatch):
    expected = git(repo, "rev-parse", "HEAD")
    original_run = subprocess.run

    def fail_describe(command, **kwargs):
        assert kwargs["check"] is False
        if command[1] == "describe":
            return subprocess.CompletedProcess(command, 128, b"", b"failure")
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_describe)
    assert comparison_point(repo) == ComparisonPoint("merge-base", expected)


def test_file_at_preserves_content_and_does_not_read_worktree(repo):
    path = repo / "space \u00e9.txt"
    content = "\ufeffcaf\u00e9\r\nsecond line\n\n"
    path.write_bytes(content.encode("utf-8"))
    git(repo, "add", "--", path.name)
    git(repo, "commit", "-m", "content")
    path.write_text("changed", encoding="utf-8")
    assert file_at(repo, "HEAD", path.name) == content
    assert file_at(repo, "HEAD", "absent.txt") is None
    assert file_at(repo, "not-a-revision", path.name) is None


def test_empty_file_is_not_missing(repo):
    (repo / "empty").write_text("", encoding="utf-8")
    git(repo, "add", "empty")
    git(repo, "commit", "-m", "empty")
    assert file_at(repo, "HEAD", "empty") == ""


def test_non_repository_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert comparison_point(tmp_path) == ComparisonPoint("none", None)
    assert list_tags(tmp_path) == []
    assert file_at(tmp_path, "HEAD", "anything") is None


def test_missing_git_degrades(repo, monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert comparison_point(repo) == ComparisonPoint("none", None)
    assert list_tags(repo) == []
    assert file_at(repo, "HEAD", "anything") is None
