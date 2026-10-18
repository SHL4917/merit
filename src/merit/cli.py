"""The argparse tree: the single source of truth for the command surface."""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from merit import doctor as doctor_module
from merit.config import find_repo_root
from merit.errors import ExitCode, MeritError
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


def _init_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=".", help="directory to scaffold (default: .)")
    parser.add_argument("--project-id", required=True, help="lowercase project id, e.g. sample")
    parser.add_argument("--id-prefix", required=True, help="2-6 uppercase letters, e.g. SMP")


def _doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="inside the repo (default: .)")


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
