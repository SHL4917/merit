"""The argparse tree: the single source of truth for the command surface."""

import argparse
import datetime
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from subprocess import list2cmdline

from merit import doctor as doctor_module
from merit.config import find_repo_root
from merit.errors import ExitCode, MeritError, UsageError
from merit.ingest import ingest as ingest_source
from merit.ingest.metadata import inspect_metadata
from merit.scaffold import init


def _init(args: argparse.Namespace) -> int | None:
    """Scaffold a new requirements repo."""
    if args.github:
        from merit.git_ops import init_github
        if not args.workflow_repo or not args.wheel_url:
            raise UsageError("--github requires --workflow-repo and --wheel-url.",
                             fix="merit init --github --help")
        for finding in init_github(Path(args.target), project_id=args.project_id, id_prefix=args.id_prefix,
                                   workflow_repo=args.workflow_repo, wheel_url=args.wheel_url,
                                   github_repo=args.github_repo):
            print(finding)
    else:
        init(Path(args.target), project_id=args.project_id, id_prefix=args.id_prefix)
    print(f"Scaffolded a MERIT requirements repo at {Path(args.target).resolve()}")
    return None


def _doctor(args: argparse.Namespace) -> int | None:
    """Check the environment, version pin, git and gh, and repo validity."""
    findings = doctor_module.doctor(find_repo_root(Path(args.path)), workflow_repo=args.workflow_repo)
    for finding in findings:
        print(finding)
    if not findings:
        print("ok: environment and repository are healthy")
    if any(f.startswith(doctor_module.ERROR) for f in findings):
        return int(ExitCode.ENVIRONMENT)
    return None


def _quote(path: Path) -> str:
    return list2cmdline([str(path)])


def _inspect(origin: Path) -> None:
    if not origin.is_file():
        raise UsageError(f"{origin} is not a file.", fix="check the path, then re-run the command")
    report = inspect_metadata(origin)
    if not report:
        print(f"no embedded metadata is reported for {origin.suffix} files")
    for key, value in report.items():
        print(f"{key}: {value if value is not None else '(not recorded)'}")


def _ingest(args: argparse.Namespace) -> int | None:
    """Convert a transcript to canonical form, retain the original, and pin the bundle."""
    origin = Path(args.origin)
    if args.inspect_metadata:
        _inspect(origin)
        return None
    required = (
        ("--record-type", args.record_type),
        ("--authored-by", args.authored_by),
        ("--session-date", args.session_date),
        ("--classification", args.classification),
        ("--ingested-by", args.ingested_by),
    )
    missing = [flag for flag, value in required if not value]
    if missing:
        raise UsageError(
            f"merit ingest needs {', '.join(missing)}.",
            fix=f"merit ingest {_quote(origin)} " + " ".join(f"{flag} <value>" for flag in missing),
        )
    try:
        session_date = datetime.date.fromisoformat(args.session_date)
    except ValueError:
        raise UsageError(
            f"--session-date {args.session_date!r} is not a YYYY-MM-DD date.",
            fix=f"merit ingest {_quote(origin)} --session-date YYYY-MM-DD (same other options)",
        ) from None
    participants = []
    for entry in args.participant:
        name, _, role = entry.partition("=")
        if not name.strip() or not role.strip():
            raise UsageError(
                f"--participant {entry!r} must look like \"Name=role\".",
                fix=f"merit ingest {_quote(origin)} --participant \"Jordan Reyes=user\" (same other options)",
            )
        participants.append({"name": name.strip(), "role": role.strip()})
    result = ingest_source(
        find_repo_root(Path.cwd()),
        origin,
        record_type=args.record_type,
        authored_by=args.authored_by,
        session_date=session_date,
        participants=participants,
        classification=args.classification,
        ingested_by=args.ingested_by,
        as_name=args.as_name,
    )
    if not result.created:
        print(f"already ingested, nothing written: {result.source}")
        return None
    print(f"Ingested {origin.name}")
    print(f"  original:  {result.origin}")
    print(f"  canonical: {result.source}")
    print(f"  sidecar:   {result.meta}")
    print(f"  bundle_sha256: {result.bundle_sha256}")
    return None


