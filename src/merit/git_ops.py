"""Git history, explicit checkpoints, publishing, and read-only PR summaries."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from merit.errors import EnvironmentError_, UsageError, ValidationFailed


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


def file_bytes_at(repo_root: Path, rev: str, path: str | Path) -> bytes | None:
    """Read exact blob bytes, including binary retained originals, read-only."""
    try:
        result = subprocess.run(["git", "show", f"{rev}:{Path(path).as_posix()}"],
                                cwd=repo_root, capture_output=True, check=False)
        return result.stdout if result.returncode == 0 else None
    except OSError:
        return None


def files_at(repo_root: Path, rev: str, prefix: str | Path) -> list[str]:
    """Enumerate exact POSIX paths, including spaces and Unicode, read-only."""
    output = _git(repo_root, "ls-tree", "-r", "--name-only", "-z", rev,
                  "--", Path(prefix).as_posix())
    return [name for name in output.split("\0") if name] if output is not None else []


def _run(repo_root: Path, *args: str, input: str | None = None) -> str:
    """Run an argv without a shell, preserving diagnostics and the exit contract."""
    fix = "gh auth status" if args[0] == "gh" else "git status"
    try:
        result = subprocess.run(list(args), cwd=repo_root, capture_output=True,
                                encoding="utf-8", errors="replace", check=False, input=input)
    except FileNotFoundError as exc:
        package = "GitHub.cli" if args[0] == "gh" else "Git.Git"
        raise EnvironmentError_(f"{args[0]} is not installed or not on PATH: {exc}",
                                fix=f"winget install --id {package} -e") from exc
    except (OSError, ValueError) as exc:
        raise EnvironmentError_(f"Could not run {args[0]}: {exc}", fix=fix) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise EnvironmentError_(f"{args[0]} {args[1]} failed ({result.returncode}): {detail}", fix=fix)
    return result.stdout.strip() or result.stderr.strip()


def start_draft(repo_root: Path, branch: str) -> str:
    """Create and check out a named working draft."""
    if not branch or branch.startswith("-") or branch.startswith("@{"):
        raise UsageError("Choose a literal branch name.", fix="merit start-draft draft/review")
    _run(repo_root, "git", "check-ref-format", f"refs/heads/{branch}")
    return _run(repo_root, "git", "switch", "-c", branch)


def save(repo_root: Path, paths: list[str | Path], message: str) -> str:
    """Commit only explicitly named files, retaining unrelated staged changes."""
    fix = 'merit save <file> -m "Checkpoint description"'
    if not paths or not message.strip():
        raise UsageError("A checkpoint needs explicit file paths and a message.", fix=fix)
    root = Path(repo_root).resolve()
    selected = []
    to_stage = []

    def exact_file(output: str, name: str) -> bool:
        return any(entry.partition("\t")[2] == name
                   and entry.partition(" ")[0] in ("100644", "100755")
                   for entry in output.split("\0"))

    for value in paths:
        path = Path(value)
        absolute = (root / path).resolve()
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            raise UsageError(f"Checkpoint path is outside the repository: {value}", fix=fix) from None
        if not relative.parts or relative.parts[0].casefold() == ".git" or absolute.is_dir():
            raise UsageError(f"Checkpoint paths must name individual repository files: {value}", fix=fix)
        name = relative.as_posix()
        pathspec = f":(literal){name}"
        already_staged_deletion = False
        if not absolute.exists():
            indexed = _run(root, "git", "ls-files", "--stage", "-z", "--", pathspec)
            if not exact_file(indexed, name):
                historical = _run(root, "git", "ls-tree", "-z", "HEAD", "--", name)
                if not exact_file(historical, name):
                    raise UsageError(f"Checkpoint paths must name individual repository files: {value}", fix=fix)
                already_staged_deletion = True
        # Missing paths must be exact tracked files, never directory prefixes.
        # Literal pathspecs prevent wildcard expansion in filenames containing [].
        selected.append(pathspec)
        if not already_staged_deletion:
            to_stage.append(pathspec)
    if to_stage:
        _run(root, "git", "add", "-A", "--", *to_stage)
    return _run(root, "git", "commit", "--only", "-m", message, "--", *selected)


def push(repo_root: Path, remote: str = "origin") -> str:
    """Publish the current branch and set its upstream."""
    if not remote or remote.startswith("-"):
        raise UsageError("Choose a remote name.", fix="git remote -v")
    return _run(repo_root, "git", "push", "--set-upstream", "--", remote, "HEAD")


def open_pr(repo_root: Path, title: str, *, base: str | None = None) -> str:
    """Open a review request using the generated repository summary."""
    if not title.strip():
        raise UsageError("A pull request needs a title.", fix='merit open-pr --title "Review requirements"')
    args = ["gh", "pr", "create", f"--title={title}", "--body-file", "-"]
    if base is not None:
        args.append(f"--base={base}")
    return _run(repo_root, *args, input=pr_body(repo_root))


def pr_body(repo_root: Path) -> str:
    """Summarize lifecycle, provenance, candidates and effective issue status."""
    from collections import Counter

    from merit.baseline import _readiness, governed_tree_hash
    from merit.handoff import handoff_block
    from merit.model import (CANDIDATE_STATUSES, EFFECTIVE_STATUSES, PROVENANCES,
                             REQUIREMENT_STATUSES, effective_status)

    root = Path(repo_root)
    repo, readiness = _readiness(root)
    if any(f.check_id == "V3" and f.severity == "error" for f in readiness.findings):
        raise ValidationFailed("Cannot summarize structurally invalid artefacts.", fix="merit validate")
    parts = [f"Requirements: {len(repo.requirements)}"]

    def counts(label, values, vocabulary):
        counts = Counter(values)
        parts.extend(["", f"### {label}", "", "| Status | Count |" if label != "Requirement provenance"
                      else "| Provenance | Count |", "| --- | ---: |"])
        parts.extend(f"| {value} | {counts[value]} |" for value in vocabulary)

    counts("Requirement status", (r.status for r in repo.requirements.values()), REQUIREMENT_STATUSES)
    counts("Requirement provenance", (r.provenance for r in repo.requirements.values()), PROVENANCES)
    parts.extend(["", f"Candidates (not requirements): {len(repo.candidates)}"])
    counts("Candidate status", (c.status for c in repo.candidates.values()), CANDIDATE_STATUSES)
    counts("Effective issue status", (effective_status(i, repo) for i in repo.issues.values()), EFFECTIVE_STATUSES)
    parts.extend(["", "Baseline ready for preparation." if readiness.can_prepare else "Baseline not ready."])
    for reason in readiness.reasons:
        parts.append(f"- {reason}")
    for finding in readiness.findings:
        parts.append(f"- {finding.check_id} {finding.severity}: {finding.message}")
    if readiness.can_prepare and repo.baselines:
        def order(item):
            suffix = item.id.rpartition("-b")[2]
            return str(item.prepared_on), int(suffix) if suffix.isdigit() else 0, item.id

        manifest = max(repo.baselines.values(), key=order)
        relative = f"baselines/{manifest.id}.yaml"
        if manifest.governed_tree_hash == governed_tree_hash(root, exclude=relative):
            parts.extend(["", handoff_block(manifest.id)])
    return "\n".join(parts) + "\n"
