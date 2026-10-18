"""Git history, explicit checkpoints, publishing, and read-only PR summaries."""

import base64
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import merit
import yaml

from merit.errors import EnvironmentError_, UsageError, ValidationFailed

WORKFLOW_PATH = ".github/workflows/merit-validate.yml"
SHA = re.compile(r"[0-9a-fA-F]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+")


def _workflow_repository(value: str) -> str:
    if not REPOSITORY.fullmatch(value) or value.split("/")[1] in (".", ".."):
        raise UsageError("An explicit workflow repository OWNER/REPO is required.",
                         fix="merit init --github --workflow-repo OWNER/REPO --help")
    return value


def _api(repo_root: Path, endpoint: str, *, missing: bool = False):
    try:
        return json.loads(_run(repo_root, "gh", "api", endpoint))
    except EnvironmentError_ as exc:
        if missing and "HTTP 404" in str(exc):
            return None
        raise
    except (ValueError, TypeError) as exc:
        raise EnvironmentError_("GitHub returned unreadable JSON.", fix="gh auth status") from exc


def resolve_workflow_sha(repo_root: Path, workflow_repo: str, *, tag: str | None = None) -> str:
    """Resolve a release tag to its commit, and verify the workflow is in that tree."""
    workflow_repo = _workflow_repository(workflow_repo)
    tag = tag or f"v{merit.__version__}"
    # commits/{ref} peels both lightweight and annotated tags to the commit.
    commit = _api(repo_root, f"repos/{workflow_repo}/commits/{quote(tag, safe='')}")
    sha = commit.get("sha", "") if isinstance(commit, dict) else ""
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise EnvironmentError_("The MERIT release tag did not resolve to a 40-hex commit.",
                                fix=f"gh api repos/{workflow_repo}/commits/{tag}")
    workflow = _api(repo_root, f"repos/{workflow_repo}/contents/{WORKFLOW_PATH}?ref={sha}")
    if not isinstance(workflow, dict) or workflow.get("type") != "file" or workflow.get("path") != WORKFLOW_PATH:
        raise EnvironmentError_("The MERIT release tree does not contain its reusable workflow.",
                                fix=f"gh api repos/{workflow_repo}/contents/{WORKFLOW_PATH}?ref={sha}")
    return sha.lower()


def _remote_content(root: Path, repository: str, branch: str, name: str) -> str | None:
    value = _api(root, f"repos/{repository}/contents/{name}?ref={quote(branch, safe='')}", missing=True)
    if value is None:
        return None
    if value.get("type") != "file" or value.get("encoding") != "base64" or value.get("path") != name:
        raise ValueError("unreadable GitHub file")
    return base64.b64decode(value["content"], validate=False).decode("utf-8")


def _codeowners_cover(text: str) -> bool:
    """Conservative CODEOWNERS subset: prove full directory coverage, honor overrides.

    Unsupported patterns cannot prove coverage. Ownerless later rules invalidate
    directory coverage, since an exception could leave a workflow editable.
    """
    import fnmatch
    directory = False
    config = False
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        pattern, *owners = parts
        valid = bool(owners) and all(re.fullmatch(r"@[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?", owner)
                                     for owner in owners)
        # CODEOWNERS does not support gitignore negation, bracket ranges or
        # escaped patterns. Do not let Python's broader fnmatch prove coverage.
        if pattern.startswith(("!", "//")) or any(char in pattern for char in "[]\\"):
            continue
        normalized = pattern.removeprefix("/")
        if normalized in ("*", "**", ".github/", ".github/**"):
            directory = valid
        elif not valid:
            directory = False
        if fnmatch.fnmatchcase("merit.toml", normalized):
            config = valid
    return directory and config


