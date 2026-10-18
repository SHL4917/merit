from pathlib import Path

import pytest

import merit
from merit.config import Config, find_repo_root, load_config
from merit.errors import EnvironmentError_, ExitCode

FULL_TOML = """\
[project]
id = "sample"
id_prefix = "SMP"

[tool]
merit_version = "{version}"

[classification]
allowed = ["PUBLIC", "INTERNAL", "CONFIDENTIAL"]

[coverage]
min_chars = 25
"""

MINIMAL_TOML = """\
[project]
id = "sample"
id_prefix = "SMP"

[tool]
merit_version = "{version}"

[classification]
allowed = ["PUBLIC"]
"""


def write_toml(root, body, version=None):
    (root / "merit.toml").write_text(
        body.format(version=version or merit.__version__), encoding="utf-8"
    )


def test_version_is_a_plain_string():
    assert merit.__version__ == "0.1.0"


def test_values_load_from_merit_toml(tmp_path):
    write_toml(tmp_path, FULL_TOML)
    assert load_config(tmp_path) == Config(
        project_id="sample",
        id_prefix="SMP",
        merit_version=merit.__version__,
        classifications=("PUBLIC", "INTERNAL", "CONFIDENTIAL"),
        coverage_min_chars=25,
    )


def test_coverage_min_chars_defaults_to_40(tmp_path):
    write_toml(tmp_path, MINIMAL_TOML)
    assert load_config(tmp_path).coverage_min_chars == 40


def test_missing_merit_toml_is_an_environment_error(tmp_path):
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "merit.toml" in str(exc.value)
    assert exc.value.fix


def test_pin_mismatch_names_both_versions(tmp_path):
    write_toml(tmp_path, FULL_TOML, version="9.9.9")
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    message = str(exc.value)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "9.9.9" in message
    assert merit.__version__ in message
    assert "pipx install git+" in exc.value.fix
    assert "@v9.9.9" in exc.value.fix
    assert "pip install merit==" not in exc.value.fix


def test_pin_uses_exact_string_comparison(tmp_path):
    write_toml(tmp_path, FULL_TOML, version=merit.__version__ + ".0")
    with pytest.raises(EnvironmentError_):
        load_config(tmp_path)


def test_missing_required_key_is_an_environment_error(tmp_path):
    raw(tmp_path, valid_with(project='[project]\nid = "sample"\n'))
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "id_prefix" in str(exc.value)
    assert exc.value.fix


def test_malformed_toml_is_an_environment_error(tmp_path):
    (tmp_path / "merit.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "merit.toml" in str(exc.value)
    assert exc.value.fix


def test_find_repo_root_walks_upward(tmp_path):
    write_toml(tmp_path, FULL_TOML)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path


def test_find_repo_root_returns_start_when_it_holds_merit_toml(tmp_path):
    write_toml(tmp_path, FULL_TOML)
    assert find_repo_root(tmp_path) == tmp_path


def test_find_repo_root_without_merit_toml_is_an_environment_error(
    tmp_path, monkeypatch
):
    # Hermetic: only paths inside tmp_path may report a merit.toml, so a stray
    # merit.toml in any ancestor of tmp_path cannot affect the walk.
    real_is_file = Path.is_file

    def is_file_inside_tmp_only(self):
        return tmp_path in self.parents and real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file_inside_tmp_only)
    with pytest.raises(EnvironmentError_) as exc:
        find_repo_root(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert "merit.toml" in str(exc.value)
    assert exc.value.fix


def raw(tmp_path, body):
    (tmp_path / "merit.toml").write_text(body, encoding="utf-8")


def valid_with(**overrides):
    """A valid merit.toml body, with whole sections replaced by name."""
    sections = {
        "project": '[project]\nid = "sample"\nid_prefix = "SMP"\n',
        "tool": f'[tool]\nmerit_version = "{merit.__version__}"\n',
        "classification": '[classification]\nallowed = ["PUBLIC"]\n',
        "coverage": "[coverage]\nmin_chars = 40\n",
    }
    sections.update(overrides)
    return "\n".join(sections.values())


@pytest.mark.parametrize(
    "section, replacement, needle",
    [
        ("classification", '[classification]\nallowed = "PUBLIC"\n', "allowed"),
        ("classification", "[classification]\nallowed = [1, 2]\n", "allowed"),
        ("coverage", '[coverage]\nmin_chars = "40"\n', "min_chars"),
        ("coverage", "[coverage]\nmin_chars = true\n", "min_chars"),
        ("project", '[project]\nid = 5\nid_prefix = "SMP"\n', "id"),
        ("project", '[project]\nid = "sample"\nid_prefix = 5\n', "id_prefix"),
        ("tool", "[tool]\nmerit_version = 1\n", "merit_version"),
    ],
)
def test_wrongly_typed_value_is_an_environment_error(
    tmp_path, section, replacement, needle
):
    raw(tmp_path, valid_with(**{section: replacement}))
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert needle in str(exc.value)
    assert exc.value.fix


@pytest.mark.parametrize("section", ["project", "tool", "classification", "coverage"])
def test_non_table_section_is_an_environment_error(tmp_path, section):
    # A bare top-level scalar must precede any table header in TOML.
    raw(tmp_path, f'{section} = "x"\n' + valid_with(**{section: ""}))
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert exc.value.exit_code == ExitCode.ENVIRONMENT
    assert f"[{section}]" in str(exc.value)
    assert exc.value.fix


def test_pin_is_compared_before_other_keys_are_validated(tmp_path):
    # A merit.toml from a newer merit with a changed layout: the useful message
    # is the version mismatch, not a missing key.
    raw(tmp_path, '[tool]\nmerit_version = "9.9.9"\n')
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert "9.9.9" in str(exc.value)
    assert merit.__version__ in str(exc.value)
    assert "pipx install git+" in exc.value.fix


def test_missing_pin_is_a_missing_key_error(tmp_path):
    raw(tmp_path, valid_with(tool=""))
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert "merit_version" in str(exc.value)
    assert exc.value.fix
