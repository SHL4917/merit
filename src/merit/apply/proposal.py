"""Parse proposals using the package-owned structural contract."""

import json
from importlib import resources
from pathlib import Path

import jsonschema
import yaml

from merit.errors import UsageError
from merit.validate.checks import json_data


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
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(data))
    if errors:
        error = errors[0]
        location = list(error.absolute_path)
        index = location[1] if len(location) > 1 and location[0] == "operations" else "unknown"
        raise UsageError(f"proposal operation index {index}: {error.message}", fix=fix)
    return data
