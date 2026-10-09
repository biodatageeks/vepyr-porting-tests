"""Stable exit codes for data comparison, installation and cache preparation."""

from __future__ import annotations

from enum import IntEnum

__all__ = ["Exit", "RunTestsError"]


class Exit(IntEnum):
    """One code per class of outcome (aligned with the eventual fetch/engine shell)."""

    OK = 0
    TESTS_FAILED = 1
    USAGE = 2
    REVISION = 3
    INCOMPLETE = 4
    VERIFY = 5
    ENGINE = 6
    STALE_CACHE = 7
    MISMATCH = 8
    """Data suite: all runs compared, at least one body md5 mismatched (#231)."""


class RunTestsError(Exception):
    """A failure with a definite :class:`Exit` code and a one-line message.

    ``verbatim`` marks a message the CLI prints exactly as it is, without the
    ``run_tests: error (…)`` prefix.
    """

    def __init__(self, code: Exit, message: str, *, verbatim: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.verbatim = verbatim
