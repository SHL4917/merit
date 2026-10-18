"""Evidence intake: convert a transcript to canonical form and pin it as a bundle.

``ingest`` is the single exception to "``merit apply`` is the only write path"
(decision D19), and it is narrow: it may only CREATE files under ``sources/``.
There is no code path here that modifies or deletes an existing source,
original or sidecar. The only filesystem-mutating functions are the four
named in ``tests/test_ingest.py``'s allow-list, and that test fails if any
other function gains one:

* ``_stage``            writes into a temporary directory outside the repo;
* ``_ensure_dir``       creates a missing directory under ``sources/``;
* ``_create_exclusive`` creates one file with ``open(path, "xb")``, which
                        raises instead of overwriting;
* ``_rollback``         removes only the files and directories this very run
                        created, when a later step fails.

The test's static scan is a regression detector (a closed import allow-list
plus a ban on mutating names outside those four functions), not a defence
against a determined author: nothing static ties ``_rollback``'s unlink to
"only paths this run created". The behavioural tests carry that guarantee.

Atomicity: collisions are decided before any work; everything is built and
verified in a staging directory; only then is it placed in the repo.
Identity is decided by content, not filename. Re-ingesting what is already
there (same original bytes, same canonical text) is a no-op that writes
nothing, because rewriting the sidecar would destroy its true ``ingested_on``.
"""

from __future__ import annotations

import datetime
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import list2cmdline
from typing import Any

import yaml

import merit
from merit.config import Config, load_config
from merit.errors import UsageError, ValidationFailed
from merit.ingest.bundle import IMMUTABLE_META_FIELDS, bundle_sha256
from merit.ingest.canonical import (
    CanonicalLine,
    normalise_text,
    parse_canonical,
    render_canonical,
    sha256_bytes,
    sha256_text,
)
from merit.ingest.docx import parse_docx
from merit.ingest.txt import parse_txt
from merit.ingest.vtt import parse_vtt

RECORD_TYPES = ("transcript", "minutes", "email", "self_recorded_note")
SUPPORTED_SUFFIXES = (".vtt", ".docx", ".txt")

# --as names become a filename and a slug: no separators, nothing hidden.
_AS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MAX_LINES = 9999
# The attribution D11 protects. ingested_by and ingested_on are deliberately
# absent: they legitimately differ on a later re-ingest.
_DIVERGENT_FIELDS = ("record_type", "classification", "participants", "authored_by")


@dataclass(frozen=True)
class IngestResult:
    """Repo-relative POSIX paths of the bundle, and whether this call created it."""

    created: bool
    source: str
    origin: str
    meta: str
    bundle_sha256: str


# ------------------------------------------------------------------ validation


def _quote(path: Path | str) -> str:
    # Imported by name, not as the subprocess module: nothing here can spawn a process.
    return list2cmdline([str(path)])


def _is_date(value: object) -> bool:
    """A real ``datetime.date``. A datetime or a date-looking string would hash differently."""
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


def _check_inputs(config: Config, origin: Path, fields: dict[str, Any]) -> None:
    rerun = (
        f"merit ingest {_quote(origin)} --record-type <type> --authored-by <name> "
        "--session-date <YYYY-MM-DD> --classification <level> --ingested-by <name>"
    )
    record_type = fields["record_type"]
    if record_type not in RECORD_TYPES:
        raise UsageError(
            f"record_type {record_type!r} is missing or not one of: {', '.join(RECORD_TYPES)}.",
            fix=rerun,
        )
    classification = fields["classification"]
    if classification not in config.classifications:
        raise UsageError(
            f"classification {classification!r} is missing or not permitted by merit.toml "
            f"(allowed: {', '.join(config.classifications)}).",
            fix=rerun,
        )
    for key in ("authored_by", "ingested_by"):
        value = fields[key]
        if not isinstance(value, str) or not value.strip():
            raise UsageError(f"{key} must be a non-empty name.", fix=rerun)
    for key in ("session_date", "ingested_on"):
        value = fields[key]
        if not (_is_date(value) or (key == "ingested_on" and value is None)):
            raise UsageError(f"{key} must be a date (YYYY-MM-DD), got {value!r}.", fix=rerun)
    for participant in fields["participants"]:
        ok = (
            isinstance(participant, dict)
            and all(isinstance(participant.get(k), str) and participant[k].strip() for k in ("name", "role"))
        )
        if not ok:
            raise UsageError(
                f"each participant needs a name and a role, got {participant!r}.",
                fix=f"{rerun} --participant \"Name=role\"",
            )
    if origin.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise UsageError(
            f"unsupported file type {origin.suffix!r} for {origin}; supported: {', '.join(SUPPORTED_SUFFIXES)}.",
            fix="convert the file to .vtt, .docx or .txt, then re-run `merit ingest`",
        )
    if not origin.is_file():
        raise UsageError(f"{origin} is not a file.", fix=f"check the path, then re-run: {rerun}")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ------------------------------------------------------------------ extraction


