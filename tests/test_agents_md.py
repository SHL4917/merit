"""AGENTS.md stays a short invariants file, not an inventory."""

import re
from pathlib import Path

AGENTS = Path(__file__).resolve().parents[1] / "AGENTS.md"
TEXT = AGENTS.read_text(encoding="utf-8")
LINES = TEXT.splitlines()


def test_under_200_lines():
    assert len(LINES) < 200


def test_names_every_check_id():
    for number in range(1, 13):
        assert re.search(rf"\bV{number}\b", TEXT), f"V{number}"


def test_states_the_four_update_triggers():
    lowered = TEXT.lower()
    for phrase in ("adding or renumbering a check",
                   "interface consumed by more than one module",
                   "reverses an earlier one",
                   "re-derive something already settled"):
        assert phrase in lowered, phrase


def test_records_the_reversals_with_reasons():
    lowered = TEXT.lower()
    for phrase in ("append-only sources became immutable",
                   "`proposed` provenance value became candidates",
                   "v9 forbidding a provisional decision became v9 permitting"):
        assert phrase in lowered, phrase
    assert "hash" in lowered and "unreachable" in lowered


def test_contains_no_file_inventory():
    # Tree glyphs or ASCII tree branches mean a pasted directory listing.
    glyphs = "".join(chr(code) for code in (0x251C, 0x2514, 0x2502, 0x2500))
    assert not any(ch in TEXT for ch in glyphs)
    assert not [line for line in LINES if re.match(r"\s*([|+`]--|[|+]\s)", line)]
    # Three consecutive lines that each open with a bare file path are a listing.
    opens_with_path = [bool(re.match(r"\s*(?:[-*]\s+)?`?[\w./\-]+\.(?:py|json|md|yaml|toml)`?(?:\s|:|$)",
                                     line)) for line in LINES]
    assert not any(all(opens_with_path[i:i + 3]) for i in range(len(opens_with_path) - 2))
    # Module and test file names are greppable; a handful of mentions is context, more is inventory.
    names = set(re.findall(r"\b[\w-]+\.py\b", TEXT))
    assert len(names) <= 3, sorted(names)
    assert not re.search(r"\btest_\w+\.py\b", TEXT)
