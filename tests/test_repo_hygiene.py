"""Repo hygiene: source and test files are pure ASCII.

A file-writing tool has silently expanded backslash-u escapes into raw
characters three times in this repo. One such expansion put a literal combining
character inside the test that proves check V5 does not Unicode-normalise, so a
formatter applying NFC would have made that test pass vacuously. A test that
stops testing without failing is the worst outcome available, so the policy is
enforced here instead of by memory.

Non-ASCII characters in code are written as chr(0x2019) or built by a helper.

Scope: EVERY file under src/ (code, templates, package data) and the *.py files
under tests/. A non-ASCII *data fixture* under tests/ (for example a transcript
used to test a paraphrase checker) is legitimate and is not scanned; but a test
that reads one must still build any non-ASCII *code literal* with chr(), because
that is where an escape was silently expanded before.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# directory -> glob. src/ is policed in full; tests/ only for code, not data fixtures.
POLICED = {"src": "*", "tests": "*.py"}
SKIPPED_DIRS = {"__pycache__"}


def _skipped(path: Path) -> bool:
    return any(part in SKIPPED_DIRS or part.endswith(".egg-info") for part in path.parts)


def find_non_ascii(root: Path, policed=POLICED):
    """Return [(relative_path, [byte offsets])] for every non-ASCII policed file."""
    offenders = []
    for name, pattern in policed.items():
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob(pattern)):
            if not path.is_file() or _skipped(path.relative_to(root)):
                continue
            offsets = [i for i, b in enumerate(path.read_bytes()) if b > 0x7F]
            if offsets:
                offenders.append((path.relative_to(root).as_posix(), offsets))
    return offenders


def test_src_and_tests_contain_no_non_ascii_bytes():
    offenders = find_non_ascii(ROOT)
    report = "\n".join(
        f"  {path}: byte offsets {offsets[:10]}{' ...' if len(offsets) > 10 else ''}"
        for path, offsets in offenders
    )
    assert not offenders, (
        "non-ASCII bytes found; write them as chr(0x....) instead of raw characters:\n" + report
    )


def test_the_scan_itself_detects_a_planted_character(tmp_path):
    # Without this the policy test could be vacuous (wrong root, wrong glob).
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "clean.py").write_bytes(b"x = 1\n")
    (tmp_path / "src" / "dirty.py").write_bytes(b"x = '" + bytes([0xC3, 0xA9]) + b"'\n")
    (tmp_path / "tests" / "dirty_test.py").write_bytes(b"ok\n" + bytes([0xE2, 0x80, 0x99]))
    # src/ is policed in full: a non-Python template or package-data file counts
    (tmp_path / "src" / "templates").mkdir()
    (tmp_path / "src" / "templates" / "AGENTS.md").write_bytes(b"a " + bytes([0xE2, 0x80, 0x94]))
    # a non-ASCII data fixture under tests/ is deliberately out of scope
    (tmp_path / "tests" / "fixture.txt").write_bytes(bytes([0xE2, 0x80, 0x99]))
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "skip.py").write_bytes(bytes([0xFF, 0xFE]))

    found = dict(find_non_ascii(tmp_path))

    assert found == {
        "src/dirty.py": [5, 6],
        "src/templates/AGENTS.md": [2, 3, 4],
        "tests/dirty_test.py": [3, 4, 5],
    }


def test_the_policed_directories_exist_so_the_scan_is_not_vacuous():
    for name in POLICED:
        assert (ROOT / name).is_dir(), name
    assert any((ROOT / "src").rglob("*.py"))
    assert any((ROOT / "tests").rglob("*.py"))


def test_src_scan_reaches_the_shipped_non_python_files():
    scanned = {p.name for p in (ROOT / "src").rglob("*") if p.is_file() and not _skipped(p)}
    assert "AGENTS.md" in scanned and "merit.toml" in scanned
