"""Release provenance, offline packaging, and executable wheel-integrity checks."""

import hashlib
import base64
import csv
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest
import yaml

from merit import __version__
from merit.git_ops import _trusted_workflow_digest
from test_git_ops import git, repo

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/merit-validate.yml"


def workflow(name):
    path = ROOT / ".github" / "workflows" / name
    assert path.is_file(), f"release bootstrap must provide {path.name}"
    # BaseLoader preserves GitHub's 'on' key rather than YAML 1.1's boolean True.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def install_step():
    steps = workflow("merit-validate.yml")["jobs"]["validate"]["steps"]
    return next(step for step in steps if step.get("id") == "install-merit")


def test_package_and_module_versions_match():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__


def test_reusable_workflow_interface_and_history():
    data = workflow("merit-validate.yml")
    inputs = data["on"]["workflow_call"]["inputs"]
    assert set(inputs) == {"wheel-url"}
    for name in ("wheel-url",):
        assert inputs[name]["required"] == "true"
        assert inputs[name]["type"] == "string"
        assert "default" not in inputs[name]
    assert data["permissions"] == {"contents": "read"}
    steps = data["jobs"]["validate"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    assert checkout["with"]["persist-credentials"] == "false"
    assert any(step.get("run", "").strip() == "merit validate" for step in steps)
    text = (ROOT / WORKFLOW).read_text(encoding="utf-8")
    assert "merit.toml" not in text
    assert "pip install merit==" not in text
    step = install_step()
    assert step["shell"] == "bash"
    assert step["env"]["MERIT_WHEEL_URL"] == "${{ inputs.wheel-url }}"
    digest = step["env"]["MERIT_WHEEL_SHA256"]
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert _trusted_workflow_digest(text) == digest
    assert "${{ inputs." not in step["run"]
    assert "sha256sum -c" in step["run"]
    assert step["run"].index("sha256sum -c") < step["run"].index("pip install")


def bash_executable():
    if os.name == "nt":
        candidate = Path(shutil.which("git")).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


def run_install(tmp_path, digest, url=f"https://example.invalid/merit-{__version__}-py3-none-any.whl", *, content=b"offline wheel fixture\n", caller_digest=None):
    bash = bash_executable()
    if bash is None:
        pytest.skip("Bash is needed to exercise the Ubuntu workflow script")
    # Only curl and pip are mocked. The downloaded bytes and sha256sum are real.
    prelude = r'''
python() { "$MERIT_TEST_PYTHON" "$@"; }
curl() {
  printf 'download\n' >> events
  while [[ "$1" != "--output" ]]; do shift; done
  cp fixture.whl "$2"
}
pip() {
  printf 'install\n' >> events
  printf '%s\n' "$@" > pip-args
}
'''
    (tmp_path / "fixture.whl").write_bytes(content)
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", prelude + install_step()["run"]],
        cwd=tmp_path, capture_output=True, encoding="utf-8", check=False,
        # Model GitHub's trusted step env overriding caller-controlled values.
        env={**os.environ, "MERIT_WHEEL_SHA256": caller_digest or "", "INPUT_WHEEL_SHA256": caller_digest or "",
             **{"MERIT_WHEEL_SHA256": digest, "MERIT_WHEEL_URL": url},
             "MERIT_TEST_PYTHON": Path(sys.executable).as_posix()},
    )
    events = (tmp_path / "events").read_text(encoding="utf-8").splitlines() if (tmp_path / "events").exists() else []
    return result, events


@pytest.mark.parametrize("uppercase", [False, True])
def test_verified_bytes_are_installed_under_a_valid_wheel_filename(tmp_path, uppercase):
    digest = hashlib.sha256(b"offline wheel fixture\n").hexdigest()
    result, events = run_install(tmp_path, digest.upper() if uppercase else digest)
    assert result.returncode == 0, result.stderr
    assert "merit.whl: OK" in result.stdout
    assert events == ["download", "install"]
    args = (tmp_path / "pip-args").read_text(encoding="utf-8").splitlines()
    assert args[0] == "install"
    assert args[-1] == f"./merit-{__version__}-py3-none-any.whl"
    assert (tmp_path / args[-1]).read_bytes() == b"offline wheel fixture\n"


