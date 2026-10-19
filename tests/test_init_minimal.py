import shlex
import subprocess

import pytest

import merit
from merit import cli, scaffold
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
    assert f'pipx install "git+{merit.SOURCE_URL}@v{merit.__version__}"' in text
    assert "<org>" not in text and r"$HOME\merit-venv\Scripts\merit.exe" in text
    assert "highest classification" in text


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True, encoding="utf-8"
    )


def test_git_ignores_proposals(repo):
    # Ask git, not the file: a rule that looks right but does nothing is the failure.
    assert git(repo, "check-ignore", "-q", "proposals/x", check=False).returncode == 0
    other = git(repo, "check-ignore", "-q", "requirements/REQ-SMP-0001.yaml", check=False)
    assert other.returncode == 1


def test_git_never_normalises_retained_originals(repo):
    # origin_sha256 hashes raw bytes; if git rewrites line endings on checkout,
    # a fresh clone fails source verification for a file nobody edited.
    raw = git(repo, "check-attr", "text", "sources/raw/a.vtt").stdout.strip()
    assert raw.endswith("text: unset")
    canonical = git(repo, "check-attr", "text", "sources/2026-09-12-a.md").stdout.strip()
    assert canonical.endswith("text: unspecified")


def test_existing_gitignore_keeps_user_lines_and_gains_merit_rules(tmp_path):
    (tmp_path / ".gitignore").write_bytes(b"node_modules/\n*.log")  # no trailing newline
    (tmp_path / ".gitattributes").write_bytes(b"*.png binary\n")
    init(tmp_path, project_id="sample", id_prefix="SMP")
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignore[:2] == ["node_modules/", "*.log"]
    assert ignore.count("proposals/") == 1
    attrs = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert attrs[0] == "*.png binary"
    assert attrs.count("sources/raw/** -text") == 1
    assert git(tmp_path, "check-ignore", "-q", "proposals/x", check=False).returncode == 0
    assert git(tmp_path, "check-attr", "text", "sources/raw/a.vtt").stdout.strip().endswith("unset")


def test_rule_already_present_is_not_appended_again(tmp_path):
    (tmp_path / ".gitignore").write_bytes(b"proposals/\nbuild/\n")
    init(tmp_path, project_id="sample", id_prefix="SMP")
    assert (tmp_path / ".gitignore").read_bytes() == b"proposals/\nbuild/\n"


def test_init_twice_does_not_duplicate_appended_lines(tmp_path):
    init(tmp_path, project_id="sample", id_prefix="SMP")
    before = {n: (tmp_path / n).read_bytes() for n in (".gitignore", ".gitattributes")}
    for name in ("merit.toml", "AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        (tmp_path / name).unlink()
    init(tmp_path, project_id="sample", id_prefix="SMP")
    assert {n: (tmp_path / n).read_bytes() for n in before} == before


def test_existing_documents_make_init_refuse_naming_every_collision(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("my own notes\n", encoding="utf-8")
    (tmp_path / "GEMINI.md").write_text("mine too\n", encoding="utf-8")
    with pytest.raises(UsageError) as exc:
        init(tmp_path, project_id="sample", id_prefix="SMP")
    assert "CLAUDE.md" in str(exc.value) and "GEMINI.md" in str(exc.value)
    assert "AGENTS.md" not in str(exc.value)
    assert exc.value.fix
    assert sorted(p.name for p in tmp_path.iterdir()) == ["CLAUDE.md", "GEMINI.md"]
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "my own notes\n"


def test_non_utf8_gitignore_is_a_collision_rather_than_corrupted(tmp_path):
    utf16 = "node_modules/\n".encode("utf-16")  # what PowerShell 5.1 '>' writes
    (tmp_path / ".gitignore").write_bytes(utf16)
    with pytest.raises(UsageError) as exc:
        init(tmp_path, project_id="sample", id_prefix="SMP")
    assert ".gitignore" in str(exc.value)
    assert (tmp_path / ".gitignore").read_bytes() == utf16


def test_collision_fix_quotes_a_path_with_spaces(tmp_path):
    target = tmp_path / "my project"
    target.mkdir()
    (target / "CLAUDE.md").write_text("x", encoding="utf-8")
    with pytest.raises(UsageError) as exc:
        init(target, project_id="sample", id_prefix="SMP")
    assert f'"{target}"' in exc.value.fix


def test_git_init_failure_is_an_environment_error_and_leaves_no_blocking_files(
    tmp_path, monkeypatch
):
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0], stderr=b"fatal: no can do")

    monkeypatch.setattr(scaffold.subprocess, "run", boom)
    with pytest.raises(EnvironmentError_) as exc:
        init(tmp_path / "p", project_id="sample", id_prefix="SMP")
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "no can do" in str(exc.value)
    assert exc.value.fix
    assert not (tmp_path / "p" / "merit.toml").exists()


def test_a_literal_brace_in_a_template_survives(tmp_path, monkeypatch):
    real = scaffold._template

    def with_braces(name, **fields):
        text = real(name, **fields)
        return text + ' {"example": 1}\n' if name == "AGENTS.md" else text

    monkeypatch.setattr(scaffold, "_template", with_braces)
    init(tmp_path, project_id="sample", id_prefix="SMP")
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert '{"example": 1}' in text
    assert "{merit_version}" not in text


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
