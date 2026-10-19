"""Numbered validation rules, with raw source integrity independent of models."""

import datetime
import json
import re
from functools import lru_cache
from importlib import resources
from pathlib import PurePosixPath

import jsonschema
import yaml

import merit
from merit import git_ops
from merit import model
from merit.errors import MeritError
from merit.anchor import anchor_evidence, evidence_fields
from merit.errors import AnchorError
from merit.ingest import SUPPORTED_SUFFIXES, _canonical_text
from merit.ingest.bundle import bundle_sha256
from merit.ingest.canonical import normalise_text, sha256_bytes, sha256_text
from merit.model import ARTEFACT_DIRS, ID_PATTERNS, derive_open_issues, effective_status
from merit.validate.runner import Finding, register


@register("V9")
def decision_rules(context):
    findings = []
    for decision in context.repo.decisions.values():
        path = context.artefact_root / "decisions" / (decision.id + ".yaml")

        def fail(message, fix=None):
            findings.append(Finding("V9", path, message,
                                    fix or f"correct the decision evidence in {path}, then run: merit validate"))

        if not decision.resolves or any(identity not in context.repo.issues for identity in decision.resolves):
            fail("resolves must name at least one existing issue and no unknown issues")
        if not str(decision.decided_on).strip() or not decision.decided_by.strip():
            fail("decided_on and decided_by are required")
        if decision.record is None:
            fail("decision requires a record")
        for location, evidence in evidence_fields(decision):
            try:
                anchor_evidence(evidence, context.artefact_root)
                if any(getattr(evidence, field) is None
                       for field in ("line", "speaker", "bundle_sha256")):
                    raise AnchorError("record is not anchored", fix=f"merit anchor {path}")
            except AnchorError as exc:
                fail(f"{location}: {exc}")
        defect = model.confirmation_defect(decision, context.repo)
        if defect is not None:
            fail(defect, _CONFIRMATION_FIX)
    return findings


# Every confirmation defect has the same remedy: a record from the confirming person.
_CONFIRMATION_FIX = (
    "get the confirmation from a second person as minutes or an email in which they speak "
    "('Name: ...' lines) or that they authored; ingest it with --record-type minutes|email and "
    "--authored-by <confirmer>; set confirmed_by to that person; check with merit apply --check <proposal>")


@register("V10")
def readiness(context):
    findings = []
    for requirement in context.repo.requirements.values():
        problem = None
        if requirement.status == "deferred" and not (requirement.deferred_reason or "").strip():
            problem = "deferred requirement requires deferred_reason"
        if requirement.status == "baselined" and not requirement.acceptance_criteria:
            problem = "baselined requirement requires acceptance_criteria"
        if problem:
            path = context.artefact_root / "requirements" / (requirement.id + ".yaml")
            findings.append(Finding("V10", path, problem,
                                    f"complete readiness fields in {path}, then run: merit validate"))
    return findings


@register("V11")
def superseded_evidence(context):
    superseded = {record.supersedes for record in context.repo.supersessions.values()}
    findings = []
    for requirement in context.repo.requirements.values():
        sources = sorted({evidence.source for evidence in requirement.evidence} & superseded)
        if sources:
            path = context.artefact_root / "requirements" / (requirement.id + ".yaml")
            findings.append(Finding("V11", path,
                                    "requirement cites superseded source(s): " + ", ".join(sources),
                                    f"review replacement sources and update {path} through merit apply",
                                    "error" if requirement.status == "baselined" else "advisory"))
    return findings


def baseline_blockers(repo):
    """Return every error finding from a complete run; advisories never block.

    ``repo`` is the repository path, so malformed artefacts and source integrity
    errors participate just as they do in validation.
    """
    from merit.validate.runner import run_checks

    return [finding for finding in run_checks(repo) if finding.severity == "error"]


