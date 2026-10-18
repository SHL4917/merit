"""The approver handoff is procedural until the merge commit exists."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from merit.handoff import handoff_block


@pytest.mark.parametrize("manifest_id", [None, "", False, 0])
@pytest.mark.parametrize("commit_sha", [None, "a" * 40])
def test_no_ready_baseline_has_no_handoff(manifest_id, commit_sha):
    assert handoff_block(manifest_id, commit_sha) == ""


def test_pre_merge_names_tag_and_explains_order_without_target():
    block = handoff_block("sample-b1")
    assert "baseline/sample-b1" in block
    assert "prepare -> commit -> review -> merge -> tag" in block.lower()
    assert "manifest" in block.lower()
    assert "pull request" in block.lower()
    assert "publish" in block.lower()
    assert "sha" not in block.lower()
    assert not re.search(r"\b[0-9a-f]{40}\b", block)
    assert "target=" not in block.lower()
    assert "target: main" not in block.lower()
    assert "releases/new" not in block


def test_post_merge_link_pins_exact_supplied_commit():
    sha = "0123456789abcdef" * 2 + "01234567"
    block = handoff_block("sample-b1", sha)
    expected = "releases/new?tag=baseline%2Fsample-b1&target=" + sha
    assert f"]({expected})" in block
    assert "baseline/sample-b1" in block
    assert "publish" in block.lower()
    assert "target: main" not in block.lower()
    assert "target=main" not in block.lower()


def test_url_encodes_tag_as_one_query_value():
    block = handoff_block("sample b1&next/#", "A" * 40)
    url = re.search(r"\]\((releases/new\?[^)]+)\)", block).group(1)
    assert url == ("releases/new?tag=baseline%2Fsample%20b1%26next%2F%23"
                   "&target=" + "A" * 40)
    assert parse_qs(urlsplit(url).query) == {
        "tag": ["baseline/sample b1&next/#"], "target": ["A" * 40]
    }


@pytest.mark.parametrize("commit_sha", [None, "a" * 40])
@pytest.mark.parametrize("manifest_id", [
    "sample`[click](https://example.invalid)",
    "sample``[click](https://example.invalid)`",
    "sample`\n\n[click](https://example.invalid)\r\n`",
])
def test_display_tag_cannot_break_out_of_code_span(manifest_id, commit_sha):
    block = handoff_block(manifest_id, commit_sha)
    # A longer delimiter keeps every attacker-controlled backtick inside code.
    # Newlines are shown visibly, so they cannot end a paragraph/code span.
    visible_id = manifest_id.replace("\r", "\\r").replace("\n", "\\n")
    fence = "`" * (max(map(len, re.findall(r"`+", manifest_id))) + 1)
    display = f"{fence} baseline/{visible_id} {fence}"
    assert block.startswith(f"Baseline tag: {display}\n\n")
    assert block.count(display) == (1 if commit_sha else 2)
    assert "\n\n[click]" not in block
    outside_code = re.sub(r"(?<!`)(`+)(?!`)(.*?)\1(?!`)", "", block)
    assert "[click]" not in outside_code
    if commit_sha:
        url = re.search(r"\]\((releases/new\?[^)]+)\)", block).group(1)
        assert parse_qs(urlsplit(url).query) == {
            "tag": ["baseline/" + manifest_id], "target": [commit_sha]
        }


def test_formatting_has_no_filesystem_or_git_side_effects(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("handoff formatting must not invoke subprocesses")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert handoff_block("sample-b1")
    assert handoff_block("sample-b1", "b" * 40)
    assert list(tmp_path.iterdir()) == []
