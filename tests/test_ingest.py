import ast
import datetime
import io
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml

import merit
import merit.ingest as ingest_package
from merit import cli
from merit.errors import ExitCode, UsageError, ValidationFailed
from merit.ingest import RECORD_TYPES, IngestResult, ingest
from merit.ingest.bundle import IMMUTABLE_META_FIELDS, bundle_sha256
from merit.ingest.canonical import parse_canonical, sha256_bytes, sha256_text
from merit.ingest.docx import parse_docx
from merit.ingest.metadata import inspect_metadata
from merit.ingest.txt import parse_txt
from merit.ingest.vtt import parse_vtt
from merit.scaffold import init

# Non-ASCII characters are written as escapes on purpose: this repo's sources
# are pure ASCII by policy (an invisible literal BOM once slipped in).
BOM = "\ufeff"
E_ACUTE = "\u00e9"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# --------------------------------------------------------------------- helpers


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "project"
    init(target, project_id="sample", id_prefix="SMP")
    return target


@pytest.fixture
def incoming(tmp_path):
    """A directory outside the repo that holds files waiting to be ingested."""
    folder = tmp_path / "incoming"
    folder.mkdir()
    return folder


KWARGS = dict(
    record_type="transcript",
    authored_by="alex",
    session_date=datetime.date(2026, 9, 12),
    participants=[
        {"name": "Jordan Reyes", "role": "user"},
        {"name": "Sam Ellis", "role": "ops lead"},
    ],
    classification="INTERNAL",
    ingested_by="alex",
    ingested_on=datetime.date(2026, 10, 18),
)

VTT_TEXT = (
    BOM
    + "WEBVTT\r\n\r\n"
    + "1\r\n00:00:01.000 --> 00:00:04.000\r\n"
    + "<v Jordan Reyes>If the connection drops we still need to work for a couple of days.</v>\r\n\r\n"
    + "2\r\n00:00:05.000 --> 00:00:07.000\r\n"
    + "<v Sam Ellis>We must be able to export to a spreadsheet.</v>\r\n"
)


def write_vtt(folder: Path, name: str = "interview.vtt", text: str = VTT_TEXT) -> Path:
    path = folder / name
    path.write_bytes(text.encode("utf-8"))  # bytes: newlines must not be translated
    return path


def make_docx(target, paragraphs, *, core: str | None = None):
    """Write a minimal .docx to a path or file object and return it."""
    body = ""
    for paragraph in paragraphs:
        if paragraph.startswith("<w:p"):
            body += paragraph
        else:
            body += f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        if core is not None:
            archive.writestr("docProps/core.xml", core)
    return target


CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:creator>Pat Author</dc:creator>"
    "<cp:lastModifiedBy>Quinn Editor</cp:lastModifiedBy>"
    "<cp:revision>7</cp:revision>"
    '<dcterms:created xsi:type="dcterms:W3CDTF">2026-09-10T08:00:00Z</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-09-13T17:30:00Z</dcterms:modified>'
    "</cp:coreProperties>"
)


def snapshot(root: Path) -> dict:
    """Every path under root: file bytes, or None for a directory."""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def age_files(root: Path) -> dict:
    """Push every file's mtime far into the past and return {path: mtime_ns}.

    A later rewrite, even with identical bytes, would bump the mtime, so
    comparing these catches a silent 'overwrite with the same content'.
    """
    old = datetime.datetime(2001, 1, 1).timestamp()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            os.utime(p, (old, old))
    return mtimes(root)


