"""Project data and escaped HTML for an offline approval review."""

import json
import re
from dataclasses import asdict
from html import escape
from importlib.resources import files
from pathlib import Path

from merit.citations import citation_coverage
from merit.config import load_config
from merit.errors import MeritError, UsageError
from merit.handoff import handoff_block
from merit.model import Repo, derive_open_issues, effective_status, is_provisional, load_repo
from merit.validate.runner import run_checks


def report_data(repo_root, config=None, *, manifest_id=None, commit_sha=None):
    """Return seven JSON-compatible review payloads; never expose recorded issue status.

    A handoff needs a prepared manifest and a ready tree. An explicit manifest ID
    lets a caller choose one; otherwise the latest prepared manifest is used.
    Source validation remains authoritative even if citation anchoring fails.
    """
    root = Path(repo_root)
    config = load_config(root) if config is None else config
    findings = run_checks(root)
    unavailable = any(f.check_id == 'V3' and f.severity == 'error' for f in findings)
    # A malformed artefact must still produce the validator's explanation, never
    # an apparently complete approval view over a silently omitted file.
    repo = Repo(root=root) if unavailable else load_repo(root)

    def evidence(item):
        data = asdict(item)
        meta = repo.source_meta.get(item.source, {})
        data.update(session_date=str(meta.get("session_date", "Not recorded")),
                    record_type=meta.get("record_type", "unknown"))
        return data

    requirements = []
    blocked = []
    for item in sorted(repo.requirements.values(), key=lambda item: item.id):
        data = asdict(item)
        data["evidence"] = [evidence(entry) for entry in item.evidence]
        data["open_issues"] = derive_open_issues(item, repo)
        requirements.append(data)
        reasons = [f"{identity}: {effective_status(repo.issues[identity], repo)} - "
                   f"{repo.issues[identity].question}" for identity in data["open_issues"]]
        if item.status not in ("deferred", "withdrawn") and not item.acceptance_criteria:
            reasons.append("Add acceptance criteria before baselining.")
        # Attribute source/decision/issue errors as well as direct requirement errors.
        dependencies = {item.id, *(issue.id for issue in repo.issues.values()
                                   if item.id in issue.affects)}
        dependencies.update(issue.resolved_by for issue in repo.issues.values()
                            if item.id in issue.affects and issue.resolved_by)
        sources = {entry.source for entry in item.evidence}
        related_paths = set(sources)
        for source in sources:
            related_paths.add(source.removesuffix(".md") + ".meta.yaml")
            origin = repo.source_meta.get(source, {}).get("origin")
            if origin:
                related_paths.add(origin)
        for finding in findings:
            if finding.severity != "error":
                continue
            relative = finding.path.relative_to(root).as_posix()
            if finding.path.stem in dependencies or relative in related_paths:
                reasons.append(f"{finding.check_id}: {finding.message}")
        if reasons and item.status not in ("deferred", "withdrawn"):
            blocked.append({"id": item.id, "reasons": reasons})

    issues = [{"id": item.id, "kind": item.kind, "summary": item.summary,
               "status": effective_status(item, repo), "owner": item.owner,
               "question": item.question, "affects": item.affects,
               "resolved_by": item.resolved_by, "dismissed_reason": item.dismissed_reason,
               "evidence": [evidence(entry) for entry in item.evidence]}
              for item in sorted(repo.issues.values(), key=lambda item: item.id)]
    decisions = []
    for item in sorted(repo.decisions.values(), key=lambda item: (str(item.decided_on), item.id)):
        data = asdict(item)
        data.update(decided_on=str(item.decided_on), provisional=is_provisional(item, repo),
                    record=evidence(item.record) if item.record else None,
                    confirmation_record=evidence(item.confirmation_record)
                    if item.confirmation_record else None)
        data["record_type"] = data["record"]["record_type"] if data["record"] else "unknown"
        decisions.append(data)
    active = [item for item in requirements if item["status"] not in ("deferred", "withdrawn")]
    ready = bool(active) and not blocked and not any(f.severity == "error" for f in findings)
    if manifest_id is None and repo.baselines:
        manifest_id = max(repo.baselines.values(), key=lambda item: (str(item.prepared_on), item.id)).id
    handoff = handoff_block(manifest_id, commit_sha) if ready else ""
    try:
        if unavailable:
            raise ValueError('Correct structural validation errors before reviewing coverage.')
        coverage = citation_coverage(root, config)
        citations = {"uncited": [asdict(item) for item in coverage.uncited],
                     "substantive_count": coverage.substantive_count,
                     "cited_count": coverage.cited_count, "percentage": coverage.percentage,
                     "unavailable": None}
    except (MeritError, OSError, UnicodeError, ValueError) as exc:
        citations = {"uncited": [], "percentage": None, "unavailable": str(exc)}
    sources = sorted(repo.source_meta.keys() |
                     {entry.source for item in repo.requirements.values() for entry in item.evidence})
    return {
        "blockers": {"project": config.project_id, "ready": ready,
                     "unavailable": unavailable,
                     "by_requirement": blocked,
                     "findings": [{"check_id": f.check_id, "severity": f.severity,
                                   "path": f.path.relative_to(root).as_posix(),
                                   "message": f.message, "fix": f.fix} for f in findings],
                     "handoff": handoff},
        "requirements": requirements,
        "candidates": [dict(asdict(item), raised_on=str(item.raised_on))
                       for item in sorted(repo.candidates.values(), key=lambda item: item.id)],
        "issues": issues,
        "decisions": decisions,
        "traceability": {"sources": sources, "rows": [
            {"id": item["id"], "sources": sorted({entry["source"] for entry in item["evidence"]})}
            for item in requirements]},
        "citations": citations,
    }


