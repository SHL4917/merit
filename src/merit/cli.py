"""The argparse tree: the single source of truth for the command surface."""

import argparse
import sys
from collections.abc import Callable, Sequence

from merit.errors import ExitCode, MeritError

# Command name -> handler taking the parsed Namespace and returning an exit
# status (None means success). Later tasks register their commands here.
COMMANDS: dict[str, Callable[[argparse.Namespace], int | None]] = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merit",
        description="Turn meeting transcripts into a traceable requirements baseline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, handler in COMMANDS.items():
        subparsers.add_parser(name, help=handler.__doc__, description=handler.__doc__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help and 2 for a usage error.
        return int(ExitCode.SUCCESS if exc.code in (None, 0) else ExitCode.USAGE)
    try:
        status = COMMANDS[args.command](args)
    except MeritError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"fix: {exc.fix}", file=sys.stderr)
        return int(exc.exit_code)
    return int(ExitCode.SUCCESS if status is None else status)


if __name__ == "__main__":
    raise SystemExit(main())
