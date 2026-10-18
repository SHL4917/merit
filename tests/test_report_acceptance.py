"""R1-R10: structural acceptance of a useful browser approval report."""

from dataclasses import replace

from merit.model import load_repo, save_artefact
from merit.report import build_report, report_data
from test_report import Document, project  # shared real-source fixture and stdlib parser


def test_r1_blocker_summary_names_requirement_and_explains_confirmation(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    sections = doc.find('section')
    assert sections[0].attrs['id'] == 'blockers'
    text = sections[0].text
    assert 'REQ-SMP-0001' in text and 'ISS-0001' in text
    assert 'awaiting_confirmation' in text and 'Which retention period applies?' in text


def test_r2_r4_separate_badges_and_native_expandable_complete_evidence(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    cards = doc.section('requirements').find('details')
    assert len(cards) == 2
    card = cards[0]
    assert card.find('summary')[0].find('span', **{'class': 'badge status'})[0].text == 'clarifying'
    assert card.find('summary')[0].find('span', **{'class': 'badge provenance'})[0].text == 'stated'
    assert 'open' not in card.attrs
    assert card.find('blockquote')[0].text == 'Keep audit records for seven years for compliance reviews.'
    caption = card.find('figcaption')[0].text
    for field in ('Sam', 'sources/2026-10-18-interview.md', '2026-10-18', 'Line: 1', 'transcript'):
        assert field in caption
    assert 'Records remain available for seven years.' in card.text


def test_r3_filter_controls_compose_and_clear_with_live_feedback(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    form = doc.find('form', id='filters')[0]
    assert doc.find('label', **{'for': 'status-filter'})
    assert doc.find('label', **{'for': 'provenance-filter'})
    assert {option.attrs['value'] for option in form.find('select', name='status')[0].find('option')} == {
        '', 'draft', 'clarifying', 'baselined', 'deferred', 'withdrawn'}
    assert {option.attrs['value'] for option in form.find('select', name='provenance')[0].find('option')} == {
        '', 'stated', 'inferred'}
    handler = form.attrs['onchange']
    for operation in ('f.elements.status.value', 'c.dataset.status', 'f.elements.provenance.value',
                      'c.dataset.provenance', 'c.hidden=', '||', 'textContent=', 'if(!c.hidden)n++'):
        assert operation in handler
    clear = form.find('button')[0].attrs['onclick']
    assert '.reset()' in clear and 'dispatchEvent' in clear and "Event('change')" in clear
    cards = doc.section('requirements').find('details')
    assert {(card.attrs['data-status'], card.attrs['data-provenance']) for card in cards} == {
        ('clarifying', 'stated'), ('draft', 'inferred')}
    assert doc.find('p', id='filter-result')[0].attrs['role'] == 'status'


def test_r5_candidates_are_explicitly_separate_and_ungrounded(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    candidate = doc.section('candidates')
    assert 'not requirements' in candidate.find('h2')[0].text
    assert 'CAN-0001' in candidate.text and 'Facilitator idea' in candidate.text and 'Alex' in candidate.text
    assert not candidate.find('blockquote')
    assert 'CAN-0001' not in doc.section('requirements').text
    assert 'CAN-0001' not in doc.section('traceability').text


def test_r6_issue_board_shows_effective_status_owner_question_both_quotes(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    issue = doc.section('issues').find('article')[0]
    assert issue.attrs['data-status'] == 'awaiting_confirmation'
    for value in ('Sam', 'Which retention period applies?', 'REQ-SMP-0001'):
        assert value in issue.text
    assert [quote.text for quote in issue.find('blockquote')] == [
        'Keep audit records for seven years for compliance reviews.',
        'Remove audit records after one year to reduce storage costs.']


def test_r7_decisions_are_chronological_with_record_type_and_provisional_badge(project):
    repo = load_repo(project)
    earlier = replace(repo.decisions['DEC-0001'], id='DEC-0002', decided_on='2026-10-01')
    save_artefact(earlier, project / 'decisions' / (earlier.id + '.yaml'))
    assert [item['id'] for item in report_data(project)['decisions']] == ['DEC-0002', 'DEC-0001']
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    decisions = doc.section('decisions').find('article')
    assert [item.find('h3')[0].text.split()[0] for item in decisions] == ['DEC-0002', 'DEC-0001']
    for item in decisions:
        assert 'self_recorded_note' in item.text
        assert 'provisional - awaiting confirmation' in item.text
        assert item.find('blockquote')


def test_r8_matrix_maps_requirements_to_sources(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    table = doc.section('traceability').find('table')[0]
    headers = [item.text for item in table.find('thead')[0].find('th')]
    assert headers == ['Requirement', 'sources/2026-10-18-confirmation.md',
                       'sources/2026-10-18-interview.md', 'sources/2026-10-18-note.md']
    rows = table.find('tbody')[0].find('tr')
    assert [item.text for item in rows[0].find('td')] == ['-', 'Cited', '-']
    link = rows[0].find('a')[0]
    assert link.attrs['href'] == '#requirement-0'
    assert doc.find('details', id='requirement-0')


def test_r9_citation_worklist_precedes_secondary_percentage(project):
    doc = Document(build_report(project).read_text(encoding='utf-8'))
    queue = doc.section('citations')
    assert 'not cited by any requirement' in queue.find('p')[0].text
    assert 'deliberately uncited' in queue.find('ol')[0].text
    assert queue.text.index('deliberately uncited') < queue.text.index('%')
    assert 'Citation is not comprehension' in queue.text
