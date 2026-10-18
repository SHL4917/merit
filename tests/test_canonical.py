import hashlib

import pytest

from merit.errors import ValidationFailed
from merit.ingest.canonical import (
    CanonicalLine,
    normalise_text,
    normalise_whitespace,
    parse_canonical,
    render_canonical,
    sha256_bytes,
    sha256_text,
)

SAMPLE = (
    "[0001] # Minutes of Sample user interview, 12 Sep 2026\n"
    "[0002] Jordan Reyes: If the connection drops we still need to work "
    "for a couple of days, maybe three.\n"
    "[0003] Sam Ellis: We must be able to export to a spreadsheet.\n"
)


def test_parse_sample_into_lines():
    lines = parse_canonical(SAMPLE)
    assert lines == [
        CanonicalLine(1, "#", "Minutes of Sample user interview, 12 Sep 2026"),
        CanonicalLine(
            2,
            "Jordan Reyes",
            "If the connection drops we still need to work for a couple of days, maybe three.",
        ),
        CanonicalLine(3, "Sam Ellis", "We must be able to export to a spreadsheet."),
    ]


def test_render_zero_pads_to_four_digits():
    out = render_canonical([CanonicalLine(7, "Sam Ellis", "Hi.")])
    assert out == "[0007] Sam Ellis: Hi.\n"


def test_round_trip_is_identity():
    assert render_canonical(parse_canonical(SAMPLE)) == SAMPLE


def test_round_trip_from_lines_is_identity():
    lines = [
        CanonicalLine(1, "#", "Agenda: three items"),
        CanonicalLine(2, "Sam Ellis", "Ratio is 3: 1 and that is fine"),
        CanonicalLine(3, "Sam Ellis", "# not a marker"),
        CanonicalLine(4, "Jordan Reyes", ""),
        CanonicalLine(5, "#", ""),
    ]
    assert parse_canonical(render_canonical(lines)) == lines


def test_structural_line_with_colon_is_not_split():
    # The '#' marker is checked before the ': ' split.
    [line] = parse_canonical("[0001] # Agenda: three items\n")
    assert line.speaker == "#"
    assert line.text == "Agenda: three items"


def test_hash_inside_text_is_not_a_marker():
    [line] = parse_canonical("[0003] Sam Ellis: # not a marker\n")
    assert line.speaker == "Sam Ellis"
    assert line.text == "# not a marker"


def test_only_the_first_colon_space_separates():
    [line] = parse_canonical("[0002] Sam Ellis: Note: the ratio is 3: 1\n")
    assert line.speaker == "Sam Ellis"
    assert line.text == "Note: the ratio is 3: 1"


def test_colon_without_space_does_not_split():
    [line] = parse_canonical("[0002] Sam Ellis: Meet at 12:30\n")
    assert line.speaker == "Sam Ellis"
    assert line.text == "Meet at 12:30"


def test_parse_accepts_crlf_and_bom():
    crlf = "﻿" + SAMPLE.replace("\n", "\r\n")
    assert parse_canonical(crlf) == parse_canonical(SAMPLE)


def test_parse_empty_is_empty_and_render_empty_is_empty():
    assert parse_canonical("") == []
    assert render_canonical([]) == ""


@pytest.mark.parametrize(
    "bad",
    [
        "no bracket here\n",
        "[1] Sam Ellis: short number\n",
        "[00001] Sam Ellis: long number\n",
        "[0001] Sam Ellis no separator\n",
        "[0001] : empty speaker\n",
        "[0001] #nospace\n",
        "[0001] Sam Ellis: ok\n\n[0003] Sam Ellis: blank line between\n",
    ],
)
def test_parse_rejects_malformed_lines(bad):
    with pytest.raises(ValidationFailed) as exc:
        parse_canonical(bad)
    assert exc.value.fix


@pytest.mark.parametrize(
    "line",
    [
        CanonicalLine(0, "Sam", "x"),
        CanonicalLine(10000, "Sam", "x"),
        CanonicalLine(1, "Sam", "two\nlines"),
        CanonicalLine(1, "Sam: Ellis", "colon in speaker would re-split"),
        CanonicalLine(1, "", "no speaker"),
    ],
)
def test_render_refuses_what_it_could_not_parse_back(line):
    with pytest.raises(ValueError):
        render_canonical([line])


# --- normalise_text -------------------------------------------------------


def test_normalise_text_folds_crlf_and_bare_cr():
    assert normalise_text("a\r\nb\rc\nd") == "a\nb\nc\nd"


def test_normalise_text_strips_leading_bom_only():
    assert normalise_text("﻿abc") == "abc"
    assert normalise_text("a﻿bc") == "a﻿bc"


def test_normalise_text_does_nothing_else():
    s = "  Tabs\tand  spaces  “quoted”  \n"
    assert normalise_text(s) == s


def test_crlf_and_lf_copies_hash_identically():
    # core.autocrlf=true: a Windows checkout must not look like tampering.
    assert sha256_text(SAMPLE.replace("\n", "\r\n")) == sha256_text(SAMPLE)


def test_bom_is_stripped_before_hashing():
    assert sha256_text("﻿" + SAMPLE) == sha256_text(SAMPLE)


def test_sha256_text_is_over_normalised_utf8():
    expected = hashlib.sha256(SAMPLE.encode("utf-8")).hexdigest()
    assert sha256_text(SAMPLE.replace("\n", "\r\n")) == expected


def test_sha256_text_known_answer():
    assert (
        sha256_text("hello\n")
        == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


def test_sha256_text_detects_a_real_edit():
    assert sha256_text(SAMPLE) != sha256_text(SAMPLE.replace("three", "four"))


def test_sha256_bytes_is_raw_and_not_normalised():
    crlf = b"a\r\nb\n"
    assert sha256_bytes(crlf) == hashlib.sha256(crlf).hexdigest()
    assert sha256_bytes(crlf) != sha256_bytes(b"a\nb\n")
    assert sha256_bytes(b"\xef\xbb\xbfabc") != sha256_bytes(b"abc")


# --- normalise_whitespace (the V5 rule, nothing more) ----------------------


def test_normalise_whitespace_collapses_and_strips():
    assert normalise_whitespace("  a \t b\n\n c  ") == "a b c"


def test_normalise_whitespace_preserves_case_and_punctuation():
    s = "We MUST export,  to a Spreadsheet."
    assert normalise_whitespace(s) == "We MUST export, to a Spreadsheet."


def test_normalise_whitespace_does_not_fold_quotes_or_unicode():
    s = "it’s “fine” café"
    assert normalise_whitespace(s) == s
    # Decomposed and precomposed forms stay distinct: no Unicode normalisation.
    assert normalise_whitespace("é") != normalise_whitespace("é")
