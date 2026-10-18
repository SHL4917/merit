"""The schema gate validates raw documents before constructing the model."""

import copy
import datetime
import json
from importlib import resources

import jsonschema
import pytest
import yaml

from merit.cli import main
from merit.ingest import ingest
from merit.ingest.bundle import IMMUTABLE_META_FIELDS
from merit.model import ID_PATTERNS
from merit.scaffold import init


@pytest.fixture
def known_good(tmp_path):
    """Reproducible corpus: every source and digest comes from real ingest."""
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    origin = tmp_path / "interview.txt"
    origin.write_text("Jordan Reyes: Keep audit logs for seven years.\n", encoding="utf-8")
    result = ingest(root, origin, record_type="transcript", authored_by="Alex",
                    session_date=datetime.date(2026, 10, 18),
                    participants=[{"name": "Jordan Reyes", "role": "user"}],
                    classification="INTERNAL", ingested_by="Alex",
                    ingested_on=datetime.date(2026, 10, 18))
    data = dict(schema_version=1, id="REQ-SMP-0001", statement="Retain audit logs.",
                type="constraint", provenance="stated", status="draft",
                evidence=[dict(source=result.source, bundle_sha256=result.bundle_sha256,
                               line=1, speaker="Jordan Reyes", quote="Keep audit logs for seven years.")])
    path = root / "requirements" / "REQ-SMP-0001.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return root


def schema(kind):
    return json.loads((resources.files("merit") / "schemas" / f"{kind}.schema.json").read_text(encoding="utf-8"))


def test_known_good(known_good):
    from merit.validate.runner import run_checks
    assert run_checks(known_good) == []


@pytest.mark.parametrize("change", [dict(evidence="bad"), dict(open_issues=[]), dict(schema_version=2), dict(type="made_up")])
def test_schema_violation(known_good, change):
    from merit.validate.runner import run_checks
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(change)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    findings = run_checks(known_good)
    assert findings and {f.check_id for f in findings} == {"V3"}
    assert all(str(path) in str(f.path) and f.fix for f in findings)


def test_malformed_yaml(known_good):
    from merit.validate.runner import run_checks
    path = known_good / "issues" / "ISS-0001.yaml"
    path.write_text("id: [\n", encoding="utf-8")
    finding, = run_checks(known_good)
    assert finding.check_id == "V3" and finding.path == path
    assert "line 2" in finding.message and finding.fix


@pytest.mark.parametrize("value", ["2026-99-99", "2026-02-30", "2026-10-18T99:00:00Z"])
def test_invalid_yaml_date_constructor_is_located_v3(known_good, capsys, value):
    from merit.validate.runner import run_checks
    path = known_good / "candidates" / "CAN-0001.yaml"
    path.write_text(
        "schema_version: 1\nid: CAN-0001\nstatement: Dark mode\nrationale: Idea\n"
        f"raised_by: Alex\nraised_on: {value}\nstatus: open\n", encoding="utf-8")
    finding, = run_checks(known_good)
    assert finding.check_id == "V3" and finding.path == path
    assert "line 6, column 12" in finding.message
    assert finding.fix == f"correct {path}, then run: merit validate"
    assert main(["validate", str(known_good)]) == 1
    output = capsys.readouterr()
    assert "V3" in output.out and "line 6, column 12" in output.out
    assert "Traceback" not in output.out + output.err


def test_candidate_evidence_is_v3(known_good):
    from merit.validate.runner import run_checks
    path = known_good / "candidates" / "CAN-0001.yaml"
    data = dict(schema_version=1, id="CAN-0001", statement="Dark mode", rationale="Idea",
                raised_by="Alex", raised_on="2026-10-18", status="open", evidence=[])
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert {f.check_id for f in run_checks(known_good)} == {"V3"}


def test_filename_mismatch_is_v3(known_good):
    from merit.validate.runner import run_checks
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    path.rename(path.with_name("REQ-SMP-0002.yaml"))
    finding, = run_checks(known_good)
    assert finding.check_id == "V3" and "file name" in finding.message
    assert finding.path.name == "REQ-SMP-0002.yaml"


def test_source_meta_contract(known_good):
    document = schema("source_meta")
    assert set(document["properties"]) == set(IMMUTABLE_META_FIELDS) | {"bundle_sha256"}
    assert document["additionalProperties"] is False
    meta = yaml.safe_load(next((known_good / "sources").glob("*.meta.yaml")).read_text(encoding="utf-8"))
    for field in ("sha256", "origin_sha256", "bundle_sha256"):
        for invalid in ("A" * 64, "a" * 63, "g" * 64, "a" * 64 + "\n"):
            broken = copy.deepcopy(meta)
            broken[field] = invalid
            from merit.validate.checks import json_data
            assert list(jsonschema.Draft202012Validator(document).iter_errors(json_data(broken)))


