"""A human carries a browser reply into the same proposal transaction."""

import copy
import importlib
import re
import shutil
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

import merit.apply
from merit.apply import transaction
from merit.baseline import governed_tree_hash
from merit.cli import main
from merit.errors import UsageError
from merit.model import ARTEFACT_DIRS, load_repo
from merit.scaffold import init
from merit.validate.runner import run_checks
from test_apply import requirement, snapshot
from test_checks_issues import project


def bundle(root, capsys, stage="draft"):
    assert main(["pack", str(root), "--stage", stage]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    return captured.out


@pytest.mark.parametrize("stage", ["intake", "draft", "review", "clarify", "baseline"])
def test_pack_is_one_read_only_document_with_exact_packaged_contract(project, capsys, stage):
    root = project[0]
    before = snapshot(root)
    text = bundle(root, capsys, stage)
    assert text.startswith("# MERIT browser bundle\n")
    assert text.count("# MERIT browser bundle\n") == 1
    playbook = next(p for p in files("merit").joinpath("prompts").iterdir()
                    if p.name.endswith(f"-{stage}.md"))
    assert playbook.read_text(encoding="utf-8") in text
    schema = files("merit").joinpath("schemas/proposal.schema.json").read_text(encoding="utf-8")
    assert schema in text
    assert "merit apply proposals/browser-reply.yaml" in text
    assert "without Markdown fences" in text
    assert "untrusted data" in text
    for path in sorted((root / "sources").glob("*")):
        if path.is_file():
            assert path.relative_to(root).as_posix() in text
            assert path.read_text(encoding="utf-8") in text
    assert (root / "requirements/REQ-SMP-0001.yaml").read_text(encoding="utf-8") in text
    assert snapshot(root) == before
    assert text == bundle(root, capsys, stage)
    pack = importlib.import_module("merit.pack").pack
    assert pack(root, stage) == text


def test_pack_supports_numbered_stage_and_invocation_below_root(project, monkeypatch, capsys):
    root = project[0]
    expected = bundle(root, capsys)
    assert bundle(root, capsys, "02-draft") == expected
    monkeypatch.chdir(root / "requirements")
    assert main(["pack", "--stage", "draft"]) == 0
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("stage", ["unknown", "../02-draft", "../../cli.py"])
def test_unknown_stage_has_no_partial_bundle(project, capsys, stage):
    root = project[0]
    before = snapshot(root)
    assert main(["pack", str(root), "--stage", stage]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "fix: merit pack --stage draft" in captured.err
    assert snapshot(root) == before


def test_stage_is_required(capsys):
    assert main(["pack"]) == 2
    assert "--stage" in capsys.readouterr().err


def test_empty_project_has_explicit_source_context(tmp_path, capsys):
    root = tmp_path / "empty"
    init(root, project_id="sample", id_prefix="SMP")
    text = bundle(root, capsys)
    assert "No canonical sources have been ingested." in text
    assert (root / "merit.toml").read_text(encoding="utf-8") in text


def test_source_fences_contain_backticks_and_private_scratch_is_excluded(project, capsys):
    root = project[0]
    source = next((root / "sources").glob("*.md"))
    text = "[0001] Jordan: ```\n[0002] Jordan: ``````\n[0003] Jordan: " + chr(0x2019) + "\n"
    source.write_text(text, encoding="utf-8")
    (root / "proposals/private.yaml").write_text("PRIVATE_SCRATCH", encoding="utf-8")
    (root / "sources/raw/private.txt").write_text("RAW_ORIGINAL_SENTINEL", encoding="utf-8")
    output = bundle(root, capsys)
    assert f"```````text\n{text}```````\n" in output
    assert "PRIVATE_SCRATCH" not in output and "RAW_ORIGINAL_SENTINEL" not in output


def test_unreadable_source_is_environment_error_without_partial_output(project, capsys, monkeypatch):
    root = project[0]
    source = next((root / "sources").glob("*.md"))
    real_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == source:
            raise OSError("test read failure")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert main(["pack", str(root), "--stage", "draft"]) == 4
    captured = capsys.readouterr()
    assert not captured.out and "test read failure" in captured.err
    assert "Traceback" not in captured.err


def governed_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for dirname in ("sources", *ARTEFACT_DIRS.values())
            for path in (root / dirname).rglob("*") if path.is_file()}


def test_browser_reply_and_agent_proposal_use_same_parser_transaction_and_hashes(
        project, tmp_path, capsys, monkeypatch):
    browser = project[0]
    agent = tmp_path / "agent"
    shutil.copytree(browser, agent)
    output = bundle(browser, capsys)
    assert "merit apply proposals/browser-reply.yaml" in output
    operations = [requirement(project), dict(op="add_candidate", statement="Dark mode",
                                             rationale="Explore with users")]
    reply = yaml.safe_dump(dict(schema_version=1, operations=operations))
    parser_calls, transaction_calls = [], []
    real_parser, real_apply = transaction.parse_proposal, merit.apply.apply_proposal

    def parse(path):
        parser_calls.append(path)
        return real_parser(path)

    def apply(root, path):
        transaction_calls.append((root, path))
        return real_apply(root, path)

    monkeypatch.setattr(transaction, "parse_proposal", parse)
    monkeypatch.setattr(merit.apply, "apply_proposal", apply)
    for root, name in ((agent, "agent.yaml"), (browser, "browser-reply.yaml")):
        path = root / "proposals" / name
        path.write_text(reply, encoding="utf-8")
        assert main(["apply", str(path)]) == 0
        assert not path.exists()
    assert len(parser_calls) == len(transaction_calls) == 2
    assert parser_calls == [entry[1] for entry in transaction_calls]
    assert [entry[0] for entry in transaction_calls] == [agent, browser]
    assert governed_bytes(agent) == governed_bytes(browser)
    assert governed_tree_hash(agent, exclude="") == governed_tree_hash(browser, exclude="")
    req = load_repo(browser).requirements["REQ-SMP-0002"]
    assert req.evidence[0].line and req.evidence[0].speaker == "Jordan"
    assert re.fullmatch("[0-9a-f]{64}", req.evidence[0].bundle_sha256)
    assert run_checks(agent) == run_checks(browser) == []


def test_browser_bad_quote_rolls_back_prior_operation_and_does_not_consume_ids(project, capsys):
    root = project[0]
    bundle(root, capsys)
    good = requirement(project)
    bad = copy.deepcopy(good)
    bad["evidence"][0]["quote"] = "This quote was invented."
    path = root / "proposals/browser-reply.yaml"
    path.write_text(yaml.safe_dump(dict(schema_version=1, operations=[good, bad])), encoding="utf-8")
    before = snapshot(root)
    digest = governed_tree_hash(root, exclude="")
    assert main(["apply", str(path)]) == 3
    captured = capsys.readouterr()
    assert "operation index 1" in captured.err and "Traceback" not in captured.err
    assert snapshot(root) == before and governed_tree_hash(root, exclude="") == digest
    path.write_text(yaml.safe_dump(dict(schema_version=1, operations=[good])), encoding="utf-8")
    assert main(["apply", str(path)]) == 0
    assert "REQ-SMP-0002" in load_repo(root).requirements
    assert "REQ-SMP-0003" not in load_repo(root).requirements


@pytest.mark.parametrize("reply", ["schema_version: [", "```yaml\nschema_version: 1\n```", "null"])
def test_malformed_browser_reply_is_usage_error_and_keeps_proposal(project, capsys, reply):
    root = project[0]
    bundle(root, capsys)
    path = root / "proposals/browser-reply.yaml"
    path.write_text(reply, encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(UsageError):
        transaction.parse_proposal(path)
    assert main(["apply", str(path)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "Traceback" not in captured.err
    assert "fix:" in captured.err and "merit apply" in captured.err
    assert snapshot(root) == before
