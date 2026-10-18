"""Decision evidence, readiness and supersession contracts with real bundles."""

import dataclasses
import datetime

import pytest

from merit.cli import main
from merit.anchor import anchor_evidence
from merit.ingest import ingest
from merit.model import Evidence
from merit import model
from merit.config import load_config
from merit.validate import checks
from merit.validate.runner import CheckContext
from merit.model import effective_status, load_repo
from merit.validate.checks import baseline_blockers
from merit.validate.runner import CHECKS, run_checks
from test_checks_issues import project, save  # shared genuine-bundle fixture


def store(project):
    root, _, issue, decision, _ = project
    issue.update(status="resolved", resolved_by=decision["id"])
    save(root, "issues", issue)
    save(root, "decisions", decision)
    return root


@pytest.mark.parametrize("case,expected", [
    ("decision_without_record", {"V9"}),
    ("decision_record_hash_mismatch", {"V5", "V9"}),
    ("unknown_issue", {"V9"}),
    ("no_issues", {"V9"}),
    ("invented_confirmer_without_record", {"V9"}),
    ("confirmation_record_from_a_self_note", {"V9"}),
    ("confirmation_record_speaker_mismatch", {"V9"}),
    ("self_note_confirmed_by_its_own_author", {"V9"}),
    ("confirmation_authored_by_note_author", {"V9"}),
    ("confirmation_hash_mismatch", {"V5", "V9"}),
    ("confirmation_fabricated_quote", {"V5", "V9"}),
    ("record_unanchored", {"V3"}),
    ("confirmation_unanchored", {"V3"}),
    ("blank_decider", {"V9"}),
])
def test_invalid_decision_fixtures(project, case, expected):
    root, _, issue, decision, confirmation = project
    if case == "decision_without_record":
        del decision["record"]
    elif case == "decision_record_hash_mismatch":
        decision["record"]["bundle_sha256"] = "0" * 64
    elif case == "unknown_issue":
        decision["resolves"].append("ISS-9999")
    elif case == "no_issues":
        decision["resolves"] = []
    elif case == "record_unanchored":
        del decision["record"]["line"]
    elif case == "blank_decider":
        decision["decided_by"] = "  "
    else:
        decision["confirmed_by"] = "Jordan"
        if case != "invented_confirmer_without_record":
            decision["confirmation_record"] = confirmation
        if case == "confirmation_record_from_a_self_note":
            decision.update(confirmed_by="Alex", confirmation_record=decision["record"])
        if case == "confirmation_record_speaker_mismatch":
            decision["confirmed_by"] = "Nobody"
        if case in ("self_note_confirmed_by_its_own_author", "confirmation_authored_by_note_author"):
            # A valid third-party transcript cannot make Alex a second person.
            original = root.parent / "second-person.txt"
            speaker = "Alex" if case == "self_note_confirmed_by_its_own_author" else "Jordan"
            original.write_text(speaker + ": I confirm.\n", encoding="utf-8")
            author = "Bob" if case == "self_note_confirmed_by_its_own_author" else "Alex"
            result = ingest(root, original, record_type="transcript", authored_by=author,
                            session_date=datetime.date(2026, 10, 18), participants=[],
                            classification="INTERNAL", ingested_by="Bob")
            evidence = anchor_evidence(Evidence(source=result.source, quote="I confirm."), root)
            decision.update(confirmed_by=speaker, confirmation_record={
                k: v for k, v in dataclasses.asdict(evidence).items() if v is not None})
        if case == "confirmation_hash_mismatch":
            confirmation["bundle_sha256"] = "0" * 64
        if case == "confirmation_fabricated_quote":
            confirmation["quote"] = "Fabricated."
        if case == "confirmation_unanchored":
            del confirmation["line"]
    save(root, "issues", issue)
    save(root, "decisions", decision)
    findings = run_checks(root)
    assert {finding.check_id for finding in findings} == expected
    assert all(f.path.name == decision["id"] + ".yaml" and f.fix for f in findings)
    assert main(["validate", str(root)]) == 1


