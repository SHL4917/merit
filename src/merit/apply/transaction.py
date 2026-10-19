"""Validate an isolated tree, then promote with a recoverable rename journal."""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from merit.apply.operations import HANDLERS
from merit.apply.proposal import parse_proposal, resolve_refs
from merit.errors import EnvironmentError_, MeritError, UsageError, ValidationFailed
from merit.model import ARTEFACT_DIRS, is_provisional, load_repo
from merit.validate.runner import run_checks

GOVERNED_DIRS = ("sources", *ARTEFACT_DIRS.values())


@dataclass(frozen=True)
class ApplyResult:
    paths: tuple[Path, ...]
    warnings: tuple[str, ...]


def _files(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _promote(root, stage, backup, proposal=None):
    """Retain original directories until every rename, including scratch, succeeds.

    A filesystem cannot rename several directories in one syscall. The journal
    supplies exception atomicity; the command does not claim crash durability or
    support concurrent writers. Original files are renamed, never reserialized,
    so rollback retains their bytes and timestamps.
    """
    journal = []
    parents = {root: root.stat()}
    if proposal is not None:
        parents[proposal.parent] = proposal.parent.stat()
    try:
        for dirname in ARTEFACT_DIRS.values():
            target, staged, old = root / dirname, stage / dirname, backup / dirname
            if target.exists() and _files(target) == _files(staged):
                continue
            existed = target.exists()
            if existed:
                target.replace(old)
            journal.append((target, old, existed))
            staged.replace(target)
        if proposal is not None:
            proposal.replace(backup / "applied-proposal.yaml")
    except OSError:
        for target, old, existed in reversed(journal):
            if target.exists():
                target.replace(stage / target.name)
            if existed:
                old.replace(target)
        for path, stat in parents.items():
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        raise


def _stage(root, stage):
    shutil.copy2(root / "merit.toml", stage / "merit.toml")
    for dirname in GOVERNED_DIRS:
        source = root / dirname
        if source.exists():
            if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
                raise UsageError("governed trees must not contain symlinks", fix="merit validate")
            shutil.copytree(source, stage / dirname)
        elif dirname != "sources":
            # Preserve missing-source V1 parity with the real repository.
            (stage / dirname).mkdir()


def apply_baseline(repo_root, prepare):
    """Promote only a computed baseline using apply's validation/rollback boundary.

    This internal command-specific entry point is not a proposal operation.
    The callback writes its computed manifest only into the staged tree.
    """
    root = Path(repo_root).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="merit-baseline-", dir=root.parent,
                                         ignore_cleanup_errors=True) as scratch:
            scratch = Path(scratch)
            stage, backup = scratch / "stage", scratch / "backup"
            stage.mkdir()
            backup.mkdir()
            _stage(root, stage)
            manifest = prepare(stage)
            findings = run_checks(stage, context_root=root)
            errors = [f for f in findings if f.severity == "error"]
            if errors:
                raise ValidationFailed("; ".join(f"{f.check_id}: {f.message}" for f in errors),
                                       fix="merit validate")
            _promote(root, stage, backup)
            return manifest
    except OSError as exc:
        raise EnvironmentError_(f"cannot prepare baseline: {exc}", fix="merit baseline") from exc


def apply_proposal(repo_root, proposal_path, check=False):
    """Apply a proposal; with ``check`` run the identical transaction but promote nothing."""
    root = Path(repo_root).resolve()
    proposal = Path(proposal_path).resolve()
    if not proposal.is_relative_to(root / "proposals") or not proposal.is_file():
        raise UsageError("proposal operation index unknown: proposal must be a file under proposals/",
                         fix="merit apply proposals/<proposal>.yaml")
    data = parse_proposal(proposal)
    index = "unknown"
    fix = f"correct {proposal}, then run: merit apply {proposal}"
    try:
        with tempfile.TemporaryDirectory(prefix="merit-apply-", dir=root.parent,
                                         ignore_cleanup_errors=True) as scratch:
            scratch = Path(scratch)
            stage, backup = scratch / "stage", scratch / "backup"
            stage.mkdir()
            backup.mkdir()
            _stage(root, stage)
            written = {}
            allocated = {}
            for index, operation in enumerate(data["operations"]):
                operation, ref = resolve_refs(operation, allocated, index, fix)
                paths = HANDLERS[operation["op"]](stage, operation)
                for path in paths:
                    written[path.relative_to(stage)] = index
                if ref is not None:
                    # Every ref-capable handler writes its new artefact first.
                    allocated[ref] = paths[0].stem
            # Always run the entire suite, using the real config AND git history.
            findings = run_checks(stage, context_root=root)
            errors = [f for f in findings if f.severity == "error"]
            if errors:
                details = []
                for finding in errors:
                    relative = finding.path.relative_to(stage)
                    owner = written.get(relative, index)
                    details.append(f"operation index {owner}: {finding.check_id}: "
                                   f"{relative}: {finding.message}")
                # Most fixes name a staged path that vanishes; keep only portable advice.
                hints = dict.fromkeys(f.fix for f in errors if str(stage) not in f.fix)
                details += [f"hint: {hint}" for hint in hints]
                raise ValidationFailed("; ".join(details), fix=fix)
            if _files(stage / "sources") != _files(root / "sources"):
                raise UsageError("sources may only be created by merit ingest", fix="merit validate")
            repo = load_repo(stage)
            warnings = tuple(f"{d.id} is provisional; awaiting_confirmation"
                             for d in repo.decisions.values() if is_provisional(d, repo)
                             and Path("decisions", d.id + ".yaml") in written)
            if check:
                return ApplyResult(tuple(root / p for p in written), warnings)
            _promote(root, stage, backup, proposal)
            return ApplyResult(tuple(root / p for p in written), warnings)
    except MeritError as exc:
        if "operation index" in str(exc):
            raise
        raise type(exc)(f"operation index {index}: {exc}", fix=fix) from exc
    except OSError as exc:
        raise EnvironmentError_(f"operation index {index}: cannot apply: {exc}", fix=fix) from exc
