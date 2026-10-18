"""Open clarification briefs."""

from dataclasses import asdict
from merit.model import effective_status, load_repo


def open_questions(repo_root):
    repo = load_repo(repo_root)
    result = []
    for issue in sorted(repo.issues.values(), key=lambda item: item.id):
        status = effective_status(issue, repo)
        if status in ("open", "awaiting_confirmation"):
            result.append(dict(asdict(issue), status=status))
    return result
