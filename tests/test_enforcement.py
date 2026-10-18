"""All GitHub commands are mocked: these tests never contact an external account."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from merit import cli, doctor, git_ops as ops
from merit.errors import EnvironmentError_, UsageError
from merit.scaffold import _template, init

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_REPO = "acme/merit"
SHA = "a" * 40
WHEEL = "https://github.com/acme/merit/releases/download/v0.1.0/merit-0.1.0-py3-none-any.whl"
DIGEST = "b" * 64


def file_response(name, content):
    return {"type": "file", "path": name, "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode()}


@pytest.fixture
def github(monkeypatch):
    """A complete readable GitHub account with T2 controls; opt into T1 explicitly."""
    project = _template("workflow.yml").replace("__WORKFLOW_REPO__", WORKFLOW_REPO).replace("__SHA__", SHA)
    responses = {
        f"repos/{WORKFLOW_REPO}/commits/v0.1.0": {"sha": SHA},
        f"repos/{WORKFLOW_REPO}/contents/{ops.WORKFLOW_PATH}?ref={SHA}": file_response(
            ops.WORKFLOW_PATH, _template("validator-workflow.yml").replace("__TRUSTED_DIGEST__", DIGEST)),
        "view": {"nameWithOwner": "acme/project", "defaultBranchRef": {"name": "main"}},
        f"repos/{WORKFLOW_REPO}": {"id": 11, "full_name": WORKFLOW_REPO},
        "repos/acme/project": {"id": 22, "full_name": "acme/project"},
        "user": {"login": "reviewer"},
        "repos/acme/project/branches/main/protection": {
            "required_status_checks": {"contexts": ["validate / validate"]},
            "required_pull_request_reviews": {"require_code_owner_reviews": True,
                                               "required_approving_review_count": 1}},
        "repos/acme/project/rules/branches/main": [],
        "repos/acme/project/contents/.github/workflows/merit.yml?ref=main": file_response(".github/workflows/merit.yml", project),
        "repos/acme/project/contents/.github/CODEOWNERS?ref=main": None,
        "repos/acme/project/contents/CODEOWNERS?ref=main": file_response("CODEOWNERS", "/.github/ @reviewer\n/merit.toml @reviewer\n"),
        "repos/acme/project/contents/docs/CODEOWNERS?ref=main": None,
        "repos/acme/project/codeowners/errors?ref=main": {"errors": []},
        "orgs/acme/rulesets/9": {"enforcement": "active", "target": "branch", "bypass_actors": []},
    }
    calls = []
    rejected = set()

    def run(root, *args, input=None):
        calls.append((args, input))
        if args[:3] == ("gh", "repo", "view"):
            return json.dumps(responses["view"])
        if args[:2] == ("gh", "api"):
            endpoint = args[2]
            if "--method" in args:
                if endpoint in rejected:
                    raise EnvironmentError_("HTTP 403: rejected", fix="gh auth status")
                return "{}"
            assert endpoint in responses, f"Unexpected API call: {endpoint}"
            response = responses[endpoint]
            if isinstance(response, Exception):
                raise response
            if response is None:
                raise EnvironmentError_("HTTP 404: not found", fix="gh auth status")
            return json.dumps(response)
        if args[:3] == ("git", "rev-parse", "--git-path"):
            return ".git/hooks"
        if args[:3] == ("gh", "repo", "create") or args[0] == "git":
            return "mocked local setup command"
        raise AssertionError(args)

    monkeypatch.setattr(ops, "_run", run)
    monkeypatch.setattr(ops.shutil, "which", lambda name: name)
    return responses, calls, rejected


def tier(github, tmp_path):
    return ops.detect_tier(tmp_path, WORKFLOW_REPO)


def t1(github):
    response, _, _ = github
    rule = {"type": "workflows", "ruleset_source_type": "Organization", "ruleset_source": "acme",
            "ruleset_id": 9, "parameters": {"workflows": [{"repository_id": 11,
                             "path": ops.WORKFLOW_PATH, "sha": SHA}]}}
    response["repos/acme/project/rules/branches/main"] = [rule]
    return rule


def change_workflow(github, old, new):
    response = github[0]
    key = "repos/acme/project/contents/.github/workflows/merit.yml?ref=main"
    text = base64.b64decode(response[key]["content"]).decode().replace(old, new)
    response[key] = file_response(".github/workflows/merit.yml", text)


def test_t2_requires_all_conditions(github, tmp_path):
    assert tier(github, tmp_path) == "T2"


@pytest.mark.parametrize("missing", ["status", "review", "review-count", "owners", "github-pattern", "config-pattern", "pin", "identity", "unrelated-check", "conditional", "dependency", "trigger"])
def test_each_t2_condition_is_independently_necessary(github, tmp_path, missing):
    response = github[0]
    protection = response["repos/acme/project/branches/main/protection"]
    if missing == "status":
        protection["required_status_checks"] = None
    elif missing == "review":
        protection["required_pull_request_reviews"]["require_code_owner_reviews"] = False
    elif missing == "review-count":
        protection["required_pull_request_reviews"]["required_approving_review_count"] = 0
    elif missing in ("owners", "github-pattern", "config-pattern"):
        text = {"owners": None, "github-pattern": "/docs/ @reviewer\n/merit.toml @reviewer\n",
                "config-pattern": "/.github/ @reviewer\n/other.toml @reviewer\n"}[missing]
        response["repos/acme/project/contents/CODEOWNERS?ref=main"] = file_response("CODEOWNERS", text) if text else None
    elif missing == "pin":
        change_workflow(github, SHA, "v0.1.0")
    elif missing == "identity":
        change_workflow(github, WORKFLOW_REPO, "attacker/merit")
    elif missing == "unrelated-check":
        protection["required_status_checks"] = {"contexts": ["some unrelated green check"]}
    elif missing == "conditional":
        change_workflow(github, "  validate:\n", "  validate:\n    if: false\n")
    elif missing == "dependency":
        change_workflow(github, "  validate:\n", "  validate:\n    needs: skip-me\n")
    else:
        change_workflow(github, "  pull_request:\n", "  workflow_dispatch:\n")
    assert tier(github, tmp_path) == ("unverified" if missing == "status" else "T3")


def test_codeowners_alone_is_not_enforcement(github, tmp_path):
    github[0]["repos/acme/project/branches/main/protection"] = {}
    assert tier(github, tmp_path) == "unverified"


@pytest.mark.parametrize("text,expected", [
    ("* @reviewer\n", True),
    ("/.github/** @reviewer\n/merit.toml @reviewer\n", True),
    ("/.github/workflows/ @reviewer\n/merit.toml @reviewer\n", False),
    ("/.github/\n/merit.toml @reviewer\n", False),
    ("* @reviewer\n/merit.toml\n", False),
    ("* @reviewer\n/.github/workflows/merit.yml\n", False),
    ("# /.github/ @reviewer\n/merit.toml @reviewer\n", False),
    ("/.github/ invalid-owner\n/merit.toml @reviewer\n", False),
    ("/.github/ @reviewer\n/merit.[t]oml @reviewer\n", False),
    ("//.github/ @reviewer\n/merit.toml @reviewer\n", False),
])
def test_codeowners_matches_directory_config_and_last_rule(text, expected):
    assert ops._codeowners_cover(text) is expected


def test_github_codeowners_precedence_is_observed(github, tmp_path):
    github[0]["repos/acme/project/contents/.github/CODEOWNERS?ref=main"] = file_response(".github/CODEOWNERS", "docs/ @reviewer\n")
    assert tier(github, tmp_path) == "T3"


def test_github_must_accept_codeowners_syntax_and_owners(github, tmp_path):
    github[0]["repos/acme/project/codeowners/errors?ref=main"] = {"errors": [{"kind": "Invalid owner"}]}
    assert tier(github, tmp_path) == "T3"


def test_unreadable_codeowners_diagnostics_are_unverified(github, tmp_path):
    github[0]["repos/acme/project/codeowners/errors?ref=main"] = EnvironmentError_("HTTP 403 denied", fix="gh auth status")
    assert tier(github, tmp_path) == "unverified"


def test_t1_is_verified_by_effective_org_rule_identity(github, tmp_path):
    t1(github)
    assert tier(github, tmp_path) == "T1"


def test_project_repository_cannot_be_its_own_trusted_validator(github, tmp_path):
    assert ops.detect_tier(tmp_path, "acme/project") == "unverified"


def test_unreadable_ruleset_bypasses_cannot_prove_t1(github, tmp_path):
    t1(github)
    del github[0]["orgs/acme/rulesets/9"]["bypass_actors"]
    assert tier(github, tmp_path) == "unverified"


@pytest.mark.parametrize("defect", ["repository", "path", "ref", "source", "source-type", "disabled", "bypass", "target", "wrong-owner", "wrong-repo"])
def test_each_t1_identity_condition_is_necessary(github, tmp_path, defect):
    rule = t1(github)
    workflow = rule["parameters"]["workflows"][0]
    detail = github[0]["orgs/acme/rulesets/9"]
    if defect == "repository":
        workflow["repository_id"] = 999
    elif defect == "path":
        workflow["path"] = ".github/workflows/fake.yml"
    elif defect == "ref":
        workflow["sha"] = "c" * 40
    elif defect == "source":
        rule["ruleset_source"] = "other-org"
    elif defect == "source-type":
        rule["ruleset_source_type"] = "Repository"
    elif defect == "disabled":
        detail["enforcement"] = "evaluate"
    elif defect == "bypass":
        detail["bypass_actors"] = [{"actor_id": 7}]
    elif defect == "target":
        detail["target"] = "tag"
    else:
        github[0][f"repos/{WORKFLOW_REPO}"]["full_name"] = "elsewhere/merit" if defect == "wrong-owner" else "acme/fake"
    assert tier(github, tmp_path) == ("unverified" if defect.startswith("wrong-") else "T2")


def test_some_ruleset_targeting_main_does_not_imply_t1(github, tmp_path):
    rule = t1(github)
    rule["type"] = "required_status_checks"
    assert tier(github, tmp_path) == "T2"


@pytest.mark.parametrize("endpoint", ["repos/acme/project/rules/branches/main", f"repos/{WORKFLOW_REPO}/commits/v0.1.0"])
def test_unreadable_settings_are_unverified(github, tmp_path, endpoint):
    t1(github)
    github[0][endpoint] = EnvironmentError_("HTTP 403 denied", fix="gh auth status")
    assert tier(github, tmp_path) == "unverified"


@pytest.mark.parametrize("default", ["main", "trunk", "release/current"])
def test_t1_uses_actual_default_branch_without_classic_protection(github, tmp_path, default):
    rules = [t1(github)]
    github[0]["view"]["defaultBranchRef"]["name"] = default
    github[0][f"repos/acme/project/rules/branches/{default.replace('/', '%2F')}"] = rules
    github[0]["repos/acme/project/branches/main/protection"] = EnvironmentError_("HTTP 404", fix="gh auth status")
    assert tier(github, tmp_path) == "T1"
    assert not any("/protection" in args[2] for args, _ in github[1] if args[:2] == ("gh", "api"))


def test_t2_still_requires_readable_classic_protection(github, tmp_path):
    github[0]["repos/acme/project/branches/main/protection"] = EnvironmentError_("HTTP 403", fix="gh auth status")
    assert tier(github, tmp_path) == "unverified"


@pytest.mark.parametrize("structural", [False, True])
@pytest.mark.parametrize("defect", ["caller-digest", "overridden-script", "unvalidated-url", "malformed-pin"])
def test_project_controlled_validator_bytes_cannot_claim_t1_or_t2(github, tmp_path, structural, defect):
    if structural:
        t1(github)
    text = _template("validator-workflow.yml").replace("__TRUSTED_DIGEST__", DIGEST)
    if defect == "caller-digest":
        text = text.replace(DIGEST, "${{ inputs.wheel-sha256 }}")
    elif defect == "overridden-script":
        text = text.replace("sha256sum -c -", "true # sha256sum -c -")
    elif defect == "unvalidated-url":
        text = text.replace("--proto '=https'", "")
    else:
        text = text.replace(DIGEST, "placeholder")
    github[0][f"repos/{WORKFLOW_REPO}/contents/{ops.WORKFLOW_PATH}?ref={SHA}"] = file_response(ops.WORKFLOW_PATH, text)
    change_workflow(github, "wheel-url: '__WHEEL_URL__'", "wheel-url: 'https://attacker.invalid/merit-0.1.0-py3-none-any.whl'\n      wheel-sha256: '" + "c" * 64 + "'")
    assert tier(github, tmp_path) == "unverified"


def test_t2_rejects_a_caller_digest_override_even_with_trusted_wrapper(github, tmp_path):
    change_workflow(github, "wheel-url: '__WHEEL_URL__'", "wheel-url: 'https://attacker.invalid/merit-0.1.0-py3-none-any.whl'\n      wheel-sha256: '" + "c" * 64 + "'")
    assert tier(github, tmp_path) == "T3"


def test_missing_gh_or_expected_identity_is_unverified(github, tmp_path, monkeypatch):
    assert ops.detect_tier(tmp_path) == "unverified"
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    assert tier(github, tmp_path) == "unverified"
    assert github[1] == []


def test_doctor_names_verified_tier(github, tmp_path):
    init(tmp_path, project_id="sample", id_prefix="SMP")
    assert "ok: verified enforcement tier: T2" in doctor.doctor(tmp_path, workflow_repo=WORKFLOW_REPO)


def test_doctor_without_expected_identity_names_unverified(github, tmp_path):
    init(tmp_path, project_id="sample", id_prefix="SMP")
    assert any("enforcement tier: unverified" in value for value in doctor.doctor(tmp_path))


def test_legacy_local_release_tag_is_peeled_but_refused_without_trusted_digest(tmp_path, monkeypatch):
    def local_api(root, endpoint, **kwargs):
        if endpoint == f"repos/{WORKFLOW_REPO}/commits/v0.1.0":
            sha = subprocess.run(["git", "rev-parse", "v0.1.0^{commit}"], cwd=ROOT,
                                 check=True, capture_output=True, encoding="utf-8").stdout.strip()
            return {"sha": sha}
        sha = endpoint.rsplit("=", 1)[1]
        content = subprocess.run(["git", "show", f"{sha}:{ops.WORKFLOW_PATH}"], cwd=ROOT,
                                 check=True, capture_output=True, encoding="utf-8").stdout
        assert "parse_wheel_filename" in content  # corrected R75/R76 tree
        return file_response(ops.WORKFLOW_PATH, content)
    monkeypatch.setattr(ops, "_api", local_api)
    with pytest.raises(EnvironmentError_, match="does not own a verified validator digest"):
        ops.resolve_workflow_sha(tmp_path, WORKFLOW_REPO)


@pytest.mark.parametrize("sha", ["v0.1.0", "a" * 39, "g" * 40, "a" * 40 + "\n"])
def test_resolve_ref_rejects_non_commit_identity(github, tmp_path, sha):
    github[0][f"repos/{WORKFLOW_REPO}/commits/v0.1.0"] = {"sha": sha}
    with pytest.raises(EnvironmentError_):
        ops.resolve_workflow_sha(tmp_path, WORKFLOW_REPO)


def test_resolve_requires_workflow_in_the_tagged_tree(github, tmp_path):
    github[0][f"repos/{WORKFLOW_REPO}/contents/{ops.WORKFLOW_PATH}?ref={SHA}"] = {"type": "dir", "path": ops.WORKFLOW_PATH}
    with pytest.raises(EnvironmentError_):
        ops.resolve_workflow_sha(tmp_path, WORKFLOW_REPO)


@pytest.mark.parametrize("existing", [".github/workflows/merit.yml", "CODEOWNERS", ".github/CODEOWNERS", ".git/hooks/pre-commit"])
def test_github_scaffold_preserves_existing_enforcement_files(github, tmp_path, existing):
    if existing.startswith(".git/"):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    path = tmp_path / existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("existing user content\n", encoding="utf-8")
    with pytest.raises(UsageError, match="overwrite"):
        ops.init_github(tmp_path, project_id="p", id_prefix="PP", workflow_repo=WORKFLOW_REPO,
                        wheel_url=WHEEL)
    assert path.read_text(encoding="utf-8") == "existing user content\n"
    assert not (tmp_path / "merit.toml").exists()
    assert not any("--method" in args or args[:3] == ("gh", "repo", "create") for args, _ in github[1])


def test_template_is_unpinned_and_scaffold_resolves_sha(github, tmp_path):
    target = tmp_path / "project"
    findings = ops.init_github(target, project_id="project", id_prefix="PRJ", workflow_repo=WORKFLOW_REPO,
                               wheel_url=WHEEL, github_repo="acme/project")
    assert "@__SHA__" in _template("workflow.yml")
    text = (target / ".github/workflows/merit.yml").read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert workflow["jobs"]["validate"]["uses"] == f"{WORKFLOW_REPO}/{ops.WORKFLOW_PATH}@{SHA}"
    assert "__" not in text
    assert workflow["jobs"]["validate"]["with"] == {"wheel-url": WHEEL}
    assert "merit report --output public/index.html" in text
    steps = workflow["jobs"]["report"]["steps"]
    installer = next(step for step in steps if step.get("name") == "Install the pinned release")
    assert installer["env"]["MERIT_WHEEL_SHA256"] == DIGEST
    assert installer["working-directory"] == "${{ runner.temp }}"
    assert "working-directory" not in next(step for step in steps if step.get("name") == "Build report")
    assert "actions/upload-artifact@" in text and "createComment" in text and "artifact-url" in text
    assert (target / ".git/hooks/pre-commit").read_text(encoding="utf-8").endswith("exec merit validate\n")
    assert any("T2" in finding for finding in findings)
    calls = github[1]
    created = next(args for args, _ in calls if args[:3] == ("gh", "repo", "create"))
    assert "--private" in created and "--push" not in created
    protection = next(json.loads(body) for args, body in calls if "PUT" in args)
    assert protection["required_pull_request_reviews"]["require_code_owner_reviews"] is True
    ruleset = next(json.loads(body) for args, body in calls if "POST" in args)
    assert ruleset["rules"][0]["parameters"]["workflows"][0] == {"repository_id": 11, "path": ops.WORKFLOW_PATH, "sha": SHA}


def test_rejected_ruleset_warns_with_the_achieved_tier(github, tmp_path):
    github[2].add("orgs/acme/rulesets")
    findings = ops.init_github(tmp_path, project_id="project", id_prefix="PRJ", workflow_repo=WORKFLOW_REPO,
                               wheel_url=WHEEL, github_repo="acme/project")
    assert any("ruleset was rejected" in finding and "achieved enforcement tier: T2" in finding for finding in findings)


def test_cli_github_requires_explicit_identity_before_scaffold(tmp_path, capsys):
    assert cli.main(["init", str(tmp_path), "--project-id", "p", "--id-prefix", "PP", "--github"]) == 2
    assert "--workflow-repo" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_cli_github_wires_every_argument(github, tmp_path, monkeypatch, capsys):
    called = []
    def setup(target, **kwargs):
        called.append((target, kwargs))
        return ["warning: ruleset rejected; achieved enforcement tier: T3"]
    monkeypatch.setattr(ops, "init_github", setup)
    assert cli.main(["init", str(tmp_path), "--project-id", "p", "--id-prefix", "PP", "--github",
                     "--workflow-repo", WORKFLOW_REPO, "--github-repo", "acme/project", "--wheel-url", WHEEL]) == 0
    assert called == [(tmp_path, {"project_id": "p", "id_prefix": "PP", "workflow_repo": WORKFLOW_REPO,
                                 "wheel_url": WHEEL, "github_repo": "acme/project"})]
    assert "achieved enforcement tier: T3" in capsys.readouterr().out


@pytest.mark.parametrize("url", ["http://example.com/merit.whl", WHEEL.replace("merit-", "evil-"),
    WHEEL + "?x=y", WHEEL.replace("https://", "https://x'@"), "https://[broken/merit-0.1.0-py3-none-any.whl"])
def test_invalid_wheel_configuration_writes_nothing(github, tmp_path, url):
    with pytest.raises(UsageError):
        ops.init_github(tmp_path, project_id="p", id_prefix="PP", workflow_repo=WORKFLOW_REPO,
                        wheel_url=url)
    assert list(tmp_path.iterdir()) == []


def test_hook_propagates_real_error_and_advisory_exit_codes(tmp_path):
    from test_release import bash_executable
    bash = bash_executable()
    if bash is None:
        pytest.skip("Bash is required to execute a Git hook")
    init(tmp_path, project_id="project", id_prefix="PRJ")
    executable = tmp_path / "merit"
    executable.write_text('#!/bin/sh\nexec "$MERIT_TEST_PYTHON" -m merit.cli "$@"\n', encoding="utf-8")
    executable.chmod(0o755)
    for exit_code in (0, 1):
        if exit_code:
            (tmp_path / "requirements/invalid.yaml").write_text("id: invalid\n", encoding="utf-8")
        result = subprocess.run([bash, "--noprofile", "--norc", "-c", 'export PATH="$(pwd):$PATH"\n' + _template("pre-commit")],
                                cwd=tmp_path, capture_output=True, check=False,
                                env={**os.environ, "MERIT_TEST_PYTHON": Path(sys.executable).as_posix()})
        assert result.returncode == exit_code