def detect_tier(repo_root: Path, workflow_repo: str | None = None) -> str:
    """Verify GitHub's effective controls against an explicitly trusted MERIT identity."""
    if shutil.which("gh") is None or not workflow_repo:
        return "unverified"
    try:
        workflow_repo = _workflow_repository(workflow_repo)
        info = json.loads(_run(repo_root, "gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"))
        repository = _workflow_repository(info["nameWithOwner"])
        if repository.casefold() == workflow_repo.casefold():
            return "unverified"  # The validator must be outside the project repository.
        branch = info["defaultBranchRef"]["name"]
        expected_sha = resolve_workflow_sha(repo_root, workflow_repo)
        trusted = _api(repo_root, f"repos/{workflow_repo}")
        if trusted["full_name"].casefold() != workflow_repo.casefold() or type(trusted["id"]) is not int:
            return "unverified"
        protection = _api(repo_root, f"repos/{repository}/branches/main/protection")
        # This endpoint returns only active rules applicable to this repository
        # and this exact branch, including inherited organization rulesets.
        rules = _api(repo_root, f"repos/{repository}/rules/branches/{quote(branch, safe='')}")
        for rule in rules:
            if rule.get("type") != "workflows" or rule.get("ruleset_source_type") != "Organization":
                continue
            source = rule["ruleset_source"]
            if source.casefold() != repository.split("/")[0].casefold():
                continue
            detail = _api(repo_root, f"orgs/{source}/rulesets/{rule['ruleset_id']}")
            if (detail["enforcement"] != "active" or detail["target"] != "branch"
                    or detail["bypass_actors"] != []):
                continue
            for required in rule.get("parameters", {}).get("workflows", []):
                if (required.get("repository_id") == trusted["id"]
                        and required.get("path") == WORKFLOW_PATH
                        and (required.get("sha") or required.get("ref")) == expected_sha):
                    return "T1"
        checks = protection.get("required_status_checks") or {}
        required = checks.get("contexts", []) or checks.get("checks", [])
        if not required:
            return "unverified"  # No verified minimum enforcement tier exists.
        reviews = protection.get("required_pull_request_reviews") or {}
        if branch != "main" or reviews.get("require_code_owner_reviews") is not True or reviews.get("required_approving_review_count", 0) < 1:
            return "T3"
        text = _remote_content(repo_root, repository, "main", ".github/workflows/merit.yml")
        if text is None:
            return "T3"
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        expected = f"{workflow_repo}/{WORKFLOW_PATH}@{expected_sha}"
        jobs = workflow.get("jobs", {})
        events = workflow.get("on", {})
        if not isinstance(events, dict) or events.get("pull_request", None) not in ("", {}):
            return "T3"
        job = jobs.get("validate", {})
        if (not isinstance(job, dict) or job.get("uses", "").casefold() != expected.casefold()
                or "if" in job or "needs" in job):
            return "T3"
        contexts = set(checks.get("contexts", [])) | {check.get("context") for check in checks.get("checks", [])}
        if "validate / validate" not in contexts:
            return "T3"
        owners = None
        for name in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
            owners = _remote_content(repo_root, repository, "main", name)
            if owners is not None:
                break
        if owners is None or not _codeowners_cover(owners):
            return "T3"
        owner_errors = _api(repo_root, f"repos/{repository}/codeowners/errors?ref=main")
        return "T2" if owner_errors["errors"] == [] else "T3"
    except (EnvironmentError_, UsageError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, OSError):
        return "unverified"


def create_remote(repo_root: Path, name: str) -> str:
    """Create a private GitHub repository and attach it as origin; never push implicitly."""
    name = _workflow_repository(name)
    return _run(repo_root, "gh", "repo", "create", name, "--private", "--source", ".", "--remote", "origin")


