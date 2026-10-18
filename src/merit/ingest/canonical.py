"""The canonical transcript format (spec section 5.4) and its normalisation.

One utterance per line, never wrapped, line numbers zero-padded to four digits:

    [0001] # Minutes of Sample user interview, 12 Sep 2026
    [0002] Jordan Reyes: If the connection drops we still need to work.

Grammar is ``[NNNN] SPEAKER: TEXT``, split on the first ``": "`` after the
bracket. Speaker ``#`` marks a structural or unattributed line. Speaker
attribution is structural, never inferred.

Two different normalisations live here and must not be conflated:

* ``normalise_text`` makes line endings and a BOM irrelevant to the canonical
  hash (so ``core.autocrlf`` cannot cause a false tamper alarm).
* ``normalise_whitespace`` is the check-V5 comparison rule and nothing more.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from merit.errors import ValidationFailed

STRUCTURAL_SPEAKER = "#"
_MAX_NUMBER = 9999
_LINE = re.compile(r"\[(\d{4})\] (.*)")
_BOM = "﻿"


@dataclass(frozen=True)
class CanonicalLine:
    number: int
    speaker: str
    text: str


def normalise_text(text: str) -> str:
    """Strip a leading UTF-8 BOM and fold CRLF and bare CR to LF.

    Applied before hashing and before parsing. Nothing else is touched.
    """
    if text.startswith(_BOM):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to one space and strip the ends.

    This is the check-V5 comparison rule. Case, punctuation, quotes and
    Unicode forms must match exactly, so a paraphrase cannot pass.
    """
    return re.sub(r"\s+", " ", text).strip()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of the UTF-8 encoding of the *normalised* text."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes. Used for retained originals; never normalised."""
    return hashlib.sha256(data).hexdigest()


def _malformed(lineno: int, raw: str, why: str) -> ValidationFailed:
    return ValidationFailed(
        f"canonical line {lineno} is malformed ({why}): {raw!r}",
        fix="re-run `merit ingest` on the original, or restore the canonical file with `git checkout -- <file>`",
    )


def parse_canonical(text: str) -> list[CanonicalLine]:
    lines: list[CanonicalLine] = []
    body = normalise_text(text)
    if body == "":
        return lines
    rows = body.split("\n")
    if rows[-1] == "":
        rows.pop()
    for lineno, raw in enumerate(rows, start=1):
        match = _LINE.fullmatch(raw)
        if match is None:
            raise _malformed(lineno, raw, "expected '[NNNN] SPEAKER: TEXT'")
        number, rest = int(match.group(1)), match.group(2)
        # The '#' marker is checked BEFORE the ': ' split, so a structural line
        # whose text contains a colon is not mistaken for a speaker.
        if rest.startswith(STRUCTURAL_SPEAKER + " "):
            lines.append(CanonicalLine(number, STRUCTURAL_SPEAKER, rest[2:]))
            continue
        speaker, sep, utterance = rest.partition(": ")
        if not sep or not speaker:
            raise _malformed(lineno, raw, "no speaker before the first ': '")
        lines.append(CanonicalLine(number, speaker, utterance))
    return lines


def render_canonical(lines: list[CanonicalLine]) -> str:
    out: list[str] = []
    for line in lines:
        if not 1 <= line.number <= _MAX_NUMBER:
            raise ValueError(f"line number {line.number} does not fit [NNNN]")
        if "\n" in line.text or "\r" in line.text:
            raise ValueError(f"line {line.number}: text must be a single line")
        if line.speaker == "" or "\n" in line.speaker or "\r" in line.speaker:
            raise ValueError(f"line {line.number}: speaker must be non-empty and single-line")
        if line.speaker != STRUCTURAL_SPEAKER and (
            ": " in line.speaker or line.speaker.startswith(STRUCTURAL_SPEAKER + " ")
        ):
            raise ValueError(f"line {line.number}: speaker {line.speaker!r} would not parse back")
        label = line.speaker if line.speaker == STRUCTURAL_SPEAKER else line.speaker + ":"
        out.append(f"[{line.number:04d}] {label} {line.text}\n")
    return "".join(out)
