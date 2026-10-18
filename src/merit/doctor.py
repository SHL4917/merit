"""``merit doctor``: environment, version pin, git/gh and repo validity.

Each finding is one string prefixed ``error: `` or ``warning: ``. An empty
list means healthy. Enforcement-tier checks join later.
"""

import shutil
import subprocess
from pathlib import Path

from merit.config import load_config
from merit.errors import EnvironmentError_

ERROR = "error: "
WARNING = "warning: "


def baseline_tag_findings(repo_root: Path) -> list[str]:
    """Verify the latest manifest against the tagged tree, never current files."""
    import re
    from merit import git_ops
    from merit.baseline import GOVERNED_DIRS, tree_digest
    from merit.errors import MeritError
    from merit.ingest.canonical import sha256_text
    from merit.model import load_artefact

    manifests = []
    for path in sorted((repo_root / "baselines").glob("*.yaml")):
        try:
            manifests.append((load_artefact(path, "baseline"), path))
        except MeritError as exc:
            return [f"{ERROR}invalid baseline manifest {path}: {exc}. Fix: merit validate"]
    if not manifests:
        return []

    def order(pair):
        manifest, _ = pair
        number = re.search(r"-b([0-9]+)$", manifest.id)
        return str(manifest.prepared_on), int(number.group(1)) if number else 0, manifest.id

    manifest, path = max(manifests, key=order)
    relative = path.relative_to(repo_root).as_posix()
    tag = "baseline/" + manifest.id
    fix = "Fix: re-run merit baseline, review and merge, then complete the tag handoff."
    if tag not in git_ops.list_tags(repo_root):
        return [f"{ERROR}missing tag {tag}. Complete the baseline handoff after merge."]
    tagged_manifest = git_ops.file_at(repo_root, tag, relative)
    if tagged_manifest is None:
        return [f"{ERROR}{tag} does not contain manifest {relative}. {fix}"]
    findings = []
    if sha256_text(tagged_manifest) != sha256_text(path.read_text(encoding="utf-8")):
        findings.append(f"{ERROR}{tag} contains different manifest content for {relative}. {fix}")
    for requirement in manifest.requirements:
        name = f"requirements/{requirement.id}.yaml"
        content = git_ops.file_at(repo_root, tag, name)
        if content is None or sha256_text(content) != requirement.content_sha256:
            findings.append(f"{ERROR}{tag}: content hash mismatch for {name} (missing or drifted). {fix}")
    files = {}
    for dirname in GOVERNED_DIRS:
        for name in git_ops.files_at(repo_root, tag, dirname):
            if name == relative:
                continue
            content = git_ops.file_bytes_at(repo_root, tag, name)
            if content is None:
                findings.append(f"{ERROR}{tag}: cannot read governed file {name}. {fix}")
            else:
                files[name] = content
    if tree_digest(files) != manifest.governed_tree_hash:
        findings.append(f"{ERROR}{tag}: governed_tree_hash mismatch (missing, extra or drifted governed files). {fix}")
    return findings


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
                findings.extend(baseline_tag_findings(repo_root))
    return findings