def init_github(target: Path, *, project_id: str, id_prefix: str, workflow_repo: str,
                wheel_url: str, wheel_sha256: str, github_repo: str | None = None) -> list[str]:
    """Scaffold, publish its initial branch, request protections, then verify them."""
    from merit.scaffold import init, _template, _write
    workflow_repo = _workflow_repository(workflow_repo)
    try:
        url = urlsplit(wheel_url)
    except ValueError as exc:
        raise UsageError("The release wheel URL is malformed.", fix="merit init --github --help") from exc
    filename = url.path.rsplit("/", 1)[-1]
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(char.isspace() or ord(char) < 32 for char in wheel_url)
            or not re.fullmatch(r"merit-[A-Za-z0-9_.+]+-[A-Za-z0-9_]+-[A-Za-z0-9_]+-[A-Za-z0-9_]+\.whl", filename)
            or not re.fullmatch(r"https://[A-Za-z0-9./_~%:+-]+", wheel_url)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", wheel_sha256)):
        raise UsageError("GitHub setup requires an explicit HTTPS MERIT wheel URL and a pinned 64-hex digest.",
                         fix="merit init --github --help")
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if (target / ".git").exists() and _git(target, "rev-parse", "--verify", "HEAD") is not None:
        raise UsageError("GitHub setup requires a new repository without existing commits.",
                         fix="merit init <new-directory> --github --help")
    # Resolve before writing any scaffold files, against the actual tagged tree.
    sha = resolve_workflow_sha(target, workflow_repo)
    identity = _api(target, "user")["login"]
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", identity):
        raise EnvironmentError_("GitHub did not return a valid authenticated login.", fix="gh auth status")
    repository = _workflow_repository(github_repo or f"{identity}/{project_id}")
    if repository.casefold() == workflow_repo.casefold():
        raise UsageError("The MERIT workflow must live outside the new project repository.",
                         fix="merit init --github --help")
    for name in (".github/workflows/merit.yml", "CODEOWNERS", ".github/CODEOWNERS", ".git/hooks/pre-commit"):
        if (target / name).exists():
            raise UsageError(f"Refusing to overwrite {name}.", fix="merit init --github --help")
    init(target, project_id=project_id, id_prefix=id_prefix)
    workflow = _template("workflow.yml").replace("__WORKFLOW_REPO__", workflow_repo).replace("__SHA__", sha)
    workflow = workflow.replace("__WHEEL_URL__", wheel_url).replace("__WHEEL_SHA256__", wheel_sha256.lower())
    (target / ".github/workflows").mkdir(parents=True, exist_ok=True)
    _write(target / ".github/workflows/merit.yml", workflow)
    _write(target / "CODEOWNERS", _template("CODEOWNERS").replace("__OWNER__", identity))
    hooks = Path(_run(target, "git", "rev-parse", "--git-path", "hooks"))
    hooks = (hooks if hooks.is_absolute() else target / hooks).resolve()
    if not hooks.is_relative_to(target):
        raise UsageError("The configured Git hooks directory is outside the new repository.",
                         fix="git config --local core.hooksPath .git/hooks")
    hooks.mkdir(parents=True, exist_ok=True)
    if (hooks / "pre-commit").exists():
        raise UsageError("Refusing to overwrite the existing pre-commit hook.", fix="merit doctor")
    _write(hooks / "pre-commit", _template("pre-commit"))
    (hooks / "pre-commit").chmod(0o755)
    # Explicit scaffold paths avoid publishing pre-existing unrelated files.
    _run(target, "git", "add", "--", "merit.toml", "AGENTS.md", "CLAUDE.md", "GEMINI.md",
         ".gitignore", ".gitattributes", ".github/workflows/merit.yml", "CODEOWNERS")
    _run(target, "git", "commit", "--only", "-m", "Initialize MERIT requirements repository", "--",
         "merit.toml", "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".gitignore", ".gitattributes",
         ".github/workflows/merit.yml", "CODEOWNERS")
    _run(target, "git", "branch", "-M", "main")
    create_remote(target, repository)
    _run(target, "git", "push", "-u", "origin", "main")
    warnings = []
    protection = {"required_status_checks": {"strict": True, "contexts": ["validate / validate"]},
                  "enforce_admins": True, "required_pull_request_reviews": {
                      "require_code_owner_reviews": True, "required_approving_review_count": 1},
                  "restrictions": None}
    try:
        _run(target, "gh", "api", f"repos/{repository}/branches/main/protection", "--method", "PUT",
             "--input", "-", input=json.dumps(protection))
    except EnvironmentError_ as exc:
        warnings.append(f"Branch protection was rejected: {exc}")
    try:
        trusted = _api(target, f"repos/{workflow_repo}")
        ruleset = {"name": "MERIT validation", "target": "branch", "enforcement": "active",
                   "conditions": {"repository_id": {"repository_ids": [_api(target, f'repos/{repository}')["id"]]},
                                  "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
                   "rules": [{"type": "workflows", "parameters": {"workflows": [{
                       "repository_id": trusted["id"], "path": WORKFLOW_PATH, "sha": sha}]}}]}
        _run(target, "gh", "api", f"orgs/{repository.split('/')[0]}/rulesets", "--method", "POST",
             "--input", "-", input=json.dumps(ruleset))
    except EnvironmentError_ as exc:
        warnings.append(f"Organization ruleset was rejected: {exc}")
    tier = detect_tier(target, workflow_repo)
    return [f"warning: {warning}; achieved enforcement tier: {tier}." for warning in warnings] + [f"Enforcement tier: {tier}"]


@dataclass(frozen=True)
class ComparisonPoint:
    kind: str
    rev: str | None


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, check=False
        )
        if result.returncode:
            return None
        return result.stdout.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def list_tags(repo_root: Path) -> list[str]:
    """Return all tag names in Git's refname order, or no tags on failure."""
    output = _git(repo_root, "for-each-ref", "--format=%(refname:strip=2)", "refs/tags/")
    return output.splitlines() if output is not None else []