def _init_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=".", help="directory to scaffold (default: .)")
    parser.add_argument("--project-id", required=True, help="lowercase project id, e.g. sample")
    parser.add_argument("--id-prefix", required=True, help="2-6 uppercase letters, e.g. SMP")
    parser.add_argument("--github", action="store_true", help="create and push a private GitHub repo and request verified protections")
    parser.add_argument("--github-repo", help="new repository OWNER/REPO (default: authenticated user/project id)")
    parser.add_argument("--workflow-repo", help="trusted MERIT workflow OWNER/REPO; required with --github")
    parser.add_argument("--wheel-url", help="explicit HTTPS release wheel URL; required with --github")


def _doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--workflow-repo", help="trusted MERIT workflow OWNER/REPO for identity verification")


def _anchor(args: argparse.Namespace) -> None:
    """Resolve unanchored quotes and verify existing evidence anchors."""
    from merit.anchor import anchor_file
    path = Path(args.file).resolve()
    anchor_file(path, find_repo_root(path.parent))


def _anchor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", help="governed artefact YAML file")


def _apply(args: argparse.Namespace) -> None:
    """Apply an ungoverned proposal transactionally after complete validation."""
    from merit.apply import apply_proposal
    proposal = Path(args.proposal).resolve()
    result = apply_proposal(find_repo_root(proposal.parent), proposal)
    for path in result.paths:
        print(f"Applied: {path}")
    for warning in result.warnings:
        print(f"warning: {warning}")


def _apply_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("proposal", help="proposal YAML file under proposals/")


def _validate(args: argparse.Namespace) -> int:
    """Validate governed artefacts with deterministic numbered checks."""
    from merit.validate import runner
    findings = runner.run_checks(find_repo_root(Path(args.path)), strict=args.strict)
    for finding in findings:
        print(f"{finding.check_id} {finding.severity}: {finding.path}: {finding.message}")
        print(f"fix: {finding.fix}")
    if not findings:
        print("ok: repository validates")
    return int(any(f.severity == "error" for f in findings))


def _validate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--strict", action="store_true", help="enable strict validation checks")


def _report(args: argparse.Namespace) -> None:
    """Build an offline, read-only HTML approval report."""
    from merit.report import build_report
    output = build_report(find_repo_root(Path(args.path)), args.output)
    print(f"Report: {output.resolve()}")


def _report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--output", help="HTML output path (default: public/index.html in the repo)")


def _questions(args: argparse.Namespace) -> None:
    """List open and awaiting_confirmation issues with quotes and drafted questions."""
    from merit.questions import open_questions
    questions = open_questions(find_repo_root(Path(args.path)))
    if not questions:
        print("No open questions.")
    for item in questions:
        print(f"{item['id']}: {item['status']} - {item['summary']}")
        for evidence in item["evidence"]:
            print(f"  {evidence['speaker']} ({evidence['source']}:{evidence['line']}): {evidence['quote']}")
        print(f"  Question: {item['question']}")


def _questions_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--open", action="store_true", help="include open and awaiting_confirmation issues (default)")


def _baseline(args: argparse.Namespace) -> int:
    """Check baseline readiness or prepare its manifest and human tag handoff."""
    from merit.baseline import baseline
    result = baseline(find_repo_root(Path(args.path)), dry_run=args.dry_run, prepared_by=args.prepared_by)
    print(f"ready: {len(result.ready)}; blocked: {len(result.blocked)}; deferred: {len(result.deferred)}")
    for reason in result.reasons:
        print(reason)
    for finding in result.findings:
        print(f"{finding.check_id} {finding.severity}: {finding.message}")
        print(f"fix: {finding.fix}")
    if result.manifest:
        print(f"Prepared baselines/{result.manifest.id}.yaml")
        print(result.handoff)
    elif not result.can_prepare:
        print("Baseline not ready. fix: merit validate")
    return int(not result.can_prepare)


def _baseline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--dry-run", action="store_true", help="report readiness and write nothing")
    parser.add_argument("--prepared-by", help="preparer's name (default: current user)")


def _start_draft(args: argparse.Namespace) -> None:
    """Create and check out a working draft branch."""
    from merit.git_ops import start_draft
    print(start_draft(find_repo_root(Path.cwd()), args.branch))


def _start_draft_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("branch", help="new draft branch name")


