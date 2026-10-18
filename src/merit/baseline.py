"""Prepare a baseline through staged validation and recoverable promotion.

Tree hashes are SHA-256 of compact, sorted JSON mapping every governed POSIX
path to its content digest. UTF-8 text is BOM/line-ending normalized (R57);
retained originals under sources/raw are always hashed as exact bytes.
"""

import datetime
import getpass
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import merit
from merit.config import load_config
from merit.errors import EnvironmentError_, ValidationFailed
from merit.handoff import handoff_block
from merit.ingest.canonical import normalise_text, sha256_text
from merit.model import (ARTEFACT_DIRS, SCHEMA_VERSION, Baseline, BaselineDeferral,
                         BaselineRequirement, Repo, derive_open_issues, effective_status,
                         load_repo, save_artefact)
from merit.validate.runner import Finding, run_checks

GOVERNED_DIRS = ("sources", *ARTEFACT_DIRS.values())


def content_digest(path: str, content: bytes) -> str:
    """Use the same fingerprint for worktree files and historical Git blobs."""
    if PurePosixPath(path).parts[:2] != ("sources", "raw"):
        try:
            content = normalise_text(content.decode("utf-8")).encode("utf-8")
        except UnicodeError:
            pass  # Every governed file participates, including opaque extra files.
    return hashlib.sha256(content).hexdigest()


def tree_digest(files: dict[str, bytes]) -> str:
    fingerprints = {name: content_digest(name, content) for name, content in files.items()}
    return hashlib.sha256(json.dumps(fingerprints, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def governed_tree_hash(repo_root, *, exclude):
    root = Path(repo_root)
    files = {}
    for dirname in GOVERNED_DIRS:
        for path in sorted((root / dirname).rglob("*")):
            name = path.relative_to(root).as_posix()
            if path.is_file() and name != exclude:
                files[name] = path.read_bytes()
    return tree_digest(files)


@dataclass(frozen=True)
class BaselineResult:
    ready: tuple[str, ...]
    blocked: tuple[str, ...]
    deferred: tuple[str, ...]
    findings: tuple[Finding, ...] = ()
    reasons: tuple[str, ...] = ()
    manifest: Baseline | None = None
    handoff: str = ""

    @property
    def can_prepare(self):
        return bool(self.ready) and not self.blocked and not any(
            finding.severity == "error" for finding in self.findings)


def _readiness(root):
    findings = tuple(run_checks(root))
    structural = any(f.check_id == "V3" and f.severity == "error" for f in findings)
    repo = Repo(root=root) if structural else load_repo(root)
    ready, blocked, deferred, reasons = [], [], [], []
    errors = any(f.severity == "error" for f in findings)
    for item in sorted(repo.requirements.values(), key=lambda item: item.id):
        if item.status == "deferred":
            deferred.append(item.id)
            continue
        if item.status == "withdrawn":
            continue
        problems = [f"{identity}: {effective_status(repo.issues[identity], repo)}"
                    for identity in derive_open_issues(item, repo)]
        if item.status != "baselined":
            problems.append(f"{item.id}: select for baseline with merit apply (status {item.status})")
        if not item.acceptance_criteria:
            problems.append(f"{item.id}: add acceptance criteria through merit apply")
        if problems or errors:
            blocked.append(item.id)
            reasons.extend(problems)
        else:
            ready.append(item.id)
    return repo, BaselineResult(tuple(ready), tuple(blocked), tuple(deferred), findings, tuple(reasons))


def baseline(repo_root, *, dry_run=False, prepared_by=None):
    """Check readiness, or prepare the next manifest without tagging anything."""
    from merit.apply.transaction import apply_baseline

    root = Path(repo_root).resolve()
    config = load_config(root)
    _, result = _readiness(root)
    if dry_run:
        return result
    if not result.can_prepare:
        details = [f"{f.check_id}: {f.message}" for f in result.findings if f.severity == "error"]
        details.extend(result.reasons)
        raise ValidationFailed("Baseline not ready: " + ("; ".join(details) or "no selected requirements"),
                               fix="merit validate")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", config.project_id):
        raise EnvironmentError_("project id is not safe for a baseline filename", fix="merit doctor")
    author = getpass.getuser() if prepared_by is None else prepared_by
    if not author.strip():
        raise EnvironmentError_("prepared_by must name the preparer", fix="merit baseline --prepared-by <name>")

    def prepare(stage):
        # Recheck the actual snapshot being promoted, with real config/history.
        repo = load_repo(stage)
        numbers = [int(match.group(1)) for name in repo.baselines
                   if (match := re.fullmatch(re.escape(config.project_id) + r"-b([0-9]+)", name))]
        identity = f"{config.project_id}-b{max(numbers, default=0) + 1}"
        relative = f"baselines/{identity}.yaml"
        manifest = Baseline(id=identity, prepared_on=datetime.date.today().isoformat(), prepared_by=author,
                            merit_version=merit.__version__, schema_version=SCHEMA_VERSION,
                            governed_tree_hash=governed_tree_hash(stage, exclude=relative),
                            requirements=[BaselineRequirement(id=item.id, content_sha256=sha256_text(
                                (stage / "requirements" / (item.id + ".yaml")).read_text(encoding="utf-8")))
                                for item in sorted(repo.requirements.values(), key=lambda item: item.id)
                                if item.status == "baselined"],
                            deferred=[BaselineDeferral(id=item.id, reason=item.deferred_reason)
                                      for item in sorted(repo.requirements.values(), key=lambda item: item.id)
                                      if item.status == "deferred"],
                            open_issues=[item.id for item in sorted(repo.issues.values(), key=lambda item: item.id)
                                         if effective_status(item, repo) in ("open", "awaiting_confirmation")])
        save_artefact(manifest, stage / relative)
        return manifest

    manifest = apply_baseline(root, prepare)
    return BaselineResult(result.ready, result.blocked, result.deferred, result.findings,
                          result.reasons, manifest, handoff_block(manifest.id))
