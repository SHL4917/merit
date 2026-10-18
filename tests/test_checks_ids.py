"""Raw identity checks survive structural gates and compare real Git history."""

import subprocess
import shutil

import pytest
import yaml

from merit.scaffold import init
from merit.git_ops import comparison_point
from merit.validate.runner import run_checks


def candidate(identity="CAN-0001"):
    return dict(schema_version=1, id=identity, statement="An idea", rationale="Explore it",
                raised_by="Alex", raised_on="2026-10-18", status="open")


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=False), encoding="utf-8")
    return path


def git(root, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                           "-c", "tag.gpgsign=false", "-c", "core.hooksPath=disabled-hooks",
                           "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
                          cwd=root, capture_output=True, check=True).stdout.decode("utf-8")


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    return root


@pytest.mark.parametrize("identity", [None, 42, [], "bad", "CAN-000" + chr(0x661),
                                     *["CAN-0001" + chr(c) for c in (10, 13, 0x2028, 0x85)]])
def test_invalid_ids_are_v4_even_with_v3(root, identity):
    write(root, "candidates/CAN-0001.yaml", candidate(identity))
    findings = run_checks(root)
    assert {"V3", "V4"} <= {f.check_id for f in findings}
    assert all(f.path.name == "CAN-0001.yaml" and f.fix for f in findings)
    assert findings[0].check_id == "V3"


def test_duplicates_not_hidden_by_model_filename_failure(root):
    write(root, "candidates/CAN-0001.yaml", candidate())
    write(root, "candidates/alias.yaml", candidate())
    assert any(f.check_id == "V4" and "duplicate" in f.message for f in run_checks(root))


@pytest.mark.parametrize("directory,identity", [
    ("requirements", "REQ-SMP-0001"), ("candidates", "CAN-0001"),
    ("issues", "ISS-0001"), ("decisions", "DEC-0001"),
    ("supersessions", "SUP-0001"),
])
def test_every_kind_rejects_duplicates_without_history(root, directory, identity):
    assert comparison_point(root).kind == "none"
    for name in (identity, "alias"):
        write(root, directory + "/" + name + ".yaml", {"id": identity})
    findings = run_checks(root)
    duplicates = [f for f in findings if f.check_id == "V4" and "duplicate" in f.message]
    assert len(duplicates) == 1
    assert duplicates[0].path.parent.name == directory
    assert identity in duplicates[0].message


def test_kind_spaces_are_independent(root):
    write(root, "candidates/CAN-0001.yaml", candidate())
    write(root, "requirements/REQ-SMP-0001.yaml", dict(
        schema_version=1, id="REQ-SMP-0001", statement="Keep logs", type="functional",
        provenance="stated", status="draft", evidence=[]))
    assert not any(f.check_id == "V4" for f in run_checks(root))


@pytest.mark.parametrize("directory,prefix", [("requirements", "REQ-SMP"), ("candidates", "CAN"),
                                              ("issues", "ISS"), ("decisions", "DEC"),
                                              ("supersessions", "SUP")])
def test_every_pattern_checked_even_with_other_structural_errors(root, directory, prefix):
    write(root, directory + "/bad.yaml", {"id": prefix + "-0001" + chr(10)})
    findings = run_checks(root)
    assert {"V3", "V4"} <= {f.check_id for f in findings}


def test_same_raw_id_in_different_kinds_is_not_a_duplicate(root):
    for directory in ("requirements", "candidates"):
        write(root, directory + "/bad.yaml", {"id": "same"})
    findings = [f for f in run_checks(root) if f.check_id == "V4"]
    assert len(findings) == 2
    assert all("invalid" in f.message and "duplicate" not in f.message for f in findings)


def test_raw_preflight_preserves_v3_then_v1_order(root):
    write(root, "candidates/bad.yaml", {"id": None})
    (root / "sources" / "raw").rmdir()
    (root / "sources").rmdir()
    findings = run_checks(root)
    assert findings[0].check_id == "V3"
    assert [f.check_id for f in findings][-2:] == ["V1", "V4"]


@pytest.mark.parametrize("boundary", ["baseline", "merge-base"])
@pytest.mark.parametrize("change", ["renumber", "delete", "move", "swap", "new"])
def test_historical_identity_and_path(root, boundary, change):
    first = write(root, "candidates/CAN-0001.yaml", candidate())
    second = write(root, "candidates/CAN-0002.yaml", candidate("CAN-0002"))
    git(root, "branch", "-M", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    if boundary == "baseline":
        git(root, "tag", "baseline/sample-b1")
    git(root, "checkout", "-b", "work")
    assert comparison_point(root).kind == boundary
    if change == "delete":
        first.unlink()
    elif change == "move":
        first.rename(first.with_name("alias.yaml"))
    elif change == "renumber":
        first.unlink()
        write(root, "candidates/CAN-0003.yaml", candidate("CAN-0003"))
    elif change == "swap":
        write(root, first.relative_to(root), candidate("CAN-0002"))
        write(root, second.relative_to(root), candidate())
    else:
        write(root, "candidates/CAN-0003.yaml", candidate("CAN-0003"))
    # Artefacts can be staged elsewhere; history must come from the real root.
    staged = root.parent / "staged"
    shutil.copytree(root, staged, ignore=shutil.ignore_patterns(".git", "merit.toml"))
    findings = run_checks(staged, context_root=root)
    assert any(f.check_id == "V4" for f in findings) == (change != "new")
    if change == "new":
        assert findings == []


def test_malformed_documents_and_missing_directories_do_not_crash(root):
    write(root, "candidates/bad.yaml", ["not a mapping"])
    (root / "requirements").rmdir()
    findings = run_checks(root)
    assert [f.check_id for f in findings] == ["V3"]