@register("V4")
def id_discipline(context):
    """Inspect raw identities, even when V3 prevents safe model construction."""
    findings = []
    point = git_ops.comparison_point(context.context_root)
    for kind, dirname in ARTEFACT_DIRS.items():
        current = {}
        for path in sorted((context.artefact_root / dirname).glob("*.yaml")):
            if not path.is_file():
                continue
            try:
                raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_LocatedSafeLoader)
            except (OSError, UnicodeError, yaml.YAMLError):
                continue  # V3 owns unreadable documents.
            if not isinstance(raw, dict):
                continue
            identity = raw.get("id")
            fix = f"restore a valid, unique {kind} id in {path}, then run: merit validate"
            pattern = ID_PATTERNS.get(kind)
            if not isinstance(identity, str) or (pattern and not re.fullmatch(pattern, identity)):
                findings.append(Finding("V4", path, f"invalid {kind} id: {identity!r}", fix))
            if not isinstance(identity, str):
                continue
            relative = path.relative_to(context.artefact_root).as_posix()
            if identity in current:
                findings.append(Finding("V4", path,
                                        f"duplicate {kind} id {identity!r}; also in {current[identity][0]}", fix))
            current.setdefault(identity, []).append(relative)
        if point.kind == "none" or point.rev is None:
            continue
        for name in git_ops.files_at(context.context_root, point.rev, dirname):
            historical_path = PurePosixPath(name)
            if len(historical_path.parts) != 2 or historical_path.suffix != ".yaml":
                continue
            text = git_ops.file_at(context.context_root, point.rev, name)
            try:
                prior = yaml.load(text, Loader=_LocatedSafeLoader) if text else None
            except yaml.YAMLError:
                continue
            identity = prior.get("id") if isinstance(prior, dict) else None
            if isinstance(identity, str) and name not in current.get(identity, []):
                path = context.artefact_root / name
                findings.append(Finding("V4", path,
                                        f"historical {kind} id {identity!r} must remain at {name} ({point.rev})",
                                        f"restore {identity!r} at {path}; allocate a new id for a new artefact"))
    return findings


@register("V7")
def issue_rules(context):
    findings = []
    for issue in context.repo.issues.values():
        path = context.artefact_root / "issues" / (issue.id + ".yaml")

        def fail(field, message):
            findings.append(Finding("V7", path, message,
                                    f"correct {field} in {path}, then run: merit validate"))

        if len(issue.evidence) < 2:
            fail("evidence", "issue evidence requires at least two entries")
        if not any(identity in context.repo.requirements for identity in issue.affects):
            fail("affects", "issue affects must name at least one existing requirement")
        if issue.status == "resolved":
            decision = context.repo.decisions.get(issue.resolved_by)
            if decision is None or issue.id not in decision.resolves:
                fail("resolved_by", "resolved_by must name an existing decision that resolves this issue")
        if issue.status == "dismissed" and not (issue.dismissed_reason or "").strip():
            fail("dismissed_reason", "dismissed issue requires dismissed_reason")
    return findings


@register("V8")
def baseline_issue_status(context):
    findings = []
    for requirement in context.repo.requirements.values():
        if requirement.status != "baselined":
            continue
        blockers = derive_open_issues(requirement, context.repo)
        if blockers:
            path = context.artefact_root / "requirements" / (requirement.id + ".yaml")
            detail = ", ".join(f"{identity} ({effective_status(context.repo.issues[identity], context.repo)})"
                               for identity in blockers)
            findings.append(Finding("V8", path, f"baselined requirement has blocking issues: {detail}",
                                    "resolve or dismiss the affecting issues with evidence and reasons; "
                                    "confirm provisional decisions, then run: merit validate"))
    return findings


@register("V6")
def provenance_and_status(context):
    findings = []

    def require(artefact, directory, field, message):
        value = getattr(artefact, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            path = context.artefact_root / directory / (artefact.id + ".yaml")
            findings.append(Finding("V6", path, message,
                                    f"set {field} in {path}, then run: merit validate"))

    for requirement in context.repo.requirements.values():
        path = context.artefact_root / "requirements" / (requirement.id + ".yaml")
        if not requirement.evidence:
            findings.append(Finding("V6", path,
                                    f"{requirement.provenance} requirement requires at least one evidence entry",
                                    f"add source evidence to {path}, then run: merit anchor {path}"))
        if requirement.provenance == "inferred":
            require(requirement, "requirements", "rationale", "inferred requirement requires rationale")
        for status, field in (("deferred", "deferred_reason"), ("withdrawn", "withdrawn_reason")):
            if requirement.status == status:
                require(requirement, "requirements", field, f"{status} requirement requires {field}")
    for candidate in context.repo.candidates.values():
        if candidate.status == "withdrawn":
            require(candidate, "candidates", "withdrawn_reason", "withdrawn candidate requires withdrawn_reason")
        if candidate.status == "promoted":
            if candidate.promoted_to not in context.repo.requirements:
                path = context.artefact_root / "candidates" / (candidate.id + ".yaml")
                findings.append(Finding("V6", path, "promoted candidate requires promoted_to naming an existing requirement",
                                        f"set promoted_to to an existing requirement in {path}, then run: merit validate"))
    return findings


@register("V5")
def evidence_anchoring(context):
    findings = []
    for directory in ("requirements", "issues", "decisions"):
        for artefact in getattr(context.repo, directory).values():
            path = context.artefact_root / directory / (artefact.id + ".yaml")
            for location, evidence in evidence_fields(artefact):
                try:
                    anchored = anchor_evidence(evidence, context.artefact_root)
                    if (evidence.line is None or evidence.speaker is None
                            or evidence.bundle_sha256 is None):
                        raise AnchorError("evidence is not anchored", fix=f"merit anchor {path}")
                    del anchored
                except AnchorError as exc:
                    findings.append(Finding("V5", path, f"{location}: {exc}",
                                            f"correct {location} in {path}, then run: merit anchor {path}"))
    return findings


def _source_sidecars(root):
    """Read usable mappings only; V3 owns malformed YAML and field types."""
    for path in sorted((root / "sources").glob("*.meta.yaml")):
        try:
            raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_LocatedSafeLoader)
            if isinstance(raw, dict):
                yield path, raw
        except (OSError, UnicodeError, yaml.YAMLError):
            continue