def test_portable_patterns_and_exact_model_ids():
    def patterns(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "pattern":
                    yield child
                yield from patterns(child)
        elif isinstance(value, list):
            for child in value:
                yield from patterns(child)
    for path in (resources.files("merit") / "schemas").iterdir():
        if path.name.endswith(".json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(doc)
            for pattern in patterns(doc):
                assert not any(token in pattern for token in ("$", r"\Z", r"\z", r"\d"))
    for kind, pattern in ID_PATTERNS.items():
        assert schema(kind)["properties"]["id"]["pattern"] == pattern
        validator = jsonschema.Draft202012Validator(schema(kind)["properties"]["id"])
        value = {"requirement": "REQ-SMP-0001", "candidate": "CAN-0001", "issue": "ISS-0001", "decision": "DEC-0001", "supersession": "SUP-0001"}[kind]
        assert validator.is_valid(value)
        assert not validator.is_valid(value + "\n")


def test_schema_rejects_trailing_newline_id():
    validator = jsonschema.Draft202012Validator(schema("requirement")["properties"]["id"])
    assert validator.is_valid("REQ-SMP-0001")
    assert not validator.is_valid("REQ-SMP-0001\n")


def test_empty_scaffold_and_missing_directories(tmp_path):
    from merit.validate.runner import run_checks
    init(tmp_path, project_id="sample", id_prefix="SMP")
    (tmp_path / "issues").rmdir()
    assert run_checks(tmp_path) == []
    (tmp_path / "sources" / "raw").rmdir()
    (tmp_path / "sources").rmdir()
    assert all(f.check_id != "V3" for f in run_checks(tmp_path))


def test_cli_validation(known_good, capsys, monkeypatch):
    from merit.validate import runner
    assert main(["validate", str(known_good)]) == 0
    monkeypatch.setattr(runner, "run_checks", lambda *a, **k: [runner.Finding("V12", known_good, "drift", "merit ingest", "advisory")])
    assert main(["validate", str(known_good)]) == 0
    monkeypatch.setattr(runner, "run_checks", lambda *a, **k: [runner.Finding("V3", known_good, "bad", "merit validate")])
    assert main(["validate", str(known_good)]) == 1
    output = capsys.readouterr().out
    assert "V3" in output and "fix:" in output


def test_registry_order_and_context(known_good, tmp_path, monkeypatch):
    from merit.validate import runner
    from merit.validate import checks
    monkeypatch.setattr(runner, "CHECKS", {"V3": checks.schema_check})
    seen = []
    for check_id in ("V12", "V2", "V1", "V10"):
        def check(context, check_id=check_id):
            assert context.artefact_root == tmp_path
            assert context.context_root == known_good
            assert context.config.id_prefix == "SMP"
            assert context.strict is True
            # V1 is raw integrity preflight; all later rules get a built model.
            assert (context.repo is None) == (check_id == "V1")
            seen.append(check_id)
            return []
        runner.register(check_id)(check)
    assert runner.run_checks(tmp_path, context_root=known_good, strict=True) == []
    assert seen == ["V1", "V2", "V10", "V12"]
    with pytest.raises(ValueError, match="duplicate"):
        runner.register("V2")(lambda context: [])
    with pytest.raises(ValueError, match="invalid"):
        runner.register("wrong")


def test_raw_validation_precedes_model(known_good, monkeypatch):
    from merit.validate import runner
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    path.write_text("schema_version: 1\nopen_issues: []\n", encoding="utf-8")
    def forbidden(*args):
        pytest.fail("the model must not see structurally invalid YAML")
    monkeypatch.setattr(runner, "load_repo", forbidden)
    findings = runner.run_checks(known_good)
    assert findings and all(f.check_id == "V3" and f.path == path for f in findings)


def test_model_validation_failure_becomes_v3(known_good, monkeypatch):
    from merit.validate import runner
    from merit.errors import ValidationFailed
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    def fail(*args):
        raise ValidationFailed(f"{path}: model rejected structure", fix="merit validate")
    monkeypatch.setattr(runner, "load_repo", fail)
    finding, = runner.run_checks(known_good)
    assert finding.check_id == "V3" and finding.path == path
    assert "model rejected" in finding.message and finding.fix


def test_context_defaults_to_artefact_root(known_good, monkeypatch):
    from merit.validate import runner
    from merit.validate import checks
    captured = []
    def capture(context):
        captured.append(context)
        return []
    monkeypatch.setattr(runner, "CHECKS", {"V3": checks.schema_check, "V99": capture})
    assert runner.run_checks(known_good) == []
    assert captured[0].context_root == known_good


@pytest.mark.parametrize("text", ["[]", "null", "a: b", "schema_version: true", "id: [x]", "a: &loop [*loop]", "1: value"])
def test_non_document_or_wrong_types_are_v3(known_good, text):
    from merit.validate.runner import run_checks
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    path.write_text(text, encoding="utf-8")
    assert {f.check_id for f in run_checks(known_good)} == {"V3"}


def test_proposals_are_not_validated(known_good):
    from merit.validate.runner import run_checks
    (known_good / "proposals" / "scratch.yaml").write_text("[bad", encoding="utf-8")
    assert run_checks(known_good) == []


def test_schema_version_required_for_all_governed_models():
    for kind in ("requirement", "candidate", "issue", "decision", "supersession", "baseline"):
        doc = schema(kind)
        assert "schema_version" in doc["required"]
        assert doc["properties"]["schema_version"] == {"const": 1, "type": "integer"}


def test_schema_enums_match_model():
    from merit.model import STORED_ENUMS
    for key, values in STORED_ENUMS.items():
        kind, field = key.split(".")
        assert schema(kind)["properties"][field]["enum"] == list(values)


def test_proposal_operation_boundary():
    validator = jsonschema.Draft202012Validator(schema("proposal"))
    valid = dict(schema_version=1, operations=[dict(op="add_candidate", statement="Dark mode", rationale="Idea")])
    assert validator.is_valid(valid)
    for extra in (dict(id="CAN-0001"), dict(evidence=[])):
        broken = copy.deepcopy(valid)
        broken["operations"][0].update(extra)
        assert not validator.is_valid(broken)
    broken = copy.deepcopy(valid)
    broken["operations"][0]["op"] = "invent_operation"
    assert not validator.is_valid(broken)


def test_invalid_utf8_is_v3(known_good):
    from merit.validate.runner import run_checks
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    path.write_bytes(bytes([255]))
    finding, = run_checks(known_good)
    assert finding.check_id == "V3" and finding.path == path
    assert "UTF-8" in finding.message and finding.fix


def test_cross_field_checks_are_not_hidden_in_schemas(known_good):
    from merit.validate.runner import run_checks
    path = known_good / "requirements" / "REQ-SMP-0001.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # V6 owns minimum evidence and inferred rationale; V10 owns acceptance criteria.
    data.update(provenance="inferred", evidence=[], status="baselined")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert all(f.check_id != "V3" for f in run_checks(known_good))


@pytest.mark.parametrize("kind", ["candidate", "issue", "decision", "supersession", "baseline", "proposal"])
def test_other_schema_shapes(kind):
    anchor = dict(source="sources/interview.md", bundle_sha256="a" * 64, line=1, speaker="Jordan", quote="Yes.")
    documents = {
        "candidate": dict(id="CAN-0001", statement="Dark mode", rationale="Idea", raised_by="Alex", raised_on="2026-10-18", status="open"),
        "issue": dict(id="ISS-0001", kind="contradiction", summary="Conflict", evidence=[anchor], affects=[], question="Which?", status="open", owner="Alex"),
        "decision": dict(id="DEC-0001", resolves=[], decided_on="2026-10-18", decided_by="Jordan", outcome="Yes", record=anchor),
        "supersession": dict(id="SUP-0001", supersedes="sources/old.md", superseded_by="sources/new.md", reason="Correction", recorded_by="Alex", recorded_on="2026-10-18"),
        "baseline": dict(id="sample-b1", prepared_on="2026-10-18", prepared_by="Alex", governed_tree_hash="a" * 64, merit_version="0.1.0", requirements=[], deferred=[], open_issues=[]),
        "proposal": dict(operations=[dict(op="add_candidate", statement="Dark mode", rationale="Idea")]),
    }
    data = dict(schema_version=1, **documents[kind])
    validator = jsonschema.Draft202012Validator(schema(kind))
    assert list(validator.iter_errors(data)) == []
    data["surprise"] = 1
    assert list(validator.iter_errors(data))
    del data["surprise"]
    del data["schema_version"]
    assert list(validator.iter_errors(data))


def test_stored_awaiting_confirmation_rejected():
    validator = jsonschema.Draft202012Validator(schema("issue")["properties"]["status"])
    assert not validator.is_valid("awaiting_confirmation")


def test_missing_decision_record_is_left_for_v9(known_good):
    from merit.validate.runner import run_checks
    from merit.model import load_repo, is_provisional
    path = known_good / "decisions" / "DEC-0001.yaml"
    data = dict(schema_version=1, id="DEC-0001", resolves=[], decided_on="2026-10-18",
                decided_by="Jordan", outcome="Yes")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert all(f.check_id != "V3" for f in run_checks(known_good))
    repo = load_repo(known_good)
    decision = repo.decisions["DEC-0001"]
    assert decision.record is None and is_provisional(decision, repo)
    from merit.model import Evidence, confirmation_defect
    source = next(iter(repo.source_meta))
    decision.confirmed_by = "Jordan Reyes"
    decision.confirmation_record = Evidence(source=source, speaker="Jordan Reyes", quote="Yes")
    assert confirmation_defect(decision, repo) is not None
    assert is_provisional(decision, repo)


@pytest.mark.parametrize("field", IMMUTABLE_META_FIELDS + ("bundle_sha256",))
def test_missing_source_integrity_metadata_is_left_for_v1(known_good, field):
    from merit.validate.runner import run_checks
    from merit.model import load_repo
    path = next((known_good / "sources").glob("*.meta.yaml"))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data[field]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert all(f.check_id != "V3" for f in run_checks(known_good))
    assert field not in next(iter(load_repo(known_good).source_meta.values()))
