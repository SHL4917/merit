"""The argparse tree: the single source of truth for the command surface."""

import argparse
import datetime
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
    init(Path(args.target), project_id=args.project_id, id_prefix=args.id_prefix)
    print(f"Scaffolded a MERIT requirements repo at {Path(args.target).resolve()}")
    return None


def _doctor(args: argparse.Namespace) -> int | None:
    """Check the environment, version pin, git and gh, and repo validity."""
    findings = doctor_module.doctor(find_repo_root(Path(args.path)))
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


def _doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")


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
    "anchor": Command(_anchor, _anchor_arguments),
    "apply": Command(_apply, _apply_arguments),
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
