"""Transactional writes use genuine bundles, history and fault injection."""

import copy
import datetime
import json
import re
from importlib import resources
from pathlib import Path

import pytest
import yaml

from merit.apply import apply_proposal
from merit.apply import operations, transaction
from merit.apply.proposal import parse_proposal
from merit.cli import main
from merit.errors import AnchorError, EnvironmentError_, UsageError, ValidationFailed
from merit.ingest.bundle import bundle_sha256
from merit.model import effective_status, load_repo
from merit.scaffold import init
from merit.validate.runner import CHECKS, run_checks
from test_checks_issues import project, save
from test_checks_ids import git


def proposal(root, *ops):
    path = root / "proposals" / "change.yaml"
    path.write_text(yaml.safe_dump(dict(schema_version=1, operations=list(ops))), encoding="utf-8")
    return path


def snapshot(root):
    return {p.relative_to(root): (p.read_bytes() if p.is_file() else None,
                                 p.stat().st_mtime_ns)
            for p in root.rglob("*") if ".git" not in p.parts}


def unanchored(evidence):
    return {k: evidence[k] for k in ("source", "quote")}


def requirement(project):
    _, req, _, _, _ = project
    return dict(op="add_requirement", statement="Keep logs", type="functional",
                provenance="stated", evidence=[unanchored(req["evidence"][0])])


def decision_op(project):
    return dict(op="add_decision", **{k: v for k, v in project[3].items()
                                     if k not in ("id", "schema_version", "record")},
                record=unanchored(project[3]["record"]))


def test_handlers_match_schema_exactly():
    schema = json.loads(resources.files("merit").joinpath(
        "schemas/proposal.schema.json").read_text(encoding="utf-8"))
    names = {item["properties"]["op"]["const"]
             for item in schema["properties"]["operations"]["items"]["oneOf"]}
    assert names == set(operations.HANDLERS) and len(names) == 10


def test_missing_sources_is_v1_through_apply_and_retains_proposal(tmp_path, capsys):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    (root / "sources" / "raw").rmdir()
    (root / "sources").rmdir()
    path = proposal(root, dict(op="add_candidate", statement="Idea", rationale="Explore"))
    before = snapshot(root)
    root_mtime = root.stat().st_mtime_ns
    with pytest.raises(ValidationFailed, match="operation index 0") as error:
        apply_proposal(root, path)
    assert set(re.findall(r"\bV[0-9]+\b", str(error.value))) == {"V1"}
    assert snapshot(root) == before
    assert root.stat().st_mtime_ns == root_mtime
    assert path.is_file() and not (root / "sources").exists()
    assert not (root / "candidates" / "CAN-0001.yaml").exists()
    assert main(["apply", str(path)]) == 1
    assert "V1" in capsys.readouterr().err
    assert snapshot(root) == before
    assert root.stat().st_mtime_ns == root_mtime


@pytest.mark.parametrize("change", [dict(id="REQ-SMP-0002"), dict(op="invented"),
                                    dict(evidence=None)])
def test_invalid_proposal_is_usage_and_unchanged(project, change):
    root = project[0]
    op = requirement(project)
    op.update(change)
    path = proposal(root, op)
    before = snapshot(root)
    with pytest.raises(UsageError, match="operation index 0"):
        apply_proposal(root, path)
    assert snapshot(root) == before


@pytest.mark.parametrize("text", ["[", "schema_version: 1\noperations: 2026-99-99", "null",
                                 "schema_version: 1\noperations: &ops [*ops]",
                                 "schema_version: 1\noperations: []\n42: bad"])
def test_malformed_proposal_is_usage(project, text):
    path = proposal(project[0])
    path.write_text(text, encoding="utf-8")
    with pytest.raises(UsageError):
        parse_proposal(path)


def test_success_anchors_allocates_cleans_and_runs_all_checks(project, monkeypatch):
    root = project[0]
    real = transaction.run_checks
    observed = []

    def checks(stage, *, context_root):
        assert context_root == root
        assert not (root / "requirements" / "REQ-SMP-0002.yaml").exists()
        assert (stage / "requirements" / "REQ-SMP-0002.yaml").exists()
        observed.append(True)
        findings = real(stage, context_root=context_root)
        assert set(CHECKS) == {f"V{i}" for i in range(1, 13)}
        return findings

    monkeypatch.setattr(transaction, "run_checks", checks)
    path = proposal(root, requirement(project), dict(op="add_candidate", statement="Dark mode",
                                                    rationale="Explore"))
    result = apply_proposal(root, path)
    assert observed and len(result.paths) == 2 and not path.exists()
    repo = load_repo(root)
    req = repo.requirements["REQ-SMP-0002"]
    assert req.evidence[0].line and req.evidence[0].speaker == "Jordan"
    assert req.evidence[0].bundle_sha256
    assert repo.candidates["CAN-0001"].status == "open"
    assert run_checks(root) == []