def _canonical_text(data: bytes, suffix: str) -> str:
    """Parse an original into rendered canonical text, numbered from 0001."""
    if suffix == ".docx":
        utterances = parse_docx(data)
    else:
        try:
            text = normalise_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValidationFailed(
                f"the {suffix} file is not valid UTF-8 ({exc.reason} at byte {exc.start}).",
                fix="re-save the file as UTF-8 text, then re-run `merit ingest`",
            ) from exc
        utterances = parse_vtt(text) if suffix == ".vtt" else parse_txt(text)
    if not utterances:
        raise ValidationFailed(
            "no utterances were found in the file.",
            fix="check the file has content in the expected format, then re-run `merit ingest`",
        )
    if len(utterances) > _MAX_LINES:
        raise ValidationFailed(
            f"{len(utterances)} lines exceed the {_MAX_LINES} that a four-digit line number allows.",
            fix="split the recording into separate sources and ingest each with `merit ingest --as <name>`",
        )
    lines = [CanonicalLine(n, speaker, text) for n, (speaker, text) in enumerate(utterances, start=1)]
    try:
        rendered = render_canonical(lines)
    except ValueError as exc:
        raise ValidationFailed(
            f"the extracted text cannot be written in canonical form: {exc}",
            fix="correct the speaker or line named above in the original, then re-run `merit ingest`",
        ) from exc
    _require_sequential(rendered, lines)
    return rendered


def _require_sequential(rendered: str, lines: Sequence[CanonicalLine]) -> None:
    """Line numbers run 1..n and the rendered text parses back to the same lines."""
    parsed = parse_canonical(rendered)
    if [line.number for line in parsed] != list(range(1, len(lines) + 1)) or parsed != list(lines):
        raise ValidationFailed(
            "the canonical text does not round-trip through the canonical parser.",
            fix="report this file as a bug in the extractor; it cannot be ingested as is",
        )


# --------------------------------------------------------------- collision rule


