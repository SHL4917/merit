"""Word intake: read ``word/document.xml``, joining ``<w:t>`` runs per ``<w:p>``.

A docx is a zip of XML from a third party, so the shared reader here refuses
the two cheap denial-of-service shapes before the XML is parsed: a part that
decompresses past ``MAX_PART_BYTES`` (zip bomb), and any DOCTYPE or entity
declaration (billion laughs, quadratic blowup, external entities). Word does
not write either.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from xml.parsers import expat

from merit.errors import ValidationFailed
from merit.ingest.txt import split_speaker

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_LINE_BREAKS = re.compile(r"[ \t]*[\r\n]+[ \t]*")
# A transcript's document.xml is a few MB at most; this is generous.
MAX_PART_BYTES = 64 * 1024 * 1024


_INGEST_FIX = "re-save the document as .docx from Word, or export it as .txt and re-run `merit ingest`"


def _fail(why: str, fix: str | None = None) -> ValidationFailed:
    return ValidationFailed(f"not a usable docx file: {why}", fix=fix or _INGEST_FIX)


def read_part(data: bytes, name: str, *, fix: str | None = None) -> bytes | None:
    """Return one part of a docx zip, or None if the archive has no such part.

    The size is bounded twice: by the declared size, and by reading at most
    ``MAX_PART_BYTES + 1`` bytes (belt and braces: CPython also stops reading
    at the declared size).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                info = archive.getinfo(name)
            except KeyError:
                return None
            if info.file_size > MAX_PART_BYTES:
                raise _fail(f"{name} is larger than {MAX_PART_BYTES} bytes", fix)
            with archive.open(info) as part:
                content = part.read(MAX_PART_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise _fail("it is not a zip archive", fix) from exc
    if len(content) > MAX_PART_BYTES:
        raise _fail(f"{name} is larger than {MAX_PART_BYTES} bytes", fix)
    return content


class _Forbidden(Exception):
    pass


def _refuse_declaration(*_ignored: object) -> None:
    raise _Forbidden


def read_xml_part(data: bytes, name: str, *, fix: str | None = None) -> ET.Element | None:
    """Parse one XML part of a docx, or None if the archive has no such part.

    A first pass with expat refuses any DOCTYPE or entity declaration. expat
    reads the declared encoding itself, so a UTF-16 part cannot slip past the
    way it would past a byte search for ``<!DOCTYPE``. Only a part with no
    declarations reaches ``ElementTree``, which then has nothing to expand.
    """
    content = read_part(data, name, fix=fix)
    if content is None:
        return None
    guard = expat.ParserCreate()
    guard.StartDoctypeDeclHandler = _refuse_declaration
    guard.EntityDeclHandler = _refuse_declaration
    try:
        guard.Parse(content, True)
        return ET.fromstring(content)
    except _Forbidden:
        raise _fail(f"{name} declares a DOCTYPE or entity, which Word never writes", fix) from None
    except (expat.ExpatError, ET.ParseError) as exc:
        raise _fail(f"{name} is not well-formed XML ({exc})", fix) from exc


def _descendants(element: ET.Element, *, stop_at_paragraph: bool):
    """Document-order descendants, skipping ``mc:Fallback`` subtrees.

    Text boxes appear in both ``mc:Choice`` and ``mc:Fallback``; reading both
    would emit the same text twice and make an honest quote unanchorable.
    Iterative, so a deeply nested part cannot exhaust the recursion limit.
    """
    stack = list(reversed(list(element)))
    while stack:
        node = stack.pop()
        if node.tag == _MC_FALLBACK:
            continue
        yield node
        if stop_at_paragraph and node.tag == _W + "p":
            continue
        stack.extend(reversed(list(node)))


def _paragraph_text(paragraph: ET.Element) -> str:
    pieces = []
    # A paragraph nested in a text box is its own paragraph, not part of its host's.
    for node in _descendants(paragraph, stop_at_paragraph=True):
        if node.tag != _W + "r":
            continue
        for child in node:
            if child.tag == _W + "t":
                pieces.append(child.text or "")
            elif child.tag in (_W + "tab", _W + "br"):
                pieces.append(" ")
    return _LINE_BREAKS.sub(" ", "".join(pieces)).strip()


def parse_docx(data: bytes) -> list[tuple[str, str]]:
    """Parse a docx into ``(speaker, text)`` utterances, one per non-empty paragraph.

    Deleted tracked-change text lives in ``<w:delText>`` and is not included;
    inserted text is. ``Name: text`` paragraphs are attributed as in plain text.
    """
    root = read_xml_part(data, "word/document.xml")
    if root is None:
        raise _fail("it has no word/document.xml")
    utterances = []
    for node in _descendants(root, stop_at_paragraph=False):
        if node.tag != _W + "p":
            continue
        text = _paragraph_text(node)
        if text:
            utterances.append(split_speaker(text))
    return utterances