def _save(args: argparse.Namespace) -> None:
    """Save a checkpoint containing only the named files."""
    from merit.git_ops import save
    # CLI paths are relative to the invocation directory, even below repo root.
    paths = [str(Path(path).absolute()) for path in args.paths]
    print(save(find_repo_root(Path.cwd()), paths, args.message))


def _save_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", help="individual files to commit")
    parser.add_argument("-m", "--message", required=True, help="checkpoint message")


def _push(args: argparse.Namespace) -> None:
    """Push the current branch for review and set its upstream."""
    from merit.git_ops import push
    print(push(find_repo_root(Path.cwd()), args.remote))


def _push_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--remote", default="origin", help="remote name (default: origin)")


def _open_pr(args: argparse.Namespace) -> None:
    """Open a pull request with a generated repository summary."""
    from merit.git_ops import open_pr
    print(open_pr(find_repo_root(Path.cwd()), args.title, base=args.base))


def _open_pr_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", required=True, help="pull request title")
    parser.add_argument("--base", help="target branch (default: repository default)")


def _pr_body(args: argparse.Namespace) -> None:
    """Generate a pull request description from repository state."""
    from merit.git_ops import pr_body
    print(pr_body(find_repo_root(Path(args.path))), end="")


def _pr_body_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")


def _ingest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("origin", help="the .vtt, .docx or .txt file to ingest")
    parser.add_argument(
        "--inspect-metadata",
        action="store_true",
        help="report metadata embedded in the original, write nothing, and stop",
    )
    parser.add_argument("--record-type", help="transcript | minutes | email | self_recorded_note")
    parser.add_argument("--authored-by", help="who produced this record, not who speaks in it")
    parser.add_argument("--session-date", help="when the session took place, YYYY-MM-DD")
    parser.add_argument(
        "--participant",
        action="append",
        default=[],
        metavar="NAME=ROLE",
        help='a participant, e.g. "Jordan Reyes=user"; repeat for each',
    )
    parser.add_argument("--classification", help="one of the levels allowed in merit.toml")
    parser.add_argument("--ingested-by", help="who is ingesting this file")
    parser.add_argument(
        "--as",
        dest="as_name",
        metavar="NAME",
        help="store under this name (no extension) instead of the file's own, to resolve a collision",
    )


def command_catalogue(parser: argparse.ArgumentParser) -> list[dict]:
    """Describe every parser, including nested subcommands, from runtime actions."""
    catalogue = []

    def visit(current: argparse.ArgumentParser, name: str) -> None:
        args = []
        flags = []
        example = [name]
        children = []
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                children.extend(action.choices.items())
            elif action.option_strings:
                flags.extend(action.option_strings)
                if action.required:
                    example.append(action.option_strings[0])
                    if action.nargs != 0:
                        example.append(f"<{action.dest}>")
            else:
                args.append(action.dest)
                if action.nargs not in ("?", "*"):
                    example.append(f"<{action.dest}>")
        catalogue.append({
            "name": name,
            "args": args,
            "flags": flags,
            "exit_codes": {str(int(code)): code.name.lower() for code in ExitCode},
            "example": " ".join(example),
        })
        for child_name, child in children:
            visit(child, f"{name} {child_name}")

    visit(parser, parser.prog)
    return catalogue


def _help_agent(args: argparse.Namespace) -> None:
    """Print the agent operating manual and runtime command catalogue."""
    catalogue = command_catalogue(build_parser())
    if args.json:
        print(json.dumps(catalogue, indent=2))
        return
    print("Read this manual at the start of every session; use merit prompt <stage> next.")
    print("merit apply is the only write path for proposed governed changes: write a proposal")
    print("under proposals/ and run merit apply. Never hand-edit governed artefacts.")
    print("merit ingest is the narrow create-only exception for source bundles.")
    print("merit baseline computes its manifest through the same staged validation and rollback.")
    print("Copy quotes verbatim; never paraphrase a quote. If anchoring fails, downgrade")
    print("the claim to inferred with an explicit rationale; unsupported ideas are candidates.")
    print("The tool never merges or tags; human approval owns baseline handoff.")
    print("Exit codes: 0 success; 1 validation failure; 2 usage error; 3 anchor failure; 4 environment error.")
    for command in catalogue:
        print(f"\n{command['name']}")
        print("  args: " + ", ".join(command["args"]))
        print("  flags: " + ", ".join(command["flags"]))
        print("  exit_codes: " + json.dumps(command["exit_codes"]))
        print("  example: " + command["example"])


