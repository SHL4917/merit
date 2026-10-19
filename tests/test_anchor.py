"""Exact textual anchoring and the V5 boundary."""

import dataclasses
import datetime

import pytest

from merit.errors import AnchorError
from merit.ingest import ingest
from merit.ingest.canonical import CanonicalLine
from merit.model import Evidence, Requirement, save_artefact
from merit.scaffold import init
from merit.validate.runner import run_checks


def test_whitespace_only_and_confusable():
    from merit.anchor import find_quote
    lines = [CanonicalLine(1, "Sam", "It's ready.")]
    assert find_quote(lines, "  It's\tready. ").line == 1
    with pytest.raises(AnchorError, match="U\\+2019"):
        find_quote(lines, "It" + chr(0x2019) + "s ready.")


@pytest.mark.parametrize("quote", ["it's ready.", "Its ready.", "It is ready.",
                                  "It's" + chr(0x200B) + " ready."])
def test_no_other_normalisation(quote):
    from merit.anchor import find_quote
    with pytest.raises(AnchorError, match="closest"):
        find_quote([CanonicalLine(1, "Sam", "It's ready.")], quote)


def test_spans_and_ambiguity():
    from merit.anchor import find_quote
    lines = [CanonicalLine(1, "Sam", "First."), CanonicalLine(2, "Sam", "Second.")]
    match = find_quote(lines, "First.\nSecond.")
    assert (match.line, match.end_line, match.speaker) == (1, 2, "Sam")
    for speaker, message in [("Lee", "speaker.*2"), ("#", "structural.*2")]:
        with pytest.raises(AnchorError, match=message):
            find_quote([lines[0], CanonicalLine(2, speaker, "Second.")], "First. Second.")
    with pytest.raises(AnchorError, match="ambiguous.*1.*2"):
        find_quote([lines[0], CanonicalLine(2, "Sam", "First.")], "First.")


def test_unicode_whitespace_and_forms():
    from merit.anchor import find_quote
    assert find_quote([CanonicalLine(1, "Sam", "Two words")],
                      "Two" + chr(0xA0) + chr(0x2028) + "words").line == 1
    with pytest.raises(AnchorError):
        find_quote([CanonicalLine(1, "Sam", chr(0xE9))], "e" + chr(0x301))


def test_canonical_numbers_not_offsets():
    from merit.anchor import find_quote
    assert find_quote([CanonicalLine(42, "Sam", "Hello")], "Hello").line == 42
    with pytest.raises(AnchorError, match="not unique"):
        find_quote([CanonicalLine(42, "Sam", "Hello"), CanonicalLine(42, "Sam", "Other")], "Hello")


@pytest.mark.parametrize("invalid", [
    [CanonicalLine(2, "#", "First Second")],
    [CanonicalLine(2, "Lee", "First"), CanonicalLine(3, "Sam", "Second")],
])
def test_unique_legal_match_ignores_illegal_candidates(invalid):
    from merit.anchor import find_quote
    match = find_quote([CanonicalLine(1, "Sam", "First Second"), *invalid], "First Second")
    assert (match.line, match.end_line, match.speaker) == (1, 1, "Sam")