def mtimes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.stat().st_mtime_ns
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def load_sidecar(repo: Path, stem: str) -> dict:
    return yaml.safe_load((repo / "sources" / f"{stem}.meta.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- parsers


def test_vtt_maps_voice_spans_to_speakers_and_drops_timing():
    text = (
        "WEBVTT\n\n"
        "NOTE a comment\nspanning two lines\n\n"
        "intro-cue\n00:00:01.000 --> 00:00:04.000\n<v Jordan Reyes>First point.</v>\n\n"
        "00:00:05.000 --> 00:00:07.000\n<v Sam Ellis>Second point.</v>\n"
    )
    assert parse_vtt(text) == [("Jordan Reyes", "First point."), ("Sam Ellis", "Second point.")]


def test_vtt_joins_a_wrapped_cue_into_one_utterance():
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Sam Ellis>We must be able\nto export to a spreadsheet.\n"
    assert parse_vtt(text) == [("Sam Ellis", "We must be able to export to a spreadsheet.")]


def test_vtt_unvoiced_cue_text_is_structural_never_guessed():
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nJordan Reyes: this looks like a speaker but is not marked\n"
    assert parse_vtt(text) == [("#", "Jordan Reyes: this looks like a speaker but is not marked")]


def test_vtt_two_voices_in_one_cue_become_two_utterances_and_markup_is_stripped():
    text = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n"
        "<v.loud Jordan Reyes><i>Yes</i> &amp; no.</v> <v Sam Ellis>Agreed.</v>\n"
    )
    assert parse_vtt(text) == [("Jordan Reyes", "Yes & no."), ("Sam Ellis", "Agreed.")]


def test_vtt_without_header_is_rejected():
    with pytest.raises(ValidationFailed, match="WEBVTT"):
        parse_vtt("00:00:01.000 --> 00:00:04.000\n<v A>hi</v>\n")


def test_vtt_block_without_a_timing_line_is_rejected_not_silently_dropped():
    with pytest.raises(ValidationFailed, match="timing"):
        parse_vtt("WEBVTT\n\nsome stray words with no cue timing\n")


def test_txt_detects_name_prefix_and_leaves_everything_else_structural():
    text = (
        "Minutes of the sample interview\n"
        "\n"
        "Jordan Reyes: If the connection drops we still need to work.\n"
        "Sam Ellis:   We must be able to export.\n"
        "Meeting at 10:30: kickoff\n"
        "see http://example.test/x for more\n"
    )
    assert parse_txt(text) == [
        ("#", "Minutes of the sample interview"),
        ("Jordan Reyes", "If the connection drops we still need to work."),
        ("Sam Ellis", "We must be able to export."),
        ("#", "Meeting at 10:30: kickoff"),
        ("#", "see http://example.test/x for more"),
    ]


def test_txt_accepts_non_ascii_and_apostrophe_names():
    text = f"Jos{E_ACUTE} O'Neil: hello\n"
    assert parse_txt(text) == [(f"Jos{E_ACUTE} O'Neil", "hello")]


def test_docx_joins_runs_per_paragraph_and_skips_empty_paragraphs():
    split_runs = (
        "<w:p><w:r><w:t>Jordan Reyes: If the connection </w:t></w:r>"
        '<w:r><w:t xml:space="preserve">drops we still need to work.</w:t></w:r></w:p>'
    )
    data = io.BytesIO()
    make_docx(data, ["Minutes of the interview", "<w:p/>", split_runs, "Sam Ellis: We must export."])
    assert parse_docx(data.getvalue()) == [
        ("#", "Minutes of the interview"),
        ("Jordan Reyes", "If the connection drops we still need to work."),
        ("Sam Ellis", "We must export."),
    ]


def test_docx_line_breaks_become_spaces_and_deleted_text_is_excluded():
    paragraph = (
        "<w:p><w:r><w:t>one</w:t><w:br/><w:t>two</w:t><w:tab/><w:t>three</w:t></w:r>"
        "<w:del><w:r><w:delText>REMOVED</w:delText></w:r></w:del></w:p>"
    )
    data = io.BytesIO()
    make_docx(data, [paragraph])
    assert parse_docx(data.getvalue()) == [("#", "one two three")]


def test_docx_that_is_not_a_zip_or_lacks_a_body_is_rejected():
    with pytest.raises(ValidationFailed, match="docx"):
        parse_docx(b"this is not a zip file")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    with pytest.raises(ValidationFailed, match="word/document.xml"):
        parse_docx(empty.getvalue())
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("word/document.xml", "<w:document")
    with pytest.raises(ValidationFailed, match="XML"):
        parse_docx(broken.getvalue())


# ------------------------------------------------------------- ingest: the happy path


def test_vtt_round_trips_into_a_hash_pinned_bundle(repo, incoming):
    original = write_vtt(incoming)
    result = ingest(repo, original, **KWARGS)

    assert isinstance(result, IngestResult) and result.created is True
    stem = "2026-09-12-interview"
    assert result.source == f"sources/{stem}.md"
    assert result.origin == "sources/raw/interview.vtt"
    assert result.meta == f"sources/{stem}.meta.yaml"

    # The original is retained byte for byte: BOM and CRLF included.
    raw_bytes = (repo / result.origin).read_bytes()
    assert raw_bytes == original.read_bytes()
    assert raw_bytes.startswith(BOM.encode("utf-8")) and b"\r\n" in raw_bytes

    # The canonical text round-trips through the Task 4 parser, numbered from 1.
    canonical = (repo / result.source).read_bytes().decode("utf-8")
    assert canonical == (
        "[0001] Jordan Reyes: If the connection drops we still need to work for a couple of days.\n"
        "[0002] Sam Ellis: We must be able to export to a spreadsheet.\n"
    )
    assert [line.number for line in parse_canonical(canonical)] == [1, 2]

    meta = load_sidecar(repo, stem)
    assert list(meta) == [*IMMUTABLE_META_FIELDS, "bundle_sha256"]
    assert meta["source"] == result.source
    assert meta["origin"] == result.origin
    assert meta["sha256"] == sha256_text(canonical)
    assert meta["origin_sha256"] == sha256_bytes(original.read_bytes())
    assert meta["sha256"] != meta["origin_sha256"]
    assert meta["extractor_version"] == merit.__version__
    assert meta["record_type"] == "transcript"
    assert meta["authored_by"] == "alex"
    assert meta["session_date"] == datetime.date(2026, 9, 12)
    assert meta["participants"] == KWARGS["participants"]
    assert meta["classification"] == "INTERNAL"
    assert meta["ingested_on"] == datetime.date(2026, 10, 18)
    assert meta["ingested_by"] == "alex"
    # Recomputed from the sidecar as read back from disk, not from what was in memory.
    assert meta["bundle_sha256"] == bundle_sha256(meta) == result.bundle_sha256


def test_the_bundle_changes_if_attribution_is_flipped(repo, incoming):
    ingest(repo, write_vtt(incoming), **{**KWARGS, "record_type": "self_recorded_note"})
    meta = load_sidecar(repo, "2026-09-12-interview")
    assert bundle_sha256({**meta, "record_type": "transcript"}) != meta["bundle_sha256"]


def test_ingested_on_defaults_to_today(repo, incoming):
    kwargs = {k: v for k, v in KWARGS.items() if k != "ingested_on"}
    ingest(repo, write_vtt(incoming), **kwargs)
    assert load_sidecar(repo, "2026-09-12-interview")["ingested_on"] == datetime.date.today()


def test_txt_with_crlf_and_bom_is_ingested_and_the_original_kept_raw(repo, incoming):
    text = BOM + "Minutes\r\nJordan Reyes: We need offline mode.\r\n"
    original = incoming / "notes.txt"
    original.write_bytes(text.encode("utf-8"))
    result = ingest(repo, original, **KWARGS)
    assert (repo / result.origin).read_bytes() == original.read_bytes()
    assert (repo / result.source).read_bytes().decode("utf-8") == (
        "[0001] # Minutes\n[0002] Jordan Reyes: We need offline mode.\n"
    )


def test_docx_is_ingested_and_the_zip_kept_unchanged(repo, incoming):
    original = make_docx(incoming / "minutes.docx", ["Sam Ellis: Export to a spreadsheet."], core=CORE_XML)
    result = ingest(repo, original, **KWARGS)
    assert (repo / result.origin).read_bytes() == original.read_bytes()
    assert (repo / result.source).read_text(encoding="utf-8") == "[0001] Sam Ellis: Export to a spreadsheet.\n"
    assert load_sidecar(repo, "2026-09-12-minutes")["origin_sha256"] == sha256_bytes(original.read_bytes())


def test_extension_matching_is_case_insensitive_but_the_name_is_kept(repo, incoming):
    result = ingest(repo, write_vtt(incoming, "Interview.VTT"), **KWARGS)
    assert result.origin == "sources/raw/Interview.VTT"
    assert result.source == "sources/2026-09-12-interview.md"


def test_ingest_creates_sources_raw_when_git_did_not_track_the_empty_directory(repo, incoming):
    (repo / "sources" / "raw").rmdir()
    (repo / "sources").rmdir()
    ingest(repo, write_vtt(incoming), **KWARGS)
    assert (repo / "sources" / "raw" / "interview.vtt").is_file()


def test_staging_directory_is_cleaned_up_after_success_and_failure(repo, incoming, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    ingest(repo, write_vtt(incoming), **KWARGS)
    assert list(scratch.iterdir()) == []
    bad = incoming / "bad.vtt"
    bad.write_bytes(b"not a vtt at all\n")
    with pytest.raises(ValidationFailed):
        ingest(repo, bad, **KWARGS)
    assert list(scratch.iterdir()) == []


# ------------------------------------------------------------ ingest: rejected input


@pytest.mark.parametrize("record_type", [None, "", "transcrpt", "TRANSCRIPT"])
def test_missing_or_unknown_record_type_is_rejected_and_nothing_is_written(repo, incoming, record_type):
    original = write_vtt(incoming)
    before = snapshot(repo)
    with pytest.raises(UsageError) as exc:
        ingest(repo, original, **{**KWARGS, "record_type": record_type})
    assert "record_type" in str(exc.value)
    for allowed in RECORD_TYPES:
        assert allowed in str(exc.value)
    assert exc.value.fix
    assert snapshot(repo) == before


def test_record_types_are_the_four_in_the_spec():
    assert RECORD_TYPES == ("transcript", "minutes", "email", "self_recorded_note")


@pytest.mark.parametrize("classification", [None, "", "SECRET", "internal"])
def test_unlisted_classification_is_rejected_against_merit_toml(repo, incoming, classification):
    original = write_vtt(incoming)
    before = snapshot(repo)
    with pytest.raises(UsageError) as exc:
        ingest(repo, original, **{**KWARGS, "classification": classification})
    assert "classification" in str(exc.value)
    assert "INTERNAL" in str(exc.value)  # the allow-list is shown
    assert snapshot(repo) == before


def test_classification_allow_list_comes_from_the_project_config(repo, incoming):
    toml = repo / "merit.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            'allowed = ["PUBLIC", "INTERNAL", "CONFIDENTIAL"]', 'allowed = ["SECRET"]'
        ),
        encoding="utf-8",
    )
    original = write_vtt(incoming)
    with pytest.raises(UsageError):
        ingest(repo, original, **KWARGS)  # INTERNAL is no longer allowed
    ingest(repo, original, **{**KWARGS, "classification": "SECRET"})


