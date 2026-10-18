"""Project configuration read from ``merit.toml``.

The tool-version pin is advisory for local use: a mismatch is refused rather
than validated under different rules. CI's authoritative version is pinned by
the reusable workflow.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import merit
from merit.errors import EnvironmentError_

CONFIG_NAME = "merit.toml"
DEFAULT_COVERAGE_MIN_CHARS = 40


@dataclass(frozen=True)
class Config:
    project_id: str
    id_prefix: str
    merit_version: str
    classifications: tuple[str, ...]
    coverage_min_chars: int


def find_repo_root(start: Path) -> Path:
    """Walk upward from ``start`` to the nearest directory holding merit.toml."""
    start = Path(start).resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_NAME).is_file():
            return directory
    raise EnvironmentError_(
        f"No {CONFIG_NAME} found in {start} or any parent directory.",
        fix="merit init --project-id <project-id> --id-prefix <ID-PREFIX>",
    )


def load_config(repo_root: Path) -> Config:
    path = Path(repo_root) / CONFIG_NAME
    if not path.is_file():
        raise EnvironmentError_(
            f"{CONFIG_NAME} not found at {path}.",
            fix="merit init --project-id <project-id> --id-prefix <ID-PREFIX>",
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise EnvironmentError_(
            f"{path} is not valid TOML: {exc}",
            fix=f"Correct the syntax in {path}, then re-run the command.",
        ) from exc

    def invalid(detail: str, key_hint: str) -> EnvironmentError_:
        return EnvironmentError_(
            f"{path} {detail}",
            fix=f"Correct {key_hint} in {path}, then re-run the command.",
        )

    def table(section: str) -> dict:
        value = data.get(section, {})
        if not isinstance(value, dict):
            raise invalid(f"has [{section}] that is not a table.", f"[{section}]")
        return value

    def required(section: str, key: str, expected: type, label: str):
        values = table(section)
        if key not in values:
            raise invalid(f"is missing required key [{section}] {key}.", f"{key}")
        value = values[key]
        if not isinstance(value, expected):
            raise invalid(
                f"has [{section}] {key} that is not {label}.", f"{key} under [{section}]"
            )
        return value

    # The pin is compared first: a merit.toml written by a different merit may
    # have a different layout, and "wrong version" is the useful message then.
    merit_version = required("tool", "merit_version", str, "a string")
    if merit_version != merit.__version__:
        raise EnvironmentError_(
            f"{CONFIG_NAME} pins merit {merit_version} but "
            f"{merit.__version__} is installed; validation would run under "
            "different rules.",
            fix=f"pipx install git+https://github.com/<org>/merit@v{merit_version}",
        )

    allowed = required("classification", "allowed", list, "a list of strings")
    if not all(isinstance(item, str) for item in allowed):
        raise invalid(
            "has [classification] allowed that is not a list of strings.",
            "allowed under [classification]",
        )

    coverage = table("coverage")
    min_chars = coverage.get("min_chars", DEFAULT_COVERAGE_MIN_CHARS)
    if not isinstance(min_chars, int) or isinstance(min_chars, bool):
        raise invalid(
            "has [coverage] min_chars that is not an integer.",
            "min_chars under [coverage]",
        )

    return Config(
        project_id=required("project", "id", str, "a string"),
        id_prefix=required("project", "id_prefix", str, "a string"),
        merit_version=merit_version,
        classifications=tuple(allowed),
        coverage_min_chars=min_chars,
    )
