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
        fix="merit init <project-id> <ID-PREFIX>",
    )


def load_config(repo_root: Path) -> Config:
    path = Path(repo_root) / CONFIG_NAME
    if not path.is_file():
        raise EnvironmentError_(
            f"{CONFIG_NAME} not found at {path}.",
            fix="merit init <project-id> <ID-PREFIX>",
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise EnvironmentError_(
            f"{path} is not valid TOML: {exc}",
            fix=f"Correct the syntax in {path}, then re-run the command.",
        ) from exc

    def required(section: str, key: str):
        try:
            return data[section][key]
        except KeyError:
            raise EnvironmentError_(
                f"{path} is missing required key [{section}] {key}.",
                fix=f"Add {key} under [{section}] in {path}, then re-run the command.",
            ) from None

    config = Config(
        project_id=required("project", "id"),
        id_prefix=required("project", "id_prefix"),
        merit_version=required("tool", "merit_version"),
        classifications=tuple(required("classification", "allowed")),
        coverage_min_chars=data.get("coverage", {}).get(
            "min_chars", DEFAULT_COVERAGE_MIN_CHARS
        ),
    )
    if config.merit_version != merit.__version__:
        raise EnvironmentError_(
            f"{CONFIG_NAME} pins merit {config.merit_version} but "
            f"{merit.__version__} is installed; validation would run under "
            "different rules.",
            fix=f"pip install merit=={config.merit_version}",
        )
    return config
