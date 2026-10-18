"""Plain-text intake: ``Name: text`` is attributed, anything else is structural.

Speaker attribution is structural, never inferred, and the one failure this
product cannot tolerate is an invented attribution. So a line is attributed
only when its prefix looks like a name label and nothing looser:

* one to four words, then a colon and a space (or the end of the line);
* the first word starts with a capital letter, or with a letter from a script
  that has no case at all (CJK, Arabic, Hebrew, Thai, Devanagari, ...);
* every other word does too, or is all digits ("Speaker 1"), or is a known
  lower-case name particle ("Ludwig van Beethoven", "Jean-Luc de Vries");
* the whole prefix is not a document-header keyword ("From", "Subject",
  "Action", ...), which would otherwise become a speaker called "Subject".

So ``note to self: x`` and ``Remember to call Bob: x`` are not speakers.
Everything else, including ``Meeting at 10:30: kickoff`` and URLs, becomes a
``#`` line. The price: a lower-case Latin label ("bell hooks"), a label of
five or more words, or a real person named like a header keyword ("Note")
is demoted to structural. That is visible (the line still shows up in
``merit citations``) and is the safe direction. In an uncased script the
capital-letter evidence is absent, so any short prefix of that script before a
colon is attributed; the colon must be an ASCII ': '.
"""

from __future__ import annotations

import re
import unicodedata

from merit.ingest.canonical import STRUCTURAL_SPEAKER

# Words are split on single spaces; a word contains no colon or whitespace.
_SPEAKER = re.compile(r"(?P<name>[^\s:]+(?: [^\s:]+){0,3}):(?: (?P<text>.*))?")

# Document-header and minutes keywords. Matched case-insensitively against the
# whole prefix. A real person named "Note" is demoted to structural by this,
# visibly, which is the safe direction.
_HEADER_KEYWORDS = frozenset(
    {
        "from", "to", "cc", "bcc", "subject", "date", "sent", "re", "fwd",
        "note", "action", "decision", "agenda", "attendees", "present", "apologies",
    }
)
# Lower-case particles that may appear inside a name. "al" and "de" are only
# honoured after the first word, which must still look like a name.
_PARTICLES = frozenset(
    {"van", "von", "der", "de", "del", "della", "da", "di", "du", "la", "le", "bin", "ibn", "al", "ter", "ten"}
)
# Characters other than letters, digits and combining marks a name word may hold.
_WORD_PUNCTUATION = frozenset(".'-" + chr(0x2019))


def _starts_like_a_name(word: str) -> bool:
    """A letter that is capital, or belongs to a script without case."""
    first = word[0]
    return first.isalpha() and (first.isupper() or first.upper() == first.lower())


def _is_word(word: str) -> bool:
    # Combining marks are accepted: \w would reject them, and without them
    # most Devanagari and Thai words and decomposed accents are unmatchable.
    return all(
        c.isalnum() or c in _WORD_PUNCTUATION or unicodedata.category(c).startswith("M") for c in word
    )


def _is_name(label: str) -> bool:
    if label.lower() in _HEADER_KEYWORDS:
        return False
    first, *rest = label.split(" ")
    if not (_is_word(first) and _starts_like_a_name(first)):
        return False
    return all(
        _is_word(word) and (_starts_like_a_name(word) or word.isdigit() or word in _PARTICLES)
        for word in rest
    )


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
