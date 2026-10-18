import shutil
import subprocess

import pytest

import merit
from merit import cli
from merit import doctor as doctor_module
from merit.doctor import doctor
from merit.scaffold import init


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "project"
    init(target, project_id="sample", id_prefix="SMP")
    return target


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def without(monkeypatch, tool):
    real = shutil.which
    monkeypatch.setattr(
        doctor_module.shutil,
        "which",
        lambda name, *a, **k: None if name == tool else real(name, *a, **k),
    )


def errors(findings):
    return [f for f in findings if f.startswith(doctor_module.ERROR)]


def warnings(findings):
    return [f for f in findings if f.startswith(doctor_module.WARNING)]


@pytest.mark.skipif(shutil.which("gh") is None, reason="a fully healthy report needs gh")
def test_fresh_scaffold_is_healthy(repo):
    assert doctor(repo) == []


def test_fresh_scaffold_has_no_errors_even_without_gh(repo, monkeypatch):
    without(monkeypatch, "gh")
    assert errors(doctor(repo)) == []


def test_tracked_proposal_file_is_a_warning(repo):
    (repo / "proposals" / "draft.yaml").write_text("quote: sensitive\n", encoding="utf-8")
    git(repo, "add", "-f", "proposals/draft.yaml")
    assert any("proposals/draft.yaml" in f for f in warnings(doctor(repo)))


def test_untracked_proposal_file_is_not_flagged(repo):
    (repo / "proposals" / "draft.yaml").write_text("quote: sensitive\n", encoding="utf-8")
    assert not any("proposals/draft.yaml" in f for f in doctor(repo))


def test_pin_mismatch_is_an_error_naming_both_versions_and_the_fix(repo):
    path = repo / "merit.toml"
    path.write_bytes(
        path.read_bytes().replace(
            f'merit_version = "{merit.__version__}"'.encode(), b'merit_version = "9.9.9"'
        )
    )
    mismatch = [f for f in errors(doctor(repo)) if "9.9.9" in f]
    assert len(mismatch) == 1
    assert merit.__version__ in mismatch[0]
    assert "pipx install git+https://github.com/<org>/merit@v9.9.9" in mismatch[0]


def test_missing_gh_is_a_warning(repo, monkeypatch):
    without(monkeypatch, "gh")
    findings = doctor(repo)
    assert any("gh" in f for f in warnings(findings))
    assert errors(findings) == []


def test_missing_git_is_an_error(repo, monkeypatch):
    without(monkeypatch, "git")
    assert any("git" in f for f in errors(doctor(repo)))


def test_directory_that_is_not_a_git_repo_is_an_error(repo):
    shutil.rmtree(repo / ".git")
    assert any("init" in f and "not a git repository" in f for f in errors(doctor(repo)))


def test_missing_merit_toml_is_an_error(tmp_path):
    assert any("merit.toml" in f for f in errors(doctor(tmp_path)))


def test_missing_artefact_directories_are_not_a_problem(repo):
    # Git cannot track an empty directory, so a fresh clone lacks them.
    for name in ["requirements", "candidates", "issues", "decisions", "supersessions", "baselines"]:
        (repo / name).rmdir()
    assert errors(doctor(repo)) == []


def test_cli_doctor_exits_0_when_there_are_only_warnings(repo, monkeypatch, capsys):
    without(monkeypatch, "gh")
    assert cli.main(["doctor", str(repo)]) == 0
    assert "gh" in capsys.readouterr().out


def test_cli_doctor_exits_4_on_an_error(repo, capsys):
    (repo / "merit.toml").write_bytes(b'[tool]\nmerit_version = "9.9.9"\n')
    assert cli.main(["doctor", str(repo)]) == 4
    assert "9.9.9" in capsys.readouterr().out


def test_cli_doctor_finds_the_repo_from_a_subdirectory(repo, monkeypatch):
    without(monkeypatch, "gh")
    assert cli.main(["doctor", str(repo / "sources" / "raw")]) == 0


def test_cli_doctor_outside_any_repo_is_an_environment_error(tmp_path, capsys):
    assert cli.main(["doctor", str(tmp_path)]) == 4
    assert "merit.toml" in capsys.readouterr().err
