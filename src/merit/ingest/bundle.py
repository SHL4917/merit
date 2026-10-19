"""The bundle hash: binds a source's text, its original and its attribution.

Normative definition (Tasks 9 and 14 must recompute this byte-identically):

    immutable   = {k: meta[k] for k in IMMUTABLE_META_FIELDS}
                  | {k: meta[k] for k in OPTIONAL_META_FIELDS if k in meta}
    canonical_json = json.dumps(immutable, sort_keys=True, separators=(",", ":"))
    bundle_sha256  = sha256((sha256_hex + origin_sha256_hex + canonical_json)
                            .encode("utf-8")).hexdigest()

* ``sha256_hex`` and ``origin_sha256_hex`` are the lowercase hex strings exactly
  as stored in the sidecar (not raw digest bytes), so the hash can be
  reproduced by hand from the YAML.
* ``json.dumps`` keeps its default ``ensure_ascii=True``, so non-ASCII names
  serialise identically on every platform.
* Both digests are also inside ``immutable`` (they are sidecar keys), so they
  are covered twice. That is intended.
* ``datetime.date`` / ``datetime.datetime`` values (what ``yaml.safe_load``
  yields for ``2026-09-12``) are converted to ISO-8601 strings first. Any other
  value JSON cannot represent raises ``TypeError``; it is never coerced with
  ``str()``, which would make the hash depend on Python's repr.
* Mapping keys must be ``str`` and NaN/infinity are refused (``allow_nan=False``),
  so the output is standard JSON that a shell script can reproduce byte for byte.
* List order is significant (``participants`` is never sorted); only mapping
  keys are sorted.
* ``bundle_sha256`` is excluded from its own input.

* An ``OPTIONAL_META_FIELDS`` key joins the input only when the sidecar has
  it, so adding one never changes the hash of a bundle written without it.

This protects the attribution fields: flipping ``record_type`` from
``self_recorded_note`` to ``transcript`` changes the hash (decision D11).
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Mapping
from typing import Any

IMMUTABLE_META_FIELDS: tuple[str, ...] = (
    "source",
    "sha256",
    "origin",
    "origin_sha256",
    "extractor_version",
    "record_type",
    "authored_by",
    "session_date",
    "participants",
    "classification",
    "ingested_on",
    "ingested_by",
)
# Recorded only when used; part of the hash when present.
# ``attribution: author``: unlabelled lines were credited to ``authored_by``.
OPTIONAL_META_FIELDS: tuple[str, ...] = ("attribution",)


def _jsonable(value: Any) -> Any:
    # datetime is a subclass of date; both become ISO-8601 strings.
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        # json.dumps would coerce int/None/bool keys to strings, so {1: "x"} and
        # {"1": "x"} would collide. Refuse instead.
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"cannot hash sidecar mapping key {k!r}: keys must be str")
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        f"cannot hash sidecar value of type {type(value).__name__}: {value!r}"
    )


def bundle_sha256(meta: Mapping[str, Any]) -> str:
    missing = [k for k in IMMUTABLE_META_FIELDS if k not in meta]
    if missing:
        raise ValueError(f"sidecar is missing immutable field(s): {', '.join(missing)}")
    immutable = {k: _jsonable(meta[k]) for k in IMMUTABLE_META_FIELDS}
    immutable.update({k: _jsonable(meta[k]) for k in OPTIONAL_META_FIELDS if k in meta})
    canonical_json = json.dumps(
        immutable, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    payload = meta["sha256"] + meta["origin_sha256"] + canonical_json
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
