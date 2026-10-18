"""Word intake: read ``word/document.xml``, joining ``<w:t>`` runs per ``<w:p>``."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from merit.errors import ValidationFailed
from merit.ingest.txt import split_speaker

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_LINE_BREAKS = re.compile(r"[ \t]*[\r\n]+[ \t]*")


def _fail(why: str) -> ValidationFailed:
    return ValidationFailed(
        f"not a usable docx file: {why}",
        fix="re-save the document as .docx from Word, or export it as .txt and re-run `merit ingest`",
    )


def read_part(data: bytes, name: str) -> bytes | None:
    """Return one part of a docx zip, or None if the archive has no such part."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                return archive.read(name)
            except KeyError:
                return None
    except zipfile.BadZipFile as exc:
        raise _fail("it is not a zip archive") from exc


def parse_docx(data: bytes) -> list[tuple[str, str]]:
    """Parse a docx into ``(speaker, text)`` utterances, one per non-empty paragraph.

    Deleted tracked-change text lives in ``<w:delText>`` and is not included;
    inserted text is. ``Name: text`` paragraphs are attributed as in plain text.
    """
    xml = read_part(data, "word/document.xml")
    if xml is None:
        raise _fail("it has no word/document.xml")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise _fail(f"word/document.xml is not well-formed XML ({exc})") from exc
    utterances = []
    for paragraph in root.iter(_W + "p"):
        pieces = []
        for run in paragraph.iter(_W + "r"):
            for child in run:
                if child.tag == _W + "t":
                    pieces.append(child.text or "")
                elif child.tag in (_W + "tab", _W + "br"):
                    pieces.append(" ")
        text = _LINE_BREAKS.sub(" ", "".join(pieces)).strip()
        if text:
            utterances.append(split_speaker(text))
    return utterances
