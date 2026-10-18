"""Disclosure control (spec 5.3): report metadata embedded in an original.

``sources/raw/`` inherits the highest classification of anything inside it. An
original ``.docx`` carries author names and revision history that a redacted
canonical extraction may have deliberately dropped, so this reports them
before anything is written.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import list2cmdline

from merit.ingest.docx import read_xml_part

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
    rerun = f"merit ingest --inspect-metadata {list2cmdline([str(path)])}"
    fix = f"re-save the document as .docx from Word, then re-run `{rerun}`"
    root = read_xml_part(path.read_bytes(), _CORE_PART, fix=fix)
    report: dict[str, str | None] = {}
    for key, tag in _CORE_FIELDS.items():
        element = root.find(tag) if root is not None else None
        text = (element.text or "").strip() if element is not None else ""
        report[key] = text or None
    return report