@pytest.mark.parametrize("field", ["authored_by", "ingested_by"])
def test_blank_attribution_is_rejected(repo, incoming, field):
    with pytest.raises(UsageError, match=field):
        ingest(repo, write_vtt(incoming), **{**KWARGS, field: "  "})
    assert not (repo / "sources" / "raw" / "interview.vtt").exists()


def test_participants_must_each_have_a_name_and_a_role(repo, incoming):
    with pytest.raises(UsageError, match="participant"):
        ingest(repo, write_vtt(incoming), **{**KWARGS, "participants": [{"name": "Jordan Reyes"}]})
    assert not (repo / "sources" / "raw" / "interview.vtt").exists()


def test_unsupported_extension_and_missing_file_are_usage_errors(repo, incoming):
    odd = incoming / "slides.pptx"
    odd.write_bytes(b"x")
    with pytest.raises(UsageError, match=r"\.pptx"):
        ingest(repo, odd, **KWARGS)
    with pytest.raises(UsageError, match="nope.vtt"):
        ingest(repo, incoming / "nope.vtt", **KWARGS)


def test_a_source_with_no_utterances_is_rejected(repo, incoming):
    empty = incoming / "empty.txt"
    empty.write_bytes(b"\n\n")
    before = snapshot(repo)
    with pytest.raises(ValidationFailed, match="no utterances"):
        ingest(repo, empty, **KWARGS)
    assert snapshot(repo) == before