@pytest.mark.parametrize("mode", ["provisional", "transcript", "confirmed"])
def test_valid_decisions_and_derived_status(project, mode):
    root, _, issue, decision, confirmation = project
    if mode == "transcript":
        decision["record"] = confirmation
    if mode == "confirmed":
        decision.update(confirmed_by="Jordan", confirmation_record=confirmation)
    store(project)
    assert run_checks(root) == []
    repo = load_repo(root)
    assert effective_status(repo.issues[issue["id"]], repo) == (
        "awaiting_confirmation" if mode == "provisional" else "resolved")
    assert main(["validate", str(root)]) == 0


def test_confirmation_can_match_source_author_instead_of_speaker(project):
    root, _, _, decision, _ = project
    original = root.parent / "author-confirmation.txt"
    original.write_text("Sam: Confirmed for release.\n", encoding="utf-8")
    result = ingest(root, original, record_type="transcript", authored_by="Bob",
                    session_date=datetime.date(2026, 10, 18), participants=[],
                    classification="INTERNAL", ingested_by="Bob")
    evidence = anchor_evidence(Evidence(source=result.source, quote="Confirmed for release."), root)
    decision.update(confirmed_by="Bob", confirmation_record={
        k: v for k, v in dataclasses.asdict(evidence).items() if v is not None})
    store(project)
    assert run_checks(root) == []


@pytest.mark.parametrize("status,reason,criteria,ids", [
    ("deferred", None, [], {"V6", "V10"}),
    ("deferred", "  ", [], {"V6", "V10"}),
    ("deferred", "Later release", [], set()),
    ("baselined", None, [], {"V10"}),
    ("baselined", None, ["Logs retained"], set()),
    ("draft", None, [], set()),
])
def test_readiness(project, status, reason, criteria, ids):
    root, requirement, _, _, _ = project
    requirement.update(status=status, acceptance_criteria=criteria)
    if reason is not None:
        requirement["deferred_reason"] = reason
    save(root, "requirements", requirement)
    assert {f.check_id for f in run_checks(root)} == ids


@pytest.mark.parametrize("status", ["baselined", "draft", "deferred", "withdrawn", "clarifying"])
def test_supersession_record_drives_severity_and_blockers(project, status):
    root, requirement, _, _, confirmation = project
    requirement.update(status=status, acceptance_criteria=["Logs retained"],
                       deferred_reason="Later", withdrawn_reason="Replaced")
    save(root, "requirements", requirement)
    save(root, "supersessions", dict(schema_version=1, id="SUP-0001",
         supersedes=requirement["evidence"][0]["source"], superseded_by=confirmation["source"],
         reason="Correction", recorded_by="Alex", recorded_on="2026-10-18"))
    findings = run_checks(root)
    assert len(findings) == 1 and findings[0].check_id == "V11"
    assert findings[0].severity == ("error" if status == "baselined" else "advisory")
    assert baseline_blockers(root) == (findings if status == "baselined" else [])


def test_registry_complete_and_blockers_include_other_errors(project):
    root, _, _, decision, _ = project
    store(project)
    assert set(CHECKS) == {f"V{i}" for i in range(1, 13)}
    assert baseline_blockers(root) == []
    del decision["record"]
    save(root, "decisions", decision)
    assert {f.check_id for f in baseline_blockers(root)} == {"V9"}


def test_v9_uses_shared_metadata_predicate(project, monkeypatch):
    root = store(project)
    monkeypatch.setattr(model, "confirmation_defect", lambda decision, repo: "shared sentinel")
    findings = run_checks(root)
    assert len(findings) == 1 and findings[0].check_id == "V9"
    assert findings[0].message == "shared sentinel"


def test_v11_ignores_source_metadata_supersession(project):
    root, requirement, _, _, confirmation = project
    repo = load_repo(root)
    repo.source_meta[requirement["evidence"][0]["source"]]["superseded_by"] = confirmation["source"]
    context = CheckContext(root, root, load_config(root), repo=repo)
    assert checks.superseded_evidence(context) == []


def test_baseline_blockers_retains_structural_and_source_errors(project):
    root, requirement, _, _, _ = project
    requirement["extra"] = True
    save(root, "requirements", requirement)
    (root / requirement["evidence"][0]["source"]).unlink()
    assert {f.check_id for f in baseline_blockers(root)} == {"V1", "V3"}