def test_rollback_does_not_consume_id(project):
    root, req, *_ = project
    req["id"] = "REQ-SMP-0007"
    (root / "requirements" / "REQ-SMP-0001.yaml").unlink()
    save(root, "requirements", req)
    bad = requirement(project)
    bad["evidence"][0]["quote"] = "Invented quotation."
    path = proposal(root, requirement(project), bad)
    before = snapshot(root)
    with pytest.raises(AnchorError, match="operation index 1"):
        apply_proposal(root, path)
    assert snapshot(root) == before
    apply_proposal(root, proposal(root, requirement(project)))
    assert "REQ-SMP-0008" in load_repo(root).requirements
    assert "REQ-SMP-0009" not in load_repo(root).requirements


@pytest.mark.parametrize("case,ids", [("requirement", {"V6"}), ("issue", {"V7"}),
                                     ("deferred", {"V6", "V10"}),
                                     ("withdrawn", {"V6"}), ("dismiss", {"V7"}),
                                     ("baseline", {"V8"})])
def test_semantic_failure_names_exact_checks_and_preserves_mtimes(project, case, ids):
    root, req, issue, *_ = project
    save(root, "issues", issue)
    if case == "requirement":
        op = requirement(project)
        op["evidence"] = []
    elif case == "issue":
        op = {k: v for k, v in issue.items() if k not in ("id", "schema_version")}
        op.update(op="add_issue", evidence=[unanchored(issue["evidence"][0])])
    elif case == "dismiss":
        op = dict(op="dismiss_issue", id=issue["id"], reason="  ")
    else:
        op = dict(op="set_status", id=req["id"], status=(
            "baselined" if case == "baseline" else case))
    path = proposal(root, op)
    before = snapshot(root)
    with pytest.raises(ValidationFailed, match="operation index 0") as error:
        apply_proposal(root, path)
    assert set(re.findall(r"\bV[0-9]+\b", str(error.value))) == ids
    assert snapshot(root) == before


def test_candidate_evidence_and_missing_confirmation_rejected(project):
    root = project[0]
    for op in (dict(op="add_candidate", statement="Idea", rationale="Maybe", evidence=[]),
               dict(op="confirm_decision", id="DEC-0001", confirmed_by="Jordan")):
        path = proposal(root, op)
        before = snapshot(root)
        with pytest.raises(UsageError, match="operation index 0"):
            apply_proposal(root, path)
        assert snapshot(root) == before


def test_candidate_defaults_and_explicit_attribution(project):
    root = project[0]
    apply_proposal(root, proposal(root,
        dict(op="add_candidate", statement="Default", rationale="Maybe"),
        dict(op="add_candidate", statement="Named", rationale="Maybe",
             raised_by="Alex", raised_on="2026-09-12")))
    repo = load_repo(root)
    assert repo.candidates["CAN-0001"].raised_by == "merit"
    assert str(repo.candidates["CAN-0001"].raised_on) == datetime.date.today().isoformat()
    assert repo.candidates["CAN-0002"].raised_by == "Alex"
    assert str(repo.candidates["CAN-0002"].raised_on) == "2026-09-12"
    assert run_checks(root) == []


def test_unquoted_yaml_dates_apply_with_shared_json_normalization(project):
    root = project[0]
    path = proposal(root)
    path.write_text("schema_version: 1\noperations:\n"
                    "  - op: add_candidate\n    statement: Idea\n    rationale: Maybe\n"
                    "    raised_by: Alex\n    raised_on: 2026-10-18\n", encoding="utf-8")
    apply_proposal(root, path)
    assert str(load_repo(root).candidates["CAN-0001"].raised_on) == "2026-10-18"


def test_issue_decision_confirmation_and_baseline_lifecycle(project, capsys):
    root, req, issue, _, confirmation = project
    op = {k: v for k, v in issue.items() if k not in ("id", "schema_version")}
    op.update(op="add_issue", evidence=[unanchored(e) for e in issue["evidence"]])
    apply_proposal(root, proposal(root, op))
    path = proposal(root, decision_op(project))
    assert main(["apply", str(path)]) == 0
    assert "provisional" in capsys.readouterr().out
    repo = load_repo(root)
    assert repo.issues[issue["id"]].status == "resolved"
    assert repo.issues[issue["id"]].resolved_by == "DEC-0001"
    assert effective_status(repo.issues[issue["id"]], repo) == "awaiting_confirmation"
    apply_proposal(root, proposal(root, dict(op="confirm_decision", id="DEC-0001",
                   confirmed_by="Jordan", confirmation_record=unanchored(confirmation))))
    repo = load_repo(root)
    assert effective_status(repo.issues[issue["id"]], repo) == "resolved"
    apply_proposal(root, proposal(root, dict(op="set_status", id=req["id"], status="baselined")))
    assert run_checks(root) == []


