"""V6 uses genuine source bundles and anchors, with no fabricated integrity data."""

import datetime
import dataclasses

import pytest
import yaml

from merit.anchor import anchor_evidence
from merit.ingest import ingest
from merit.model import Evidence
from merit.scaffold import init
from merit.validate.runner import run_checks


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    original = tmp_path / "interview.txt"
    original.write_text("Jordan: Keep audit logs.\n", encoding="utf-8")
    result = ingest(root, original, record_type="transcript", authored_by="Alex",
                    session_date=datetime.date(2026, 10, 18), participants=[],
                    classification="INTERNAL", ingested_by="Alex")
    evidence = dataclasses.asdict(anchor_evidence(
        Evidence(source=result.source, quote="Keep audit logs."), root))
    evidence = {key: value for key, value in evidence.items() if value is not None}
    requirement = dict(schema_version=1, id="REQ-SMP-0001", statement="Keep audit logs",
                       type="functional", provenance="stated", status="draft", evidence=[evidence])
    return root, requirement


def save(root, directory, data):
    path = root / directory / (data["id"] + ".yaml")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.parametrize("changes,field", [
    ({"evidence": []}, "evidence"),
    ({"provenance": "inferred"}, "rationale"),
    ({"provenance": "inferred", "rationale": "Reason", "evidence": []}, "evidence"),
    ({"provenance": "inferred", "rationale": "  "}, "rationale"),
    ({"status": "deferred"}, "deferred_reason"),
    ({"status": "withdrawn"}, "withdrawn_reason"),
    ({"status": "withdrawn", "withdrawn_reason": "  "}, "withdrawn_reason"),
])
def test_requirement_conditions_are_v6(project, changes, field):
    root, requirement = project
    requirement.update(changes)
    save(root, "requirements", requirement)
    findings = run_checks(root)
    expected = {"V6", "V10"} if changes.get("status") == "deferred" else {"V6"}
    assert findings and {f.check_id for f in findings} == expected
    assert any(field in f.message for f in findings)
    assert all(f.path.name == "REQ-SMP-0001.yaml" and f.fix for f in findings)


@pytest.mark.parametrize("status,reason", [("draft", None), ("baselined", None),
                                          ("deferred", "Later"), ("withdrawn", "Retired")])
@pytest.mark.parametrize("provenance", ["stated", "inferred"])
def test_valid_requirements_and_v10_boundary(project, status, reason, provenance):
    root, requirement = project
    requirement.update(status=status, provenance=provenance, rationale="Derived from the quote")
    if status == "baselined":
        requirement["acceptance_criteria"] = ["Audit logs are retained"]
    if reason:
        requirement[status + "_reason"] = reason
    save(root, "requirements", requirement)
    assert run_checks(root) == []


@pytest.mark.parametrize("status,target,reason,valid", [
    ("promoted", None, None, False), ("promoted", "REQ-SMP-9999", None, False),
    ("promoted", "REQ-SMP-0001", None, True), ("open", None, None, True),
    ("withdrawn", None, None, False), ("withdrawn", None, "  ", False),
    ("withdrawn", None, "No longer relevant", True),
])
def test_candidate_conditions(project, status, target, reason, valid):
    root, requirement = project
    save(root, "requirements", requirement)
    data = dict(schema_version=1, id="CAN-0001", statement="Idea", rationale="Explore",
                raised_by="Alex", raised_on="2026-10-18", status=status)
    if target is not None:
        data["promoted_to"] = target
    if reason is not None:
        data["withdrawn_reason"] = reason
    save(root, "candidates", data)
    findings = run_checks(root)
    assert (findings == []) == valid
    assert all(f.check_id == "V6" and f.path.name == "CAN-0001.yaml" and f.fix for f in findings)


def test_candidate_evidence_stays_v3(project):
    root, requirement = project
    data = dict(schema_version=1, id="CAN-0001", statement="Idea", rationale="Explore",
                raised_by="Alex", raised_on="2026-10-18", status="open", evidence=requirement["evidence"])
    save(root, "candidates", data)
    assert {f.check_id for f in run_checks(root)} == {"V3"}
