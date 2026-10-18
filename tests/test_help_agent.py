"""The agent manual follows the actual parser, including future extensions."""

import argparse
import json
from importlib.resources import files

import pytest

from merit import cli
from merit.errors import UsageError


def parser_surface(parser, prefix="merit"):
    """Independent recursive inventory used as the completeness oracle."""
    yield prefix, None
    for action in parser._actions:
        for flag in action.option_strings:
            yield prefix, flag
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from parser_surface(child, prefix + " " + name)
        elif not action.option_strings:
            yield prefix, action.dest


def test_manual_covers_runtime_tree_and_future_nested_commands(monkeypatch, capsys):
    def arguments(parser):
        parser.add_argument("--future-root", action="store_true")
        children = parser.add_subparsers()
        child = children.add_parser("nested", aliases=["alias"])
        child.add_argument("payload")
        child.add_argument("-z", "--future-flag")

    monkeypatch.setitem(cli.COMMANDS, "future", cli.Command(lambda args: None, arguments))
    assert cli.main(["help-agent"]) == 0
    manual = capsys.readouterr().out
    assert cli.main(["help-agent", "--json"]) == 0
    catalogue = json.loads(capsys.readouterr().out)
    by_name = {entry["name"]: entry for entry in catalogue}
    for name, token in parser_surface(cli.build_parser()):
        assert name in manual
        assert name in by_name
        if token:
            assert token in manual
            assert token in by_name[name]["args"] + by_name[name]["flags"]
    for entry in catalogue:
        assert set(entry) == {"name", "args", "flags", "exit_codes", "example"}
        assert set(entry["exit_codes"]) == {"0", "1", "2", "3", "4"}
        assert entry["example"].startswith(entry["name"])
    for phrase in ("0 success", "1 validation failure", "2 usage error", "3 anchor failure", "4 environment error"):
        assert phrase in manual
    assert "merit apply is the only write path" in manual
    assert "create-only exception" in manual


@pytest.mark.parametrize("stage", ["intake", "draft", "review", "clarify", "baseline"])
def test_playbooks_are_packaged_and_preserve_write_and_evidence_rules(stage, capsys):
    assert cli.main(["prompt", stage]) == 0
    text = capsys.readouterr().out
    for phrase in ("proposal", "proposals/", "merit apply", "only write path", "verbatim",
                   "never paraphrase", "inferred", "rationale", "candidates"):
        assert phrase in text
    packaged = [item for item in files("merit").joinpath("prompts").iterdir()
                if item.name.endswith(f"-{stage}.md")]
    assert len(packaged) == 1
    assert text == packaged[0].read_text(encoding="utf-8")


def test_schema_proposal_is_packaged_json(capsys):
    assert cli.main(["schema", "proposal"]) == 0
    schema = json.loads(capsys.readouterr().out)
    resource = files("merit").joinpath("schemas", "proposal.schema.json")
    assert schema == json.loads(resource.read_text(encoding="utf-8"))
    assert "operations" in schema["properties"]


@pytest.mark.parametrize("command", ["prompt", "schema"])
@pytest.mark.parametrize("name", ["unknown", "../proposal", "../../cli.py"])
def test_unknown_resource_is_usage_error(command, name, capsys):
    assert cli.main([command, name]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "Unknown" in captured.err and f"fix: merit {command}" in captured.err
    handler = cli.COMMANDS[command].handler
    with pytest.raises(UsageError):
        handler(argparse.Namespace(stage=name, artefact=name))
