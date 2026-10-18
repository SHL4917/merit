"""Plain-text intake: ``Name: text`` is attributed, anything else is structural.

Speaker attribution is structural, never inferred: a line is attributed only
when it starts with a name-shaped prefix (one to four words, each beginning
with a letter) followed by a colon and a space. Everything else, including
``Meeting at 10:30: kickoff`` and ``see http://x``, becomes a ``#`` line.
"""

from __future__ import annotations

import re

from merit.ingest.canonical import STRUCTURAL_SPEAKER

# A word starts with a letter (so "10:30" is not a name) and may contain digits,
# periods, hyphens and apostrophes. The escape u+2019 is the typographic apostrophe; it is
# written as an escape on purpose, as this repo's sources are pure ASCII.
_WORD = r"[^\W\d_][\w.'\u2019-]*"
_SPEAKER = re.compile(rf"(?P<name>{_WORD}(?: {_WORD}){{0,3}}):(?: (?P<text>.*))?")


def split_speaker(line: str) -> tuple[str, str]:
    """Return ``(speaker, text)`` for one already-stripped, non-empty line."""
    match = _SPEAKER.fullmatch(line)
    if match is None:
        return STRUCTURAL_SPEAKER, line
    return match.group("name"), (match.group("text") or "").strip()


def parse_txt(text: str) -> list[tuple[str, str]]:
    """Parse normalised text into ``(speaker, text)`` utterances; blank lines are skipped."""
    utterances = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line:
            utterances.append(split_speaker(line))
    return utterances
