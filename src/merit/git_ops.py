"""Read-only Git context for history-sensitive validation."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ComparisonPoint:
    kind: str
    rev: str | None


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, check=False
        )
        if result.returncode:
            return None
        return result.stdout.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def list_tags(repo_root: Path) -> list[str]:
    """Return all tag names in Git's refname order, or no tags on failure."""
    output = _git(repo_root, "for-each-ref", "--format=%(refname:strip=2)", "refs/tags/")
    return output.splitlines() if output is not None else []


def comparison_point(repo_root: Path) -> ComparisonPoint:
    """Prefer the nearest reachable baseline, then the merge-base with main."""
    tag = _git(repo_root, "describe", "--tags", "--match", "baseline/*", "--abbrev=0", "HEAD")
    if tag and tag.strip():
        return ComparisonPoint("baseline", tag.strip())
    base = _git(repo_root, "merge-base", "HEAD", "refs/heads/main")
    if base and base.strip():
        return ComparisonPoint("merge-base", base.strip())
    return ComparisonPoint("none", None)


def file_at(repo_root: Path, rev: str, path: str | Path) -> str | None:
    """Read a repository-relative file verbatim from a revision, if present."""
    return _git(repo_root, "show", f"{rev}:{Path(path).as_posix()}")
