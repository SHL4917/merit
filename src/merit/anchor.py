"""Anchor evidence to canonical sources."""

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

import yaml

from merit.errors import AnchorError, MeritError
from merit.ingest.bundle import bundle_sha256
from merit.ingest.canonical import normalise_whitespace, parse_canonical, sha256_text
from merit.model import ARTEFACT_DIRS, load_artefact, save_artefact


def _error(message):
    return AnchorError(message, fix="correct the evidence quote or source, then run: merit anchor <file>")


@dataclass(frozen=True)
class QuoteMatch:
    line: int
    end_line: int
    speaker: str


def _span(lines):
    for line in lines:
        if line.speaker == "#":
            raise _error(f"structural line {line.number} cannot support evidence")
        if line.speaker != lines[0].speaker:
            raise _error(f"speaker change at line {line.number}: {line.speaker}")
    for endpoint in (lines[0], lines[-1]):
        if endpoint.number < 1:
            raise _error(f"invalid canonical line number {endpoint.number}")
    return QuoteMatch(lines[0].number, lines[-1].number, lines[0].speaker)


def find_quote(lines, quote):
    """Find one complete line or contiguous join, using whitespace only."""
    target = normalise_whitespace(quote)
    matches = []
    if not target:
        raise _error("empty quote cannot be anchored")
    for start in range(len(lines)):
        text = ""
        for end in range(start, len(lines)):
            text = normalise_whitespace(text + " " + lines[end].text)
            if text == target:
                matches.append(lines[start:end + 1])
            if len(text) > len(target):
                break
    if len(matches) > 1:
        raise _error("ambiguous quote; candidate lines: " + ", ".join(
            f"{span[0].number}-{span[-1].number}" for span in matches))
    if matches:
        match = _span(matches[0])
        for number in (match.line, match.end_line):
            if sum(line.number == number for line in lines) != 1:
                raise _error(f"canonical line {number} is not unique")
        return match
    closest = max(lines, key=lambda line: SequenceMatcher(
        None, target, normalise_whitespace(line.text)).ratio(), default=None)
    detail = "source is empty" if closest is None else f"closest line {closest.number}: {closest.text!r}"
    # Diagnostics expose invisible and confusable characters without accepting them.
    unusual = sorted({c for c in quote + (closest.text if closest else "")
                      if ord(c) > 127 and not c.isspace()})
    if unusual:
        detail += "; differing characters: " + ", ".join(f"{c!r} U+{ord(c):04X}" for c in unusual)
    raise _error("quote not found verbatim; " + detail)


def _source(root, source):
    path = PurePosixPath(source)
    if (path.as_posix() != source or len(path.parts) != 2 or path.parts[0] != "sources"
            or path.suffix != ".md" or "\\" in source or ":" in source or ".." in path.parts):
        raise _error(f"invalid source path {source!r}")
    canonical = Path(root).joinpath(*path.parts)
    sidecar = canonical.with_suffix(".meta.yaml")
    try:
        for candidate in (canonical, sidecar):
            if not candidate.resolve().is_relative_to(Path(root).resolve()):
                raise ValueError(f"source escapes repository: {source}")
        text = canonical.read_text(encoding="utf-8")
        meta = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or meta.get("source") != source:
            raise ValueError("sidecar source does not match citation")
        if sha256_text(text) != meta.get("sha256"):
            raise ValueError("canonical SHA-256 mismatch")
        digest = bundle_sha256(meta)
        if digest != meta.get("bundle_sha256"):
            raise ValueError("bundle SHA-256 mismatch")
        return parse_canonical(text), digest
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, MeritError) as exc:
        raise _error(f"{source}: cannot anchor source: {exc}") from exc


def anchor_evidence(evidence, root):
    """Return an anchored copy; supplied anchor fields are assertions, never hints."""
    lines, digest = _source(root, evidence.source)
    if evidence.line is None:
        match = find_quote(lines, evidence.quote)
    else:
        end = evidence.end_line if evidence.end_line is not None else evidence.line
        positions = []
        for number in (evidence.line, end):
            found = [i for i, line in enumerate(lines) if line.number == number]
            if len(found) != 1:
                raise _error(f"cited canonical line {number} must match exactly one line")
            positions.append(found[0])
        start, stop = positions
        if stop < start or end < evidence.line:
            raise _error("end_line precedes line")
        span = lines[start:stop + 1]
        match = _span(span)
        if normalise_whitespace(" ".join(line.text for line in span)) != normalise_whitespace(evidence.quote):
            # Reuse the richer not-found diagnostic when no other span matches.
            find_quote(lines, evidence.quote)
            raise _error(f"quote does not match cited line(s) {evidence.line}-{end}")
    for name, actual in (("speaker", match.speaker), ("bundle_sha256", digest),
                         ("end_line", match.end_line)):
        supplied = getattr(evidence, name)
        if supplied is not None and supplied != actual:
            raise _error(f"{name} mismatch: cited {supplied!r}, actual {actual!r}")
    return replace(evidence, line=match.line, end_line=match.end_line,
                   speaker=match.speaker, bundle_sha256=digest)


def evidence_fields(artefact):
    """Yield model evidence and its precise field location."""
    for index, evidence in enumerate(getattr(artefact, "evidence", [])):
        yield f"evidence[{index}]", evidence
    for field in ("record", "confirmation_record"):
        evidence = getattr(artefact, field, None)
        if evidence is not None:
            yield field, evidence


def anchor_file(path, root=None):
    """Resolve all evidence before writing one artefact; a failure writes nothing."""
    path = Path(path)
    root = Path(root) if root is not None else path.parent.parent
    kind = next((kind for kind, directory in ARTEFACT_DIRS.items()
                 if path.parent.name == directory), None)
    if kind is None:
        raise _error(f"unknown artefact directory: {path.parent}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise _error(f"artefact escapes repository: {path}")
    try:
        artefact = load_artefact(path, kind)
    except MeritError as exc:
        raise _error(str(exc)) from exc
    updates = [(location, anchor_evidence(evidence, root))
               for location, evidence in evidence_fields(artefact)]
    for location, evidence in updates:
        if location.startswith("evidence["):
            artefact.evidence[int(location[9:-1])] = evidence
        else:
            setattr(artefact, location, evidence)
    save_artefact(artefact, path)
    return artefact
