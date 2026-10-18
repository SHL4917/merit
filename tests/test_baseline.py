"""Baseline preparation and tag verification over real Git content."""

import os
import subprocess
import hashlib
import json

import pytest
import yaml

import merit
from merit import baseline as baseline_module, doctor as doctor_module
from merit.baseline import baseline, governed_tree_hash
from merit.cli import main
from merit.errors import EnvironmentError_, ValidationFailed
from merit.handoff import handoff_block
from merit.ingest.canonical import normalise_text, sha256_text
from merit.model import Candidate, load_repo, save_artefact
from merit.validate.runner import Finding, run_checks
from test_checks_issues import project, save


def git(root, *args):
    result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                             "-c", "core.hooksPath=", *args], cwd=root, capture_output=True,
                            encoding="utf-8", env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def ready(project):
    root, req, _, _, _ = project
    req["status"] = "baselined"
    save(root, "requirements", req)
    save(root, "requirements", {**req, "id": "REQ-SMP-0002", "status": "deferred",
                                "deferred_reason": "Awaiting interface specification."})
    save(root, "requirements", {**req, "id": "REQ-SMP-0003", "status": "withdrawn",
                                "withdrawn_reason": "No longer needed."})
    save_artefact(Candidate(schema_version=1, id="CAN-0001", statement="An idea", rationale="No evidence",
                            raised_by="Alex", raised_on="2026-10-18", status="open"),
                  root / "candidates" / "CAN-0001.yaml")
    return root