def _text(value):
    return escape(str(value) if value is not None else "Not recorded", quote=True)


def _badge(value, kind):
    return f'<span class="badge {kind}">{_text(value)}</span>'


def _evidence(entries):
    parts = []
    for entry in entries:
        line = str(entry["line"]) if entry["line"] is not None else "Not anchored"
        if entry["end_line"] is not None:
            line += f'-{entry["end_line"]}'
        parts.append(f'<figure><blockquote>{_text(entry["quote"])}</blockquote>'
                     f'<figcaption>Speaker: {_text(entry["speaker"])} | '
                     f'Source: {_text(entry["source"])} | Session: {_text(entry["session_date"])} | '
                     f'Line: {_text(line)} | Record: {_text(entry["record_type"])}</figcaption></figure>')
    return "".join(parts) or '<p>No evidence recorded.</p>'


def _list(values):
    return '<ul>' + ''.join(f'<li>{_text(value)}</li>' for value in values) + '</ul>'


def _render(data):
    summary = data["blockers"]
    parts = ['<section id="blockers" aria-labelledby="blocker-title"><h2 id="blocker-title">'
             'Baseline readiness</h2>']
    parts.append('<p>' + ('Ready for baseline review.' if summary["ready"] else
                         'Baseline not ready. Resolve the items below before approval.') + '</p>')
    if summary["unavailable"]:
        parts.append('<p>Artefact views are unavailable until structural errors are corrected. '
                     'The findings below identify the files to repair.</p>')
    elif not data["requirements"]:
        parts.append('<p>No requirements yet. Ingest evidence and draft requirements to begin review.</p>')
    for item in summary["by_requirement"]:
        parts.append(f'<article class="blocker"><h3>{_text(item["id"])}</h3>{_list(item["reasons"])}</article>')
    for finding in summary["findings"]:
        parts.append(f'<p class="finding">{_badge(finding["severity"], "severity")} '
                     f'{_text(finding["check_id"])}: {_text(finding["path"])} - '
                     f'{_text(finding["message"])}<br>Next: {_text(finding["fix"])}</p>')
    parts.append('</section><section id="requirements"><h2>Requirements</h2>')
    # Fixed handler contains no project text. Both filters compose with AND.
    handler = ("const f=this;let n=0;document.querySelectorAll('.requirement').forEach(c=>{"
               "c.hidden=!!((f.elements.status.value&&c.dataset.status!==f.elements.status.value)||"
               "(f.elements.provenance.value&&c.dataset.provenance!==f.elements.provenance.value));"
               "if(!c.hidden)n++;});document.getElementById('filter-result').textContent="
               "n+' requirements shown';")
    parts.append(f'<form id="filters" onchange="{_text(handler)}"><label for="status-filter">Status</label>'
                 '<select id="status-filter" name="status"><option value="">All statuses</option>')
    for value in ("draft", "clarifying", "baselined", "deferred", "withdrawn"):
        parts.append(f'<option value="{value}">{value}</option>')
    parts.append('</select><label for="provenance-filter">Provenance</label>'
                 '<select id="provenance-filter" name="provenance"><option value="">All provenance</option>'
                 '<option value="stated">stated</option><option value="inferred">inferred</option></select>'
                 '<button type="button" onclick="this.form.reset();this.form.dispatchEvent(new Event(\'change\'));">'
                 'Clear filters</button></form>')
    parts.append(f'<p id="filter-result" role="status">{len(data["requirements"])} requirements shown</p>')
    for number, item in enumerate(data["requirements"]):
        parts.append(f'<details id="requirement-{number}" class="requirement" '
                     f'data-status="{_text(item["status"])}" data-provenance="{_text(item["provenance"])}">'
                     f'<summary><strong>{_text(item["id"])}</strong> {_badge(item["status"], "status")} '
                     f'{_badge(item["provenance"], "provenance")}<span class="statement">'
                     f'{_text(item["statement"])}</span></summary><div class="detail-body">'
                     f'<p>Type: {_text(item["type"])} | Owner: {_text(item["owner"])}</p>')
        for field, label in (("rationale", "Rationale"), ("deferred_reason", "Deferred reason"),
                             ("withdrawn_reason", "Withdrawn reason")):
            if item[field]:
                parts.append(f'<p>{label}: {_text(item[field])}</p>')
        parts.append('<h3>Acceptance criteria</h3>' + (_list(item["acceptance_criteria"]) if
                     item["acceptance_criteria"] else '<p>Not recorded.</p>'))
        if item["open_issues"]:
            parts.append('<h3>Blocking issues</h3>' + _list(item["open_issues"]))
        parts.append('<h3>Evidence</h3>' + _evidence(item["evidence"]) + '</div></details>')
    parts.append('</section><section id="candidates"><h2>Candidates - not requirements</h2>'
                 '<p>Ideas awaiting grounded evidence. Candidates never enter a baseline.</p>')
    for item in data["candidates"]:
        parts.append(f'<article><h3>{_text(item["id"])}</h3><p>{_text(item["statement"])}</p>'
                     f'<p>{_text(item["rationale"])}</p><p>Raised by {_text(item["raised_by"])} '
                     f'on {_text(item["raised_on"])} | {_text(item["status"])}</p>')
        if item["promoted_to"]:
            parts.append(f'<p>Promoted to {_text(item["promoted_to"])}</p>')
        if item["withdrawn_reason"]:
            parts.append(f'<p>Withdrawn: {_text(item["withdrawn_reason"])}</p>')
        parts.append('</article>')
    if not data["candidates"]:
        parts.append('<p>No candidates recorded.</p>')
    parts.append('</section><section id="issues"><h2>Issue board</h2>')
    for item in data["issues"]:
        parts.append(f'<article class="issue" data-status="{_text(item["status"])}"><h3>'
                     f'{_text(item["id"])} {_badge(item["status"], "status")}</h3>'
                     f'<p>{_text(item["summary"])}</p><p>Kind: {_text(item["kind"])} | '
                     f'Owner: {_text(item["owner"])}</p><p>Question: {_text(item["question"])}</p>'
                     f'<p>Affects: {_text(", ".join(item["affects"]))}</p>')
        if item["dismissed_reason"]:
            parts.append(f'<p>Dismissed: {_text(item["dismissed_reason"])}</p>')
        parts.append(_evidence(item["evidence"]) + '</article>')
    if not data["issues"]:
        parts.append('<p>No issues recorded.</p>')
    parts.append('</section><section id="decisions"><h2>Decision register</h2>')
    for item in data["decisions"]:
        badge = _badge("provisional - awaiting confirmation", "provisional") if item["provisional"] else ''
        parts.append(f'<article class="decision"><h3>{_text(item["id"])} {badge}</h3>'
                     f'<p>{_text(item["decided_on"])} | Decided by {_text(item["decided_by"])} | '
                     f'Record type: {_text(item["record_type"])}</p><p>{_text(item["outcome"])}</p>'
                     f'<p>Resolves: {_text(", ".join(item["resolves"]))}</p>'
                     + _evidence([item["record"]] if item["record"] else []))
        if item["confirmation_record"]:
            parts.append(f'<h4>Confirmation by {_text(item["confirmed_by"])}</h4>'
                         + _evidence([item["confirmation_record"]]))
        parts.append('</article>')
    if not data["decisions"]:
        parts.append('<p>No decisions recorded.</p>')
    matrix = data["traceability"]
    parts.append('</section><section id="traceability"><h2>Traceability matrix</h2>'
                 '<div class="table-scroll"><table><caption>Requirement evidence by source</caption>'
                 '<thead><tr><th scope="col">Requirement</th>')
    parts.extend(f'<th scope="col">{_text(source)}</th>' for source in matrix["sources"])
    parts.append('</tr></thead><tbody>')
    for number, row in enumerate(matrix["rows"]):
        parts.append(f'<tr><th scope="row"><a href="#requirement-{number}" '
                     f'onclick="document.getElementById(\'requirement-{number}\').open=true;">'
                     f'{_text(row["id"])}</a></th>')
        parts.extend('<td>' + ('Cited' if source in row["sources"] else '-') + '</td>'
                     for source in matrix["sources"])
        parts.append('</tr>')
    citations = data["citations"]
    parts.append('</tbody></table></div></section><section id="citations"><h2>Citation attention queue</h2>')
    if citations["unavailable"]:
        parts.append(f'<p>Coverage unavailable: {_text(citations["unavailable"])}. Resolve evidence errors first.</p>')
    else:
        parts.append(f'<p>{len(citations["uncited"])} substantive lines are not cited by any requirement.</p>')
        parts.append('<ol>' + ''.join(f'<li>{_text(row["source"])}:{row["line"]} '
                                     f'{_text(row["speaker"])}: {_text(row["text"])}</li>'
                                     for row in citations["uncited"]) + '</ol>')
        parts.append(f'<p>{citations["percentage"]:.1f}% of substantive lines cited '
                     f'({citations["cited_count"]}/{citations["substantive_count"]}).</p>')
    parts.append('<p>Citation is not comprehension. This queue directs attention; it does not measure completeness.</p></section>')
    if summary["handoff"]:
        # The shared formatter produces Markdown. Preserve its exact human procedure
        # as readable text; never interpret project-controlled Markdown as HTML.
        parts.append('<section id="handoff"><h2>Baseline tag handoff</h2><pre>'
                     + _text(summary["handoff"]) + '</pre></section>')
    return ''.join(parts)


def build_report(repo_root, output=None, *, config=None, manifest_id=None, commit_sha=None):
    """Build one HTML file (default public/index.html) and return its path."""
    root = Path(repo_root)
    target = Path(output) if output is not None else root / 'public' / 'index.html'
    for dirname in ('sources', 'requirements', 'candidates', 'issues', 'decisions',
                    'supersessions', 'baselines'):
        if target.resolve().is_relative_to((root / dirname).resolve()):
            raise UsageError('Reports cannot overwrite governed artefacts.',
                             fix='merit report --output public/index.html')
    data = report_data(root, config, manifest_id=manifest_id, commit_sha=commit_sha)
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False)
    for character in '<>&':
        payload = payload.replace(character, '\\u%04x' % ord(character))
    template = files('merit.report').joinpath('template.html').read_text(encoding='utf-8')
    # One-pass substitution: inserted project text cannot become a template token.
    replacements = {'title': _text(data['blockers']['project']), 'content': _render(data), 'data': payload}
    html = re.sub(r'@@(title|content|data)@@', lambda match: replacements[match[1]], template)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding='utf-8')
    return target
