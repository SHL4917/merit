"""``merit doctor``: environment, version pin, git/gh and repo validity.

Each finding is one string prefixed ``error: `` or ``warning: ``. An empty
list means healthy. Enforcement-tier and baseline-tag checks join later.
"""

import shutil
import subprocess
from pathlib import Path

from merit.config import load_config
from merit.errors import EnvironmentError_

ERROR = "error: "
WARNING = "warning: "


def _quote(path: Path) -> str:
    return subprocess.list2cmdline([str(path)])


def _git_failure(repo_root: Path, exc: subprocess.CalledProcessError) -> str:
    detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
    if "dubious ownership" in detail:
        fix = f"git config --global --add safe.directory {_quote(repo_root)}"
    else:
        fix = f"git -C {_quote(repo_root)} status"
    return f"{ERROR}git could not read {repo_root}: {detail} Fix: {fix}"


def _tracked_proposals(repo_root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "proposals"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    return [name for name in listed.stdout.decode("utf-8").split("\0") if name]


def doctor(repo_root: Path) -> list[str]:
    repo_root = Path(repo_root)
    findings: list[str] = []

    has_git = shutil.which("git") is not None
    if not has_git:
        findings.append(
            f"{ERROR}git is not installed or not on PATH. "
            "Fix: winget install --id Git.Git -e  (or https://git-scm.com/downloads)"
        )
    if shutil.which("gh") is None:
        findings.append(
            f"{WARNING}gh (GitHub CLI) is not on PATH, so the enforcement tier "
            "cannot be verified. Fix: winget install --id GitHub.cli -e  (or https://cli.github.com)"
        )

    try:
        load_config(repo_root)
    except EnvironmentError_ as exc:
        findings.append(f"{ERROR}{exc} Fix: {exc.fix}")

    if has_git:
        if not (repo_root / ".git").exists():
            findings.append(
                f"{ERROR}{repo_root} is not a git repository. Fix: git -C {_quote(repo_root)} init"
            )
        else:
            try:
                tracked = _tracked_proposals(repo_root)
            except subprocess.CalledProcessError as exc:
                findings.append(_git_failure(repo_root, exc))
            else:
                for name in tracked:
                    findings.append(
                        f"{WARNING}{name} is tracked, but proposals/ is ungoverned scratch "
                        f"that may quote sensitive material. "
                        f"Fix: git rm --cached {subprocess.list2cmdline([name])}"
                    )
    return findings
