"""Clarification briefs use derived status and retain both sides of a conflict."""

import pytest

from merit.cli import main
from merit.questions import open_questions
from test_checks_issues import project, save


@pytest.mark.parametrize("mode,status", [("open", "open"), ("provisional", "awaiting_confirmation"),
                                         ("confirmed", None), ("dismissed", None),
                                         ("dangling", "open")])
def test_questions_derive_status_and_keep_evidence(project, mode, status):
    root, _, issue, decision, confirmation = project
    if mode in ("provisional", "confirmed", "dangling"):
        issue.update(status="resolved", resolved_by=decision["id"])
        if mode == "confirmed":
            decision.update(confirmed_by="Jordan", confirmation_record=confirmation)
        if mode != "dangling":
            save(root, "decisions", decision)
    if mode == "dismissed":
        issue.update(status="dismissed", dismissed_reason="Duplicate")
    save(root, "issues", issue)
    result = open_questions(root)
    if status is None:
        assert result == []
    else:
        assert len(result) == 1
        question = result[0]
        assert question["id"] == issue["id"] and question["status"] == status
        assert question["question"] == issue["question"]
        assert question["evidence"] == issue["evidence"]


def test_cli_question_brief_and_order(project, capsys):
    root, _, issue, decision, _ = project
    issue.update(status="resolved", resolved_by=decision["id"])
    save(root, "issues", issue)
    save(root, "decisions", decision)
    save(root, "issues", {**issue, "id": "ISS-0002", "status": "open", "resolved_by": None})
    assert main(["questions", str(root), "--open"]) == 0
    output = capsys.readouterr().out
    assert output.index("ISS-0001") < output.index("ISS-0002")
    assert "awaiting_confirmation" in output and issue["question"] in output
    for evidence in issue["evidence"]:
        assert evidence["quote"] in output
        assert evidence["speaker"] in output and evidence["source"] in output