def comparison_point(repo_root: Path) -> ComparisonPoint:
    """Prefer the nearest reachable baseline, then the merge-base with main."""
    tag = _git(repo_root, "describe", "--tags", "--match", "baseline/*", "--abbrev=0", "HEAD")
    if tag and tag.strip():
        return ComparisonPoint("baseline", tag.strip())
    base = _git(repo_root, "merge-base", "HEAD", "refs/heads/main")
    if base and base.strip():
        return ComparisonPoint("merge-base", base.strip())
    return ComparisonPoint("none", None)


def file_at(repo_root: Path, rev: str, path: str | Path) -> str | None:
    """Read a repository-relative file verbatim from a revision, if present."""
    return _git(repo_root, "show", f"{rev}:{Path(path).as_posix()}")


def file_bytes_at(repo_root: Path, rev: str, path: str | Path) -> bytes | None:
    """Read exact blob bytes, including binary retained originals, read-only."""
    try:
        result = subprocess.run(["git", "show", f"{rev}:{Path(path).as_posix()}"],
                                cwd=repo_root, capture_output=True, check=False)
        return result.stdout if result.returncode == 0 else None
    except OSError:
        return None


def files_at(repo_root: Path, rev: str, prefix: str | Path) -> list[str]:
    """Enumerate exact POSIX paths, including spaces and Unicode, read-only."""
    output = _git(repo_root, "ls-tree", "-r", "--name-only", "-z", rev,
                  "--", Path(prefix).as_posix())
    return [name for name in output.split("\0") if name] if output is not None else []


def _run(repo_root: Path, *args: str, input: str | None = None) -> str:
    """Run an argv without a shell, preserving diagnostics and the exit contract."""
    fix = "gh auth status" if args[0] == "gh" else "git status"
    try:
        result = subprocess.run(list(args), cwd=repo_root, capture_output=True,
                                encoding="utf-8", errors="replace", check=False, input=input)
    except FileNotFoundError as exc:
        package = "GitHub.cli" if args[0] == "gh" else "Git.Git"
        raise EnvironmentError_(f"{args[0]} is not installed or not on PATH: {exc}",
                                fix=f"winget install --id {package} -e") from exc
    except (OSError, ValueError) as exc:
        raise EnvironmentError_(f"Could not run {args[0]}: {exc}", fix=fix) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise EnvironmentError_(f"{args[0]} {args[1]} failed ({result.returncode}): {detail}", fix=fix)
    return result.stdout.strip() or result.stderr.strip()


def start_draft(repo_root: Path, branch: str) -> str:
    """Create and check out a named working draft."""
    if not branch or branch.startswith("-") or branch.startswith("@{"):
        raise UsageError("Choose a literal branch name.", fix="merit start-draft draft/review")
    _run(repo_root, "git", "check-ref-format", f"refs/heads/{branch}")
    return _run(repo_root, "git", "switch", "-c", branch)