@pytest.mark.parametrize("case", ["self_note", "speaker", "own_author", "fabricated"])
def test_confirmation_failures_roll_back(project, case):
    root, _, issue, decision, confirmation = project
    save(root, "issues", issue)
    apply_proposal(root, proposal(root, decision_op(project)))
    op = dict(op="confirm_decision", id="DEC-0001", confirmed_by="Jordan",
              confirmation_record=unanchored(confirmation))
    if case == "self_note":
        op["confirmation_record"] = unanchored(decision["record"])
    elif case == "speaker":
        op["confirmed_by"] = "Nobody"
    elif case == "own_author":
        op["confirmed_by"] = "Alex"
    else:
        op["confirmation_record"]["quote"] = "Fabricated."
    path = proposal(root, op)
    before = snapshot(root)
    with pytest.raises(AnchorError if case == "fabricated" else ValidationFailed,
                       match="operation index 0" if case == "fabricated" else "V9"):
        apply_proposal(root, path)
    assert snapshot(root) == before


def test_promotion_retains_candidate_with_both_links_and_anchor(project):
    root = project[0]
    apply_proposal(root, proposal(root, dict(op="add_candidate", statement="Idea", rationale="Maybe")))
    op = requirement(project)
    op.pop("statement")
    op.update(op="promote_candidate", id="CAN-0001")
    empty = copy.deepcopy(op)
    empty["evidence"] = []
    with pytest.raises(ValidationFailed, match="V6"):
        apply_proposal(root, proposal(root, empty))
    apply_proposal(root, proposal(root, op))
    repo = load_repo(root)
    assert repo.candidates["CAN-0001"].promoted_to == "REQ-SMP-0002"
    assert repo.requirements["REQ-SMP-0002"].promoted_from == "CAN-0001"
    assert repo.requirements["REQ-SMP-0002"].evidence[0].line


def test_status_dismiss_criterion_and_append_only_supersession(project):
    root, req, issue, *_ = project
    save(root, "issues", issue)
    before = snapshot(root / "sources")
    apply_proposal(root, proposal(root,
        dict(op="dismiss_issue", id=issue["id"], reason="Different scenarios"),
        dict(op="add_acceptance_criterion", id=req["id"], criterion="Retain one year"),
        dict(op="set_status", id=req["id"], status="deferred", reason="Later release"),
        dict(op="record_supersession", supersedes=issue["evidence"][0]["source"],
             superseded_by=issue["evidence"][1]["source"], reason="Correction",
             recorded_by="Alex", recorded_on="2026-10-18")))
    repo = load_repo(root)
    assert repo.issues[issue["id"]].status == "dismissed"
    assert repo.requirements[req["id"]].deferred_reason == "Later release"
    assert repo.requirements[req["id"]].acceptance_criteria[-1] == "Retain one year"
    assert "SUP-0001" in repo.supersessions
    assert snapshot(root / "sources") == before


@pytest.mark.parametrize("case,check_id", [("source", "V2"), ("identity", "V4")])
def test_forbidden_history_mutations_fail_through_apply(project, monkeypatch, case, check_id):
    root = project[0]
    git(root, "init")
    git(root, "branch", "-M", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "History boundary")
    real = operations.HANDLERS["add_candidate"]

    def forbidden(stage, op):
        paths = real(stage, op)
        if case == "source":
            target = (stage / project[4]["source"]).with_suffix(".meta.yaml")
            data = yaml.safe_load(target.read_text(encoding="utf-8"))
            data["authored_by"] = "Changed author"
            data["bundle_sha256"] = bundle_sha256(data)
            target.write_text(yaml.safe_dump(data), encoding="utf-8")
        else:
            (stage / "requirements" / "REQ-SMP-0001.yaml").unlink()
        return paths

    monkeypatch.setitem(operations.HANDLERS, "add_candidate", forbidden)
    path = proposal(root, dict(op="add_candidate", statement="Idea", rationale="Maybe"))
    before = snapshot(root)
    with pytest.raises(ValidationFailed, match=check_id) as error:
        apply_proposal(root, path)
    assert set(re.findall(r"\bV[0-9]+\b", str(error.value))) == {check_id}
    assert snapshot(root) == before


@pytest.mark.parametrize("failure", ["requirements", "candidates", "proposal"])
def test_late_promotion_failure_restores_original_files_and_proposal(project, monkeypatch, failure):
    root = project[0]
    path = proposal(root, requirement(project), dict(op="add_candidate", statement="Idea", rationale="Maybe"))
    before = snapshot(root)
    root_mtime = root.stat().st_mtime_ns
    original = Path.replace

    def fail(self, target):
        if ((self.name == failure and self.parent.name == "stage")
                or (failure == "proposal" and self == path)):
            raise OSError("injected promotion failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(EnvironmentError_, match="operation index 1"):
        apply_proposal(root, path)
    assert snapshot(root) == before
    assert root.stat().st_mtime_ns == root_mtime