def test_a_text_file_that_is_not_utf8_is_rejected(repo, incoming):
    latin = incoming / "latin.txt"
    latin.write_bytes(b"Jos\xe9: hello\n")
    before = snapshot(repo)
    with pytest.raises(ValidationFailed, match="UTF-8"):
        ingest(repo, latin, **KWARGS)
    assert snapshot(repo) == before


def test_more_lines_than_the_four_digit_numbering_allows_is_rejected_cleanly(repo, incoming):
    big = incoming / "big.txt"
    big.write_bytes(("\n".join(f"Line {i}" for i in range(10_000)) + "\n").encode("utf-8"))
    before = snapshot(repo)
    with pytest.raises(ValidationFailed, match="9999"):
        ingest(repo, big, **KWARGS)
    assert snapshot(repo) == before


def test_a_speaker_that_cannot_round_trip_is_rejected(repo, incoming):
    odd = incoming / "odd.vtt"
    odd.write_bytes(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Re: budget>hi</v>\n")
    before = snapshot(repo)
    with pytest.raises(ValidationFailed):
        ingest(repo, odd, **KWARGS)
    assert snapshot(repo) == before


# ---------------------------------------------------------------- ingest: atomicity


@pytest.mark.parametrize(
    "name, payload",
    [
        ("bad.vtt", b"no header here\n"),
        ("bad.docx", b"not a zip"),
        ("bad.txt", b"\xff\xfe\x00 not utf-8"),
    ],
)
def test_a_parser_failure_leaves_the_repo_byte_identical(repo, incoming, name, payload):
    bad = incoming / name
    bad.write_bytes(payload)
    before = snapshot(repo)
    with pytest.raises(ValidationFailed):
        ingest(repo, bad, **KWARGS)
    assert snapshot(repo) == before


def _fail_on_call(monkeypatch, nth: int):
    """Make the nth file placement fail after the earlier ones really happened."""
    real = ingest_package._create_exclusive
    calls = []

    def flaky(path, data, created):
        calls.append(path)
        if len(calls) == nth:
            raise OSError("disk full (injected)")
        return real(path, data, created)

    monkeypatch.setattr(ingest_package, "_create_exclusive", flaky)
    return calls


@pytest.mark.parametrize("nth", [1, 2, 3])
def test_a_failure_while_placing_files_rolls_back_everything_it_created(repo, incoming, monkeypatch, nth):
    original = write_vtt(incoming)
    before = snapshot(repo)
    calls = _fail_on_call(monkeypatch, nth)
    with pytest.raises(OSError, match="injected"):
        ingest(repo, original, **KWARGS)
    assert len(calls) == nth
    assert snapshot(repo) == before


def test_rollback_also_removes_directories_it_created(repo, incoming, monkeypatch):
    (repo / "sources" / "raw").rmdir()
    (repo / "sources").rmdir()
    before = snapshot(repo)
    _fail_on_call(monkeypatch, 3)
    with pytest.raises(OSError):
        ingest(repo, write_vtt(incoming), **KWARGS)
    assert snapshot(repo) == before
    assert not (repo / "sources").exists()


def test_rollback_never_deletes_a_file_it_did_not_create(repo, incoming, monkeypatch):
    # Someone else creates the canonical file between our collision check and
    # our placement. Exclusive create must fail, we must roll back only what
    # we made, and the other file must survive untouched.
    original = write_vtt(incoming)
    squatter = repo / "sources" / "2026-09-12-interview.md"
    real = ingest_package._create_exclusive

    def racing(path, data, created):
        if path.name.endswith(".md"):
            squatter.write_bytes(b"someone else's file\n")
        return real(path, data, created)

    monkeypatch.setattr(ingest_package, "_create_exclusive", racing)
    with pytest.raises(FileExistsError):
        ingest(repo, original, **KWARGS)
    assert squatter.read_bytes() == b"someone else's file\n"
    assert not (repo / "sources" / "raw" / "interview.vtt").exists()
    assert not (repo / "sources" / "2026-09-12-interview.meta.yaml").exists()


def test_nothing_is_written_if_the_staged_copy_fails_verification(repo, incoming, monkeypatch):
    original = write_vtt(incoming)
    before = snapshot(repo)
    real_stage = ingest_package._stage

    def corrupting(staging, name, data):
        # The staged copy of the original silently differs from what was read.
        return real_stage(staging, name, data + b"x" if name == "origin" else data)

    monkeypatch.setattr(ingest_package, "_stage", corrupting)
    with pytest.raises(ValidationFailed):
        ingest(repo, original, **KWARGS)
    assert snapshot(repo) == before


# ------------------------------------------- ingest: collisions, --as, idempotency


def test_same_raw_filename_with_different_content_names_both_paths(repo, incoming, tmp_path):
    ingest(repo, write_vtt(incoming), **KWARGS)
    other_folder = tmp_path / "elsewhere"
    other_folder.mkdir()
    changed = write_vtt(other_folder, text=VTT_TEXT.replace("couple of days", "week"))
    before, stamps = snapshot(repo), age_files(repo)

    with pytest.raises(UsageError) as exc:
        ingest(repo, changed, **KWARGS)
    message = str(exc.value)
    assert str(changed) in message  # the incoming file
    assert "sources/raw/interview.vtt" in message  # what it collides with
    assert "--as" in exc.value.fix
    assert snapshot(repo) == before and mtimes(repo) == stamps


def test_same_canonical_stem_from_a_different_raw_filename_is_a_collision(repo, incoming):
    ingest(repo, write_vtt(incoming, "kick_off.vtt"), **KWARGS)
    other = write_vtt(incoming, "kick-off.vtt", VTT_TEXT.replace("spreadsheet", "report"))
    before = snapshot(repo)
    with pytest.raises(UsageError) as exc:
        ingest(repo, other, **KWARGS)
    assert "sources/2026-09-12-kick-off.md" in str(exc.value)
    assert str(other) in str(exc.value)
    assert snapshot(repo) == before


def test_same_raw_filename_on_another_session_date_is_still_a_collision(repo, incoming):
    original = write_vtt(incoming)
    ingest(repo, original, **KWARGS)
    before = snapshot(repo)
    with pytest.raises(UsageError, match="sources/raw/interview.vtt"):
        ingest(repo, original, **{**KWARGS, "session_date": datetime.date(2026, 9, 13)})
    assert snapshot(repo) == before


def test_a_stray_raw_file_alone_is_a_collision(repo, incoming):
    (repo / "sources" / "raw" / "interview.vtt").write_bytes(b"left over\n")
    before = snapshot(repo)
    with pytest.raises(UsageError, match="sources/raw/interview.vtt"):
        ingest(repo, write_vtt(incoming), **KWARGS)
    assert snapshot(repo) == before


def test_an_unreadable_existing_sidecar_is_a_collision_not_a_crash(repo, incoming):
    (repo / "sources" / "2026-09-12-interview.meta.yaml").write_text(": : [unbalanced", encoding="utf-8")
    before = snapshot(repo)
    with pytest.raises(UsageError, match="2026-09-12-interview.meta.yaml"):
        ingest(repo, write_vtt(incoming), **KWARGS)
    assert snapshot(repo) == before


def test_as_resolves_a_collision_and_leaves_the_first_bundle_untouched(repo, incoming, tmp_path):
    ingest(repo, write_vtt(incoming), **KWARGS)
    other_folder = tmp_path / "elsewhere"
    other_folder.mkdir()
    changed = write_vtt(other_folder, text=VTT_TEXT.replace("couple of days", "week"))
    first = snapshot(repo)
    stamps = age_files(repo)

    result = ingest(repo, changed, **KWARGS, as_name="interview-retake")

    assert result.created is True
    assert result.origin == "sources/raw/interview-retake.vtt"
    assert result.source == "sources/2026-09-12-interview-retake.md"
    assert (repo / result.origin).read_bytes() == changed.read_bytes()
    after = snapshot(repo)
    assert {k: v for k, v in after.items() if k in first} == first  # first bundle byte-identical
    assert {k: v for k, v in mtimes(repo).items() if k in stamps} == stamps
    assert sorted(set(after) - set(first)) == [
        "sources/2026-09-12-interview-retake.md",
        "sources/2026-09-12-interview-retake.meta.yaml",
        "sources/raw/interview-retake.vtt",
    ]


@pytest.mark.parametrize("bad", ["", " ", "a/b", "..\\x", "../escape", ".hidden", "bad name", "!!"])
def test_as_rejects_names_that_could_escape_or_cannot_form_a_stem(repo, incoming, bad):
    before = snapshot(repo)
    with pytest.raises(UsageError, match="--as"):
        ingest(repo, write_vtt(incoming), **KWARGS, as_name=bad)
    assert snapshot(repo) == before


def test_re_ingesting_the_same_content_is_a_no_op_that_writes_nothing(repo, incoming):
    original = write_vtt(incoming)
    first = ingest(repo, original, **KWARGS)
    before, stamps = snapshot(repo), age_files(repo)

    # A later day, a different person: the sidecar can never be byte-identical,
    # so identity must be decided by the two content digests alone.
    again = ingest(
        repo,
        original,
        **{**KWARGS, "ingested_on": datetime.date(2026, 11, 2), "ingested_by": "sam"},
    )

    assert again.created is False
    assert (again.source, again.origin, again.meta) == (first.source, first.origin, first.meta)
    assert again.bundle_sha256 == first.bundle_sha256
    assert snapshot(repo) == before and mtimes(repo) == stamps
    meta = load_sidecar(repo, "2026-09-12-interview")
    assert meta["ingested_on"] == datetime.date(2026, 10, 18)  # the true original date survives
    assert meta["ingested_by"] == "alex"


def test_re_ingesting_identical_bytes_under_a_colliding_stem_is_also_a_no_op(repo, incoming):
    ingest(repo, write_vtt(incoming, "kick_off.vtt"), **KWARGS)
    before = snapshot(repo)
    again = ingest(repo, write_vtt(incoming, "kick-off.vtt"), **KWARGS)
    assert again.created is False
    assert snapshot(repo) == before  # no kick-off.vtt appears


def test_same_original_but_different_canonical_text_is_not_a_no_op(repo, incoming):
    # Same origin_sha256, different canonical sha256 (e.g. a parser change):
    # one digest matching is not enough.
    original = write_vtt(incoming)
    ingest(repo, original, **KWARGS)
    meta_path = repo / "sources" / "2026-09-12-interview.meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = "f" * 64
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    with pytest.raises(UsageError):
        ingest(repo, original, **KWARGS)


def test_same_canonical_text_but_different_original_is_not_a_no_op(repo, incoming):
    # A re-save of the same words (different bytes) is a different original.
    ingest(repo, write_vtt(incoming), **KWARGS)
    resaved = write_vtt(incoming, text=VTT_TEXT.replace("\r\n", "\n"))
    assert resaved.read_bytes() != (repo / "sources/raw/interview.vtt").read_bytes()
    with pytest.raises(UsageError):
        ingest(repo, resaved, **KWARGS)


# ---------------------------------------------------------------- inspect_metadata


def test_inspect_metadata_reports_docx_creator_revision_and_dates(incoming):
    docx = make_docx(incoming / "minutes.docx", ["Sam Ellis: hi"], core=CORE_XML)
    assert inspect_metadata(docx) == {
        "creator": "Pat Author",
        "last_modified_by": "Quinn Editor",
        "revision": "7",
        "created": "2026-09-10T08:00:00Z",
        "modified": "2026-09-13T17:30:00Z",
    }


def test_inspect_metadata_marks_missing_fields_as_none(incoming):
    core = CORE_XML.replace("<cp:revision>7</cp:revision>", "")
    docx = make_docx(incoming / "minutes.docx", ["x"], core=core)
    assert inspect_metadata(docx)["revision"] is None
    assert inspect_metadata(docx)["creator"] == "Pat Author"


def test_inspect_metadata_on_a_docx_without_core_properties_and_on_text_formats(incoming):
    bare = make_docx(incoming / "bare.docx", ["x"])
    assert inspect_metadata(bare) == {
        "creator": None,
        "last_modified_by": None,
        "revision": None,
        "created": None,
        "modified": None,
    }
    assert inspect_metadata(write_vtt(incoming)) == {}


def test_inspect_metadata_writes_nothing_and_rejects_a_broken_docx(repo, incoming):
    docx = make_docx(incoming / "minutes.docx", ["x"], core=CORE_XML)
    before = snapshot(repo)
    inspect_metadata(docx)
    assert snapshot(repo) == before
    broken = incoming / "broken.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(ValidationFailed, match="docx"):
        inspect_metadata(broken)


# ------------------------------------------------------------------------- the CLI


def cli_args(path, *extra):
    return [
        "ingest", str(path),
        "--record-type", "transcript",
        "--authored-by", "alex",
        "--session-date", "2026-09-12",
        "--participant", "Jordan Reyes=user",
        "--participant", "Sam Ellis=ops lead",
        "--classification", "INTERNAL",
        "--ingested-by", "alex",
        *extra,
    ]


def test_cli_ingest_end_to_end_then_idempotent_rerun(repo, incoming, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    original = write_vtt(incoming)
    assert cli.main(cli_args(original)) == 0
    out = capsys.readouterr().out
    assert "sources/2026-09-12-interview.md" in out and "sources/raw/interview.vtt" in out
    meta = load_sidecar(repo, "2026-09-12-interview")
    assert meta["participants"] == KWARGS["participants"]
    assert meta["session_date"] == datetime.date(2026, 9, 12)
    assert meta["bundle_sha256"] == bundle_sha256(meta)

    before = snapshot(repo)
    assert cli.main(cli_args(original)) == 0
    assert "already ingested" in capsys.readouterr().out
    assert snapshot(repo) == before


def test_cli_collision_exits_2_with_a_fix_and_as_resolves_it(repo, incoming, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cli.main(cli_args(write_vtt(incoming)))
    capsys.readouterr()
    other_folder = tmp_path / "elsewhere"
    other_folder.mkdir()
    changed = write_vtt(other_folder, text=VTT_TEXT.replace("days", "weeks"))
    assert cli.main(cli_args(changed)) == int(ExitCode.USAGE)
    err = capsys.readouterr().err
    assert "sources/raw/interview.vtt" in err and "--as" in err
    assert cli.main(cli_args(changed, "--as", "interview-2")) == 0


def test_cli_missing_required_options_is_a_usage_error_naming_them(repo, incoming, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["ingest", str(write_vtt(incoming))]) == int(ExitCode.USAGE)
    err = capsys.readouterr().err
    for flag in ("--record-type", "--authored-by", "--session-date", "--classification", "--ingested-by"):
        assert flag in err
    assert not (repo / "sources" / "raw" / "interview.vtt").exists()


def test_cli_rejects_a_malformed_date_and_participant(repo, incoming, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    original = write_vtt(incoming)
    args = cli_args(original)
    args[args.index("2026-09-12")] = "12/09/2026"
    assert cli.main(args) == int(ExitCode.USAGE)
    assert "--session-date" in capsys.readouterr().err
    assert cli.main(cli_args(original, "--participant", "no-role-given")) == int(ExitCode.USAGE)
    assert "--participant" in capsys.readouterr().err


def test_cli_inspect_metadata_reports_and_writes_nothing_and_needs_no_other_options(
    repo, incoming, monkeypatch, capsys
):
    monkeypatch.chdir(repo)
    docx = make_docx(incoming / "minutes.docx", ["x"], core=CORE_XML)
    before = snapshot(repo)
    assert cli.main(["ingest", "--inspect-metadata", str(docx)]) == 0
    out = capsys.readouterr().out
    assert "Pat Author" in out and "Quinn Editor" in out and "7" in out
    assert snapshot(repo) == before

    assert cli.main(["ingest", "--inspect-metadata", str(write_vtt(incoming))]) == 0
    assert "no embedded metadata" in capsys.readouterr().out


def test_cli_ingest_outside_a_repo_is_an_environment_error(incoming, tmp_path, monkeypatch):
    outside = tmp_path / "not_a_repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert cli.main(cli_args(write_vtt(incoming))) == int(ExitCode.ENVIRONMENT)


# --------------------------------------------------------------------------- D19
#
# `merit ingest` may only CREATE files under sources/. A grep for "unlink"
# would be cosmetic: shutil.rmtree, os.replace, Path.write_text or open(p, "w")
# modify or delete just as well. So the static check below is a closed
# allow-list over the whole package (each mutating call is permitted only
# inside one named function), and the behavioural tests prove the invariant
# from the outside (bytes AND mtimes of existing sources).

MUTATING_CALLS = {
    "unlink", "rmdir", "rmtree", "remove", "removedirs", "rename", "renames",
    "truncate", "write_text", "write_bytes", "move", "copy", "copy2",
    "copyfile", "copyfileobj", "copytree", "symlink_to", "hardlink_to", "link",
    "symlink", "utime", "chmod", "chown", "touch", "mkdir", "makedirs",
    "writestr", "write", "writelines", "extractall", "extract",
}
# Ways to reach any of the above without naming it, or to leave Python.
ESCAPE_CALLS = {"getattr", "eval", "exec", "__import__", "system", "popen", "Popen", "run"}
READ_ONLY_OPEN_MODES = {"r", "rb"}
# The only places allowed to touch the filesystem destructively, and how.
ALLOWED_MUTATIONS = {
    "_stage": {"write_bytes"},  # writes inside the temporary staging directory
    "_ensure_dir": {"mkdir"},  # creates a missing directory under sources/
    "_create_exclusive": {"write"},  # open(path, "xb"): fails if the file exists
    "_rollback": {"unlink", "rmdir"},  # removes only what this very run created
}


def _mode_argument(node: ast.Call, position: int):
    """The positional-or-keyword ``mode`` argument of a call, or None."""
    if len(node.args) > position:
        return node.args[position]
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def mutation_violations(source: str, filename: str = "<module>") -> list[str]:
    found: list[str] = []

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            where = f"{filename}:{node.lineno} {scope}()"
            if name in MUTATING_CALLS and name not in ALLOWED_MUTATIONS.get(scope, set()):
                found.append(f"{where} calls {name}")
            # str.replace(old, new) is everywhere; Path.replace(target) and
            # os.replace(src, dst) are the file operations.
            if name == "replace" and (
                len(node.args) == 1
                or (isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "os")
            ):
                found.append(f"{where} calls replace")
            if name in ESCAPE_CALLS:
                found.append(f"{where} calls {name}")
            if name == "open":
                if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "os":
                    found.append(f"{where} calls os.open")
                # open(path, mode) versus path.open(mode)
                mode = _mode_argument(node, 1 if isinstance(func, ast.Name) else 0)
                literal = mode.value if isinstance(mode, ast.Constant) else None
                if literal == "xb":
                    if scope != "_create_exclusive":
                        found.append(f"{where} creates a file outside _create_exclusive")
                elif mode is not None and literal not in READ_ONLY_OPEN_MODES:
                    found.append(f"{where} opens with mode {literal!r}")
            if name == "ZipFile":
                mode = _mode_argument(node, 1)
                if mode is not None and not (isinstance(mode, ast.Constant) and mode.value == "r"):
                    found.append(f"{where} opens a zip for writing")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(ast.parse(source, filename), "<module>")
    return found


def test_the_d19_scanner_catches_every_way_to_modify_or_delete():
    # Test the test: each of these is a real way to alter an existing source.
    bad_snippets = [
        "def ingest():\n    Path('x').write_text('a')",
        "def ingest():\n    Path('x').unlink()",
        "def ingest():\n    shutil.rmtree('sources')",
        "def ingest():\n    os.replace('a', 'b')",
        "def ingest():\n    path.replace(target)",
        "def ingest():\n    os.remove('a')",
        "def ingest():\n    open('x', 'w')",
        "def ingest():\n    open('x', mode='a')",
        "def ingest():\n    open('x', 'r+b')",
        "def ingest():\n    p.open('wb')",
        "def ingest():\n    os.open('x', os.O_WRONLY)",
        "def ingest():\n    shutil.move('a', 'b')",
        "def ingest():\n    shutil.copyfile('a', 'b')",
        "def ingest():\n    zipfile.ZipFile('x', 'w')",
        "def ingest():\n    getattr(os, 'remove')('a')",
        "def ingest():\n    Path('x').touch()",
        "def ingest():\n    f.truncate()",
        "def ingest():\n    subprocess.run(['rm', 'x'])",
        # An allowed call used from the wrong function is still a violation.
        "def ingest():\n    p.unlink()",
        "def _stage():\n    p.unlink()",
        "def _rollback():\n    shutil.rmtree(d)",
        "def ingest():\n    open(p, 'xb')",
    ]
    for snippet in bad_snippets:
        assert mutation_violations(snippet), snippet
    good_snippets = [
        "def _read():\n    return open('x', 'rb').read()\n",
        "def _norm(t):\n    return t.replace('a', 'b')\n",
        "def _read():\n    return p.open('r').read()\n",
        "def _rollback(files):\n    for f in files:\n        f.unlink()\n",
        "def _create_exclusive(p, d, c):\n    with open(p, 'xb') as f:\n        f.write(d)\n",
        "def parse():\n    return zipfile.ZipFile(io.BytesIO(data))\n",
    ]
    for snippet in good_snippets:
        assert mutation_violations(snippet) == [], snippet


def test_the_ingest_package_has_no_modify_or_delete_path_outside_its_choke_points():
    folder = Path(ingest_package.__file__).parent
    modules = sorted(folder.glob("*.py"))
    assert {m.name for m in modules} >= {"__init__.py", "txt.py", "vtt.py", "docx.py", "metadata.py"}
    violations = []
    for module in modules:
        violations += mutation_violations(module.read_text(encoding="utf-8"), module.name)
    assert violations == []


def test_the_allow_list_names_real_functions_so_it_cannot_rot_into_a_no_op():
    source = (Path(ingest_package.__file__).parent / "__init__.py").read_text(encoding="utf-8")
    defined = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}
    assert set(ALLOWED_MUTATIONS) <= defined


def test_no_ingest_path_modifies_or_deletes_an_existing_source(repo, incoming, tmp_path):
    # Behavioural: run every outcome (no-op, collisions, a parse failure, --as,
    # a legitimate create) against populated sources, and require every
    # pre-existing file to keep its bytes AND its modification time.
    ingest(repo, write_vtt(incoming), **KWARGS)
    ingest(repo, write_vtt(incoming, "other.vtt"), **{**KWARGS, "session_date": datetime.date(2026, 9, 14)})
    existing = snapshot(repo)
    stamps = age_files(repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    changed = write_vtt(elsewhere, text=VTT_TEXT.replace("days", "weeks"))
    bad = incoming / "bad.vtt"
    bad.write_bytes(b"junk")

    attempts = [
        lambda: ingest(repo, incoming / "interview.vtt", **KWARGS),  # no-op
        lambda: ingest(repo, changed, **KWARGS),  # raw collision
        lambda: ingest(repo, changed, **{**KWARGS, "session_date": datetime.date(2026, 9, 14)}),
        lambda: ingest(repo, bad, **KWARGS),  # parse failure
        lambda: ingest(repo, changed, **KWARGS, as_name="interview"),  # --as onto a taken name
        lambda: ingest(repo, changed, **KWARGS, as_name="fresh"),  # legitimate create
    ]
    for attempt in attempts:
        try:
            attempt()
        except (UsageError, ValidationFailed):
            pass
        after = snapshot(repo)
        assert {k: after.get(k) for k in existing} == existing  # nothing changed or vanished
        assert {k: v for k, v in mtimes(repo).items() if k in stamps} == stamps