def _existing_paths(paths: Sequence[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def _identical_sidecar(sidecar: Path, origin_digest: str, canonical_digest: str) -> dict | None:
    """The existing sidecar if it carries both incoming digests, else None."""
    try:
        meta = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("origin_sha256") == origin_digest and meta.get("sha256") == canonical_digest:
        return meta
    return None


def _digest(path: Path, kind: str) -> str | None:
    """Digest of an existing file by the rule its sidecar pin uses; None if unreadable."""
    try:
        data = path.read_bytes()
        return sha256_bytes(data) if kind == "bytes" else sha256_text(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _first_damage(repo_root: Path, meta: dict, sidecar_rel: str) -> tuple[str, str] | None:
    """Read-only integrity check of an existing bundle: (path, problem) or None.

    A no-op may only claim "already ingested" if the bundle is still intact:
    both files exist and match their pins, and the bundle hash recomputes.
    """
    for rel, pin, kind in (
        (meta.get("origin"), "origin_sha256", "bytes"),
        (meta.get("source"), "sha256", "text"),
    ):
        if not isinstance(rel, str):
            return sidecar_rel, f"does not name its {'original' if kind == 'bytes' else 'canonical'} file"
        path = repo_root / rel
        if not path.is_file():
            return rel, "is missing"
        if _digest(path, kind) != meta.get(pin):
            return rel, f"no longer matches the {pin} recorded in {sidecar_rel}"
    try:
        intact = bundle_sha256(meta) == meta.get("bundle_sha256")
    except (ValueError, TypeError):
        intact = False
    if not intact:
        return sidecar_rel, "has a bundle_sha256 that does not match its own fields"
    return None


def _no_op_or_raise(
    repo_root: Path,
    origin: Path,
    meta: dict,
    supplied: dict[str, Any],
    rel_meta: str,
) -> IngestResult:
    """Content is identical to an existing bundle: confirm it is intact and attributed the same.

    Raises if the bundle is damaged (we cannot say "already ingested") or if the
    supplied attribution differs from what was recorded (a silent no-op would
    keep the old value, e.g. the weaker record_type). Writes nothing either way.
    """
    damage = _first_damage(repo_root, meta, rel_meta)
    if damage is not None:
        path, problem = damage
        raise ValidationFailed(
            f"{origin} is already ingested, but {path} {problem}. Sources are immutable, "
            "so the existing bundle cannot be repaired by ingesting again.",
            fix=f"git checkout -- {_quote(path)}",
        )
    divergent = [
        f"{key} (recorded {meta.get(key)!r}, supplied {supplied[key]!r})"
        for key in _DIVERGENT_FIELDS
        if meta.get(key) != supplied[key]
    ]
    if divergent:
        raise UsageError(
            f"{origin} is already ingested as {meta['source']}, but the attribution differs: "
            f"{'; '.join(divergent)}. Nothing is rewritten, and keeping the recorded value silently "
            "could weaken a confirmation requirement. To record this file under different "
            "attribution, ingest it under a new name; to correct the recorded one, supersede the source.",
            fix=f"merit ingest {_quote(origin)} --as <new-name> (same other options)",
        )
    return IngestResult(False, meta["source"], meta["origin"], rel_meta, meta["bundle_sha256"])


# ----------------------------------------------------------- filesystem choke points


def _stage(staging: Path, name: str, data: bytes) -> Path:
    path = staging / name
    path.write_bytes(data)  # bytes: no newline translation on Windows
    return path


def _ensure_dir(directory: Path, created_dirs: list[Path]) -> None:
    missing = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for path in reversed(missing):
        path.mkdir()
        created_dirs.append(path)


def _create_exclusive(path: Path, data: bytes, created: list[Path]) -> None:
    # "xb" raises FileExistsError rather than overwrite. The path is recorded
    # as soon as it exists, so a failed write is rolled back too.
    with open(path, "xb") as handle:
        created.append(path)
        handle.write(data)


def _rollback(created_files: list[Path], created_dirs: list[Path]) -> list[Path]:
    """Undo this run's own creations; never touches anything it did not create.

    Returns the paths it could not remove, so the caller can say so.
    """
    leftover: list[Path] = []
    for path in reversed(created_files):
        try:
            path.unlink()
        except OSError:
            leftover.append(path)
    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except OSError:
            leftover.append(directory)
    return leftover


def _place(staged: Sequence[tuple[Path, Path]]) -> None:
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for source, destination in staged:
            _ensure_dir(destination.parent, created_dirs)
            _create_exclusive(destination, source.read_bytes(), created_files)
    except BaseException as failure:
        leftover = _rollback(created_files, created_dirs)
        if leftover and isinstance(failure, Exception):
            names = ", ".join(str(p) for p in leftover)
            raise OSError(
                f"{failure}. Rollback could not remove what this run created, so a partial "
                f"bundle remains; delete these by hand before retrying: {names}"
            ) from failure
        raise


# ----------------------------------------------------------------------- ingest


def ingest(
    repo_root: Path,
    origin: Path,
    *,
    record_type: str | None,
    authored_by: str,
    session_date: datetime.date,
    participants: Sequence[dict[str, str]],
    classification: str | None,
    ingested_by: str,
    ingested_on: datetime.date | None = None,
    as_name: str | None = None,
) -> IngestResult:
    """Ingest one original into ``sources/`` as a hash-pinned bundle.

    Creates ``sources/raw/<name><ext>`` (the original, byte for byte),
    ``sources/<date>-<slug>.md`` (canonical text) and
    ``sources/<date>-<slug>.meta.yaml`` (the sidecar). Returns the bundle with
    ``created=False`` if an identical bundle is already present, writing nothing.
    """
    repo_root, origin = Path(repo_root), Path(origin)
    config = load_config(repo_root)
    participants = [dict(p) if isinstance(p, dict) else p for p in participants]
    _check_inputs(
        config,
        origin,
        dict(
            record_type=record_type,
            classification=classification,
            authored_by=authored_by,
            ingested_by=ingested_by,
            session_date=session_date,
            ingested_on=ingested_on,
            participants=participants,
        ),
    )
    if as_name is not None and not (_AS_NAME.fullmatch(as_name) and _slug(as_name)):
        raise UsageError(
            f"--as {as_name!r} must start with a letter or digit and use only letters, digits, '.', '_' and '-'.",
            fix=f"merit ingest {_quote(origin)} --as <name> (same other options)",
        )

    suffix = origin.suffix.lower()
    raw_name = f"{as_name}{origin.suffix}" if as_name else origin.name
    stem = f"{session_date.isoformat()}-{_slug(as_name or origin.stem)}"
    if not _slug(as_name or origin.stem):
        raise UsageError(
            f"cannot derive a name from {origin.name!r}.",
            fix=f"merit ingest {_quote(origin)} --as <name> (same other options)",
        )
    rel_raw = f"sources/raw/{raw_name}"
    rel_source = f"sources/{stem}.md"
    rel_meta = f"sources/{stem}.meta.yaml"
    targets = [repo_root / rel_raw, repo_root / rel_source, repo_root / rel_meta]

    # Collisions are decided before any extraction work.
    existing = _existing_paths(targets)
    if existing:
        data = origin.read_bytes()
        canonical = _canonical_text(data, suffix)
        meta = _identical_sidecar(targets[2], sha256_bytes(data), sha256_text(canonical))
        if meta is not None:
            supplied = dict(
                record_type=record_type,
                classification=classification,
                participants=participants,
                authored_by=authored_by,
            )
            return _no_op_or_raise(repo_root, origin, meta, supplied, rel_meta)
        names = ", ".join(p.relative_to(repo_root).as_posix() for p in existing)
        raise UsageError(
            f"{origin} would be ingested as {rel_raw} and {rel_source}, but {names} already exists "
            "and is not an intact bundle of this exact file. Sources are immutable and never overwritten.",
            fix=f"merit ingest {_quote(origin)} --as <new-name> (same other options)",
        )

    data = origin.read_bytes()
    canonical = _canonical_text(data, suffix)
    meta = {
        "source": rel_source,
        "sha256": sha256_text(canonical),
        "origin": rel_raw,
        "origin_sha256": sha256_bytes(data),
        "extractor_version": merit.__version__,
        "record_type": record_type,
        "authored_by": authored_by,
        "session_date": session_date,
        "participants": participants,
        "classification": classification,
        "ingested_on": ingested_on or datetime.date.today(),
        "ingested_by": ingested_by,
    }
    meta["bundle_sha256"] = bundle_sha256(meta)
    sidecar = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).encode("utf-8")

    with tempfile.TemporaryDirectory(prefix="merit-ingest-") as scratch:
        staging = Path(scratch)
        staged_raw = _stage(staging, "origin", data)
        staged_source = _stage(staging, "source.md", canonical.encode("utf-8"))
        staged_meta = _stage(staging, "source.meta.yaml", sidecar)
        _verify_staged(staged_raw, staged_source, staged_meta, meta)
        _place([(staged_raw, targets[0]), (staged_source, targets[1]), (staged_meta, targets[2])])
    return IngestResult(True, rel_source, rel_raw, rel_meta, meta["bundle_sha256"])


def _verify_staged(raw: Path, source: Path, sidecar: Path, meta: dict[str, Any]) -> None:
    """Re-read what was staged and prove it is the bundle the sidecar describes."""
    loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    problems = []
    if sha256_bytes(raw.read_bytes()) != meta["origin_sha256"]:
        problems.append("the staged original differs from the file supplied")
    if sha256_text(source.read_bytes().decode("utf-8")) != meta["sha256"]:
        problems.append("the staged canonical text does not match its sha256")
    if loaded != meta or list(loaded) != [*IMMUTABLE_META_FIELDS, "bundle_sha256"]:
        problems.append("the staged sidecar does not read back as written")
    elif bundle_sha256(loaded) != loaded["bundle_sha256"]:
        problems.append("the staged sidecar's bundle_sha256 does not recompute")
    if problems:
        raise ValidationFailed(
            "ingest verification failed: " + "; ".join(problems) + ". Nothing was written.",
            fix="re-run `merit ingest`; if it fails again, report it as a bug",
        )
