"""Issue rules exercise genuine immutable bundles and anchored evidence."""

import dataclasses
import datetime

import pytest
import yaml

from merit.anchor import anchor_evidence
from merit.ingest import ingest
from merit.model import Evidence
from merit.scaffold import init
from merit.validate.runner import run_checks


def save(root, directory, data):
    (root / directory / (data["id"] + ".yaml")).write_text(
        yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")

    def source(name, kind, author, quote):
        original = tmp_path / (name + ".txt")
        original.write_text(author + ": " + quote + "\n", encoding="utf-8")
        result = ingest(root, original, record_type=kind, authored_by=author,
                        session_date=datetime.date(2026, 10, 18), participants=[],
                        classification="INTERNAL", ingested_by="Alex")
        evidence = anchor_evidence(Evidence(source=result.source, quote=quote), root)
        return {k: v for k, v in dataclasses.asdict(evidence).items() if v is not None}

    first = source("first", "transcript", "Jordan", "Keep logs.")
    second = source("second", "transcript", "Sam", "Delete logs.")
    note = source("note", "self_recorded_note", "Alex", "Keep logs for a year.")
    confirmation = source("confirmation", "transcript", "Jordan", "I confirm a year.")
    req = dict(schema_version=1, id="REQ-SMP-0001", statement="Keep logs", type="functional",
               provenance="stated", status="draft", evidence=[first],
               acceptance_criteria=["Logs are retained"])
    issue = dict(schema_version=1, id="ISS-0001", kind="contradiction", summary="Log retention",
                 evidence=[first, second], affects=[req["id"]], question="How long?",
                 status="open", owner="Alex")
    decision = dict(schema_version=1, id="DEC-0001", resolves=[issue["id"]],
                    decided_on="2026-10-18", decided_by="Alex", outcome="One year", record=note)
    save(root, "requirements", req)
    return root, req, issue, decision, confirmation


@pytest.mark.parametrize("change,field", [
    ({"evidence": []}, "evidence"),
    ({"one_quote": True}, "evidence"),
    ({"affects": []}, "affects"),
    ({"affects": ["REQ-SMP-9999"]}, "affects"),
    ({"status": "resolved"}, "resolved_by"),
    ({"status": "resolved", "resolved_by": "DEC-9999"}, "resolved_by"),
    ({"status": "dismissed"}, "dismissed_reason"),
    ({"status": "dismissed", "dismissed_reason": "  "}, "dismissed_reason"),
])
def test_issue_conditions_are_v7(project, change, field):
    root, _, issue, _, _ = project
    if change.get("one_quote"):
        issue["evidence"] = issue["evidence"][:1]
    else:
        issue.update(change)
    save(root, "issues", issue)
    findings = run_checks(root)
    assert findings and {f.check_id for f in findings} == {"V7"}
    assert any(field in f.message for f in findings)
    assert all(f.path.name == "ISS-0001.yaml" and f.fix for f in findings)


@pytest.mark.parametrize("mode,blocked", [
    ("open", True), ("dismissed", False), ("provisional", True),
    ("confirmed", False), ("bare_name", True), ("self_confirmation", True),
    ("transcript", False),
])
@pytest.mark.parametrize("status", ["draft", "baselined", "clarifying"])
def test_baseline_uses_effective_status(project, mode, blocked, status):
    root, req, issue, decision, confirmation = project
    req["status"] = status
    if mode == "dismissed":
        issue.update(status="dismissed", dismissed_reason="Duplicate question")
    elif mode != "open":
        issue.update(status="resolved", resolved_by=decision["id"])
        if mode in ("confirmed", "bare_name", "self_confirmation"):
            decision["confirmed_by"] = "Jordan"
        if mode == "confirmed":
            decision["confirmation_record"] = confirmation
        if mode == "self_confirmation":
            decision.update(confirmed_by="Alex", confirmation_record=decision["record"])
        if mode == "transcript":
            decision["record"] = confirmation
        save(root, "decisions", decision)
    save(root, "requirements", req)
    save(root, "issues", issue)
    findings = run_checks(root)
    expected = blocked and status == "baselined"
    ids = {"V8"} if expected else set()
    if mode in ("bare_name", "self_confirmation"):
        ids.add("V9")
    assert {f.check_id for f in findings} == ids
    if expected:
        findings = [f for f in findings if f.check_id == "V8"]
        assert all(f.path.name == req["id"] + ".yaml" and "ISS-0001" in f.message
                   and f.fix for f in findings)
        if mode not in ("open",):
            assert "awaiting_confirmation" in findings[0].message


@pytest.mark.parametrize("pointer", [None, "DEC-9999", "DEC-0001"])
def test_dangling_or_unrelated_resolution_fails_closed(project, pointer):
    root, req, issue, decision, _ = project
    req["status"] = "baselined"
    issue.update(status="resolved")
    if pointer:
        issue["resolved_by"] = pointer
    if pointer == "DEC-0001":
        decision["resolves"] = ["ISS-9999"]
    # A decision listing the issue cannot mask a missing/wrong resolved_by.
    save(root, "decisions", decision)
    save(root, "requirements", req)
    save(root, "issues", issue)
    findings = run_checks(root)
    ids = {"V7", "V8", "V9"} if pointer == "DEC-0001" else {"V7", "V8"}
    assert {f.check_id for f in findings} == ids


def test_missing_directories_and_structural_gate(project):
    root, _, issue, _, _ = project
    (root / "issues").rmdir()
    (root / "decisions").rmdir()
    assert run_checks(root) == []
    (root / "issues").mkdir()
    issue["affects"] = None
    save(root, "issues", issue)
    assert {f.check_id for f in run_checks(root)} == {"V3"}


def test_existing_requirement_suffices_and_all_blockers_are_listed(project):
    root, req, issue, _, _ = project
    req["status"] = "baselined"
    issue["affects"].append("REQ-SMP-9999")
    save(root, "requirements", req)
    save(root, "issues", issue)
    issue["id"] = "ISS-0002"
    save(root, "issues", issue)
    findings = run_checks(root)
    assert {f.check_id for f in findings} == {"V8"}
    assert len(findings) == 1
    assert all(identity in findings[0].message for identity in ("ISS-0001", "ISS-0002"))


@pytest.mark.parametrize("affects_baseline", [False, True])
def test_only_affected_baselined_requirements_are_blocked(project, affects_baseline):
    root, req, issue, _, _ = project
    req["id"] = "REQ-SMP-0002"
    req["status"] = "baselined"
    save(root, "requirements", req)
    if affects_baseline:
        issue["affects"].append(req["id"])
    save(root, "issues", issue)
    findings = run_checks(root)
    assert {f.check_id for f in findings} == ({"V8"} if affects_baseline else set())
    if affects_baseline:
        assert len(findings) == 1 and findings[0].path.name == req["id"] + ".yaml"
