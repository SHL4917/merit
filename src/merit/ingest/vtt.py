"""WebVTT intake: map ``<v Speaker>`` voice spans, drop timing and cue metadata.

Attribution is structural: text is credited to a speaker only while a
``<v Speaker>`` span is open. ``</v>`` closes it, and any text after that (or
before the first ``<v>``) is a ``#`` line. Crediting trailing text to the
previous speaker would write words into someone's mouth, and every later check
trusts the canonical file's labels.
"""

from __future__ import annotations

import html
import re

from merit.errors import ValidationFailed
from merit.ingest.canonical import STRUCTURAL_SPEAKER

_BLOCK_BREAK = re.compile(r"\n[ \t]*\n+")
# The header line is "WEBVTT" alone or followed by whitespace and text.
_HEADER = re.compile(r"WEBVTT(?:[ \t]|$)")
# A NOTE/STYLE/REGION block starts with the keyword as a whole word; an
# identifier like NOTEBOOK-1 is an ordinary cue.
_SKIPPED_BLOCK = re.compile(r"(?:NOTE|STYLE|REGION)(?:[ \t]|$)")
# <v Name> (optionally with classes, <v.loud Name>), a bare <v> with no name, or
# the closing </v>. A bare <v> is a voice token too: stripping it as an ordinary
# tag would silently widen the previous speaker's span.
_VOICE_TOKEN = re.compile(r"<v(?:\.[^\s>]*)*(?:[ \t]+([^>]*))?>|</v>")
_TAG = re.compile(r"<[^>]*>")


def _fail(why: str) -> ValidationFailed:
    return ValidationFailed(
        f"not a usable WebVTT file: {why}",
        fix="check the file is WebVTT, or save it as .txt with 'Name: text' lines and re-run `merit ingest`",
    )


def _clean(fragment: str) -> str:
    # Tags are removed before entities are decoded so an escaped "&lt;" survives.
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def _cue_utterances(payload: str) -> list[tuple[str, str]]:
    utterances: list[tuple[str, str]] = []
    speaker = STRUCTURAL_SPEAKER
    position = 0

    def emit(fragment: str) -> None:
        spoken = _clean(fragment)
        if spoken:
            utterances.append((speaker, spoken))

    for token in _VOICE_TOKEN.finditer(payload):
        emit(payload[position : token.start()])
        position = token.end()
        # group(1) is None for </v> and a bare <v>; an empty name is unattributed.
        name = html.unescape(token.group(1) or "").strip()
        speaker = name or STRUCTURAL_SPEAKER
    emit(payload[position:])
    return utterances


def parse_vtt(text: str) -> list[tuple[str, str]]:
    """Parse normalised WebVTT into ``(speaker, text)`` utterances.

    A cue's wrapped lines are joined, then split into one utterance per
    stretch of text between voice tokens.
    """
    blocks = _BLOCK_BREAK.split(text.strip("\n"))
    # Only the first LINE is the header; "Kind: captions" lines may follow it.
    if not _HEADER.match(blocks[0].split("\n")[0]):
        raise _fail("it does not begin with a WEBVTT header")
    utterances: list[tuple[str, str]] = []
    for block in blocks[1:]:
        lines = block.split("\n")
        if _SKIPPED_BLOCK.match(lines[0]):
            continue
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            raise _fail(f"a block has no timing line: {lines[0]!r}")
        payload = " ".join(line.strip() for line in lines[timing + 1 :])
        utterances.extend(_cue_utterances(payload))
    return utterances
