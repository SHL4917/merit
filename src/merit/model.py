"""The artefact model: dataclasses, byte-deterministic load/save, and derived status.

Two things in this module are load-bearing for the whole product.

**Derived, never stored.** ``open_issues`` on a requirement and the
``awaiting_confirmation`` issue status do not exist as stored fields. Stored,
an agent could clear a block by editing the very thing being blocked.
``is_provisional``, ``effective_status`` and ``derive_open_issues`` compute
them on every read, and they are the single source of truth for everything
user-facing: the report, ``merit questions``, ``merit pr-body``,
``merit baseline`` and check V8 all call them; none displays a raw ``status``.

**Byte-deterministic save.** Task 20's baseline manifest records a
``content_sha256`` per requirement, so re-saving an unchanged artefact must
reproduce the same bytes: fixed key order (dataclass field order, never
sorted), no aliases, no wrapping, LF line ends, explicit UTF-8, and
``allow_unicode`` so a reviewer reads an apostrophe or an accent, not an escape.

The derived functions take ``(artefact, repo)``. Neither can be computed from
its argument alone (``is_provisional`` needs the record source's ``record_type``
from a sidecar; ``effective_status`` needs the issue's resolving decision), and
an explicit ``Repo`` keeps them testable without a filesystem and guarantees
they never re-read state mid-run.

Loading is strict about *structure* (unknown fields, missing required fields,
non-list where a list is required) and raises ``ValidationFailed`` naming the
file. It does not check enum values, patterns or cross-field rules: those are
the JSON Schemas' and the numbered checks' job.
"""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from merit.errors import UsageError, ValidationFailed

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Stored enums. `awaiting_confirmation` is deliberately in none of them.
# ---------------------------------------------------------------------------

REQUIREMENT_TYPES = ("functional", "constraint", "quality", "interface")
PROVENANCES = ("stated", "inferred")  # no third value: ungrounded ideas are candidates
REQUIREMENT_STATUSES = ("draft", "clarifying", "baselined", "deferred", "withdrawn")
CANDIDATE_STATUSES = ("open", "promoted", "withdrawn")
ISSUE_KINDS = ("contradiction", "revision", "scenario_difference", "misunderstanding")
ISSUE_STATUSES = ("open", "resolved", "dismissed")  # the RECORDED status

STORED_ENUMS: dict[str, tuple[str, ...]] = {
    "requirement.type": REQUIREMENT_TYPES,
    "requirement.provenance": PROVENANCES,
    "requirement.status": REQUIREMENT_STATUSES,
    "candidate.status": CANDIDATE_STATUSES,
    "issue.kind": ISSUE_KINDS,
    "issue.status": ISSUE_STATUSES,
}

# Derived values only: what a human is shown. Never written to a file.
AWAITING_CONFIRMATION = "awaiting_confirmation"
EFFECTIVE_STATUSES = ISSUE_STATUSES + (AWAITING_CONFIRMATION,)
BLOCKING_STATUSES = ("open", AWAITING_CONFIRMATION)

SELF_RECORDED_NOTE = "self_recorded_note"

# ASCII digits via [0-9]: \d matches non-ASCII digits (found in Task 4's review).
# Anchored with (?![\s\S]) ("no character follows"), not $ or \Z. The python
# jsonschema library applies `pattern` with re.search, where $ also matches before
# a trailing newline. \Z is Python-only and a SyntaxError in ECMA-262 unicode mode
# (Ajv compiles with the u flag). The lookahead is strict and valid in Python re
# and in ECMA with or without u, so the schemas reuse these exact strings.
ID_PATTERNS: dict[str, str] = {
    "requirement": r"^REQ-[A-Z]{2,6}-[0-9]{4}(?![\s\S])",
    "candidate": r"^CAN-[0-9]{4}(?![\s\S])",
    "issue": r"^ISS-[0-9]{4}(?![\s\S])",
    "decision": r"^DEC-[0-9]{4}(?![\s\S])",
    "supersession": r"^SUP-[0-9]{4}(?![\s\S])",
}

# A date as YAML gives it (2026-10-18 loads as a date) or, if quoted, a string.
Date = datetime.date | str


# ---------------------------------------------------------------------------
# Dataclasses. Field order is the on-disk key order. All keyword-only, so
# required and optional fields can sit in the order the spec lists them.
# ---------------------------------------------------------------------------

def _list_field(of: type | None = None, *, required: bool = True) -> Any:
    meta: dict[str, Any] = {"list": True}
    if of is not None:
        meta["of"] = of
    if required:
        return field(metadata=meta)
    return field(default_factory=list, metadata=meta)