def test_dry_run_counts_and_zero_writes(ready, capsys):
    before = snapshot(ready)
    result = baseline(ready, dry_run=True, prepared_by="Alex")
    assert result.ready == ("REQ-SMP-0001",) and result.blocked == ()
    assert result.deferred == ("REQ-SMP-0002",) and result.manifest is None
    assert snapshot(ready) == before
    assert main(["baseline", str(ready), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "ready: 1" in output and "blocked: 0" in output and "deferred: 1" in output
    assert snapshot(ready) == before


@pytest.mark.parametrize("mode", ["open", "provisional"])
def test_baseline_refuses_derived_issue_blocks_even_for_drafts(project, mode, capsys):
    root, _, issue, decision, _ = project
    if mode == "provisional":
        issue.update(status="resolved", resolved_by=decision["id"])
        save(root, "decisions", decision)
    save(root, "issues", issue)
    before = snapshot(root)
    result = baseline(root, dry_run=True)
    assert result.ready == () and result.blocked == ("REQ-SMP-0001",)
    with pytest.raises(ValidationFailed) as caught:
        baseline(root)
    assert "ISS-0001" in str(caught.value) and caught.value.fix
    assert main(["baseline", str(root), "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "ready: 0" in output and "blocked: 1" in output
    if mode == "provisional":
        assert "awaiting_confirmation" in str(caught.value)
    assert snapshot(root) == before


@pytest.mark.parametrize("kind", ["source", "structure", "criterion"])
def test_baseline_refuses_errors_and_missing_readiness(project, kind):
    root, req, _, _, _ = project
    if kind == "source":
        (root / req["evidence"][0]["source"]).write_text("tampered", encoding="utf-8")
    elif kind == "structure":
        (root / "issues" / "bad.yaml").write_text("[", encoding="utf-8")
    else:
        req["status"] = "baselined"
        req["acceptance_criteria"] = []
        save(root, "requirements", req)
    before = snapshot(root)
    with pytest.raises(ValidationFailed) as caught:
        baseline(root)
    if kind == "source":
        assert "V1" in str(caught.value)
    if kind == "structure":
        assert "V3" in str(caught.value)
    if kind == "criterion":
        assert "V10" in str(caught.value)
    assert snapshot(root) == before


def test_manifest_versions_normalized_requirement_hash_candidates_and_handoff(ready, monkeypatch, capsys):
    calls = []
    real = subprocess.run

    def observe(args, *a, **kwargs):
        calls.append(args)
        assert not (args[0] == "git" and "tag" in args[1:]), "baseline must never invoke git tag"
        return real(args, *a, **kwargs)

    monkeypatch.setattr(subprocess, "run", observe)
    path = ready / "requirements" / "REQ-SMP-0001.yaml"
    path.write_bytes(normalise_text(path.read_text(encoding="utf-8")).replace("\n", "\r\n").encode())
    result = baseline(ready, prepared_by="Alex")
    manifest = result.manifest
    assert manifest.id == "sample-b1" and manifest.prepared_by == "Alex"
    assert manifest.merit_version == merit.__version__ and manifest.schema_version == 1
    assert [(r.id, r.content_sha256) for r in manifest.requirements] == [
        ("REQ-SMP-0001", sha256_text(path.read_text(encoding="utf-8")))]
    assert [(r.id, r.reason) for r in manifest.deferred] == [
        ("REQ-SMP-0002", "Awaiting interface specification.")]
    assert manifest.open_issues == []
    text = (ready / "baselines" / "sample-b1.yaml").read_text(encoding="utf-8")
    assert "target_commit" not in text and "CAN-" not in text and "REQ-SMP-0003" not in text
    assert result.handoff == handoff_block("sample-b1")
    assert manifest.governed_tree_hash == governed_tree_hash(ready, exclude="baselines/sample-b1.yaml")
    assert run_checks(ready) == []
    assert main(["baseline", str(ready), "--prepared-by", "Alex"]) == 0
    assert handoff_block("sample-b2") in capsys.readouterr().out
    assert calls


def test_advisories_do_not_block(ready, monkeypatch):
    advisory = Finding("V12", ready / "sources", "extractor drift", "merit validate", "advisory")
    monkeypatch.setattr(baseline_module, "run_checks", lambda *a, **k: [advisory])
    assert baseline(ready).manifest is not None


def test_no_eligible_requirements_refuses_empty_manifest(ready):
    (ready / "requirements" / "REQ-SMP-0001.yaml").unlink()
    with pytest.raises(ValidationFailed):
        baseline(ready)


@pytest.mark.parametrize("status", ["draft", "clarifying"])
def test_unselected_requirements_block_without_changing_status(project, status):
    root, req, _, _, _ = project
    req["status"] = status
    save(root, "requirements", req)
    before = snapshot(root)
    result = baseline(root, dry_run=True)
    assert result.blocked == (req["id"],) and not result.ready
    assert any(status in reason for reason in result.reasons)
    with pytest.raises(ValidationFailed):
        baseline(root)
    assert snapshot(root) == before


def test_conflict_confirmation_and_explicit_selection_lifecycle(project):
    root, req, issue, decision, confirmation = project
    save(root, "issues", issue)
    assert not baseline(root, dry_run=True).can_prepare
    issue.update(status="resolved", resolved_by=decision["id"])
    save(root, "issues", issue)
    save(root, "decisions", decision)
    assert not baseline(root, dry_run=True).can_prepare
    decision.update(confirmed_by="Jordan", confirmation_record=confirmation)
    save(root, "decisions", decision)
    assert not baseline(root, dry_run=True).can_prepare  # Still an unselected draft.
    req["status"] = "baselined"
    save(root, "requirements", req)
    result = baseline(root)
    assert result.manifest.requirements[0].id == req["id"]
    assert not result.manifest.open_issues


def test_open_issues_affecting_only_deferred_requirements_are_snapshotted(ready, project):
    root, _, issue, _, _ = project
    issue["affects"] = ["REQ-SMP-0002"]
    save(root, "issues", issue)
    result = baseline(ready)
    assert result.manifest.open_issues == [issue["id"]]


def test_tree_hash_format_is_deterministic_and_normalizes_text(tmp_path):
    (tmp_path / "sources" / "raw").mkdir(parents=True)
    (tmp_path / "requirements").mkdir()
    (tmp_path / "sources" / "raw" / "binary").write_bytes(bytes([255, 13, 10]))
    text = tmp_path / "requirements" / "example.yaml"
    text.write_bytes(b"a: b\r\n")
    expected = {"requirements/example.yaml": hashlib.sha256(b"a: b\n").hexdigest(),
                "sources/raw/binary": hashlib.sha256(bytes([255, 13, 10])).hexdigest()}
    digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert governed_tree_hash(tmp_path, exclude="baselines/new.yaml") == digest
    text.write_bytes(b"a: b\n")
    assert governed_tree_hash(tmp_path, exclude="baselines/new.yaml") == digest
    (tmp_path / "sources" / "raw" / "binary").write_bytes(bytes([255, 10]))
    assert governed_tree_hash(tmp_path, exclude="baselines/new.yaml") != digest


def test_promotion_rolls_back_on_failure(ready, monkeypatch):
    from pathlib import Path
    real = Path.replace
    before = snapshot(ready)

    def fail(source, target):
        if source.name == "baselines" and source.parent.name == "stage":
            raise OSError("forced promotion failure")
        return real(source, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(EnvironmentError_):
        baseline(ready)
    assert snapshot(ready) == before


def test_all_governed_files_affect_tree_hash_and_only_manifest_is_excluded(ready):
    original = governed_tree_hash(ready, exclude="baselines/sample-b1.yaml")
    for name in ("sources/raw/binary.bin", "sources/nested/extra.txt", "requirements/extra.txt",
                 "candidates/extra.txt", "issues/extra.txt", "decisions/extra.txt",
                 "supersessions/extra.txt", "baselines/old.yaml"):
        path = ready / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([255, 0, 1]) if "/raw/" in name else b"extra\n")
        assert governed_tree_hash(ready, exclude="baselines/sample-b1.yaml") != original, name
        path.unlink()
    excluded = ready / "baselines" / "sample-b1.yaml"
    excluded.write_text("excluded", encoding="utf-8")
    (ready / "proposals" / "scratch.yaml").write_text("ungoverned", encoding="utf-8")
    assert governed_tree_hash(ready, exclude="baselines/sample-b1.yaml") == original


@pytest.fixture
def prepared(ready):
    git(ready, "config", "user.name", "Test")
    git(ready, "config", "user.email", "test@example.invalid")
    git(ready, "config", "core.autocrlf", "false")
    git(ready, "add", ".")
    git(ready, "commit", "-m", "Before manifest")
    before = git(ready, "rev-parse", "HEAD")
    result = baseline(ready, prepared_by="Alex")
    git(ready, "add", ".")
    git(ready, "commit", "-m", "Prepared manifest")
    return ready, before, result


def tag_errors(root):
    return [f for f in doctor_module.doctor(root) if "baseline/" in f]


def test_doctor_missing_tag_and_manifest_not_present(prepared):
    root, before, _ = prepared
    assert any("missing tag" in f for f in tag_errors(root))
    git(root, "tag", "baseline/sample-b1", before)
    assert any("does not contain" in f and "manifest" in f for f in tag_errors(root))


@pytest.mark.parametrize("change", ["requirement", "deleted_requirement", "deleted_source", "manifest"])
def test_doctor_content_drift(prepared, change):
    root, _, _ = prepared
    if change == "requirement":
        path = root / "requirements" / "REQ-SMP-0001.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    elif change == "deleted_requirement":
        (root / "requirements" / "REQ-SMP-0001.yaml").unlink()
    elif change == "deleted_source":
        next((root / "sources" / "raw").iterdir()).unlink()
    else:
        path = root / "baselines" / "sample-b1.yaml"
        original = path.read_bytes()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["prepared_by"] = "Different approver"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "Drift after preparation")
    git(root, "tag", "baseline/sample-b1")
    if change == "manifest":
        path.write_bytes(original)
    findings = tag_errors(root)
    assert findings, change
    if change in ("requirement", "deleted_requirement"):
        assert any("content hash" in f for f in findings)
    if change != "manifest":
        assert any("governed_tree_hash" in f for f in findings)


@pytest.fixture
def tag_on_commit_with_extra_governed_files(prepared):
    root, _, _ = prepared
    from merit.anchor import anchor_evidence
    from merit.model import Evidence
    first = load_repo(root).requirements["REQ-SMP-0001"].evidence[0]
    second = anchor_evidence(Evidence(source="sources/2026-10-18-second.md", quote="Delete logs."), root)
    from dataclasses import asdict
    save(root, "issues", dict(schema_version=1, id="ISS-0001", kind="contradiction",
                              summary="A later deferred issue", evidence=[asdict(first), asdict(second)],
                              affects=["REQ-SMP-0002"], question="Which policy applies?",
                              status="open", owner="Alex"))
    assert run_checks(root) == []  # The race is real even for otherwise valid later work.
    git(root, "add", ".")
    git(root, "commit", "-m", "Unrelated governed commit")
    git(root, "tag", "baseline/sample-b1")
    return root


def test_tag_on_commit_with_extra_governed_files(tag_on_commit_with_extra_governed_files):
    findings = tag_errors(tag_on_commit_with_extra_governed_files)
    assert any("governed_tree_hash" in f for f in findings)
    assert not any("content hash" in f for f in findings)


@pytest.mark.parametrize("annotated", [False, True])
def test_doctor_verifies_tag_content_not_current_worktree(prepared, annotated):
    root, _, _ = prepared
    args = ["tag", "baseline/sample-b1"]
    if annotated:
        args += ["-a", "-m", "Approved"]
    git(root, *args)
    assert tag_errors(root) == []
    path = root / "requirements" / "REQ-SMP-0001.yaml"
    path.write_text("Uncommitted change", encoding="utf-8")
    (root / "issues" / "uncommitted.txt").write_text("Uncommitted file", encoding="utf-8")
    assert tag_errors(root) == []


def test_binary_original_round_trips_through_tag_hash(prepared):
    root, _, _ = prepared
    path = root / "sources" / "raw" / "binary.docx"
    path.write_bytes(bytes([255, 0, 13, 10, 128]))
    result = baseline(root)
    git(root, "add", ".")
    git(root, "commit", "-m", "Binary governed content")
    git(root, "tag", "baseline/" + result.manifest.id)
    assert tag_errors(root) == []
