"""Offline report and injection contracts, checked with a real HTML parser."""

import datetime
import json
from collections import Counter
from dataclasses import replace
from html.parser import HTMLParser

import pytest

from merit.anchor import anchor_evidence
from merit.cli import main
from merit.errors import UsageError
from merit.ingest import ingest
from merit.model import (Baseline, Candidate, Decision, Evidence, Issue, Requirement,
                         load_repo, save_artefact)
from merit.report import build_report, report_data
from merit.scaffold import init


class Node:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    @property
    def text(self):
        return ''.join(child if isinstance(child, str) else child.text for child in self.children)

    def find(self, tag=None, **attrs):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and all(child.attrs.get(k) == v for k, v in attrs.items()):
                    result.append(child)
                result.extend(child.find(tag, **attrs))
        return result


class Document(HTMLParser):
    VOID = {'meta', 'link', 'br', 'hr', 'input', 'img', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'col', 'track'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('document')
        self.stack = [self.root]
        self.feed(html)
        self.close()
        assert self.stack == [self.root], 'unclosed generated element'

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        assert self.stack[-1].tag == tag, f'unexpected closing tag {tag}'
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def find(self, tag=None, **attrs):
        return self.root.find(tag, **attrs)

    def section(self, identity):
        return self.find('section', id=identity)[0]

    @property
    def payload(self):
        return json.loads(self.find('script')[0].text)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    init(root, project_id='sample', id_prefix='SMP')
    quotes = ['Keep audit records for seven years for compliance reviews.',
              'Remove audit records after one year to reduce storage costs.',
              'This source line is deliberately uncited and needs human attention.']
    entries = []
    for name, record_type, text in [('interview', 'transcript', '\n'.join('Sam: ' + q for q in quotes)),
                                    ('note', 'self_recorded_note', 'Alex: Retain seven years of audit history.'),
                                    ('confirmation', 'email', 'Lee: I confirm that seven years is the agreed retention.')]:
        origin = tmp_path / (name + '.txt')
        origin.write_text(text + '\n', encoding='utf-8')
        result = ingest(root, origin, record_type=record_type, authored_by='Alex' if name == 'note' else 'Lee',
                        session_date=datetime.date(2026, 10, 18), participants=[],
                        classification='INTERNAL', ingested_by='Alex')
        entries.append((result.source, text))
    source = entries[0][0]
    evidence = [anchor_evidence(Evidence(source=source, quote=quote), root) for quote in quotes[:2]]
    requirement = Requirement(schema_version=1, id='REQ-SMP-0001', statement='Keep audit records for seven years.',
                              type='constraint', provenance='stated', status='clarifying', evidence=[evidence[0]],
                              acceptance_criteria=['Records remain available for seven years.'], owner='Sam')
    other = replace(requirement, id='REQ-SMP-0002', statement='Support retention review.',
                    provenance='inferred', rationale='Audit retention needs a review process.', status='draft')
    candidate = Candidate(schema_version=1, id='CAN-0001', statement='Offer a dark theme.',
                          rationale='Facilitator idea without user evidence.', raised_by='Alex',
                          raised_on='2026-10-17', status='open')
    issue = Issue(schema_version=1, id='ISS-0001', kind='contradiction', summary='Retention periods conflict.',
                  evidence=evidence, affects=[requirement.id], question='Which retention period applies?',
                  status='resolved', owner='Sam', resolved_by='DEC-0001')
    decision = Decision(schema_version=1, id='DEC-0001', resolves=[issue.id], decided_on='2026-10-18',
                        decided_by='Alex', outcome='Retain seven years.',
                        record=anchor_evidence(Evidence(source=entries[1][0],
                                                       quote='Retain seven years of audit history.'), root))
    for dirname, item in [('requirements', requirement), ('requirements', other),
                          ('candidates', candidate), ('issues', issue), ('decisions', decision)]:
        save_artefact(item, root / dirname / (item.id + '.yaml'))
    return root


def test_empty_repo_and_cli_are_self_contained(tmp_path, capsys):
    root = tmp_path / 'empty'
    init(root, project_id='empty', id_prefix='EMP')
    target = tmp_path / 'report.html'
    assert main(['report', str(root), '--output', str(target)]) == 0
    assert str(target) in capsys.readouterr().out
    document = Document(target.read_text(encoding='utf-8'))
    assert len(document.find('script')) == 1
    assert document.find('script')[0].attrs == {'id': 'report-data', 'type': 'application/json'}
    assert not document.find('script', src='')
    assert all('src' not in item.attrs for item in document.find('script'))
    assert not [item for item in document.find('link') if item.attrs.get('rel') == 'stylesheet']
    assert 'No requirements yet' in document.section('blockers').text
    assert document.payload['requirements'] == []
    assert not document.payload['blockers']['ready']
    assert not document.find('section', id='handoff')


@pytest.mark.parametrize('quote', [
    '</script><img src=x onerror=alert(1)> "quoted" & \'single\' @@content@@',
    '</ScRiPt><svg onload=alert(1)><script> @@data@@',
    '<!-- </script> --> <a href="javascript:alert(1)">Click</a>',
    'Unicode ' + chr(0x2028) + chr(0x2029) + chr(0x85) + ' < > &',
])
def test_parser_proves_json_and_text_injection_safety(project, tmp_path, quote):
    baseline = Document(build_report(project).read_text(encoding='utf-8'))
    repo = load_repo(project)
    item = repo.requirements['REQ-SMP-0001']
    item.evidence[0].quote = quote  # Invalid anchoring must not prevent seeing the V5 blocker.
    item.statement = quote
    save_artefact(item, project / 'requirements' / (item.id + '.yaml'))
    target = build_report(project, tmp_path / 'adversarial.html')
    document = Document(target.read_text(encoding='utf-8'))
    scripts = document.find('script')
    assert len(scripts) == 1
    assert '<' not in scripts[0].text
    assert '>' not in scripts[0].text and '&' not in scripts[0].text
    for character, encoded in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026')]:
        if character in quote:
            assert encoded in scripts[0].text
    assert document.payload['requirements'][0]['evidence'][0]['quote'] == quote
    assert document.payload['requirements'][0]['statement'] == quote
    assert document.find('blockquote')[0].text == quote
    # Compare actual parser elements to the trusted generator's element vocabulary.
    assert set(node.tag for node in document.find()) <= set(node.tag for node in baseline.find()) | {'br'}
    assert not document.find('img') and not document.find('svg')
    assert Counter(node.tag for node in document.find())['script'] == 1
    assert not any('onerror' in node.attrs or 'onload' in node.attrs for node in document.find())
    assert any(finding['check_id'] == 'V5' for finding in document.payload['blockers']['findings'])


def test_issue_status_and_decision_provisional_share_model_semantics(project):
    data = report_data(project)
    assert data['issues'][0]['status'] == 'awaiting_confirmation'
    assert data['decisions'][0]['provisional'] is True
    assert data['requirements'][0]['open_issues'] == ['ISS-0001']
    repo = load_repo(project)
    decision = repo.decisions['DEC-0001']
    decision.confirmed_by = 'Lee'
    save_artefact(decision, project / 'decisions' / (decision.id + '.yaml'))
    assert report_data(project)['issues'][0]['status'] == 'awaiting_confirmation'
    decision.confirmation_record = anchor_evidence(Evidence(
        source='sources/2026-10-18-confirmation.md', quote='I confirm that seven years is the agreed retention.'), project)
    save_artefact(decision, project / 'decisions' / (decision.id + '.yaml'))
    data = report_data(project)
    assert data['issues'][0]['status'] == 'resolved'
    assert data['decisions'][0]['provisional'] is False
    assert data['requirements'][0]['open_issues'] == []


def test_dangling_resolution_stays_open_and_blocked(project):
    repo = load_repo(project)
    issue = replace(repo.issues['ISS-0001'], resolved_by='DEC-9999')
    save_artefact(issue, project / 'issues' / (issue.id + '.yaml'))
    data = report_data(project)
    assert data['issues'][0]['status'] == 'open'
    assert not data['blockers']['ready']
    assert data['blockers']['by_requirement'][0]['id'] == 'REQ-SMP-0001'


def test_source_integrity_and_confirmation_anchor_errors_surface(project):
    repo = load_repo(project)
    decision = repo.decisions['DEC-0001']
    decision.confirmed_by = 'Lee'
    decision.confirmation_record = Evidence(source='sources/2026-10-18-confirmation.md', quote='Fabricated confirmation.',
                                          line=1, speaker='Lee', bundle_sha256='0' * 64)
    save_artefact(decision, project / 'decisions' / (decision.id + '.yaml'))
    data = report_data(project)
    assert data['issues'][0]['status'] == 'resolved'  # Metadata predicate deliberately does no anchoring.
    assert not data['blockers']['ready']
    assert any('V9' in reason for item in data['blockers']['by_requirement'] for reason in item['reasons'])
    source = project / 'sources' / '2026-10-18-interview.md'
    source.write_text(source.read_text(encoding='utf-8').replace('seven', 'eight'), encoding='utf-8')
    document = Document(build_report(project).read_text(encoding='utf-8'))
    assert 'V1' in document.section('blockers').text
    assert 'Coverage unavailable' in document.section('citations').text


def test_handoff_uses_shared_formatter_only_when_ready(project):
    from merit.handoff import handoff_block
    repo = load_repo(project)
    issue = replace(repo.issues['ISS-0001'], status='dismissed', dismissed_reason='Different retention scopes.')
    save_artefact(issue, project / 'issues' / (issue.id + '.yaml'))
    baseline = Baseline(id='sample-b1', prepared_on='2026-10-18', prepared_by='Alex',
                        governed_tree_hash='0' * 64, merit_version='0.1.0', schema_version=1,
                        requirements=[], deferred=[], open_issues=[])
    save_artefact(baseline, project / 'baselines' / (baseline.id + '.yaml'))
    data = report_data(project)
    assert data['blockers']['ready']
    assert data['blockers']['handoff'] == handoff_block('sample-b1')
    document = Document(build_report(project).read_text(encoding='utf-8'))
    assert document.section('handoff').find('pre')[0].text == handoff_block('sample-b1')
    assert report_data(project, commit_sha='a' * 40)['blockers']['handoff'] == handoff_block('sample-b1', 'a' * 40)


def test_real_anchored_source_quote_roundtrips_without_new_elements(project, tmp_path):
    quote = 'Require literal </script><img src=x onerror=alert(1)> in "quotes" & text.'
    origin = tmp_path / 'adversarial.txt'
    origin.write_text('Sam: ' + quote + '\n', encoding='utf-8')
    result = ingest(project, origin, record_type='transcript', authored_by='Lee',
                    session_date=datetime.date(2026, 10, 18), participants=[],
                    classification='INTERNAL', ingested_by='Alex')
    assert quote in (project / result.source).read_text(encoding='utf-8')
    repo = load_repo(project)
    item = repo.requirements['REQ-SMP-0001']
    before = Document(build_report(project).read_text(encoding='utf-8'))
    item.evidence = [anchor_evidence(Evidence(source=result.source, quote=quote), project)]
    save_artefact(item, project / 'requirements' / (item.id + '.yaml'))
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    assert len(doc.find('script')) == 1
    assert '<' not in doc.find('script')[0].text
    assert doc.payload['requirements'][0]['evidence'][0]['quote'] == quote
    assert doc.find('blockquote')[0].text == quote
    assert not doc.payload['blockers']['findings']
    expected = Counter(node.tag for node in before.find())
    expected['li'] -= 1  # The newly cited source line leaves the attention queue.
    assert Counter(node.tag for node in doc.find()) == expected


def test_deferred_requirement_issue_is_visible_but_does_not_block_active_scope(project):
    repo = load_repo(project)
    item = replace(repo.requirements['REQ-SMP-0001'], status='deferred', deferred_reason='Next baseline.')
    save_artefact(item, project / 'requirements' / (item.id + '.yaml'))
    data = report_data(project)
    assert data['blockers']['ready']
    assert data['blockers']['by_requirement'] == []
    assert data['issues'][0]['status'] == 'awaiting_confirmation'
    assert data['blockers']['handoff'] == ''  # No manifest prepared yet.


def test_malformed_yaml_still_produces_a_readable_validation_report(project):
    (project / 'requirements' / 'REQ-SMP-0001.yaml').write_text('statement: [broken\n', encoding='utf-8')
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    summary = doc.section('blockers').text
    assert 'V3' in summary and 'REQ-SMP-0001.yaml' in summary and 'line' in summary
    assert 'unavailable until structural errors are corrected' in summary
    assert not doc.payload['blockers']['ready']
    assert not doc.find('section', id='handoff')


def test_prospective_readiness_requires_acceptance_criteria(project):
    repo = load_repo(project)
    issue = replace(repo.issues['ISS-0001'], status='dismissed', dismissed_reason='Different scope.')
    save_artefact(issue, project / 'issues' / (issue.id + '.yaml'))
    item = replace(repo.requirements['REQ-SMP-0001'], acceptance_criteria=[])
    save_artefact(item, project / 'requirements' / (item.id + '.yaml'))
    data = report_data(project, manifest_id='sample-b1')
    assert not data['blockers']['ready'] and data['blockers']['handoff'] == ''
    assert 'acceptance criteria' in data['blockers']['by_requirement'][0]['reasons'][0]


@pytest.mark.parametrize('directory', ['sources', 'requirements', 'candidates', 'issues',
                                       'decisions', 'supersessions', 'baselines'])
def test_report_output_cannot_write_governed_artefacts(project, directory):
    target = project / 'public' / '..' / directory / 'report.html'
    with pytest.raises(UsageError, match='cannot overwrite governed') as raised:
        build_report(project, target)
    assert raised.value.fix == 'merit report --output public/index.html'
    assert not target.resolve().exists()


def test_invalid_dismissal_is_attributed_to_affected_requirement(project):
    repo = load_repo(project)
    issue = replace(repo.issues['ISS-0001'], status='dismissed', dismissed_reason=None)
    save_artefact(issue, project / 'issues' / (issue.id + '.yaml'))
    data = report_data(project)
    assert data['issues'][0]['status'] == 'dismissed'
    assert not data['blockers']['ready']
    assert data['blockers']['by_requirement'][0]['id'] == 'REQ-SMP-0001'
    assert any('V7' in reason for reason in data['blockers']['by_requirement'][0]['reasons'])
