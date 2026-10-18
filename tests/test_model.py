"""The artefact model, its byte-deterministic round trip, and the derived status.

The three derived functions (is_provisional, effective_status, derive_open_issues)
are the single source of truth for issue state: the report, `merit questions`,
`merit pr-body`, `merit baseline` and check V8 all call them and none displays a
raw `status`. They take an explicit Repo, so these tests need no filesystem.
"""

import dataclasses
import datetime
import re
import shutil

import pytest
import yaml

from merit import model
from merit.errors import MeritError, UsageError, ValidationFailed
from merit.model import (
    ID_PATTERNS,
    Baseline,
    Candidate,
    Decision,
    Evidence,
    Issue,
    Repo,
    Requirement,
    Supersession,
    confirmation_defect,
    derive_open_issues,
    dump_artefact,
    effective_status,
    is_provisional,
    load_artefact,
    load_repo,
    save_artefact,
)

NOTE = "sources/2026-09-24-note.md"
TRANSCRIPT = "sources/2026-09-12-interview.md"
EMAIL = "sources/2026-09-25-email.md"


def field_names(cls):
    return [f.name for f in dataclasses.fields(cls)]


def ev(source=TRANSCRIPT, **kw):
    kw.setdefault("quote", "We need the logs kept for seven years.")
    return Evidence(source=source, **kw)


def make_issue(id="ISS-0001", status="resolved", resolved_by="DEC-0001", affects=None, **kw):
    return Issue(
        schema_version=1,
        id=id,
        kind="contradiction",
        summary="Export scope is unclear.",
        evidence=[ev(), ev()],
        affects=["REQ-SMP-0001"] if affects is None else affects,
        question="Does it cover aggregates?",
        status=status,
        owner="alex",
        resolved_by=resolved_by,
        **kw,
    )


def make_decision(id="DEC-0001", resolves=None, record_source=NOTE, confirmed_by=None,
                  confirmation_record=None):
    return Decision(
        schema_version=1,
        id=id,
        resolves=["ISS-0001"] if resolves is None else resolves,
        decided_on=datetime.date(2026, 9, 24),
        decided_by="Jordan Reyes",
        outcome="Aggregate exports permitted.",
        record=ev(record_source),
        confirmed_by=confirmed_by,
        confirmation_record=confirmation_record,
    )


def make_requirement(id="REQ-SMP-0001", **kw):
    kw.setdefault("status", "baselined")
    kw.setdefault("evidence", [ev()])
    kw.setdefault("statement", "Audit logs shall be retained for 7 years.")
    return Requirement(
        schema_version=1,
        id=id,
        type="functional",
        provenance="stated",
        acceptance_criteria=["A 7-year-old log entry can be retrieved."],
        **kw,
    )


