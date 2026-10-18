import shlex
import subprocess

import pytest

import merit
from merit import cli
from merit.config import find_repo_root, load_config
from merit.errors import EnvironmentError_, ExitCode, UsageError
from merit.scaffold import init

GOVERNED = [
    "sources",
    "requirements",
    "candidates",
    "issues",
    "decisions",
    "supersessions",
    "baselines",
]


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "project"
    init(target, project_id="sample", id_prefix="SMP")
    return target


def test_every_directory_is_created(repo):
    for name in [*GOVERNED, "sources/raw", "proposals"]:
        assert (repo / name).is_dir(), name


def test_scaffold_contains_no_symlinks(repo):
    assert [p for p in repo.rglob("*") if p.is_symlink()] == []


def test_claude_and_gemini_are_one_line_import_pointers(repo):
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert (repo / "GEMINI.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"


def test_agents_md_carries_the_bootstrap_rules(repo):
    text = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "merit help-agent" in text
    assert "Never write or edit" in text
    assert "proposals/" in text
    assert "merit apply" in text
    for directory in GOVERNED:
        assert f"`{directory}/`" in text
    assert f"pipx install git+https://github.com/<org>/merit@v{merit.__version__}" in text
    assert "highest classification" in text


def test_gitignore_excludes_proposals(repo):
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "proposals/" in lines


def test_gitattributes_never_normalises_retained_originals(repo):
    lines = (repo / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "sources/raw/** -text" in lines
    # origin_sha256 hashes raw bytes; the reason belongs next to the rule.
    assert any(line.startswith("#") and "origin_sha256" in line for line in lines)


def test_merit_toml_loads_and_pins_the_installed_version(repo):
    config = load_config(repo)
    assert config.project_id == "sample"
    assert config.id_prefix == "SMP"
    assert config.merit_version == merit.__version__
    assert config.classifications == ("PUBLIC", "INTERNAL", "CONFIDENTIAL")
    assert config.coverage_min_chars == 40


def test_scaffold_files_use_lf_line_endings(repo):
    names = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "merit.toml", ".gitignore", ".gitattributes"]
    for name in names:
        assert b"\r" not in (repo / name).read_bytes(), name


def test_init_runs_git_init_when_needed(repo):
    assert (repo / ".git").is_dir()


def test_init_leaves_an_existing_git_repo_alone(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    head = (tmp_path / ".git" / "HEAD").read_bytes()
    init(tmp_path, project_id="sample", id_prefix="SMP")
    assert (tmp_path / ".git" / "HEAD").read_bytes() == head
    assert (tmp_path / "merit.toml").is_file()


def test_init_refuses_to_overwrite_an_existing_project(repo):
    original = (repo / "merit.toml").read_bytes()
    with pytest.raises(UsageError) as exc:
        init(repo, project_id="other", id_prefix="OTH")
    assert exc.value.exit_code == ExitCode.USAGE
    assert exc.value.fix
    assert (repo / "merit.toml").read_bytes() == original


@pytest.mark.parametrize(
    "project_id, id_prefix",
    [
        ("Sample", "SMP"),
        ("sample", "smp"),
        ("sample", "S"),
        ("sample", "TOOLONGX"),
        ('x"y', "SMP"),
        ("", "SMP"),
    ],
)
def test_init_rejects_ids_that_would_break_the_toml_or_id_patterns(
    tmp_path, project_id, id_prefix
):
    with pytest.raises(UsageError) as exc:
        init(tmp_path / "p", project_id=project_id, id_prefix=id_prefix)
    assert exc.value.fix
    assert not (tmp_path / "p").exists()


def test_cli_init_scaffolds_the_target(tmp_path):
    target = tmp_path / "via-cli"
    status = cli.main(["init", str(target), "--project-id", "sample", "--id-prefix", "SMP"])
    assert status == 0
    assert (target / "merit.toml").is_file()


def test_cli_init_requires_project_id_and_prefix(tmp_path, capsys):
    assert cli.main(["init", str(tmp_path / "x")]) == 2
    assert "--project-id" in capsys.readouterr().err


def _runnable(fix: str) -> list[str]:
    """The argv a user would get by filling in the placeholders of ``fix``."""
    fix = fix.replace("<project-id>", "sample").replace("<ID-PREFIX>", "SMP")
    argv = shlex.split(fix)
    assert argv[0] == "merit"
    return argv[1:]


def test_config_fix_strings_name_a_command_the_parser_accepts(tmp_path):
    with pytest.raises(EnvironmentError_) as missing_file:
        load_config(tmp_path)
    with pytest.raises(EnvironmentError_) as missing_root:
        find_repo_root(tmp_path)
    for error in (missing_file.value, missing_root.value):
        args = cli.build_parser().parse_args(_runnable(error.fix))
        assert args.command == "init"
        assert (args.project_id, args.id_prefix) == ("sample", "SMP")
