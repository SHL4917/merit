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
colon is attributed. The colon is an ASCII ': ' (or end of line) or a
full-width colon, which needs no following space.

Predicate edits are risky: it is orthographic guesswork, and "stricter" is not
"safer" when its job is to permit legitimate attribution, because every needless
rejection makes a real utterance uncitable. tests/test_ingest.py holds a
label-shape corpus with the expected outcome of every shape; run it, and read
its trade-off comments, before changing anything here.
"""

from __future__ import annotations

import re
import unicodedata

from merit.ingest.canonical import STRUCTURAL_SPEAKER

# U+FF1A, written with chr() to keep this file pure ASCII.
_FULLWIDTH_COLON = chr(0xFF1A)
# Words are split on single spaces; a word contains no colon (either kind) or
# whitespace. After an ASCII colon a space or the end of the line is required;
# a full-width colon needs neither.
_SPEAKER = re.compile(
    rf"(?P<name>[^\s:{_FULLWIDTH_COLON}]+(?: [^\s:{_FULLWIDTH_COLON}]+){{0,3}})"
    rf"(?:{_FULLWIDTH_COLON}|:(?: |$))(?P<text>.*)"
)

# Document-header and minutes keywords. Matched case-insensitively against the
# whole prefix. A real person named "Note" is demoted to structural by this,
# visibly, which is the safe direction.
_HEADER_KEYWORDS = frozenset(
    {
        "from", "to", "cc", "bcc", "subject", "date", "sent", "re", "fwd",
        "note", "action", "decision", "agenda", "attendees", "present", "apologies",
        # Plural and minutes-style headings. Whole-prefix match, so "Action Items"
        # is stopped consistently with "Action".
        "notes", "minutes", "todo", "resolved", "decisions", "actions",
        "see also", "next steps", "action items",
    }
)
# Lower-case particles that may appear inside a name. "al" and "de" are only
# honoured after the first word, which must still look like a name.
_PARTICLES = frozenset(
    {"van", "von", "der", "de", "del", "della", "da", "di", "du", "la", "le", "bin", "ibn", "al", "ter", "ten"}
)
# Characters other than letters, digits and combining marks a name word may hold.
# The underscore is needed for diarisation labels such as SPEAKER_00.
_WORD_PUNCTUATION = frozenset(".'-_" + chr(0x2019))


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
    return match.group("name"), match.group("text").strip()


# Near-miss label shapes, used only to explain an unattributed line. They never
# attribute anything: the predicate above stays the single attribution rule.
_NUMBER_PREFIX = re.compile(r"\[\d+\]\s*")
_NUMBER_HINT = "lines start with a '[0001]' style number; strip the leading line numbers"
_NEAR_MISSES = (
    (re.compile(r"[^\s:]+ \([^)]*\):"),
     "labels carry a '(ROLE)' suffix; drop it and record roles with --participant NAME=ROLE"),
    (re.compile(r"[a-z][^\s:]*:(?: |$)"),
     "labels start lower-case; a speaker label must start with a capital letter (e.g. 'Sh2:')"),
)


def unattributed_hints(texts: list[str]) -> list[str]:
    """Explain why structural lines carry no speaker, most common shape first."""
    counts = {_NUMBER_HINT: 0, **{hint: 0 for _, hint in _NEAR_MISSES}}
    for text in texts:
        number = _NUMBER_PREFIX.match(text)
        if number and number.end() < len(text):
            counts[_NUMBER_HINT] += 1
            text = text[number.end():]
        for pattern, hint in _NEAR_MISSES:
            if pattern.match(text):
                counts[hint] += 1
                break
    return [hint for hint, count in sorted(counts.items(), key=lambda item: -item[1]) if count]


# Author attribution (``merit ingest --attribute-to-author``) credits a
# single-author record's unlabelled lines to its recorded author. It never
# credits words the author is quoting or forwarding from someone else:
# transport headers, quoted replies, "On ... wrote:" lines and everything after
# a forwarded/original-message separator stay structural.
_EMAIL_HEADER = re.compile(r"(?:from|to|cc|bcc|subject|date|sent|reply-to)\s*:", re.IGNORECASE)
_QUOTE_INTRO = re.compile(r"on\b.*\bwrote:", re.IGNORECASE)
_FORWARD_SEPARATOR = re.compile(
    r"-{2,}\s*(?:original message|forwarded message|begin forwarded message)\s*-{0,}:?", re.IGNORECASE)


def credit_author(utterances: list[tuple[str, str]], author: str) -> list[tuple[str, str]]:
    """Credit unlabelled lines to ``author``; labelled and quoted lines are kept as they are."""
    credited = []
    forwarded = False
    for speaker, text in utterances:
        if speaker == STRUCTURAL_SPEAKER and not forwarded:
            if _FORWARD_SEPARATOR.fullmatch(text):
                forwarded = True
            elif not (text.startswith(">") or _EMAIL_HEADER.match(text) or _QUOTE_INTRO.fullmatch(text)):
                speaker = author
        credited.append((speaker, text))
    return credited


def parse_txt(text: str) -> list[tuple[str, str]]:
    """Parse normalised text into ``(speaker, text)`` utterances; blank lines are skipped."""
    utterances = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line:
            utterances.append(split_speaker(line))
    return utterances