@pytest.mark.parametrize("filename, suffix", [
    ("merit-0.2.0-py3-none-any.whl", ""),
    ("merit-0.1.0-1-py3-none-any.whl", "?download=1"),
    ("merit-0.1.0-py2.py3-none-any.whl", "?filename=untrusted.whl#ignored"),
    ("merit-0.1.0-py3-none-any.whl", "?token=$(touch%20injected)"),
])
def test_install_filename_matches_url_path_despite_query_or_fragment(tmp_path, filename, suffix):
    digest = hashlib.sha256(b"offline wheel fixture\n").hexdigest()
    result, events = run_install(tmp_path, digest, f"https://example.invalid/releases/{filename}{suffix}")
    assert result.returncode == 0, result.stderr
    assert events == ["download", "install"]
    args = (tmp_path / "pip-args").read_text(encoding="utf-8").splitlines()
    assert args[-1] == f"./{filename}"
    assert (tmp_path / filename).read_bytes() == b"offline wheel fixture\n"
    assert not (tmp_path / "injected").exists()


@pytest.mark.parametrize("filename", [
    "merit.whl", "other-0.1.0-py3-none-any.whl", "merit-not_a_version-py3-none-any.whl",
    "merit-0.1.0-py3--any.whl", "merit-0.1.0-py3-none-any.whl.exe",
    "merit-0.1.0-py3-none-any.whl%0A", "%2e%2e%2fmerit-0.1.0-py3-none-any.whl",
    "merit-0.1.0-py3-none-$(touch%20injected).whl", "",
])
def test_invalid_or_mismatched_wheel_filename_is_rejected_before_download(tmp_path, filename):
    result, events = run_install(tmp_path, "a" * 64, f"https://example.invalid/{filename}?filename=merit-0.1.0-py3-none-any.whl")
    assert result.returncode != 0
    assert events == []
    assert not (tmp_path / "pip-args").exists()
    assert not (tmp_path / "injected").exists()


def test_wrong_digest_stops_before_install(tmp_path):
    result, events = run_install(tmp_path, "0" * 64)
    assert result.returncode != 0
    assert events == ["download"]
    assert "FAILED" in result.stdout
    assert not (tmp_path / "pip-args").exists()
    assert not (tmp_path / f"merit-{__version__}-py3-none-any.whl").exists()


def test_replacing_caller_url_and_digest_cannot_replace_trusted_validator_bytes(tmp_path):
    trusted = hashlib.sha256(b"offline wheel fixture\n").hexdigest()
    malicious = b"replacement validator that always passes\n"
    result, events = run_install(tmp_path, trusted,
        "https://attacker.invalid/merit-0.1.0-py3-none-any.whl", content=malicious,
        caller_digest=hashlib.sha256(malicious).hexdigest())
    assert result.returncode != 0
    assert events == ["download"]
    assert not (tmp_path / "pip-args").exists()


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", "$(touch injected)"])
def test_malformed_digest_is_rejected_before_download(tmp_path, digest):
    result, events = run_install(tmp_path, digest)
    assert result.returncode != 0
    assert events == []
    assert not (tmp_path / "injected").exists()


@pytest.mark.parametrize("url", ["file:///tmp/wheel.whl", "http://example.invalid/wheel.whl", "--help"])
def test_download_requires_an_explicit_https_url(tmp_path, url):
    result, events = run_install(tmp_path, "a" * 64, url)
    assert result.returncode != 0
    assert events == []


def test_release_builds_wheel_and_attaches_it_to_the_existing_tag():
    data = workflow("release.yml")
    assert data["on"]["push"]["tags"] == ["v*"]
    job = data["jobs"]["release"]
    assert job["permissions"] == {"contents": "write"}
    steps = job["steps"]
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "python -m build --wheel" in scripts
    assert "--no-isolation" in scripts
    assert data["jobs"]["release"]["env"]["SOURCE_DATE_EPOCH"] == "315532800"
    assert "setuptools==84.0.0" in scripts and "wheel==0.48.0" in scripts
    assert "MERIT_WHEEL_SHA256" in scripts
    assert "sha256sum" in scripts and "dist/*.whl" in scripts
    assert "GITHUB_REF_NAME" in scripts and "pyproject.toml" in scripts
    assert WORKFLOW in scripts
    assert 'gh release create "$GITHUB_REF_NAME"' in scripts
    assert "--verify-tag" in scripts
    assert 'gh release upload "$GITHUB_REF_NAME" dist/*.whl dist/SHA256SUMS' in scripts
    assert "git tag" not in scripts and "git push" not in scripts


def test_ci_tests_push_and_pull_requests_with_warnings_as_errors():
    data = workflow("test.yml")
    assert {"push", "pull_request"} <= data["on"].keys()
    scripts = "\n".join(step.get("run", "") for step in data["jobs"]["test"]["steps"])
    assert '".[dev]"' in scripts
    assert "pytest" in scripts and "-W error" in scripts
    # The packaging regression must execute in CI, where build tools are installed.
    assert "build" in scripts and "wheel" in scripts and "setuptools>=68" in scripts


