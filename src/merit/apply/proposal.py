"""Parse proposals using the package-owned structural contract.

Proposal-local references: an ID-allocating operation may carry ``ref: name``,
and any later string value that is exactly ``@name`` becomes the ID that
operation actually allocated. IDs are never predicted; the transaction
substitutes them op by op. For schema validation each ``@name`` is replaced by a
pattern-valid placeholder of the referenced kind, so the packaged schema is
unchanged.
"""

import copy
import json
import re
from importlib import resources
from pathlib import Path

import jsonschema
import yaml

from merit.errors import UsageError
from merit.validate.checks import json_data

# The op that allocates, and a placeholder that satisfies that kind's ID pattern.
REF_PLACEHOLDERS = {
    "add_requirement": "REQ-REF-0000",
    "promote_candidate": "REQ-REF-0000",
    "add_candidate": "CAN-0000",
    "add_issue": "ISS-0000",
    "add_decision": "DEC-0000",
    "record_supersession": "SUP-0000",
}
_REF_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_REF_USE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")


def _substitute(value, lookup):
    """Replace every exact ``@name`` string inside ``value`` using ``lookup(name)``."""
    if isinstance(value, str):
        match = _REF_USE.fullmatch(value)
        return lookup(match.group(1)) if match else value
    if isinstance(value, list):
        return [_substitute(item, lookup) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, lookup) for key, item in value.items()}
    return value


def resolve_refs(operation, allocated, index, fix):
    """Return ``(operation without ref, its ref name or None)`` with ``@name`` replaced."""
    operation = dict(operation)
    ref = operation.pop("ref", None)

    def lookup(name):
        if name not in allocated:
            raise UsageError(f"proposal operation index {index}: unknown reference @{name}; "
                             "define it with ref: on an earlier operation", fix=fix)
        return allocated[name]

    return _substitute(operation, lookup), ref


def _schema_view(data, fix):
    """A copy for schema validation: refs checked, stripped, and uses replaced by placeholders."""
    view = copy.deepcopy(data)
    operations = view.get("operations") if isinstance(view, dict) else None
    if not isinstance(operations, list):
        return view
    placeholders = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        clean, ref = resolve_refs(operation, placeholders, index, fix)
        if ref is not None:
            kind = REF_PLACEHOLDERS.get(clean.get("op"))
            if kind is None:
                raise UsageError(f"proposal operation index {index}: ref is only allowed on "
                                 f"{', '.join(REF_PLACEHOLDERS)}", fix=fix)
            if not isinstance(ref, str) or not _REF_NAME.fullmatch(ref):
                raise UsageError(f"proposal operation index {index}: ref {ref!r} must start with a "
                                 "letter and use letters, digits, '_' or '-'", fix=fix)
            if ref in placeholders:
                raise UsageError(f"proposal operation index {index}: duplicate ref {ref!r}", fix=fix)
            placeholders[ref] = kind
        operations[index] = clean
    return view


def parse_proposal(path):
    path = Path(path)
    fix = f"correct {path}, then run: merit apply {path}"
    try:
        data = json_data(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise UsageError(f"proposal operation index unknown: cannot parse {path}: {exc}",
                         fix=fix) from exc
    schema = json.loads(resources.files("merit").joinpath(
        "schemas/proposal.schema.json").read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(_schema_view(data, fix)))
    if errors:
        error = errors[0]
        location = list(error.absolute_path)
        index = location[1] if len(location) > 1 and location[0] == "operations" else "unknown"
        raise UsageError(f"proposal operation index {index}: {error.message}", fix=fix)
    return data