def _one_field(of: type, *, required: bool = True) -> Any:
    meta = {"of": of}
    return field(metadata=meta) if required else field(default=None, metadata=meta)


@dataclass(kw_only=True)
class Evidence:
    """An anchor into a hash-pinned source.

    ``line`` (and ``bundle_sha256``/``speaker``) load as ``None`` when absent:
    an agent writes only the quote text and code fills the anchor in later.
    """

    source: str
    bundle_sha256: str | None = None
    line: int | None = None
    end_line: int | None = None
    speaker: str | None = None
    quote: str


@dataclass(kw_only=True)
class Requirement:
    # No `open_issues`: it is derived from the issue files (derive_open_issues).
    schema_version: int
    id: str
    statement: str
    type: str
    provenance: str
    status: str
    rationale: str | None = None
    evidence: list[Evidence] = _list_field(Evidence)
    acceptance_criteria: list[str] = _list_field(required=False)
    owner: str | None = None
    promoted_from: str | None = None
    deferred_reason: str | None = None
    withdrawn_reason: str | None = None


@dataclass(kw_only=True)
class Candidate:
    # No `evidence`, not even an empty one: an evidence-bearing candidate is a
    # structural error, which keeps "every requirement traces to evidence" true.
    schema_version: int
    id: str
    statement: str
    rationale: str
    raised_by: str
    raised_on: Date
    status: str
    promoted_to: str | None = None
    withdrawn_reason: str | None = None


@dataclass(kw_only=True)
class Issue:
    schema_version: int
    id: str
    kind: str
    summary: str
    evidence: list[Evidence] = _list_field(Evidence)
    affects: list[str] = _list_field()
    question: str
    status: str  # the RECORDED status; never display it, use effective_status
    owner: str
    resolved_by: str | None = None
    dismissed_reason: str | None = None


@dataclass(kw_only=True)
class Decision:
    schema_version: int
    id: str
    resolves: list[str] = _list_field()
    decided_on: Date
    decided_by: str
    outcome: str
    # Absence belongs to V9; an explicitly null/non-object record is structural.
    record: Evidence | None = field(default=None, metadata={"of": Evidence, "non_null": True})
    confirmed_by: str | None = None
    confirmation_record: Evidence | None = _one_field(Evidence, required=False)


@dataclass(kw_only=True)
class Supersession:
    schema_version: int
    id: str
    supersedes: str
    superseded_by: str
    reason: str
    recorded_by: str
    recorded_on: Date


@dataclass(kw_only=True)
class BaselineRequirement:
    id: str
    content_sha256: str


@dataclass(kw_only=True)
class BaselineDeferral:
    id: str
    reason: str


@dataclass(kw_only=True)
class Baseline:
    id: str
    prepared_on: Date
    prepared_by: str
    governed_tree_hash: str
    merit_version: str
    schema_version: int
    requirements: list[BaselineRequirement] = _list_field(BaselineRequirement)
    deferred: list[BaselineDeferral] = _list_field(BaselineDeferral)
    open_issues: list[str] = _list_field()  # a snapshot at baseline time; not a Requirement field


ARTEFACT_CLASSES: dict[str, type] = {
    "requirement": Requirement,
    "candidate": Candidate,
    "issue": Issue,
    "decision": Decision,
    "supersession": Supersession,
    "baseline": Baseline,
}

# kind -> directory under the repo root.
ARTEFACT_DIRS: dict[str, str] = {
    "requirement": "requirements",
    "candidate": "candidates",
    "issue": "issues",
    "decision": "decisions",
    "supersession": "supersessions",
    "baseline": "baselines",
}


