"""WebVTT intake: map ``<v Speaker>`` voice spans, drop timing and cue metadata."""

from __future__ import annotations

import html
import re

from merit.errors import ValidationFailed
from merit.ingest.canonical import STRUCTURAL_SPEAKER

_BLOCK_BREAK = re.compile(r"\n[ \t]*\n+")
# <v Name>, optionally with classes: <v.loud Name>.
_VOICE = re.compile(r"<v(?:\.[^\s>]*)*[ \t]+([^>]*)>")
_TAG = re.compile(r"<[^>]*>")
_SKIPPED_BLOCKS = ("NOTE", "STYLE", "REGION")


def _fail(why: str) -> ValidationFailed:
    return ValidationFailed(
        f"not a usable WebVTT file: {why}",
        fix="check the file is WebVTT, or save it as .txt with 'Name: text' lines and re-run `merit ingest`",
    )


def _clean(fragment: str) -> str:
    # Tags are removed before entities are decoded so an escaped "&lt;" survives.
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def parse_vtt(text: str) -> list[tuple[str, str]]:
    """Parse normalised WebVTT into ``(speaker, text)`` utterances.

    A cue's wrapped lines are joined into one utterance per voice span. Text
    outside any voice span is structural: a speaker is never guessed.
    """
    blocks = _BLOCK_BREAK.split(text.strip("\n"))
    if not blocks or not blocks[0].startswith("WEBVTT"):
        raise _fail("it does not begin with a WEBVTT header")
    utterances: list[tuple[str, str]] = []
    for block in blocks[1:]:
        lines = block.split("\n")
        if lines[0].startswith(_SKIPPED_BLOCKS):
            continue
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            raise _fail(f"a block has no timing line: {lines[0]!r}")
        payload = " ".join(line.strip() for line in lines[timing + 1 :])
        # re.split with one group yields [text, name, text, name, text, ...].
        parts = _VOICE.split(payload)
        leading = _clean(parts[0])
        if leading:
            utterances.append((STRUCTURAL_SPEAKER, leading))
        for name, spoken in zip(parts[1::2], parts[2::2]):
            spoken = _clean(spoken)
            if spoken:
                utterances.append((html.unescape(name).strip() or STRUCTURAL_SPEAKER, spoken))
    return utterances