def _bundle_path(root, value, *, raw=False):
    """Accept exact repository POSIX paths, never absolute or escaping aliases."""
    if not isinstance(value, str) or "\\" in value:
        return None
    path = PurePosixPath(value)
    parts = path.parts
    if (path.as_posix() != value or path.is_absolute() or ".." in parts
            or any(":" in part for part in parts)):
        return None
    if raw:
        if len(parts) != 3 or parts[:2] != ("sources", "raw"):
            return None
    elif len(parts) != 2 or parts[0] != "sources" or path.suffix != ".md":
        return None
    candidate = root.joinpath(*parts)
    try:
        confined = candidate.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        # Valid YAML strings need not be valid filesystem paths (e.g. NUL).
        return None
    if not confined:
        return None
    return candidate


@register("V1")
def source_integrity(context):
    findings = []
    root = context.artefact_root

    def fail(path, message):
        findings.append(Finding("V1", path, message,
                                f"restore {path} from the committed bundle, then run: merit validate"))

    if not (root / "sources").is_dir():
        fail(root / "sources", "sources directory is missing or is not a directory")
        return findings

    for canonical in sorted((root / "sources").glob("*.md")):
        sidecar = canonical.with_suffix(".meta.yaml")
        if not sidecar.is_file():
            fail(sidecar, "source sidecar is missing")
    for sidecar, data in _source_sidecars(root):
        expected = "sources/" + sidecar.name[:-len(".meta.yaml")] + ".md"
        canonical = _bundle_path(root, data.get("source"))
        if data.get("source") != expected or canonical is None:
            fail(sidecar, "source path must match the canonical bundle path " + expected)
        canonical = _bundle_path(root, expected)
        if canonical is None:
            fail(sidecar, "canonical bundle path escapes the repository")
        else:
            try:
                text = canonical.read_text(encoding="utf-8")
                if sha256_text(text) != data.get("sha256"):
                    fail(canonical, "canonical SHA-256 mismatch")
            except (OSError, UnicodeError) as exc:
                fail(canonical, f"canonical file missing or unreadable: {exc}")
        origin = _bundle_path(root, data.get("origin"), raw=True)
        if origin is None:
            fail(sidecar, "original origin path is missing or invalid")
        else:
            try:
                if sha256_bytes(origin.read_bytes()) != data.get("origin_sha256"):
                    fail(origin, "original raw SHA-256 mismatch in source bundle")
            except OSError as exc:
                fail(origin, f"original file missing or unreadable in source bundle: {exc}")
        for field in ("record_type", "authored_by"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                fail(sidecar, f"source bundle missing {field}")
        if data.get("classification") not in context.config.classifications:
            fail(sidecar, "source classification is not permitted by configuration")
        try:
            recomputed = bundle_sha256(data)
            if recomputed != data.get("bundle_sha256"):
                fail(sidecar, "bundle SHA-256 mismatch: immutable metadata has changed")
        except (ValueError, TypeError, RecursionError) as exc:
            fail(sidecar, f"cannot recompute bundle SHA-256: {exc}")
    return findings


@register("V2")
def source_immutability(context):
    point = git_ops.comparison_point(context.context_root)
    if point.kind == "none" or point.rev is None:
        return []
    findings = []
    for name in git_ops.files_at(context.context_root, point.rev, "sources"):
        path = PurePosixPath(name)
        if len(path.parts) != 2 or not name.endswith(".meta.yaml"):
            continue
        prior_text = git_ops.file_at(context.context_root, point.rev, name)
        try:
            prior = yaml.load(prior_text, Loader=_LocatedSafeLoader) if prior_text else None
            now = yaml.load((context.artefact_root / name).read_text(encoding="utf-8"),
                            Loader=_LocatedSafeLoader)
            same = (isinstance(prior, dict) and isinstance(now, dict)
                    and prior.get("bundle_sha256") is not None
                    and prior["bundle_sha256"] == now.get("bundle_sha256")
                    and bundle_sha256(now) == prior["bundle_sha256"])
            # Compare actual content too: retaining the old digest is not immutability.
            canonical = _bundle_path(context.artefact_root, now.get("source")) if isinstance(now, dict) else None
            origin = _bundle_path(context.artefact_root, now.get("origin"), raw=True) if isinstance(now, dict) else None
            same = (same and canonical is not None and origin is not None
                    and sha256_text(canonical.read_text(encoding="utf-8")) == now.get("sha256")
                    and sha256_bytes(origin.read_bytes()) == now.get("origin_sha256"))
        except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError):
            same = False
        if not same:
            findings.append(Finding("V2", context.artefact_root / name,
                                    f"source bundle differs from comparison point {point.rev}",
                                    "restore the historical bundle; ingest corrections as a new source"))
    return findings


