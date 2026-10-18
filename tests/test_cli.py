import pytest

from merit import cli
from merit.errors import (
    AnchorError,
    EnvironmentError_,
    ExitCode,
    MeritError,
    UsageError,
    ValidationFailed,
)


def test_exit_code_values_are_the_contract():
    assert [(c.name, int(c)) for c in ExitCode] == [
        ("SUCCESS", 0),
        ("VALIDATION", 1),
        ("USAGE", 2),
        ("ANCHOR", 3),
        ("ENVIRONMENT", 4),
    ]


def test_command_configure_adds_its_own_arguments(monkeypatch):
    seen = []
    monkeypatch.setitem(
        cli.COMMANDS,
        "greet",
        cli.Command(
            lambda args: seen.append(args.name),
            lambda parser: parser.add_argument("--name", required=True),
        ),
    )
    assert cli.main(["greet", "--name", "ada"]) == 0
    assert seen == ["ada"]
    assert cli.main(["greet"]) == 2


@pytest.mark.parametrize(
    "error_class, expected",
    [
        (ValidationFailed, ExitCode.VALIDATION),
        (UsageError, ExitCode.USAGE),
        (AnchorError, ExitCode.ANCHOR),
        (EnvironmentError_, ExitCode.ENVIRONMENT),
    ],
)
def test_each_error_carries_its_own_exit_code(error_class, expected):
    error = error_class("something broke", fix="merit doctor")
    assert isinstance(error, MeritError)
    assert error.exit_code == expected
    assert error.fix == "merit doctor"
    assert "something broke" in str(error)


def test_unknown_command_exits_2(capsys):
    assert cli.main(["no-such-command"]) == 2
    assert "no-such-command" in capsys.readouterr().err


def test_no_command_exits_2(capsys):
    assert cli.main([]) == 2
    assert capsys.readouterr().err


def test_help_exits_0(capsys):
    assert cli.main(["--help"]) == 0
    assert "merit" in capsys.readouterr().out


def test_registered_command_runs_and_returns_its_status(monkeypatch):
    seen = []
    monkeypatch.setitem(cli.COMMANDS, "ok", cli.Command(lambda args: seen.append(args.command)))
    assert cli.main(["ok"]) == 0
    assert seen == ["ok"]


@pytest.mark.parametrize(
    "error_class, expected",
    [
        (ValidationFailed, 1),
        (UsageError, 2),
        (AnchorError, 3),
        (EnvironmentError_, 4),
    ],
)
def test_raised_merit_error_maps_to_its_exit_code(monkeypatch, error_class, expected):
    def boom(args):
        raise error_class("it broke", fix="merit do-the-thing")

    monkeypatch.setitem(cli.COMMANDS, "boom", cli.Command(boom))
    assert cli.main(["boom"]) == expected


def test_stderr_carries_the_fix_string_verbatim(monkeypatch, capsys):
    fix = "merit apply proposals/2026-10-18-fix.yaml --dry-run"

    def boom(args):
        raise ValidationFailed("V3 violated", fix=fix)

    monkeypatch.setitem(cli.COMMANDS, "boom", cli.Command(boom))
    assert cli.main(["boom"]) == 1
    captured = capsys.readouterr()
    assert fix in captured.err
    assert "V3 violated" in captured.err
    assert captured.out == ""
