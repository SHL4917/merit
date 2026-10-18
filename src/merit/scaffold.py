"""Write a new requirements repo: the scaffold half of ``merit init``."""

import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import merit
from merit.config import CONFIG_NAME
from merit.errors import EnvironmentError_, UsageError

# The governed directories exist so a fresh scaffold is browsable. Git cannot
# track an empty directory, so a committed scaffold loses them, and that is
# fine: a missing artefact directory is a clean pass, never an error.
DIRECTORIES = (
    "sources/raw",
    "proposals",
    "requirements",
    "candidates",
    "issues",
    "decisions",
    "supersessions",
    "baselines",
)

# Matches the REQ-<PREFIX>-<NNNN> ID pattern; project ids also name baseline
# files (<project>-b<N>.yaml), so they must be safe in a filename and in TOML.
PROJECT_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
ID_PREFIX = re.compile(r"[A-Z]{2,6}")

GITIGNORE = """\
# Ungoverned scratch: unreviewed agent output that may quote sensitive material.
proposals/
"""

GITATTRIBUTES = """\
# origin_sha256 hashes the retained original's raw bytes. Never let git
# normalise line endings in them, or a fresh clone would fail source
# verification for a file nobody edited.
sources/raw/** -text
"""


def _template(name: str) -> str:
    text = (resources.files("merit") / "templates" / name).read_text(encoding="utf-8")
    # Normalise in case autocrlf converted the template on checkout.
    return text.replace("\r\n", "\n")


def _write(path: Path, text: str) -> None:
    # write_text would translate newlines on Windows; the scaffold is LF.
    path.write_bytes(text.encode("utf-8"))


def init(target: Path, *, project_id: str, id_prefix: str) -> None:
    """Scaffold a requirements repo at ``target``, running ``git init`` if needed."""
    target = Path(target)
    if not PROJECT_ID.fullmatch(project_id):
        raise UsageError(
            f"Project id {project_id!r} must be lowercase letters, digits and hyphens.",
            fix="merit init --project-id <project-id> --id-prefix <ID-PREFIX>",
        )
    if not ID_PREFIX.fullmatch(id_prefix):
        raise UsageError(
            f"ID prefix {id_prefix!r} must be 2 to 6 uppercase letters.",
            fix="merit init --project-id <project-id> --id-prefix <ID-PREFIX>",
        )
    if (target / CONFIG_NAME).exists():
        raise UsageError(
            f"{target / CONFIG_NAME} already exists; refusing to overwrite a project.",
            fix=f"merit doctor {target}",
        )
    needs_git = not (target / ".git").exists()
    if needs_git and shutil.which("git") is None:
        raise EnvironmentError_(
            "git is not installed or not on PATH.",
            fix="Install git (https://git-scm.com/downloads), then re-run merit init.",
        )

    target.mkdir(parents=True, exist_ok=True)
    for name in DIRECTORIES:
        (target / name).mkdir(parents=True, exist_ok=True)
    fields = {
        "project_id": project_id,
        "id_prefix": id_prefix,
        "merit_version": merit.__version__,
    }
    _write(target / "merit.toml", _template("merit.toml").format(**fields))
    _write(target / "AGENTS.md", _template("AGENTS.md").format(**fields))
    _write(target / "CLAUDE.md", _template("CLAUDE.md"))
    _write(target / "GEMINI.md", _template("GEMINI.md"))
    _write(target / ".gitignore", GITIGNORE)
    _write(target / ".gitattributes", GITATTRIBUTES)
    if needs_git:
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