@pytest.fixture
def anchored_repo(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    origin = tmp_path / "interview.txt"
    origin.write_text("Sam: Keep audit logs.\nSam: For seven years.\n", encoding="utf-8")
    result = ingest(root, origin, record_type="transcript", authored_by="Alex",
                    session_date=datetime.date(2026, 10, 18), participants=[],
                    classification="INTERNAL", ingested_by="Alex")
    return root, Evidence(source=result.source, quote="Keep audit logs.")


def test_anchor_idempotent_and_reverify(anchored_repo):
    from merit.anchor import anchor_evidence
    root, evidence = anchored_repo
    result = anchor_evidence(evidence, root)
    assert result.line == 1 and result.speaker == "Sam" and result.bundle_sha256
    assert anchor_evidence(result, root) == result
    for change in [dict(line=2), dict(end_line=2), dict(speaker="Lee"),
                   dict(bundle_sha256="0" * 64)]:
        with pytest.raises(AnchorError):
            anchor_evidence(dataclasses.replace(result, **change), root)


@pytest.mark.parametrize("change", [dict(quote="Invented."), dict(quote="Retain logs."),
                                   dict(speaker="Lee"), dict(bundle_sha256="0" * 64),
                                   dict(line=2), dict(end_line=2)])
def test_v5_located_failures(anchored_repo, change):
    from merit.anchor import anchor_evidence
    from merit.cli import main
    root, evidence = anchored_repo
    evidence = dataclasses.replace(anchor_evidence(evidence, root), **change)
    req = Requirement(schema_version=1, id="REQ-SMP-0001", statement="Keep logs.",
                      type="functional", provenance="stated", status="draft", evidence=[evidence])
    path = root / "requirements" / (req.id + ".yaml")
    save_artefact(req, path)
    findings = run_checks(root)
    assert {f.check_id for f in findings} == {"V5"}
    assert all(f.path == path and "evidence[0]" in f.message and f.fix for f in findings)
    assert main(["validate", str(root)]) == 1


@pytest.mark.parametrize("source", ["../secret.md", "sources/../secret.md", "/secret.md",
                                    "sources\\secret.md", "sources/C:secret.md"])
def test_unsafe_source(anchored_repo, source):
    from merit.anchor import anchor_evidence
    root, evidence = anchored_repo
    with pytest.raises(AnchorError):
        anchor_evidence(dataclasses.replace(evidence, source=source), root)


def test_anchor_command_atomic_and_exit_codes(anchored_repo):
    from merit.cli import main
    from merit.model import load_artefact
    root, evidence = anchored_repo
    req = Requirement(schema_version=1, id="REQ-SMP-0001", statement="Keep logs.",
                      type="functional", provenance="stated", status="draft", evidence=[evidence])
    path = root / "requirements" / (req.id + ".yaml")
    save_artefact(req, path)
    assert main(["anchor", str(path)]) == 0
    before = path.read_bytes()
    assert main(["anchor", str(path)]) == 0 and path.read_bytes() == before
    assert run_checks(root) == []
    req = load_artefact(path, "requirement")
    req.evidence.append(dataclasses.replace(evidence, quote="Invented"))
    save_artefact(req, path)
    before = path.read_bytes()
    assert main(["anchor", str(path)]) == 3
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["record", "confirmation_record"])
def test_decision_evidence_is_v5(anchored_repo, field):
    from merit.anchor import anchor_evidence
    from merit.model import Decision
    root, evidence = anchored_repo
    good = anchor_evidence(evidence, root)
    decision = Decision(schema_version=1, id="DEC-0001", resolves=[],
                        decided_on="2026-10-18", decided_by="Alex", outcome="Keep logs.",
                        record=good)
    setattr(decision, field, dataclasses.replace(good, quote="Invented"))
    path = root / "decisions" / "DEC-0001.yaml"
    save_artefact(decision, path)
    findings = run_checks(root)
    assert {f.check_id for f in findings} == {"V5", "V9"}
    finding, = [f for f in findings if f.check_id == "V5"]
    assert finding.check_id == "V5" and finding.path == path and field in finding.message


def test_issue_evidence_is_v5(anchored_repo):
    from merit.anchor import anchor_evidence
    from merit.model import Issue
    root, evidence = anchored_repo
    good = anchor_evidence(evidence, root)
    requirement = Requirement(schema_version=1, id="REQ-SMP-0001", statement="Keep logs.",
                              type="functional", provenance="stated", status="draft", evidence=[good])
    save_artefact(requirement, root / "requirements" / (requirement.id + ".yaml"))
    issue = Issue(schema_version=1, id="ISS-0001", kind="contradiction", summary="Which?",
                  evidence=[good, dataclasses.replace(good, speaker="Lee")],
                  affects=[requirement.id], question="Which?", status="open", owner="Alex")
    path = root / "issues" / "ISS-0001.yaml"
    save_artefact(issue, path)
    finding, = run_checks(root)
    assert finding.check_id == "V5" and finding.path == path and "evidence[1]" in finding.message


@pytest.mark.parametrize("speaker,reason", [("Lee", "speaker change at line 2"),
                                         ("#", "structural line 2")])
