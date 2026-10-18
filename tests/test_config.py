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
    assert exc.value.fix


def test_pin_uses_exact_string_comparison(tmp_path):
    write_toml(tmp_path, FULL_TOML, version=merit.__version__ + ".0")
    with pytest.raises(EnvironmentError_):
        load_config(tmp_path)


def test_missing_required_key_is_an_environment_error(tmp_path):
    (tmp_path / "merit.toml").write_text('[project]\nid = "sample"\n', encoding="utf-8")
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert "id_prefix" in str(exc.value)


def test_malformed_toml_is_an_environment_error(tmp_path):
    (tmp_path / "merit.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(EnvironmentError_) as exc:
        load_config(tmp_path)
    assert "merit.toml" in str(exc.value)


def test_find_repo_root_walks_upward(tmp_path):
    write_toml(tmp_path, FULL_TOML)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path


def test_find_repo_root_returns_start_when_it_holds_merit_toml(tmp_path):
    write_toml(tmp_path, FULL_TOML)
    assert find_repo_root(tmp_path) == tmp_path


def test_find_repo_root_without_merit_toml_is_an_environment_error(tmp_path):
    with pytest.raises(EnvironmentError_) as exc:
        find_repo_root(tmp_path)
    assert "merit.toml" in str(exc.value)
    assert exc.value.fix
