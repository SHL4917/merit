"""Citation attention queues use real canonical bundles and anchored evidence."""

import datetime
from dataclasses import replace

import pytest

from merit.anchor import anchor_evidence
from merit.config import load_config
from merit.ingest import ingest
from merit.model import Candidate, Evidence, Issue, Requirement, save_artefact
from merit.scaffold import init


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    sources = {}
    for name, text in [
        ("zeta", "Meeting heading with enough characters to exceed forty.\n"
         "Sam: " + "a" * 40 + "\nSam: " + "b" * 41 + "\nSam: short\n"
         "Sam: " + "c" * 39 + "\n"),
        ("alpha", "Another structural heading long enough for the threshold.\nLee: "
         + "d" * 40 + "\n"),
    ]:
        origin = tmp_path / (name + ".txt")
        origin.write_text(text, encoding="utf-8")
        result = ingest(root, origin, record_type="transcript", authored_by="Alex",
                        session_date=datetime.date(2026, 10, 18), participants=[],
                        classification="INTERNAL", ingested_by="Alex")
        sources[name] = result.source
    return root, sources, load_config(root)


def requirement(root, number, evidence):
    item = Requirement(schema_version=1, id=f"REQ-SMP-{number:04d}",
                       statement="Retain the requested information.", type="functional",
                       provenance="stated", status="draft", evidence=evidence)
    save_artefact(item, root / "requirements" / (item.id + ".yaml"))


def test_distinct_canonical_span_lines_and_worklist_first(project):
    from merit.citations import citation_coverage
    root, sources, config = project
    span = anchor_evidence(Evidence(source=sources["zeta"],
                                   quote="a" * 40 + " " + "b" * 41), root)
    single = anchor_evidence(Evidence(source=sources["zeta"], quote="a" * 40), root)
    requirement(root, 1, [span, single])
    requirement(root, 2, [single])
    result = citation_coverage(root, config)
    assert result.substantive_count == 3
    assert result.cited_count == 2
    assert result.percentage == pytest.approx(200 / 3)
    assert [(line.source, line.line, line.speaker, line.text) for line in result.uncited] == [
        (sources["alpha"], 2, "Lee", "d" * 40)]
    output = str(result)
    assert output.startswith("1 substantive lines are not cited by any requirement\n")
    assert output.index(sources["alpha"]) < output.index("66.7%")


def test_config_threshold_and_deterministic_order(project):
    from merit.citations import citation_coverage
    root, sources, config = project
    result = citation_coverage(root, replace(config, coverage_min_chars=39))
    assert [(line.source, line.line) for line in result.uncited] == [
        (sources["alpha"], 2), (sources["zeta"], 2),
        (sources["zeta"], 3), (sources["zeta"], 5)]
    assert result.cited_count == 0
    assert result.percentage == 0
    assert citation_coverage(root, config).substantive_count == 3


def test_candidates_and_issue_evidence_do_not_count(project):
    from merit.citations import citation_coverage
    root, sources, config = project
    evidence = anchor_evidence(Evidence(source=sources["alpha"], quote="d" * 40), root)
    short = anchor_evidence(Evidence(source=sources["zeta"], quote="short"), root)
    other = anchor_evidence(Evidence(source=sources["zeta"], quote="b" * 41), root)
    requirement(root, 1, [short])
    candidate = Candidate(schema_version=1, id="CAN-0001", statement="Consider retention.",
                          rationale="Explore this possibility.", raised_by="Alex",
                          raised_on="2026-10-18", status="open")
    save_artefact(candidate, root / "candidates" / "CAN-0001.yaml")
    issue = Issue(schema_version=1, id="ISS-0001", kind="contradiction", summary="Clarify.",
                  evidence=[evidence, other], affects=["REQ-SMP-0001"], question="Which policy?",
                  status="open", owner="Alex")
    save_artefact(issue, root / "issues" / "ISS-0001.yaml")
    result = citation_coverage(root, config)
    assert result.cited_count == 0
    assert len(result.uncited) == 3


def test_empty_denominator(project):
    from merit.citations import citation_coverage
    root, _, config = project
    result = citation_coverage(root, replace(config, coverage_min_chars=1000))
    assert result.uncited == ()
    assert result.substantive_count == result.cited_count == 0
    assert result.percentage == 0
    assert str(result).endswith("0.0% of substantive lines cited (0/0).")
