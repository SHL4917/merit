"""Source rules are exercised with bundles emitted by the real ingest path."""

import datetime
import subprocess
import shutil
import zipfile

import pytest
import yaml

import merit
from merit.cli import main
from merit.ingest import ingest
from merit.ingest.bundle import IMMUTABLE_META_FIELDS, bundle_sha256
from merit.ingest.canonical import sha256_bytes, sha256_text
from merit.scaffold import init
from merit.validate.runner import run_checks


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "project"
    init(root, project_id="sample", id_prefix="SMP")
    original = tmp_path / "interview.txt"
    original.write_bytes(b"\xef\xbb\xbfJordan Reyes: Keep audit logs.\r\n")
    result = ingest(root, original, record_type="transcript", authored_by="Alex",
                    session_date=datetime.date(2026, 10, 18), participants=[],
                    classification="INTERNAL", ingested_by="Alex")
    return root, result


def meta(bundle):
    root, result = bundle
    return yaml.safe_load((root / result.meta).read_text(encoding="utf-8"))


def save(bundle, data, *, rehash=False):
    root, result = bundle
    if rehash:
        data["bundle_sha256"] = bundle_sha256(data)
    (root / result.meta).write_text(yaml.safe_dump(data), encoding="utf-8")


def ids(root):
    return {f.check_id for f in run_checks(root)}


def test_first_run_no_baseline(bundle):
    assert run_checks(bundle[0]) == []


@pytest.mark.parametrize("field", [*IMMUTABLE_META_FIELDS, "bundle_sha256"])
def test_missing_integrity_field_is_v1(bundle, field):
    data = meta(bundle)
    del data[field]
    save(bundle, data)
    assert "V1" in ids(bundle[0])


@pytest.mark.parametrize("part, change, cause", [
    ("meta", "delete", "sidecar"), ("source", "delete", "canonical"),
    ("origin", "delete", "original"), ("source", "edit", "canonical"),
    ("origin", "edit", "original"),
])
def test_damaged_files_are_located_v1(bundle, part, change, cause):
    root, result = bundle
    path = root / getattr(result, part)
    if change == "delete":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"changed\n")
    findings = [f for f in run_checks(root) if f.check_id == "V1"]
    assert findings and any(cause in f.message.lower() for f in findings)
    assert all(f.fix for f in findings)
    assert main(["validate", str(root)]) == 1


@pytest.mark.parametrize("field,value", [("record_type", "minutes"), ("authored_by", "Other")])
def test_attribution_tamper_names_bundle(bundle, field, value):
    data = meta(bundle)
    data[field] = value
    save(bundle, data)
    assert any(f.check_id == "V1" and "bundle" in f.message.lower()
               for f in run_checks(bundle[0]))


def test_classification_is_configured(bundle):
    data = meta(bundle)
    data["classification"] = "UNKNOWN"
    save(bundle, data, rehash=True)
    assert "V1" in ids(bundle[0])


def test_v3_does_not_hide_missing_immutable_bundle_field(bundle):
    data = meta(bundle)
    del data["authored_by"]
    data["unexpected"] = True
    save(bundle, data)
    assert {"V1", "V3"} <= ids(bundle[0])


def test_normalisation_preserves_canonical_but_not_raw_bytes(bundle):
    root, result = bundle
    path = root / result.source
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    assert run_checks(root) == []
    raw = root / result.origin
    raw.write_bytes(raw.read_bytes().replace(b"\r\n", b"\n"))
    assert "V1" in ids(root)


def test_reextraction_detects_consistently_rehashed_canonical_edit(bundle):
    root, result = bundle
    path = root / result.source
    path.write_bytes(path.read_bytes().replace(b"logs", b"records"))
    data = meta(bundle)
    data["sha256"] = sha256_text(path.read_text(encoding="utf-8"))
    save(bundle, data, rehash=True)
    assert ids(root) == {"V12"}


def test_extractor_drift_is_advisory_and_missing_original_stays_v1(bundle):
    root, result = bundle
    data = meta(bundle)
    data["extractor_version"] = "0.0.0"
    save(bundle, data, rehash=True)
    finding, = run_checks(root)
    assert finding.check_id == "V12" and finding.severity == "advisory"
    assert "0.0.0" in finding.message and merit.__version__ in finding.message
    assert main(["validate", str(root)]) == 0
    (root / result.origin).unlink()
    assert ids(root) == {"V1"}


