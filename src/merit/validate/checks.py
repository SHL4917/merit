"""V3: package schemas validate the raw YAML, before model construction."""

import datetime
import json
from functools import lru_cache
from importlib import resources

import jsonschema
import yaml

from merit.model import ARTEFACT_DIRS
from merit.validate.runner import Finding, register


@lru_cache(maxsize=None)
def load_schema(kind):
    """Read installed package data, independent of the working directory."""
    path = resources.files("merit") / "schemas" / f"{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def json_data(value, _active=None):
    """YAML dates are JSON strings; leave all other types for schema checking."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        active = set() if _active is None else _active
        if id(value) in active:
            raise ValueError("recursive YAML aliases are not JSON documents")
        active.add(id(value))
        try:
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("JSON object field names must be strings")
                return {key: json_data(child, active) for key, child in value.items()}
            return [json_data(child, active) for child in value]
        finally:
            active.remove(id(value))
    return value


@register("V3")
def schema_check(context):
    findings = []
    directories = [(kind, dirname, "*.yaml") for kind, dirname in ARTEFACT_DIRS.items()]
    directories.append(("source_meta", "sources", "*.meta.yaml"))
    for kind, dirname, glob in directories:
        for path in sorted((context.artefact_root / dirname).glob(glob)):
            if not path.is_file():
                continue
            fix = f"correct {path}, then run: merit validate"
            try:
                text = path.read_text(encoding="utf-8")
                raw = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                location = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown line"
                findings.append(Finding("V3", path, f"{location}: {getattr(exc, 'problem', None) or 'invalid YAML'}", fix))
                continue
            except (OSError, UnicodeError) as exc:
                findings.append(Finding("V3", path, f"cannot read UTF-8 YAML: {exc}", fix))
                continue
            try:
                data = json_data(raw)
            except ValueError as exc:
                findings.append(Finding("V3", path, str(exc), fix))
                continue
            validator = jsonschema.Draft202012Validator(load_schema(kind))
            for error in validator.iter_errors(data):
                location = ".".join(str(part) for part in error.absolute_path) or "document"
                findings.append(Finding("V3", path, f"{location}: {error.message}", fix))
    return findings