def save(repo_root: Path, paths: list[str | Path], message: str) -> str:
    """Commit only explicitly named files, retaining unrelated staged changes."""
    fix = 'merit save <file> -m "Checkpoint description"'
    if not paths or not message.strip():
        raise UsageError("A checkpoint needs explicit file paths and a message.", fix=fix)
    root = Path(repo_root).resolve()
    selected = []
    to_stage = []

    def exact_file(output: str, name: str) -> bool:
        return any(entry.partition("\t")[2] == name
                   and entry.partition(" ")[0] in ("100644", "100755")
                   for entry in output.split("\0"))

    for value in paths:
        path = Path(value)
        absolute = (root / path).resolve()
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            raise UsageError(f"Checkpoint path is outside the repository: {value}", fix=fix) from None
        if not relative.parts or relative.parts[0].casefold() == ".git" or absolute.is_dir():
            raise UsageError(f"Checkpoint paths must name individual repository files: {value}", fix=fix)
        name = relative.as_posix()
        pathspec = f":(literal){name}"
        already_staged_deletion = False
        if not absolute.exists():
            indexed = _run(root, "git", "ls-files", "--stage", "-z", "--", pathspec)
            if not exact_file(indexed, name):
                historical = _run(root, "git", "ls-tree", "-z", "HEAD", "--", name)
                if not exact_file(historical, name):
                    raise UsageError(f"Checkpoint paths must name individual repository files: {value}", fix=fix)
                already_staged_deletion = True
        # Missing paths must be exact tracked files, never directory prefixes.
        # Literal pathspecs prevent wildcard expansion in filenames containing [].
        selected.append(pathspec)
        if not already_staged_deletion:
            to_stage.append(pathspec)
    if to_stage:
        _run(root, "git", "add", "-A", "--", *to_stage)
    return _run(root, "git", "commit", "--only", "-m", message, "--", *selected)


def push(repo_root: Path, remote: str = "origin") -> str:
    """Publish the current branch and set its upstream."""
    if not remote or remote.startswith("-"):
        raise UsageError("Choose a remote name.", fix="git remote -v")
    return _run(repo_root, "git", "push", "--set-upstream", "--", remote, "HEAD")


def open_pr(repo_root: Path, title: str, *, base: str | None = None) -> str:
    """Open a review request using the generated repository summary."""
    if not title.strip():
        raise UsageError("A pull request needs a title.", fix='merit open-pr --title "Review requirements"')
    args = ["gh", "pr", "create", f"--title={title}", "--body-file", "-"]
    if base is not None:
        args.append(f"--base={base}")
    return _run(repo_root, *args, input=pr_body(repo_root))


def pr_body(repo_root: Path) -> str:
    """Summarize lifecycle, provenance, candidates and effective issue status."""
    from collections import Counter

    from merit.baseline import _readiness, governed_tree_hash
    from merit.handoff import handoff_block
    from merit.model import (CANDIDATE_STATUSES, EFFECTIVE_STATUSES, PROVENANCES,
                             REQUIREMENT_STATUSES, effective_status)

    root = Path(repo_root)
    repo, readiness = _readiness(root)
    if any(f.check_id == "V3" and f.severity == "error" for f in readiness.findings):
        raise ValidationFailed("Cannot summarize structurally invalid artefacts.", fix="merit validate")
    parts = [f"Requirements: {len(repo.requirements)}"]

    def counts(label, values, vocabulary):
        counts = Counter(values)
        parts.extend(["", f"### {label}", "", "| Status | Count |" if label != "Requirement provenance"
                      else "| Provenance | Count |", "| --- | ---: |"])
        parts.extend(f"| {value} | {counts[value]} |" for value in vocabulary)

    counts("Requirement status", (r.status for r in repo.requirements.values()), REQUIREMENT_STATUSES)
    counts("Requirement provenance", (r.provenance for r in repo.requirements.values()), PROVENANCES)
    parts.extend(["", f"Candidates (not requirements): {len(repo.candidates)}"])
    counts("Candidate status", (c.status for c in repo.candidates.values()), CANDIDATE_STATUSES)
    counts("Effective issue status", (effective_status(i, repo) for i in repo.issues.values()), EFFECTIVE_STATUSES)
    parts.extend(["", "Baseline ready for preparation." if readiness.can_prepare else "Baseline not ready."])
    for reason in readiness.reasons:
        parts.append(f"- {reason}")
    for finding in readiness.findings:
        parts.append(f"- {finding.check_id} {finding.severity}: {finding.message}")
    if readiness.can_prepare and repo.baselines:
        def order(item):
            suffix = item.id.rpartition("-b")[2]
            return str(item.prepared_on), int(suffix) if suffix.isdigit() else 0, item.id

        manifest = max(repo.baselines.values(), key=order)
        relative = f"baselines/{manifest.id}.yaml"
        if manifest.governed_tree_hash == governed_tree_hash(root, exclude=relative):
            parts.extend(["", handoff_block(manifest.id)])
    return "\n".join(parts) + "\n"