def _help_agent_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit the command catalogue as JSON")


def _resource(name: str, directory: str, suffix: str, command: str) -> str:
    # Compare resource names, never interpret user input as a resource path.
    resources = {item.name.removesuffix(suffix): item
                 for item in files("merit").joinpath(directory).iterdir()
                 if item.name.endswith(suffix)}
    if name not in resources:
        raise UsageError(f"Unknown {command}: {name!r}. Choose from {', '.join(sorted(resources))}.",
                         fix=f"merit {command} {sorted(resources)[0]}")
    return resources[name].read_text(encoding="utf-8")


def _prompt(args: argparse.Namespace) -> None:
    """Print a packaged stage playbook (intake, draft, review, clarify, baseline)."""
    stages = {item.name[3:-3]: item.name[:-3]
              for item in files("merit").joinpath("prompts").iterdir()
              if item.name.endswith(".md")}
    stage = stages.get(args.stage, args.stage)
    print(_resource(stage, "prompts", ".md", "prompt"), end="")


def _prompt_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("stage", help="intake, draft, review, clarify, baseline (or numbered resource name)")


def _schema(args: argparse.Namespace) -> None:
    """Print a packaged artefact JSON Schema."""
    print(_resource(args.artefact, "schemas", ".schema.json", "schema"), end="")


def _schema_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("artefact", help="artefact name, for example proposal or requirement")


def _pack(args: argparse.Namespace) -> None:
    """Print one Markdown bundle for a browser chat; apply replies with merit apply."""
    from merit.pack import pack
    print(pack(find_repo_root(Path(args.path)), args.stage), end="")


def _pack_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")
    parser.add_argument("--stage", required=True,
                        help="intake, draft, review, clarify, baseline (or numbered resource name)")


@dataclass(frozen=True)
class Command:
    """One CLI command: its handler and how it adds its own arguments."""

    # Takes the parsed Namespace and returns an exit status (None means success).
    handler: Callable[[argparse.Namespace], int | None]
    # Adds this command's arguments to its subparser; None for a bare command.
    configure: Callable[[argparse.ArgumentParser], None] | None = None


# Command name -> Command. Later tasks register their commands here, read at
# call time by build_parser and main.
COMMANDS: dict[str, Command] = {
    "init": Command(_init, _init_arguments),
    "doctor": Command(_doctor, _doctor_arguments),
    "ingest": Command(_ingest, _ingest_arguments),
    "validate": Command(_validate, _validate_arguments),
    "report": Command(_report, _report_arguments),
    "questions": Command(_questions, _questions_arguments),
    "baseline": Command(_baseline, _baseline_arguments),
    "start-draft": Command(_start_draft, _start_draft_arguments),
    "save": Command(_save, _save_arguments),
    "push": Command(_push, _push_arguments),
    "open-pr": Command(_open_pr, _open_pr_arguments),
    "pr-body": Command(_pr_body, _pr_body_arguments),
    "anchor": Command(_anchor, _anchor_arguments),
    "apply": Command(_apply, _apply_arguments),
    "help-agent": Command(_help_agent, _help_agent_arguments),
    "prompt": Command(_prompt, _prompt_arguments),
    "schema": Command(_schema, _schema_arguments),
    "pack": Command(_pack, _pack_arguments),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merit",
        description="Turn meeting transcripts into a traceable requirements baseline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, command in COMMANDS.items():
        doc = command.handler.__doc__
        subparser = subparsers.add_parser(name, help=doc, description=doc)
        if command.configure is not None:
            command.configure(subparser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help and 2 for a usage error.
        return int(ExitCode.SUCCESS if exc.code in (None, 0) else ExitCode.USAGE)
    try:
        status = COMMANDS[args.command].handler(args)
    except MeritError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"fix: {exc.fix}", file=sys.stderr)
        return int(exc.exit_code)
    return int(ExitCode.SUCCESS if status is None else status)


if __name__ == "__main__":
    raise SystemExit(main())
