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

# Line-based files are appended to, never replaced: the rule is the idempotence
# key, and the block carries the comment that explains it.
GITIGNORE_RULE = "proposals/"
GITIGNORE = """# Ungoverned scratch: unreviewed agent output that may quote sensitive material.
proposals/
"""

GITATTRIBUTES_RULE = "sources/raw/** -text"
GITATTRIBUTES = """# origin_sha256 hashes the retained original's raw bytes. Never let git
# normalise line endings in them, or a fresh clone would fail source
# verification for a file nobody edited.
sources/raw/** -text
"""

APPENDED = {
    ".gitignore": (GITIGNORE_RULE, GITIGNORE),
    ".gitattributes": (GITATTRIBUTES_RULE, GITATTRIBUTES),
}
# Whole documents with no well-defined merge: an existing one is a collision.
DOCUMENTS = ("merit.toml", "AGENTS.md", "CLAUDE.md", "GEMINI.md")


def _template(name: str, **fields: str) -> str:
    text = (resources.files("merit") / "templates" / name).read_text(encoding="utf-8")
    # Normalise in case autocrlf converted the template on checkout.
    text = text.replace("\r\n", "\n")
    # str.replace, not str.format: a literal brace in a template must survive.
    for key, value in fields.items():
        text = text.replace("{" + key + "}", value)
    return text


def _write(path: Path, text: str) -> None:
    # write_text would translate newlines on Windows; the scaffold is LF.
    path.write_bytes(text.encode("utf-8"))


def _append_rule(path: Path, rule: str, block: str) -> None:
    """Append ``block`` to ``path`` unless ``rule`` is already one of its lines."""
    if not path.exists():
        _write(path, block)
        return
    existing = path.read_bytes().decode("utf-8")
    if rule in (line.strip() for line in existing.splitlines()):
        return
    if not existing or existing.endswith("\n\n"):
        separator = ""
    elif existing.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    _write(path, existing + separator + block)


def _collisions(target: Path) -> list[Path]:
    found = [target / name for name in DOCUMENTS if (target / name).exists()]
    for name in APPENDED:
        path = target / name
        if path.exists():
            try:
                path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                found.append(path)  # appending UTF-8 to e.g. UTF-16 would corrupt it
    return found


def _quote(path: Path) -> str:
    return subprocess.list2cmdline([str(path)])


def init(target: Path, *, project_id: str, id_prefix: str) -> None:
    """Scaffold a requirements repo at ``target``, running ``git init`` if needed.

    Existing ``.gitignore`` and ``.gitattributes`` gain MERIT's lines; an existing
    ``merit.toml``, ``AGENTS.md``, ``CLAUDE.md`` or ``GEMINI.md`` makes ``init``
    refuse, naming every collision, before anything is written.
    """
    target = Path(target)
    rerun = f"merit init {_quote(target)} --project-id <project-id> --id-prefix <ID-PREFIX>"
    if not PROJECT_ID.fullmatch(project_id):
        raise UsageError(
            f"Project id {project_id!r} must be lowercase letters, digits and hyphens.",
            fix=rerun,
        )
    if not ID_PREFIX.fullmatch(id_prefix):
        raise UsageError(f"ID prefix {id_prefix!r} must be 2 to 6 uppercase letters.", fix=rerun)
    collisions = _collisions(target)
    if collisions:
        names = ", ".join(str(path) for path in collisions)
        if target / CONFIG_NAME in collisions:
            fix = f"merit doctor {_quote(target)}"
        else:
            fix = f"Rename or remove {names}, then re-run: {rerun}"
        raise UsageError(f"Refusing to overwrite existing files: {names}.", fix=fix)

    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        # First, so a failure leaves nothing that would block a re-run.
        _git_init(target)
    for name in DIRECTORIES:
        (target / name).mkdir(parents=True, exist_ok=True)
    fields = {
        "project_id": project_id,
        "id_prefix": id_prefix,
        "merit_version": merit.__version__,
        "source_url": merit.SOURCE_URL,
    }
    for name in DOCUMENTS:
        _write(target / name, _template(name, **fields))
    for name, (rule, block) in APPENDED.items():
        _append_rule(target / name, rule, block)


def _git_init(target: Path) -> None:
    if shutil.which("git") is None:
        raise EnvironmentError_(
            "git is not installed or not on PATH.",
            fix="winget install --id Git.Git -e  (or https://git-scm.com/downloads), "
            "then re-run merit init",
        )
    try:
        subprocess.run(["git", "init", "-q"], cwd=target, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise EnvironmentError_(
            f"git init failed in {target}: {detail}",
            fix=f"git -C {_quote(target)} init",
        ) from exc