def test_releasing_documents_commit_then_tag_then_publish():
    path = ROOT / "docs" / "RELEASING.md"
    assert path.is_file(), "document the release bootstrap order"
    text = path.read_text(encoding="utf-8").lower()
    positions = [text.index(f"{number}. {verb}") for number, verb in enumerate(["commit", "tag", "publish"], 1)]
    assert positions == sorted(positions)
    assert "merit-validate.yml" in text and "sha-256" in text
    assert "merit init" in text and "sha" in text
    assert "valid wheel filename" in " ".join(text.split())


def assert_tag_workflow(root, tag):
    resolved = git(root, "rev-parse", f"{tag}^{{commit}}")
    assert re.fullmatch("[0-9a-f]{40}", resolved)
    result = subprocess.run(["git", "cat-file", "-e", f"{resolved}:{WORKFLOW}"], cwd=root, capture_output=True, check=False)
    assert result.returncode == 0, "release tag predates the reusable workflow"


def test_resolved_release_tag_contains_workflow_and_rejects_early_tag(repo):
    git(repo, "-c", "tag.gpgsign=false", "tag", "too-early")
    path = repo / WORKFLOW
    path.parent.mkdir(parents=True)
    path.write_text((ROOT / WORKFLOW).read_text(encoding="utf-8"), encoding="utf-8")
    git(repo, "add", WORKFLOW)
    git(repo, "commit", "-m", "release package and workflow")
    git(repo, "-c", "tag.gpgsign=false", "tag", "v0.1.0")
    assert_tag_workflow(repo, "v0.1.0")
    with pytest.raises(AssertionError, match="predates"):
        assert_tag_workflow(repo, "too-early")


def test_local_release_tag_contains_workflow_when_present():
    tag = f"v{__version__}"
    if tag in git(ROOT, "tag", "--list").splitlines():
        assert_tag_workflow(ROOT, tag)


def test_built_wheel_contains_shipped_templates_and_resources(tmp_path):
    interpreter = os.environ.get("MERIT_BUILD_PYTHON", sys.executable)
    probe = subprocess.run([interpreter, "-c", "import setuptools, wheel"], capture_output=True, check=False)
    if probe.returncode:
        pytest.skip("offline wheel build needs locally installed setuptools and wheel")
    source = tmp_path / "package"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(ROOT / name, source / name)
    shutil.copytree(ROOT / "src" / "merit", source / "src" / "merit", ignore=shutil.ignore_patterns("__pycache__"))
    release_steps = workflow("release.yml")["jobs"]["release"]["steps"]
    recipe = next(step["run"] for step in release_steps if step.get("name") == "Build wheel and digest")
    python_blocks = re.findall(r"python - <<'PY'\n(.*?)\nPY", recipe, re.S)
    assert len(python_blocks) == 2
    subprocess.run([sys.executable, "-c", python_blocks[0]], cwd=source, check=True, capture_output=True)
    (source / ".github/workflows").mkdir(parents=True)
    shutil.copyfile(ROOT / WORKFLOW, source / WORKFLOW)
    output = source / "dist"
    result = subprocess.run(
        [interpreter, "-m", "pip", "wheel", "--no-index", "--no-build-isolation", "--no-deps", "--wheel-dir", str(output), str(source)],
        capture_output=True, encoding="utf-8", check=False,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "SOURCE_DATE_EPOCH": "315532800"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(output.glob("*.whl"))
    assert [path.name for path in wheels] == [f"merit-{__version__}-py3-none-any.whl"]
    canonical = subprocess.run([sys.executable, "-c", python_blocks[1]], cwd=source,
                               capture_output=True, encoding="utf-8", check=False)
    assert canonical.returncode == 0, canonical.stdout + canonical.stderr
    assert hashlib.sha256(wheels[0].read_bytes()).hexdigest() == install_step()["env"]["MERIT_WHEEL_SHA256"]
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.testzip() is None
        record = next(name for name in archive.namelist() if name.endswith(".dist-info/RECORD"))
        for name, digest, size in csv.reader(io.StringIO(archive.read(record).decode("utf-8"))):
            if name != record:
                data = archive.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
                assert digest == "sha256=" + expected and int(size) == len(data)
        names = set(archive.namelist())
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "merit.toml", "workflow.yml", "validator-workflow.yml", "CODEOWNERS", "pre-commit"):
            assert f"merit/templates/{name}" in names
        for resource in (ROOT / "src" / "merit").rglob("*"):
            if resource.is_file() and resource.suffix in (".json", ".md", ".html", ".toml"):
                relative = resource.relative_to(ROOT / "src").as_posix()
                assert relative in names
