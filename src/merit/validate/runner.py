"""Run registered checks against artefacts in their real repository context."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from merit.config import Config, load_config
from merit.errors import ValidationFailed
from merit.model import Repo, load_repo


@dataclass(frozen=True)
class Finding:
    check_id: str
    path: Path
    message: str
    fix: str
    severity: Literal["error", "advisory"] = "error"

    def __post_init__(self):
        if self.severity not in ("error", "advisory"):
            raise ValueError("severity must be error or advisory")
        if not self.fix:
            raise ValueError("a finding needs a fix")


@dataclass
class CheckContext:
    artefact_root: Path
    context_root: Path
    config: Config
    strict: bool = False
    repo: Repo | None = None


CHECKS: dict[str, Callable[[CheckContext], list[Finding]]] = {}


def register(check_id):
    """Register exactly one implementation per numbered rule."""
    if not (check_id.startswith("V") and check_id[1:].isascii() and check_id[1:].isdigit()):
        raise ValueError(f"invalid check ID: {check_id}")

    def decorate(check):
        if check_id in CHECKS:
            raise ValueError(f"duplicate check ID: {check_id}")
        CHECKS[check_id] = check
        return check
    return decorate


def run_checks(artefact_root, *, context_root=None, strict=False):
    """Validate raw YAML first; only structurally valid trees reach model checks.

    Configuration and future comparison-point queries use context_root, never
    the staged artefact tree. V3 is the structural prerequisite, even when
    lower-numbered semantic checks have been registered.
    """
    from merit.validate import checks  # noqa: F401 -- installs built-in checks

    root = Path(artefact_root)
    context_root = root if context_root is None else Path(context_root)
    context = CheckContext(root, context_root, load_config(context_root), strict)
    findings = CHECKS["V3"](context)
    schema_failed = bool(findings)
    # Raw integrity and identity own findings even when V3 rejects other fields.
    if "V1" in CHECKS:
        findings.extend(CHECKS["V1"](context))
    if "V4" in CHECKS:
        findings.extend(CHECKS["V4"](context))
    if schema_failed:
        return findings
    try:
        context.repo = load_repo(root)
    except ValidationFailed as exc:
        # load_repo supplies a located message (including a filename/id mismatch).
        path = next((p for p in root.rglob("*.yaml") if str(exc).startswith(f"{p}:")), root)
        return findings + [Finding("V3", path, str(exc), exc.fix)]
    for check_id in sorted(CHECKS, key=lambda value: int(value[1:])):
        if check_id not in ("V1", "V3", "V4"):
            findings.extend(CHECKS[check_id](context))
    return findings
