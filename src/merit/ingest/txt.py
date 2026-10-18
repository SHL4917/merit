"""Plain-text intake: ``Name: text`` is attributed, anything else is structural.

Speaker attribution is structural, never inferred, and the one failure this
product cannot tolerate is an invented attribution. So a line is attributed
only when its prefix looks like a name label and nothing looser:

* one to four words, then a colon and a space (or the end of the line);
* the first word begins with a capital letter;
* every other word begins with a capital letter or is all digits
  ("Speaker 1"), so ``note to self: x`` and ``Remember to call Bob: x`` are
  not speakers.

Everything else, including ``Meeting at 10:30: kickoff`` and URLs, becomes a
``#`` line. The price is that a legitimate label outside this shape (a
lower-case particle as in "Ludwig van Beethoven", or five words) is treated as
structural. That is the safe direction: the line is still ingested and still
shows up in ``merit citations``, it just carries no speaker.
"""

from __future__ import annotations

import re

from merit.ingest.canonical import STRUCTURAL_SPEAKER

# The apostrophe class includes the typographic apostrophe, written as an
# escape on purpose: this repo's sources are pure ASCII.
_WORD = r"[\w.'\u2019-]+"
_SPEAKER = re.compile(rf"(?P<name>{_WORD}(?: {_WORD}){{0,3}}):(?: (?P<text>.*))?")


def _is_name(label: str) -> bool:
    first, *rest = label.split(" ")
    return first[0].isupper() and all(word[0].isupper() or word.isdigit() for word in rest)


def split_speaker(line: str) -> tuple[str, str]:
    """Return ``(speaker, text)`` for one already-stripped, non-empty line."""
    match = _SPEAKER.fullmatch(line)
    if match is None or not _is_name(match.group("name")):
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
