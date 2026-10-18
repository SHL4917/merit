"""Disclosure control (spec 5.3): report metadata embedded in an original.

``sources/raw/`` inherits the highest classification of anything inside it. An
original ``.docx`` carries author names and revision history that a redacted
canonical extraction may have deliberately dropped, so this reports them
before anything is written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from merit.errors import ValidationFailed
from merit.ingest.docx import read_part

_CORE_PART = "docProps/core.xml"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_CORE_FIELDS = {
    "creator": "{http://purl.org/dc/elements/1.1/}creator",
    "last_modified_by": _CP + "lastModifiedBy",
    "revision": _CP + "revision",
    "created": "{http://purl.org/dc/terms/}created",
    "modified": "{http://purl.org/dc/terms/}modified",
}


def inspect_metadata(path: Path) -> dict[str, str | None]:
    """Embedded metadata of ``path``; empty for formats that carry none.

    For ``.docx`` every field is present, ``None`` when the file does not record it.
    """
    path = Path(path)
    if path.suffix.lower() != ".docx":
        return {}
    xml = read_part(path.read_bytes(), _CORE_PART)
    root = None
    if xml is not None:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValidationFailed(
                f"{path} has a malformed {_CORE_PART} ({exc})",
                fix="re-save the document as .docx from Word and re-run `merit ingest --inspect-metadata`",
            ) from exc
    report: dict[str, str | None] = {}
    for key, tag in _CORE_FIELDS.items():
        element = root.find(tag) if root is not None else None
        text = (element.text or "").strip() if element is not None else ""
        report[key] = text or None
    return report