def test_forbidden_spans_fail_v5(anchored_repo, speaker, reason):
    import yaml
    from merit.ingest.bundle import bundle_sha256
    from merit.ingest.canonical import render_canonical, sha256_text
    root, evidence = anchored_repo
    canonical = root / evidence.source
    text = render_canonical([CanonicalLine(1, "Sam", "Keep audit logs."),
                             CanonicalLine(2, speaker, "For seven years.")])
    canonical.write_text(text, encoding="utf-8")
    sidecar = canonical.with_suffix(".meta.yaml")
    meta = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    meta["sha256"] = sha256_text(text)
    meta["bundle_sha256"] = bundle_sha256(meta)
    # Different extractor makes re-extraction advisory, isolating V5's error.
    meta["extractor_version"] = "0.0.0"
    meta["bundle_sha256"] = bundle_sha256(meta)
    sidecar.write_text(yaml.safe_dump(meta), encoding="utf-8")
    evidence = dataclasses.replace(evidence, quote="Keep audit logs. For seven years.",
                                   line=1, end_line=2, speaker="Sam",
                                   bundle_sha256=meta["bundle_sha256"])
    req = Requirement(schema_version=1, id="REQ-SMP-0001", statement="Keep logs.",
                      type="functional", provenance="stated", status="draft", evidence=[evidence])
    save_artefact(req, root / "requirements" / (req.id + ".yaml"))
    errors = [f for f in run_checks(root) if f.severity == "error"]
    assert len(errors) == 1 and errors[0].check_id == "V5" and reason in errors[0].message


@pytest.mark.parametrize("mode", ["anchor", "validate"])
def test_empty_preanchored_quote_fails_anchor_and_v5(anchored_repo, mode):
    import yaml
    from merit.anchor import anchor_evidence
    from merit.ingest.bundle import bundle_sha256
    from merit.ingest.canonical import render_canonical, sha256_text
    root, evidence = anchored_repo
    canonical = root / evidence.source
    text = render_canonical([CanonicalLine(1, "Sam", "")])
    canonical.write_text(text, encoding="utf-8")
    sidecar = canonical.with_suffix(".meta.yaml")
    meta = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    meta.update(sha256=sha256_text(text), extractor_version="0.0.0")
    meta["bundle_sha256"] = bundle_sha256(meta)
    sidecar.write_text(yaml.safe_dump(meta), encoding="utf-8")
    evidence = dataclasses.replace(evidence, quote=" \t ", line=1, speaker="Sam",
                                   bundle_sha256=meta["bundle_sha256"])
    if mode == "anchor":
        with pytest.raises(AnchorError, match="empty quote"):
            anchor_evidence(evidence, root)
        return
    req = Requirement(schema_version=1, id="REQ-SMP-0001", statement="Keep logs.",
                      type="functional", provenance="stated", status="draft", evidence=[evidence])
    path = root / "requirements" / (req.id + ".yaml")
    save_artefact(req, path)
    errors = [f for f in run_checks(root) if f.severity == "error"]
    assert len(errors) == 1 and errors[0].check_id == "V5"
    assert errors[0].path == path and "empty quote" in errors[0].message


# ------------------------------------------- diagnostic misses (POC friction fix 6)

def test_a_fragment_quote_names_the_whole_line_to_quote():
    from merit.anchor import find_quote
    lines = [CanonicalLine(1, "Sam", "Um yes it is a design a new remote control um as you can see"),
             CanonicalLine(2, "Lee", "Uh-huh")]
    with pytest.raises(AnchorError, match=r"part of line 1;.*whole line: 'Um yes it is a design") as excinfo:
        find_quote(lines, "a design a new remote control")
    assert "closest line" in str(excinfo.value)


def test_a_fragment_of_a_structural_line_says_it_cannot_be_cited():
    from merit.anchor import find_quote
    lines = [CanonicalLine(1, "#", "[0046] FEE005 (PM): a new remote control")]
    with pytest.raises(AnchorError, match="no attributed lines.*part of line 1.*structural"):
        find_quote(lines, "a new remote control")


def test_a_miss_against_a_normal_source_keeps_the_plain_diagnostic():
    from merit.anchor import find_quote
    with pytest.raises(AnchorError) as excinfo:
        find_quote([CanonicalLine(1, "Sam", "It's ready.")], "Nothing like it")
    assert "part of line" not in str(excinfo.value) and "no attributed" not in str(excinfo.value)
