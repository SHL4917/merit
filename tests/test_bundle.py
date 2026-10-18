import copy
import datetime
import hashlib
import json

import pytest

from merit.ingest.bundle import IMMUTABLE_META_FIELDS, bundle_sha256


def make_meta():
    return {
        "source": "sources/2026-09-12-sample-interview.md",
        "sha256": "9f2c8a1e" * 8,
        "origin": "sources/raw/sample_interview_120926.vtt",
        "origin_sha256": "41bd0c7e" * 8,
        "extractor_version": "0.1.0",
        "record_type": "self_recorded_note",
        "authored_by": "alex",
        "session_date": "2026-09-12",
        "participants": [
            {"name": "Jordan Reyes", "role": "user"},
            {"name": "Sam Ellis", "role": "ops lead"},
        ],
        "classification": "INTERNAL",
        "ingested_on": "2026-10-18",
        "ingested_by": "alex",
        "bundle_sha256": "0" * 64,
    }


def formula_by_hand(meta):
    immutable = {k: meta[k] for k in IMMUTABLE_META_FIELDS}
    blob = json.dumps(immutable, sort_keys=True, separators=(",", ":"))
    # Operands are lowercase HEX strings, not raw digest bytes.
    return hashlib.sha256(
        (meta["sha256"] + meta["origin_sha256"] + blob).encode("utf-8")
    ).hexdigest()


def test_immutable_fields_are_every_sidecar_key_but_bundle_sha256():
    assert set(IMMUTABLE_META_FIELDS) == set(make_meta()) - {"bundle_sha256"}
    assert "bundle_sha256" not in IMMUTABLE_META_FIELDS
    # Both digests are inside the JSON as well as the explicit prefix.
    assert {"sha256", "origin_sha256"} <= set(IMMUTABLE_META_FIELDS)


def test_matches_the_normative_formula_by_hand():
    meta = make_meta()
    assert bundle_sha256(meta) == formula_by_hand(meta)


def test_hash_is_reproducible_and_pinned():
    assert bundle_sha256(make_meta()) == bundle_sha256(make_meta())
    assert bundle_sha256(make_meta()) == "a20b11aab12d71ecf121656df36411769f3aa503a1c136a1702cf5249ff545c1"


def test_excludes_bundle_sha256_itself():
    a = make_meta()
    b = make_meta()
    b["bundle_sha256"] = "f" * 64
    assert bundle_sha256(a) == bundle_sha256(b)
    del b["bundle_sha256"]
    assert bundle_sha256(a) == bundle_sha256(b)


def test_key_order_independent():
    meta = make_meta()
    reordered = dict(reversed(list(meta.items())))
    reordered["participants"] = [
        dict(reversed(list(p.items()))) for p in meta["participants"]
    ]
    assert list(reordered) != list(meta)
    assert bundle_sha256(reordered) == bundle_sha256(meta)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "a" * 64),
        ("origin_sha256", "b" * 64),
        # D11: flipping record_type must not be a free edit.
        ("record_type", "transcript"),
        ("authored_by", "sam"),
        ("source", "sources/other.md"),
        ("origin", "sources/raw/other.vtt"),
        ("extractor_version", "0.2.0"),
        ("session_date", "2026-09-13"),
        ("classification", "PUBLIC"),
        ("ingested_on", "2026-10-19"),
        ("ingested_by", "sam"),
        ("participants", [{"name": "Jordan Reyes", "role": "ops lead"}]),
    ],
)
def test_changing_any_immutable_field_changes_the_hash(field, value):
    base = make_meta()
    changed = copy.deepcopy(base)
    changed[field] = value
    assert bundle_sha256(changed) != bundle_sha256(base)


def test_metadata_only_edit_changes_the_bundle_hash():
    # The exit criterion: canonical text untouched, only attribution edited.
    before = make_meta()
    after = make_meta()
    after["record_type"] = "transcript"
    assert before["sha256"] == after["sha256"]
    assert bundle_sha256(before) != bundle_sha256(after)


def test_swapping_the_two_digests_changes_the_hash():
    a = make_meta()
    b = make_meta()
    b["sha256"], b["origin_sha256"] = a["origin_sha256"], a["sha256"]
    assert bundle_sha256(a) != bundle_sha256(b)


def test_yaml_dates_hash_like_their_iso_strings():
    # yaml.safe_load turns `session_date: 2026-09-12` into datetime.date.
    as_str = make_meta()
    as_date = make_meta()
    as_date["session_date"] = datetime.date(2026, 9, 12)
    as_date["ingested_on"] = datetime.date(2026, 10, 18)
    assert bundle_sha256(as_date) == bundle_sha256(as_str)


def test_datetimes_become_iso_strings():
    as_str = make_meta()
    as_str["ingested_on"] = "2026-10-18T09:30:00"
    as_dt = make_meta()
    as_dt["ingested_on"] = datetime.datetime(2026, 10, 18, 9, 30)
    assert bundle_sha256(as_dt) == bundle_sha256(as_str)


def test_dates_nested_in_participants_are_canonicalised():
    meta = make_meta()
    meta["participants"][0]["joined"] = datetime.date(2026, 9, 12)
    meta2 = copy.deepcopy(meta)
    meta2["participants"][0]["joined"] = "2026-09-12"
    assert bundle_sha256(meta) == bundle_sha256(meta2)


def test_unserialisable_values_raise_instead_of_being_str_coerced():
    meta = make_meta()
    meta["participants"][0]["role"] = {"a", "b"}
    with pytest.raises(TypeError):
        bundle_sha256(meta)


def test_non_ascii_names_hash_via_ascii_escaped_json():
    meta = make_meta()
    meta["participants"][0]["name"] = "Zoë Muñoz"
    immutable = {k: meta[k] for k in IMMUTABLE_META_FIELDS}
    assert "\\u00eb" in json.dumps(immutable, sort_keys=True, separators=(",", ":"))
    assert bundle_sha256(meta) == formula_by_hand(meta)


def test_missing_immutable_field_raises():
    meta = make_meta()
    del meta["authored_by"]
    with pytest.raises(ValueError, match="authored_by"):
        bundle_sha256(meta)