def git(root, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                           "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
                          cwd=root, check=True, capture_output=True).stdout.decode("utf-8")


@pytest.mark.parametrize("change", ["modified", "appended", "raw", "metadata", "deleted"])
def test_historical_bundles_are_immutable(bundle, change):
    root, result = bundle
    git(root, "branch", "-M", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    data = meta(bundle)
    if change == "deleted":
        for name in (result.source, result.origin, result.meta):
            (root / name).unlink()
    elif change == "metadata":
        data["authored_by"] = "Other"
        save(bundle, data, rehash=True)
    else:
        path = root / (result.origin if change == "raw" else result.source)
        content = path.read_bytes()
        content = content.replace(b"logs", b"records") if change == "modified" else content + b"extra\n"
        path.write_bytes(content)
        data["origin_sha256" if change == "raw" else "sha256"] = (
            sha256_bytes(content) if change == "raw" else sha256_text(content.decode("utf-8")))
        save(bundle, data, rehash=True)
    assert "V2" in ids(root)


def test_new_source_allowed_at_comparison_point(bundle, tmp_path):
    root, _ = bundle
    git(root, "branch", "-M", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    new = tmp_path / "next.txt"
    new.write_text("Jordan Reyes: Another session.\n", encoding="utf-8")
    ingest(root, new, record_type="minutes", authored_by="Alex", participants=[],
           session_date=datetime.date(2026, 10, 19), classification="INTERNAL", ingested_by="Alex")
    assert run_checks(root) == []


@pytest.mark.parametrize("origin", ["../interview.txt", "sources/raw/../interview.txt",
                                  "sources\\raw\\interview.txt", "C:/external.txt",
                                  "sources/raw//interview.txt"])
def test_origin_paths_are_exact_and_confined(bundle, origin):
    data = meta(bundle)
    data["origin"] = origin
    save(bundle, data, rehash=True)
    assert "V1" in ids(bundle[0])


@pytest.mark.parametrize("field,value", [("origin", "sources/raw/a\0.txt"),
                                        ("source", "sources/a\0.md")])
def test_invalid_path_characters_are_located_v1_without_traceback(bundle, capsys, field, value):
    root, result = bundle
    data = meta(bundle)
    data[field] = value
    save(bundle, data, rehash=True)
    findings = run_checks(root)
    assert any(f.check_id == "V1" and f.path == root / result.meta for f in findings)
    assert main(["validate", str(root)]) == 1
    output = capsys.readouterr()
    assert "V1" in output.out and "Traceback" not in output.out + output.err


@pytest.mark.parametrize("suffix", [".vtt", ".docx"])
def test_reextraction_dispatches_real_registered_parser(bundle, tmp_path, suffix):
    root, _ = bundle
    original = tmp_path / ("additional" + suffix)
    if suffix == ".vtt":
        original.write_bytes(b"WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\n<v Jordan Reyes>Keep logs.</v>\r\n")
    else:
        with zipfile.ZipFile(original, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Jordan Reyes: Keep logs.</w:t></w:r></w:p></w:body></w:document>')
    result = ingest(root, original, record_type="transcript", authored_by="Alex",
                    session_date=datetime.date(2026, 10, 19), participants=[],
                    classification="INTERNAL", ingested_by="Alex")
    assert run_checks(root) == []
    path = root / result.source
    path.write_bytes(path.read_bytes().replace(b"logs", b"records"))
    other = (root, result)
    data = meta(other)
    data["sha256"] = sha256_text(path.read_text(encoding="utf-8"))
    save(other, data, rehash=True)
    assert ids(root) == {"V12"}


def test_staged_artefacts_compare_against_real_context(bundle, tmp_path):
    root, result = bundle
    git(root, "branch", "-M", "main")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    staged = tmp_path / "staged"
    shutil.copytree(root / "sources", staged / "sources")
    data = meta(bundle)
    data["authored_by"] = "Other"
    save((staged, result), data, rehash=True)
    findings = run_checks(staged, context_root=root)
    assert {f.check_id for f in findings} == {"V2"}
    assert all(f.path.is_relative_to(staged) for f in findings)