@dataclass(kw_only=True)
class Repo:
    """Every artefact in a requirements repo, keyed by id, plus source sidecars.

    ``source_meta`` maps a source path (``sources/<stem>.md``, the string an
    evidence entry cites) to its parsed ``<stem>.meta.yaml`` sidecar.
    """

    root: Path | None = None
    requirements: dict[str, Requirement] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    issues: dict[str, Issue] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    supersessions: dict[str, Supersession] = field(default_factory=dict)
    baselines: dict[str, Baseline] = field(default_factory=dict)
    source_meta: dict[str, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def _fail(path: Path | str, problem: str) -> ValidationFailed:
    return ValidationFailed(
        f"{path}: {problem}",
        fix=f"correct {path}, then run: merit validate",
    )


def _build(cls: type, data: Any, path: Path | str, loc: str) -> Any:
    where = f"{loc}: " if loc else ""
    if not isinstance(data, dict):
        raise _fail(path, f"{where}expected a mapping of fields, found {type(data).__name__}")
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(str(k) for k in data if k not in fields)
    if unknown:
        raise _fail(path, f"{where}unknown field(s): {', '.join(unknown)}")
    required = [
        f.name for f in fields.values()
        if f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
        and f.name not in data
    ]
    if required:
        raise _fail(path, f"{where}missing required field(s): {', '.join(required)}")

    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        meta = fields[name].metadata
        of = meta.get("of")
        here = f"{loc}.{name}" if loc else name
        if meta.get("list"):
            if not isinstance(value, list):
                raise _fail(path, f"{here}: expected a list, found {type(value).__name__}")
            if of is not None:
                value = [_build(of, item, path, f"{here}[{i}]") for i, item in enumerate(value)]
        elif of is not None:
            if value is None:
                if fields[name].default is dataclasses.MISSING or meta.get("non_null"):
                    raise _fail(path, f"{here}: required, but is empty")
            else:
                value = _build(of, value, path, here)
        kwargs[name] = value
    return cls(**kwargs)


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(path, f"cannot read as UTF-8 text ({exc})") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise _fail(path, f"YAML syntax error{line}: {problem}") from exc


def load_artefact(path: Path | str, kind: str) -> Any:
    """Load one artefact file as a dataclass of ``kind``."""
    cls = ARTEFACT_CLASSES.get(kind)
    if cls is None:
        raise UsageError(
            f"unknown artefact kind {kind!r}.",
            fix=f"use one of: {', '.join(ARTEFACT_CLASSES)}",
        )
    path = Path(path)
    return _build(cls, _read_yaml(path), path, "")


def load_repo(root: Path | str) -> Repo:
    """Load every artefact under ``root``. A missing directory is an empty one."""
    root = Path(root)
    repo = Repo(root=root)
    for kind, dirname in ARTEFACT_DIRS.items():
        directory = root / dirname
        if not directory.is_dir():
            continue
        target = getattr(repo, dirname)  # Repo attributes are named after the directories
        for path in sorted(directory.glob("*.yaml")):
            if not path.is_file():
                continue
            artefact = load_artefact(path, kind)
            if path.stem != artefact.id:
                raise _fail(
                    path,
                    f"file name does not match its id {artefact.id!r}; "
                    "a mismatch could hide a duplicate id",
                )
            target[artefact.id] = artefact

    sources = root / "sources"
    if sources.is_dir():
        for path in sorted(sources.glob("*.meta.yaml")):
            meta = _read_yaml(path)
            if not isinstance(meta, dict):
                raise _fail(path, "a source sidecar must be a mapping of fields")
            stem = path.name[: -len(".meta.yaml")]
            repo.source_meta[f"sources/{stem}.md"] = meta
    return repo


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:  # never emit &id001 / *id001
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        # Indent list items under their key so a reviewer can read the file.
        return super().increase_indent(flow, False)


# NEL (U+0085), LS (U+2028) and PS (U+2029) are YAML line breaks. Inside a
# single-quoted scalar they fold to a space on reload, silently changing a quote.
# Built with chr() because source files here are pure ASCII.
_LINE_BREAKS = (chr(0x85), chr(0x2028), chr(0x2029))


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = '"' if any(c in data for c in _LINE_BREAKS) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def _to_data(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _to_data(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if getattr(value, f.name) is not None
        }
    if isinstance(value, (list, tuple)):
        return [_to_data(item) for item in value]
    return value


def dump_artefact(artefact: Any) -> bytes:
    """The exact bytes ``save_artefact`` writes: UTF-8, LF, deterministic."""
    if not any(isinstance(artefact, cls) for cls in ARTEFACT_CLASSES.values()):
        raise TypeError(f"not an artefact: {type(artefact).__name__}")
    text = yaml.dump(
        _to_data(artefact),
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,       # dataclass field order, never alphabetical
        allow_unicode=True,    # readable by a non-developer reviewer
        width=2**31 - 1,       # never fold a long quote across lines
        line_break="\n",
    )
    return text.encode("utf-8")


def save_artefact(artefact: Any, path: Path | str) -> None:
    """Write one artefact. Not atomic: ``merit apply`` owns transactions."""
    data = dump_artefact(artefact)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)  # bytes, so Windows never rewrites LF as CRLF


# ---------------------------------------------------------------------------
# Derived status: computed on read, never stored.
# ---------------------------------------------------------------------------

def _record_type(source: Any, repo: Repo) -> str | None:
    meta = repo.source_meta.get(source) if isinstance(source, str) else None
    value = meta.get("record_type") if isinstance(meta, dict) else None
    return value if isinstance(value, str) and value else None


def _authored_by(source: Any, repo: Repo) -> str | None:
    meta = repo.source_meta.get(source) if isinstance(source, str) else None
    value = meta.get("authored_by") if isinstance(meta, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _named(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def confirmation_defect(decision: Decision, repo: Repo) -> str | None:
    """Why this decision's confirmation does not count, or ``None`` if it does.

    The single implementation of the *metadata* conditions D20 and V9 place on a
    confirmation. ``is_provisional`` treats a defect as "not confirmed"; check V9
    turns the same non-``None`` reason into a finding. One rule, two consumers,
    so the display and the validator cannot disagree.

    A decision that names no ``confirmed_by`` makes no confirmation claim and has
    no defect (it is simply unconfirmed). Otherwise all of these must hold:

    * a ``confirmation_record`` exists (a bare name is self-attestation);
    * its source has a known ``record_type`` that is not ``self_recorded_note``;
    * its ``speaker``, or its source's ``authored_by``, equals ``confirmed_by``;
    * when the decision's own record is a self-recorded note, neither
      ``confirmed_by`` nor the confirmation source's ``authored_by`` is that
      note's ``authored_by``. The first closes "Alex confirms Alex" using a
      third party's transcript in which Alex speaks; both implement the spec's
      "until a second named person confirms" (D7).

    Deliberate boundary: anchoring validity is V9's alone, through ``anchor``.
    This function reads sidecar metadata only. It never reads source text or
    checks that a quote exists, because it runs once per issue on every display
    path; a fabricated confirmation quote surfaces through the report's R1
    blocker summary, which comes from the check run, not from here.
    """
    name = _named(decision.confirmed_by)
    if name is None:
        return None
    if decision.record is None:
        return "the decision has no record to confirm"
    record = decision.confirmation_record
    if record is None:
        return "confirmed_by is set but there is no confirmation_record"
    source = record.source
    record_type = _record_type(source, repo)
    if record_type is None:
        return "the confirmation_record source has no known record_type"
    if record_type == SELF_RECORDED_NOTE:
        return "the confirmation_record is itself a self-recorded note"
    confirmer_authored = _authored_by(source, repo)
    if _named(record.speaker) != name and confirmer_authored != name:
        return "neither the confirmation_record speaker nor its source author is confirmed_by"
    record_source = decision.record.source if decision.record is not None else None
    if _record_type(record_source, repo) == SELF_RECORDED_NOTE:
        note_author = _authored_by(record_source, repo)
        if note_author is not None and name == note_author:
            return "confirmed_by is the note's own author (a second person must confirm)"
        if note_author is not None and confirmer_authored == note_author:
            return "the confirmation_record is authored by the note's own author"
    return None


def is_confirmed(decision: Decision, repo: Repo) -> bool:
    return _named(decision.confirmed_by) is not None and confirmation_defect(decision, repo) is None


def is_provisional(decision: Decision, repo: Repo) -> bool:
    """True when the decision rests on a self-recorded note nobody has confirmed.

    Confirmed means ``is_confirmed``: ``confirmed_by`` is set and
    ``confirmation_defect`` is ``None``. Fails closed: a record source with no
    readable ``record_type`` cannot be shown to be anything but unverified, so
    it counts as provisional. Reads sidecar metadata only, never source text.
    """
    record_type = _record_type(decision.record.source if decision.record is not None else None, repo)
    unverified_basis = record_type is None or record_type == SELF_RECORDED_NOTE
    return unverified_basis and not is_confirmed(decision, repo)


def effective_status(issue: Issue, repo: Repo) -> str:
    """The status a human is shown. Never the raw ``issue.status``.

    ``dismissed`` and ``open`` are as recorded. ``resolved`` is only honoured
    when a decision that lists this issue backs it: a missing or unrelated
    ``resolved_by`` is still ``open``, so a bare ``status: resolved`` clears
    nothing. A provisional backing decision is ``awaiting_confirmation``.
    Any other recorded value (including a stored ``awaiting_confirmation``)
    is ``open``: only ``dismissed`` and a backed ``resolved`` lift a block.
    """
    if issue.status == "dismissed":
        return "dismissed"
    if issue.status != "resolved":
        return "open"
    decision = (
        repo.decisions.get(issue.resolved_by) if isinstance(issue.resolved_by, str) else None
    )
    if decision is None or issue.id not in decision.resolves:
        return "open"
    return AWAITING_CONFIRMATION if is_provisional(decision, repo) else "resolved"


def derive_open_issues(requirement: Requirement, repo: Repo) -> list[str]:
    """Ids of the issues affecting ``requirement`` that block a baseline, sorted."""
    return sorted(
        issue.id
        for issue in repo.issues.values()
        if requirement.id in issue.affects
        and effective_status(issue, repo) in BLOCKING_STATUSES
    )
