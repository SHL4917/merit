"""Exit codes and the error hierarchy that maps onto them.

Exit codes are a contract: 0 success, 1 validation failure, 2 usage error,
3 anchor failure, 4 environment error.
"""

import enum


class ExitCode(enum.IntEnum):
    SUCCESS = 0
    VALIDATION = 1
    USAGE = 2
    ANCHOR = 3
    ENVIRONMENT = 4


class MeritError(Exception):
    """A failure that names what is wrong and the exact command that fixes it."""

    exit_code: ExitCode = ExitCode.VALIDATION

    def __init__(self, message: str, *, fix: str) -> None:
        super().__init__(message)
        self.fix = fix


class ValidationFailed(MeritError):
    exit_code = ExitCode.VALIDATION


class UsageError(MeritError):
    exit_code = ExitCode.USAGE


class AnchorError(MeritError):
    exit_code = ExitCode.ANCHOR


class EnvironmentError_(MeritError):
    exit_code = ExitCode.ENVIRONMENT