@register("V12")
def source_reextraction(context):
    findings = []
    for sidecar, data in _source_sidecars(context.artefact_root):
        origin = _bundle_path(context.artefact_root, data.get("origin"), raw=True)
        canonical = _bundle_path(context.artefact_root, data.get("source"))
        if origin is None or canonical is None:
            continue
        try:
            raw = origin.read_bytes()
            text = canonical.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue  # V1 owns missing and unreadable bundle files.
        if sha256_bytes(raw) != data.get("origin_sha256"):
            continue  # An altered original is a V1 error, never an advisory.
        version = data.get("extractor_version")
        if not isinstance(version, str):
            continue
        fix = "ingest the original as a new source with the installed extractor"
        if version != merit.__version__:
            findings.append(Finding("V12", sidecar,
                                    f"recorded extractor {version}; installed extractor {merit.__version__}",
                                    fix, "advisory"))
            continue
        try:
            if origin.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ValueError(f"unsupported original parser: {origin.suffix}")
            author = data.get("authored_by") if data.get("attribution") == "author" else None
            if normalise_text(_canonical_text(raw, origin.suffix.lower(), author)) == normalise_text(text):
                continue
            message = "canonical text does not match re-extraction of retained original"
        except (MeritError, ValueError, TypeError) as exc:
            message = f"cannot re-extract retained original: {exc}"
        findings.append(Finding("V12", canonical, message, fix))
    return findings


class _LocatedSafeLoader(yaml.SafeLoader):
    """Preserve the node location when a safe scalar constructor rejects data."""

    def construct_object(self, node, deep=False):
        try:
            return super().construct_object(node, deep=deep)
        except ValueError as exc:
            # SafeLoader's timestamp constructor raises bare ValueError for
            # impossible dates/times, losing the mark the V3 guard needs.
            raise yaml.constructor.ConstructorError(
                None, None, str(exc), node.start_mark) from exc


@lru_cache(maxsize=None)
def load_schema(kind):
    """Read installed package data, independent of the working directory."""
    path = resources.files("merit") / "schemas" / f"{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def json_data(value, _active=None):
    """YAML dates are JSON strings; leave all other types for schema checking."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        active = set() if _active is None else _active
        if id(value) in active:
            raise ValueError("recursive YAML aliases are not JSON documents")
        active.add(id(value))
        try:
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("JSON object field names must be strings")
                return {key: json_data(child, active) for key, child in value.items()}
            return [json_data(child, active) for child in value]
        finally:
            active.remove(id(value))
    return value


@register("V3")
def schema_check(context):
    findings = []
    directories = [(kind, dirname, "*.yaml") for kind, dirname in ARTEFACT_DIRS.items()]
    directories.append(("source_meta", "sources", "*.meta.yaml"))
    for kind, dirname, glob in directories:
        for path in sorted((context.artefact_root / dirname).glob(glob)):
            if not path.is_file():
                continue
            fix = f"correct {path}, then run: merit validate"
            try:
                text = path.read_text(encoding="utf-8")
                raw = yaml.load(text, Loader=_LocatedSafeLoader)
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                location = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown line"
                findings.append(Finding("V3", path, f"{location}: {getattr(exc, 'problem', None) or 'invalid YAML'}", fix))
                continue
            except (OSError, UnicodeError) as exc:
                findings.append(Finding("V3", path, f"cannot read UTF-8 YAML: {exc}", fix))
                continue
            try:
                data = json_data(raw)
            except ValueError as exc:
                findings.append(Finding("V3", path, str(exc), fix))
                continue
            validator = jsonschema.Draft202012Validator(load_schema(kind))
            for error in validator.iter_errors(data):
                location = ".".join(str(part) for part in error.absolute_path) or "document"
                findings.append(Finding("V3", path, f"{location}: {error.message}", fix))
    return findings