def make_repo(issues=(), decisions=(), requirements=(), self_note=True):
    return Repo(
        requirements={r.id: r for r in requirements},
        issues={i.id: i for i in issues},
        decisions={d.id: d for d in decisions},
        source_meta={
            NOTE: {"record_type": "self_recorded_note" if self_note else "transcript",
                   "authored_by": "alex"},
            TRANSCRIPT: {"record_type": "transcript"},
            EMAIL: {"record_type": "email", "authored_by": "Sam Ellis"},
        },
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_candidate_has_no_evidence_attribute_at_all():
    assert not hasattr(Candidate, "evidence")
    assert "evidence" not in field_names(Candidate)
    candidate = Candidate(
        schema_version=1, id="CAN-0001", statement="s", rationale="r", raised_by="alex",
        raised_on=datetime.date(2026, 10, 18), status="open",
    )
    assert not hasattr(candidate, "evidence")


def test_an_evidence_bearing_candidate_is_a_structural_error_on_load(tmp_path):
    path = tmp_path / "candidates" / "CAN-0001.yaml"
    path.parent.mkdir()
    path.write_text(
        "schema_version: 1\nid: CAN-0001\nstatement: s\nrationale: r\nraised_by: alex\n"
        "raised_on: 2026-10-18\nstatus: open\nevidence: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(path, "candidate")
    assert "evidence" in str(exc.value)
    assert "CAN-0001.yaml" in str(exc.value)


def test_provenance_has_exactly_two_values_and_excludes_proposed():
    assert model.PROVENANCES == ("stated", "inferred")
    assert "proposed" not in model.PROVENANCES


def test_requirement_has_no_stored_open_issues_field():
    assert "open_issues" not in field_names(Requirement)
    assert not hasattr(make_requirement(), "open_issues")


def test_a_requirement_file_smuggling_open_issues_is_rejected(tmp_path):
    path = tmp_path / "requirements" / "REQ-SMP-0001.yaml"
    save_artefact(make_requirement(), path)
    path.write_text(path.read_text(encoding="utf-8") + "open_issues: []\n", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(path, "requirement")
    assert "open_issues" in str(exc.value)


def test_evidence_without_line_loads_as_none(tmp_path):
    path = tmp_path / "requirements" / "REQ-SMP-0001.yaml"
    path.parent.mkdir()
    path.write_text(
        "schema_version: 1\nid: REQ-SMP-0001\nstatement: s\ntype: functional\nprovenance: stated\n"
        "status: draft\nevidence:\n  - source: sources/a.md\n    quote: kept seven years\n",
        encoding="utf-8",
    )
    requirement = load_artefact(path, "requirement")
    entry = requirement.evidence[0]
    assert entry.line is None
    assert entry.end_line is None
    assert entry.bundle_sha256 is None
    assert entry.speaker is None
    assert entry.quote == "kept seven years"
    assert requirement.acceptance_criteria == []


def test_dataclass_field_order_follows_the_spec():
    assert field_names(Requirement) == [
        "schema_version", "id", "statement", "type", "provenance", "status", "rationale", "evidence",
        "acceptance_criteria", "owner", "promoted_from", "deferred_reason", "withdrawn_reason",
    ]
    assert field_names(Evidence) == [
        "source", "bundle_sha256", "line", "end_line", "speaker", "quote",
    ]
    assert field_names(Candidate) == [
        "schema_version", "id", "statement", "rationale", "raised_by", "raised_on", "status",
        "promoted_to", "withdrawn_reason",
    ]
    assert field_names(Issue) == [
        "schema_version", "id", "kind", "summary", "evidence", "affects", "question", "status", "owner",
        "resolved_by", "dismissed_reason",
    ]
    assert field_names(Decision) == [
        "schema_version", "id", "resolves", "decided_on", "decided_by", "outcome", "record", "confirmed_by",
        "confirmation_record",
    ]
    assert field_names(Supersession) == [
        "schema_version", "id", "supersedes", "superseded_by", "reason", "recorded_by", "recorded_on",
    ]
    assert field_names(Baseline) == [
        "id", "prepared_on", "prepared_by", "governed_tree_hash", "merit_version",
        "schema_version", "requirements", "deferred", "open_issues",
    ]


def test_awaiting_confirmation_is_derived_only_never_in_a_stored_enum():
    assert model.ISSUE_STATUSES == ("open", "resolved", "dismissed")
    assert "awaiting_confirmation" in model.EFFECTIVE_STATUSES
    assert set(model.ISSUE_STATUSES) < set(model.EFFECTIVE_STATUSES)
    for name, values in model.STORED_ENUMS.items():
        assert "awaiting_confirmation" not in values, name
    assert set(model.STORED_ENUMS) >= {
        "requirement.type", "requirement.provenance", "requirement.status",
        "candidate.status", "issue.kind", "issue.status",
    }


def test_id_patterns_use_only_constructs_valid_in_python_and_ecmascript():
    # The schemas reuse these exact strings, so no Python-only anchor: \Z is a
    # SyntaxError in ECMA-262 unicode mode, and $ is lax in Python re.search.
    for kind, pattern in ID_PATTERNS.items():
        assert pattern.startswith("^"), kind
        assert pattern.endswith(r"(?![\s\S])"), kind
        assert "\\Z" not in pattern and "\\z" not in pattern, kind
        assert not pattern.endswith("$"), kind
        assert "\\d" not in pattern, kind
        assert pattern.isascii(), kind


def test_id_patterns_are_ascii_only_and_anchored_as_the_jsonschema_library_applies_them():
    # jsonschema applies `pattern` with re.search, where `$` also matches before a
    # trailing newline. Test through the library itself, not through re.fullmatch.
    import jsonschema

    assert set(ID_PATTERNS) == {"requirement", "candidate", "issue", "decision", "supersession"}
    arabic_indic_four = chr(0x0664)  # a Unicode digit that \d would accept
    good = {
        "requirement": "REQ-SMP-0007", "candidate": "CAN-0002", "issue": "ISS-0001",
        "decision": "DEC-0001", "supersession": "SUP-0001",
    }
    for kind, value in good.items():
        validator = jsonschema.Draft202012Validator({"type": "string", "pattern": ID_PATTERNS[kind]})
        assert validator.is_valid(value), kind
        assert re.search(ID_PATTERNS[kind], value), kind
        bad = [value + "\n", value + "\n\n", value + "0", value.lower(),
               value[:-1] + arabic_indic_four, " " + value, value + " ",
               value + chr(13), value + chr(0x2028), value + chr(0x85)]
        for candidate in bad:
            assert not validator.is_valid(candidate), (kind, candidate)
            assert not re.search(ID_PATTERNS[kind], candidate), (kind, candidate)

    requirement = jsonschema.Draft202012Validator(
        {"type": "string", "pattern": ID_PATTERNS["requirement"]})
    assert not requirement.is_valid("REQ-S-0001")
    assert not requirement.is_valid("REQ-SMPLESS-0001")
    assert requirement.is_valid("REQ-SM-0001")
    assert requirement.is_valid("REQ-SMPLES-0001")


# ---------------------------------------------------------------------------
# Load and save
# ---------------------------------------------------------------------------

def write_full_repo(root):
    def put(rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    put("requirements/REQ-SMP-0001.yaml",
        "schema_version: 1\nid: REQ-SMP-0001\nstatement: s\ntype: functional\nprovenance: stated\nstatus: draft\n"
        "evidence:\n  - source: sources/2026-09-24-note.md\n    quote: q\n")
    put("candidates/CAN-0001.yaml",
        "schema_version: 1\nid: CAN-0001\nstatement: s\nrationale: r\nraised_by: alex\nraised_on: 2026-10-18\n"
        "status: open\n")
    put("issues/ISS-0001.yaml",
        "schema_version: 1\nid: ISS-0001\nkind: contradiction\nsummary: s\nevidence:\n"
        "  - {source: sources/2026-09-24-note.md, quote: a}\n"
        "  - {source: sources/2026-09-24-note.md, quote: b}\n"
        "affects: [REQ-SMP-0001]\nquestion: q\nstatus: open\nowner: alex\n")
    put("decisions/DEC-0001.yaml",
        "schema_version: 1\nid: DEC-0001\nresolves: [ISS-0001]\ndecided_on: 2026-09-24\ndecided_by: Jordan\n"
        "outcome: o\nrecord: {source: sources/2026-09-24-note.md, quote: q}\n")
    put("supersessions/SUP-0001.yaml",
        "schema_version: 1\nid: SUP-0001\nsupersedes: sources/a.md\nsuperseded_by: sources/b.md\nreason: r\n"
        "recorded_by: alex\nrecorded_on: 2026-10-02\n")
    put("baselines/sample-b1.yaml",
        "id: sample-b1\nprepared_on: 2026-10-20\nprepared_by: alex\ngoverned_tree_hash: abc\n"
        "merit_version: 0.1.0\nschema_version: 1\n"
        "requirements:\n  - {id: REQ-SMP-0001, content_sha256: ff}\n"
        "deferred:\n  - {id: REQ-SMP-0002, reason: later}\nopen_issues: [ISS-0001]\n")
    put("sources/2026-09-24-note.meta.yaml",
        "source: sources/2026-09-24-note.md\nrecord_type: self_recorded_note\nauthored_by: alex\n")
    put("sources/2026-09-24-note.md", "# note\n")


def test_load_repo_collects_every_artefact_kind(tmp_path):
    write_full_repo(tmp_path)
    repo = load_repo(tmp_path)

    assert list(repo.requirements) == ["REQ-SMP-0001"]
    assert isinstance(repo.requirements["REQ-SMP-0001"], Requirement)
    assert isinstance(repo.candidates["CAN-0001"], Candidate)
    assert isinstance(repo.issues["ISS-0001"], Issue)
    assert isinstance(repo.decisions["DEC-0001"], Decision)
    assert isinstance(repo.supersessions["SUP-0001"], Supersession)
    assert isinstance(repo.baselines["sample-b1"], Baseline)
    assert repo.baselines["sample-b1"].requirements[0].content_sha256 == "ff"
    assert repo.baselines["sample-b1"].deferred[0].reason == "later"
    assert repo.source_meta["sources/2026-09-24-note.md"]["record_type"] == "self_recorded_note"
    assert repo.root == tmp_path
    # nested evidence is real Evidence, not dicts
    assert isinstance(repo.decisions["DEC-0001"].record, Evidence)
    assert isinstance(repo.issues["ISS-0001"].evidence[1], Evidence)


@pytest.mark.parametrize("missing", [
    "requirements", "candidates", "issues", "decisions", "supersessions", "baselines", "sources",
])
def test_load_repo_tolerates_a_missing_artefact_directory(tmp_path, missing):
    write_full_repo(tmp_path)
    shutil.rmtree(tmp_path / missing)
    repo = load_repo(tmp_path)  # must not raise
    assert getattr(repo, "source_meta" if missing == "sources" else missing) == {}


def test_load_repo_on_an_empty_directory_is_a_clean_empty_repo(tmp_path):
    repo = load_repo(tmp_path)
    for name in ("requirements", "candidates", "issues", "decisions", "supersessions",
                 "baselines", "source_meta"):
        assert getattr(repo, name) == {}


def test_load_repo_ignores_non_yaml_files_and_loads_in_sorted_order(tmp_path):
    write_full_repo(tmp_path)
    other = (tmp_path / "requirements" / "REQ-SMP-0001.yaml").read_text(encoding="utf-8")
    (tmp_path / "requirements" / "REQ-SMP-0002.yaml").write_text(
        other.replace("REQ-SMP-0001", "REQ-SMP-0002"), encoding="utf-8")
    (tmp_path / "requirements" / "notes.txt").write_text("not yaml: [", encoding="utf-8")
    assert list(load_repo(tmp_path).requirements) == ["REQ-SMP-0001", "REQ-SMP-0002"]


def test_a_filename_that_does_not_match_its_id_is_refused_not_silently_overwritten(tmp_path):
    write_full_repo(tmp_path)
    text = (tmp_path / "requirements" / "REQ-SMP-0001.yaml").read_text(encoding="utf-8")
    (tmp_path / "requirements" / "REQ-SMP-0009.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_repo(tmp_path)
    assert "REQ-SMP-0009.yaml" in str(exc.value)
    assert "REQ-SMP-0001" in str(exc.value)


def test_load_failures_are_meriterrors_naming_the_file_never_tracebacks(tmp_path):
    (tmp_path / "issues").mkdir()
    cases = {
        "ISS-0001.yaml": "schema_version: 1\nid: ISS-0001\nsummary: [unclosed\n",
        "ISS-0002.yaml": "- just\n- a list\n",
        "ISS-0003.yaml": "schema_version: 1\nid: ISS-0003\n",  # required fields missing
        "ISS-0004.yaml": "",
    }
    for name, text in cases.items():
        path = tmp_path / "issues" / name
        path.write_text(text, encoding="utf-8")
        with pytest.raises(MeritError) as exc:
            load_artefact(path, "issue")
        assert name in str(exc.value)
        assert exc.value.fix
    # a YAML syntax error names the line
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(tmp_path / "issues" / "ISS-0001.yaml", "issue")
    assert re.search(r"line \d+", str(exc.value))
    # missing fields are listed
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(tmp_path / "issues" / "ISS-0003.yaml", "issue")
    for missing in ("kind", "summary", "evidence", "affects", "question", "status", "owner"):
        assert missing in str(exc.value)


def test_a_non_list_where_a_list_is_required_is_a_validation_failure(tmp_path):
    path = tmp_path / "issues" / "ISS-0001.yaml"
    path.parent.mkdir()
    path.write_text(
        "schema_version: 1\nid: ISS-0001\nkind: contradiction\nsummary: s\nevidence: nope\n"
        "affects: REQ-SMP-0001\nquestion: q\nstatus: open\nowner: alex\n", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        load_artefact(path, "issue")


def test_an_unknown_kind_is_a_usage_error(tmp_path):
    with pytest.raises(UsageError):
        load_artefact(tmp_path / "x.yaml", "widget")


ALL_SAMPLES = {
    "requirement": lambda: make_requirement(rationale="why", owner="ops-lead",
                                            promoted_from="CAN-0002"),
    "candidate": lambda: Candidate(
        schema_version=1, id="CAN-0002", statement="Offer a dark theme.", rationale="Raised in passing.",
        raised_by="alex", raised_on=datetime.date(2026, 10, 18), status="promoted",
        promoted_to="REQ-SMP-0021"),
    "issue": lambda: make_issue(),
    "decision": lambda: make_decision(
        confirmed_by="Sam Ellis",
        confirmation_record=ev(EMAIL, bundle_sha256="b19d", line=4, speaker="Sam Ellis")),
    "supersession": lambda: Supersession(
        schema_version=1, id="SUP-0001", supersedes="sources/a.md", superseded_by="sources/b.md",
        reason="r",
        recorded_by="alex", recorded_on=datetime.date(2026, 10, 2)),
    "baseline": lambda: Baseline(
        id="sample-b1", prepared_on=datetime.date(2026, 10, 20), prepared_by="alex",
        governed_tree_hash="e8b1", merit_version="0.1.0", schema_version=1,
        requirements=[model.BaselineRequirement(id="REQ-SMP-0007", content_sha256="a71f")],
        deferred=[model.BaselineDeferral(id="REQ-SMP-0014", reason="Awaiting spec.")],
        open_issues=["ISS-0005"]),
}


@pytest.mark.parametrize("kind", sorted(ALL_SAMPLES))
def test_save_then_load_round_trips_every_kind_and_the_bytes_are_stable(tmp_path, kind):
    artefact = ALL_SAMPLES[kind]()
    first = tmp_path / "first" / f"{artefact.id}.yaml"
    second = tmp_path / "second" / f"{artefact.id}.yaml"

    save_artefact(artefact, first)
    loaded = load_artefact(first, kind)
    save_artefact(loaded, second)

    assert loaded == artefact
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == dump_artefact(artefact)


def test_save_uses_dataclass_field_order_never_sorted_keys():
    text = dump_artefact(make_requirement()).decode("utf-8")
    keys = re.findall(r"^([a-z_]+):", text, flags=re.M)
    assert keys == ["schema_version", "id", "statement", "type", "provenance", "status", "evidence",
                    "acceptance_criteria"]
    assert keys != sorted(keys)  # 'acceptance_criteria' sorts before 'evidence'


def test_save_omits_unset_optionals_and_never_writes_null():
    text = dump_artefact(make_requirement()).decode("utf-8")
    for absent in ("rationale", "owner", "promoted_from", "deferred_reason", "null", "None", "~"):
        assert absent not in text
    keys = set(re.findall(r"^\s*(?:- )?([a-z_]+):", text, flags=re.M))
    assert not keys & {"line", "end_line", "bundle_sha256", "speaker"}  # 'baselined' has 'line'


def test_save_keeps_non_ascii_readable_in_utf8_not_escaped():
    quote = "Jordan" + chr(0x2019) + "s caf" + chr(0xE9) + " note"
    speaker = "Jos" + chr(0xE9)
    requirement = make_requirement(evidence=[ev(quote=quote, speaker=speaker)])
    raw = dump_artefact(requirement)
    assert quote.encode("utf-8") in raw
    assert speaker.encode("utf-8") in raw
    assert b"\\u" not in raw
    assert b"\\x" not in raw
    assert yaml.safe_load(raw.decode("utf-8"))["evidence"][0]["quote"] == quote


def test_save_writes_lf_only_and_one_trailing_newline(tmp_path):
    path = tmp_path / "REQ-SMP-0001.yaml"
    save_artefact(make_requirement(), path)
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")


def test_save_emits_no_yaml_aliases_even_when_an_object_is_shared():
    shared = ev(NOTE, line=3, speaker="Jordan Reyes")
    decision = make_decision(confirmed_by="Sam Ellis", confirmation_record=shared)
    decision.record = shared
    raw = dump_artefact(decision).decode("utf-8")
    assert "&id" not in raw and "*id" not in raw


def test_save_does_not_wrap_long_quotes_across_lines():
    quote = "word " * 60 + "end"
    raw = dump_artefact(make_requirement(evidence=[ev(quote=quote)])).decode("utf-8")
    assert quote in raw


def test_dates_stay_plain_yaml_dates_and_are_not_requoted(tmp_path):
    path = tmp_path / "CAN-0002.yaml"
    save_artefact(ALL_SAMPLES["candidate"](), path)
    text = path.read_text(encoding="utf-8")
    assert "raised_on: 2026-10-18\n" in text
    assert isinstance(load_artefact(path, "candidate").raised_on, datetime.date)


def test_save_creates_the_artefact_directory_on_first_write(tmp_path):
    path = tmp_path / "requirements" / "REQ-SMP-0001.yaml"
    save_artefact(make_requirement(), path)
    assert path.is_file()


def test_save_refuses_a_non_artefact(tmp_path):
    with pytest.raises(TypeError):
        save_artefact({"id": "x"}, tmp_path / "x.yaml")


# ---------------------------------------------------------------------------
# is_provisional
# ---------------------------------------------------------------------------

def test_is_provisional_true_for_a_self_note_without_confirmation():
    repo = make_repo()
    assert is_provisional(make_decision(record_source=NOTE), repo) is True


def test_is_provisional_false_once_a_second_person_confirms_with_a_record():
    decision = make_decision(
        record_source=NOTE, confirmed_by="Sam Ellis",
        confirmation_record=ev(EMAIL, speaker="Sam Ellis"))
    assert is_provisional(decision, make_repo()) is False


def test_is_provisional_false_for_a_transcript_record_with_no_confirmation():
    assert is_provisional(make_decision(record_source=TRANSCRIPT), make_repo()) is False


def test_is_provisional_false_for_email_and_minutes_records():
    repo = make_repo()
    repo.source_meta["sources/minutes.md"] = {"record_type": "minutes"}
    assert is_provisional(make_decision(record_source=EMAIL), repo) is False
    assert is_provisional(make_decision(record_source="sources/minutes.md"), repo) is False


def test_is_provisional_reads_the_record_source_not_the_confirmation_source():
    repo = make_repo()
    assert is_provisional(make_decision(record_source=NOTE), repo) is True
    transcript_basis = make_decision(
        record_source=TRANSCRIPT, confirmed_by="Sam Ellis",
        confirmation_record=ev(NOTE, speaker="Sam Ellis"))
    assert is_provisional(transcript_basis, repo) is False


def test_a_bare_confirmed_by_name_without_a_record_does_not_confirm():
    # D20: a name is self-attestation. Without an anchored confirmation_record
    # the decision is still provisional, so typing a colleague's name clears nothing.
    decision = make_decision(record_source=NOTE, confirmed_by="Sam Ellis")
    assert is_provisional(decision, make_repo()) is True
    blank = make_decision(record_source=NOTE, confirmed_by="   ",
                          confirmation_record=ev(EMAIL))
    assert is_provisional(blank, make_repo()) is True


def test_an_unclassifiable_record_source_is_provisional_fail_closed():
    decision = make_decision(record_source="sources/does-not-exist.md")
    assert is_provisional(decision, make_repo()) is True
    no_type = make_repo()
    no_type.source_meta[NOTE] = {}
    assert is_provisional(make_decision(record_source=NOTE), no_type) is True


# ---------------------------------------------------------------------------
# effective_status
# ---------------------------------------------------------------------------

def test_effective_status_is_awaiting_confirmation_for_a_provisionally_resolved_issue():
    issue = make_issue(status="resolved", resolved_by="DEC-0001")
    repo = make_repo(issues=[issue], decisions=[make_decision(record_source=NOTE)])
    assert effective_status(issue, repo) == "awaiting_confirmation"


def test_effective_status_is_resolved_once_confirmed():
    decision = make_decision(
        record_source=NOTE, confirmed_by="Sam Ellis",
        confirmation_record=ev(EMAIL, speaker="Sam Ellis"))
    issue = make_issue()
    repo = make_repo(issues=[issue], decisions=[decision])
    assert effective_status(issue, repo) == "resolved"


def test_effective_status_is_resolved_for_a_transcript_backed_decision():
    issue = make_issue()
    repo = make_repo(issues=[issue], decisions=[make_decision(record_source=TRANSCRIPT)])
    assert effective_status(issue, repo) == "resolved"


def test_effective_status_passes_open_through_even_with_a_decision_on_file():
    # A stale resolved_by on an open issue must not matter, whatever the decision is.
    issue = make_issue(status="open", resolved_by="DEC-0001")
    confirmed = make_decision(record_source=TRANSCRIPT)
    assert effective_status(issue, make_repo(issues=[issue], decisions=[confirmed])) == "open"
    provisional = make_decision(record_source=NOTE)
    assert effective_status(issue, make_repo(issues=[issue], decisions=[provisional])) == "open"
    assert effective_status(make_issue(status="open", resolved_by=None), make_repo()) == "open"


def test_effective_status_passes_dismissed_through_even_with_a_provisional_decision():
    issue = make_issue(status="dismissed", resolved_by="DEC-0001", dismissed_reason="moot")
    repo = make_repo(issues=[issue], decisions=[make_decision(record_source=NOTE)])
    assert effective_status(issue, repo) == "dismissed"
    assert effective_status(issue, make_repo()) == "dismissed"


def test_a_resolved_issue_with_no_backing_decision_is_not_resolved_fail_closed():
    # Otherwise status: resolved + resolved_by: DEC-9999 clears the block with nothing behind it.
    dangling = make_issue(resolved_by="DEC-9999")
    assert effective_status(dangling, make_repo(issues=[dangling])) == "open"
    unset = make_issue(resolved_by=None)
    assert effective_status(unset, make_repo(issues=[unset])) == "open"


def test_a_decision_that_does_not_resolve_this_issue_does_not_back_it():
    other = make_decision(resolves=["ISS-0099"], record_source=TRANSCRIPT)
    issue = make_issue(resolved_by="DEC-0001")
    assert effective_status(issue, make_repo(issues=[issue], decisions=[other])) == "open"


def test_a_confirmed_decision_for_the_same_issue_cannot_mask_the_cited_provisional_one():
    provisional = make_decision(id="DEC-0001", resolves=["ISS-0001"], record_source=NOTE)
    confirmed = make_decision(id="DEC-0002", resolves=["ISS-0001"], record_source=TRANSCRIPT)
    issue = make_issue(resolved_by="DEC-0001")
    repo = make_repo(issues=[issue], decisions=[provisional, confirmed])
    assert effective_status(issue, repo) == "awaiting_confirmation"


def test_a_stored_awaiting_confirmation_cannot_pass_through_as_a_status():
    issue = make_issue(status="awaiting_confirmation")
    assert effective_status(issue, make_repo(issues=[issue])) == "open"
    assert effective_status(make_issue(status="closed"), make_repo()) == "open"


def test_effective_status_never_returns_the_recorded_status_for_a_provisional_issue():
    issue = make_issue(status="resolved")
    repo = make_repo(issues=[issue], decisions=[make_decision(record_source=NOTE)])
    assert issue.status == "resolved"
    assert effective_status(issue, repo) != issue.status


def test_effective_status_always_returns_a_member_of_the_effective_enum():
    for status in ("open", "resolved", "dismissed", "awaiting_confirmation", "garbage"):
        for resolved_by in (None, "DEC-0001", "DEC-9999"):
            for note in (True, False):
                issue = make_issue(status=status, resolved_by=resolved_by)
                repo = make_repo(issues=[issue], decisions=[make_decision()], self_note=note)
                assert effective_status(issue, repo) in model.EFFECTIVE_STATUSES


# ---------------------------------------------------------------------------
# derive_open_issues
# ---------------------------------------------------------------------------

def test_derive_open_issues_lists_blocking_issues_by_effective_status():
    requirement = make_requirement()
    issues = [
        make_issue("ISS-0001", status="open", resolved_by=None),
        make_issue("ISS-0002", status="resolved", resolved_by="DEC-0001"),   # provisional
        make_issue("ISS-0003", status="resolved", resolved_by="DEC-0002"),   # confirmed
        make_issue("ISS-0004", status="dismissed", resolved_by=None, dismissed_reason="moot"),
        make_issue("ISS-0005", status="open", resolved_by=None, affects=["REQ-SMP-0099"]),
    ]
    decisions = [
        make_decision("DEC-0001", resolves=["ISS-0002"], record_source=NOTE),
        make_decision("DEC-0002", resolves=["ISS-0003"], record_source=TRANSCRIPT),
    ]
    repo = make_repo(issues=issues, decisions=decisions, requirements=[requirement])
    assert derive_open_issues(requirement, repo) == ["ISS-0001", "ISS-0002"]


def test_derive_open_issues_is_sorted_and_empty_when_nothing_blocks():
    requirement = make_requirement()
    issues = [make_issue("ISS-0010", status="open", resolved_by=None),
              make_issue("ISS-0002", status="open", resolved_by=None)]
    assert derive_open_issues(requirement, make_repo(issues=issues)) == ["ISS-0002", "ISS-0010"]
    assert derive_open_issues(requirement, make_repo()) == []


def test_derive_open_issues_blocks_on_a_dangling_resolution():
    requirement = make_requirement()
    issue = make_issue("ISS-0001", status="resolved", resolved_by="DEC-9999")
    assert derive_open_issues(requirement, make_repo(issues=[issue])) == ["ISS-0001"]


def test_derive_open_issues_depends_on_issue_files_not_on_anything_the_requirement_says():
    requirement = make_requirement(status="baselined")
    issue = make_issue("ISS-0001", status="open", resolved_by=None)
    repo = make_repo(issues=[issue], requirements=[requirement])
    before = derive_open_issues(requirement, repo)
    requirement.statement = "Edited to look innocent."
    requirement.acceptance_criteria = []
    assert derive_open_issues(requirement, repo) == before == ["ISS-0001"]


def test_derive_open_issues_matches_effective_status_for_every_issue():
    requirement = make_requirement()
    decisions = [make_decision("DEC-0001", resolves=["ISS-0001", "ISS-0002", "ISS-0003"])]
    issues = [
        make_issue("ISS-0001", status="resolved"),
        make_issue("ISS-0002", status="open", resolved_by=None),
        make_issue("ISS-0003", status="dismissed", resolved_by=None, dismissed_reason="x"),
    ]
    repo = make_repo(issues=issues, decisions=decisions)
    blocking = {i.id for i in issues if effective_status(i, repo) in model.BLOCKING_STATUSES}
    assert set(derive_open_issues(requirement, repo)) == blocking == {"ISS-0001", "ISS-0002"}


def test_blocking_statuses_are_open_and_awaiting_confirmation():
    assert set(model.BLOCKING_STATUSES) == {"open", "awaiting_confirmation"}


# ---------------------------------------------------------------------------
# resolved_by is the pointer: scanning decision.resolves instead must fail
# ---------------------------------------------------------------------------

def test_a_nonexistent_resolved_by_is_open_even_if_another_confirmed_decision_lists_the_issue():
    confirmed_elsewhere = make_decision(id="DEC-0002", resolves=["ISS-0001"],
                                        record_source=TRANSCRIPT)
    issue = make_issue(resolved_by="DEC-9999")
    repo = make_repo(issues=[issue], decisions=[confirmed_elsewhere])
    assert effective_status(issue, repo) == "open"
    assert derive_open_issues(make_requirement(), repo) == ["ISS-0001"]


def test_a_resolved_by_naming_a_non_listing_decision_is_open_even_if_another_lists_the_issue():
    not_listing = make_decision(id="DEC-0001", resolves=["ISS-0099"], record_source=TRANSCRIPT)
    listing_and_confirmed = make_decision(id="DEC-0002", resolves=["ISS-0001"],
                                          record_source=TRANSCRIPT)
    issue = make_issue(resolved_by="DEC-0001")
    repo = make_repo(issues=[issue], decisions=[not_listing, listing_and_confirmed])
    assert effective_status(issue, repo) == "open"


def test_the_masking_check_holds_in_either_insertion_order():
    provisional = make_decision(id="DEC-0001", resolves=["ISS-0001"], record_source=NOTE)
    confirmed = make_decision(id="DEC-0002", resolves=["ISS-0001"], record_source=TRANSCRIPT)
    issue = make_issue(resolved_by="DEC-0001")
    for decisions in ([provisional, confirmed], [confirmed, provisional]):
        repo = make_repo(issues=[issue], decisions=decisions)
        assert effective_status(issue, repo) == "awaiting_confirmation"
    # and the other way round: the cited decision is the confirmed one
    issue2 = make_issue(resolved_by="DEC-0002")
    for decisions in ([provisional, confirmed], [confirmed, provisional]):
        repo = make_repo(issues=[issue2], decisions=decisions)
        assert effective_status(issue2, repo) == "resolved"


# ---------------------------------------------------------------------------
# schema_version is required on every artefact and leads the key order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["requirement", "candidate", "issue", "decision", "supersession"])
def test_schema_version_is_a_required_first_key_on_every_artefact(tmp_path, kind):
    artefact = ALL_SAMPLES[kind]()
    assert field_names(type(artefact))[0] == "schema_version"
    assert artefact.schema_version == 1
    text = dump_artefact(artefact).decode("utf-8")
    assert text.startswith("schema_version: 1\nid: " + artefact.id + "\n")

    path = tmp_path / f"{artefact.id}.yaml"
    path.write_text(
        "".join(line for line in text.splitlines(keepends=True)
                if not line.startswith("schema_version:")),
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(path, kind)
    assert "schema_version" in str(exc.value)


def test_baseline_keeps_schema_version_where_the_spec_puts_it():
    text = dump_artefact(ALL_SAMPLES["baseline"]()).decode("utf-8")
    keys = re.findall(r"^([a-z_]+):", text, flags=re.M)
    assert keys == ["id", "prepared_on", "prepared_by", "governed_tree_hash", "merit_version",
                    "schema_version", "requirements", "deferred", "open_issues"]


# ---------------------------------------------------------------------------
# confirmation_defect: the shared D20/V9 metadata rule behind is_provisional
# ---------------------------------------------------------------------------

def confirmed_by_sam(record_source=NOTE, confirmation=None, confirmed_by="Sam Ellis"):
    return make_decision(
        record_source=record_source, confirmed_by=confirmed_by,
        confirmation_record=confirmation if confirmation is not None else ev(EMAIL, speaker="Sam Ellis"))


def test_a_note_confirmed_by_a_note_stays_awaiting_confirmation():
    for confirming_source in (NOTE, "sources/2026-09-26-other-note.md"):
        repo = make_repo()
        repo.source_meta["sources/2026-09-26-other-note.md"] = {
            "record_type": "self_recorded_note", "authored_by": "Sam Ellis"}
        decision = confirmed_by_sam(confirmation=ev(confirming_source, speaker="Sam Ellis"))
        issue = make_issue()
        repo.issues = {issue.id: issue}
        repo.decisions = {decision.id: decision}
        assert confirmation_defect(decision, repo) is not None
        assert "self-recorded note" in confirmation_defect(decision, repo)
        assert is_provisional(decision, repo) is True
        assert effective_status(issue, repo) == "awaiting_confirmation"


def test_a_confirmation_by_someone_other_than_confirmed_by_does_not_confirm():
    decision = confirmed_by_sam(confirmation=ev(EMAIL, speaker="Somebody Else"))
    repo = make_repo()
    repo.source_meta[EMAIL]["authored_by"] = "Somebody Else"
    assert confirmation_defect(decision, repo) is not None
    assert is_provisional(decision, repo) is True


def test_authored_by_can_stand_in_for_a_missing_speaker():
    decision = confirmed_by_sam(confirmation=ev(EMAIL))  # no speaker, source authored by Sam
    repo = make_repo()
    assert confirmation_defect(decision, repo) is None
    assert is_provisional(decision, repo) is False


def test_the_speaker_can_stand_in_for_a_missing_authored_by():
    decision = confirmed_by_sam(confirmation=ev(EMAIL, speaker="Sam Ellis"))
    repo = make_repo()
    del repo.source_meta[EMAIL]["authored_by"]
    assert confirmation_defect(decision, repo) is None


def test_a_confirmation_authored_by_the_note_author_is_self_confirmation():
    # The speaker line names Sam, but the file was written by the note's own author.
    decision = confirmed_by_sam(confirmation=ev(EMAIL, speaker="Sam Ellis"))
    repo = make_repo()
    repo.source_meta[EMAIL]["authored_by"] = "alex"  # same as the note's authored_by
    assert "note's own author" in confirmation_defect(decision, repo)
    assert is_provisional(decision, repo) is True


def test_the_note_author_cannot_confirm_through_a_third_partys_transcript():
    # Alex wrote the note. Bob's transcript is a legitimate third-party source in
    # which Alex speaks. Every literal condition passes (source is not a self-note,
    # speaker matches confirmed_by, source author Bob is not Alex) - but Alex is
    # confirming Alex, so no second person is involved.
    repo = make_repo()
    repo.source_meta["sources/2026-09-27-bobs-transcript.md"] = {
        "record_type": "transcript", "authored_by": "Bob"}
    decision = make_decision(
        record_source=NOTE, confirmed_by="alex",
        confirmation_record=ev("sources/2026-09-27-bobs-transcript.md", speaker="alex"))
    issue = make_issue()
    repo.issues = {issue.id: issue}
    repo.decisions = {decision.id: decision}

    assert "note's own author" in confirmation_defect(decision, repo)
    assert is_provisional(decision, repo) is True
    assert effective_status(issue, repo) == "awaiting_confirmation"
    assert derive_open_issues(make_requirement(), repo) == ["ISS-0001"]

    # control: the same transcript confirming a different person is accepted
    other = make_decision(
        record_source=NOTE, confirmed_by="Bob",
        confirmation_record=ev("sources/2026-09-27-bobs-transcript.md", speaker="Bob"))
    assert confirmation_defect(other, repo) is None
    assert is_provisional(other, repo) is False


def test_the_note_author_rule_applies_only_to_a_note_basis():
    # A transcript basis is not provisional; authorship of a note is irrelevant to it.
    repo = make_repo()
    decision = confirmed_by_sam(record_source=TRANSCRIPT, confirmed_by="alex",
                                confirmation=ev(EMAIL, speaker="alex"))
    repo.source_meta[EMAIL]["authored_by"] = "alex"
    assert is_provisional(decision, repo) is False


def test_a_confirmation_from_an_unknown_source_is_a_defect():
    decision = confirmed_by_sam(confirmation=ev("sources/nowhere.md", speaker="Sam Ellis"))
    assert "no known record_type" in confirmation_defect(decision, make_repo())
    assert is_provisional(decision, make_repo()) is True


def test_confirmation_defect_is_none_for_a_valid_confirmation_and_for_no_claim():
    assert confirmation_defect(confirmed_by_sam(), make_repo()) is None
    assert confirmation_defect(make_decision(record_source=NOTE), make_repo()) is None
    bare = make_decision(record_source=NOTE, confirmed_by="Sam Ellis")
    assert "no confirmation_record" in confirmation_defect(bare, make_repo())


def test_confirmation_defect_reads_metadata_only_so_it_needs_no_filesystem_or_source_text():
    # repo.root is None and no source text exists anywhere: it still decides.
    repo = make_repo()
    assert repo.root is None
    assert confirmation_defect(confirmed_by_sam(), repo) is None


def test_a_defective_confirmation_does_not_unblock_a_transcript_basis_either_way():
    # A transcript basis is not provisional whatever the confirmation says (V9 reports the defect).
    decision = confirmed_by_sam(record_source=TRANSCRIPT,
                                confirmation=ev(NOTE, speaker="Sam Ellis"))
    repo = make_repo()
    assert confirmation_defect(decision, repo) is not None
    assert is_provisional(decision, repo) is False


# ---------------------------------------------------------------------------
# Hostile structure must not crash the display path
# ---------------------------------------------------------------------------

def test_a_required_nested_object_set_to_null_is_a_validation_failure(tmp_path):
    path = tmp_path / "decisions" / "DEC-0001.yaml"
    path.parent.mkdir()
    path.write_text(
        "schema_version: 1\nid: DEC-0001\nresolves: [ISS-0001]\ndecided_on: 2026-09-24\n"
        "decided_by: Jordan\noutcome: o\nrecord: ~\n", encoding="utf-8")
    with pytest.raises(ValidationFailed) as exc:
        load_artefact(path, "decision")
    assert "record" in str(exc.value)
    assert "DEC-0001.yaml" in str(exc.value)


def test_an_optional_nested_object_may_be_null(tmp_path):
    path = tmp_path / "decisions" / "DEC-0001.yaml"
    path.parent.mkdir()
    path.write_text(
        "schema_version: 1\nid: DEC-0001\nresolves: [ISS-0001]\ndecided_on: 2026-09-24\n"
        "decided_by: Jordan\noutcome: o\nrecord: {source: sources/a.md, quote: q}\n"
        "confirmation_record: ~\n", encoding="utf-8")
    assert load_artefact(path, "decision").confirmation_record is None


def test_an_unhashable_source_is_unclassifiable_not_a_crash():
    decision = make_decision()
    decision.record.source = ["not", "a", "path"]
    assert is_provisional(decision, make_repo()) is True
    decision = confirmed_by_sam()
    decision.confirmation_record.source = {"x": 1}
    assert confirmation_defect(decision, make_repo()) is not None


# ---------------------------------------------------------------------------
# Quotes are the core datum: YAML must never read them back as another type
# ---------------------------------------------------------------------------

YAML_FOOTGUN_TEXTS = [
    "yes", "no", "on", "off", "true", "false", "y", "n", "null", "Null", "~", "",
    "2026-09-12", "2026-09-12T10:30:00", "12:30", "1", "0", "1.5", "1e3", ".inf", "0x1F",
    "0o17", "- a dash", "# not a comment", "key: value", "[a, b]", "{a: b}", "'quoted'",
    '"double"', "& anchor", "* alias", "! tag", "@at", "`tick", "%percent", "|", ">",
    " leading space", "trailing space ", "line one\nline two", "line one\n\nline three\n",
    "tab\there", "colon: and # hash", "100%", "a: b: c",
    # YAML line-separator class members: NEL, LS, PS (single-quoted, NEL reloads as a space)
    "a" + chr(0x85) + "b", chr(0x85), "a" + chr(0x2028) + "b", "a" + chr(0x2029) + "b",
    "a" + chr(0x2028) + chr(0x85) + chr(0x2029), "a\r\nb", "a\rb",
    # other characters YAML treats specially
    "a" + chr(0xA0) + "b", chr(0xFEFF) + "x", "a" + chr(0x7F) + "b", "a" + chr(0x9F) + "b",
    "a" + chr(0x1B) + "b", "end" + chr(0x85),
]


@pytest.mark.parametrize("text", YAML_FOOTGUN_TEXTS)
def test_quotes_and_statements_round_trip_as_the_same_string(tmp_path, text):
    requirement = make_requirement(
        statement=text, evidence=[ev(quote=text, speaker=text or "x")])
    path = tmp_path / "REQ-SMP-0001.yaml"
    save_artefact(requirement, path)
    loaded = load_artefact(path, "requirement")
    assert loaded.evidence[0].quote == text
    assert type(loaded.evidence[0].quote) is str
    assert loaded.statement == text and type(loaded.statement) is str
    assert loaded == requirement
    save_artefact(loaded, tmp_path / "again.yaml")  # and the bytes are stable
    assert (tmp_path / "again.yaml").read_bytes() == path.read_bytes()


def test_a_quote_with_non_ascii_text_round_trips(tmp_path):
    text = "caf" + chr(0xE9) + " " + chr(0x201C) + "quoted" + chr(0x201D) + " " + chr(0x4E2D)
    requirement = make_requirement(evidence=[ev(quote=text)])
    path = tmp_path / "REQ-SMP-0001.yaml"
    save_artefact(requirement, path)
    assert load_artefact(path, "requirement").evidence[0].quote == text
