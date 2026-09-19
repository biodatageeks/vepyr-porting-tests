"""Exit codes and the error type for ``python -m issue_check``.

A structurally invalid issue body is :attr:`Exit.INVALID`; a broken command line or
an unreadable ``--body-file`` is :attr:`Exit.USAGE`. The two are kept apart so CI can
tell "the issue is not compliant" from "the check was invoked wrong".
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["Exit", "IssueCheckError"]


class Exit(IntEnum):
    """One code per class of outcome."""

    OK = 0
    INVALID = 1
    USAGE = 2


class IssueCheckError(Exception):
    """A failure with a definite :class:`Exit` code and a one-line message.

    ``verbatim`` marks a message the CLI prints exactly as it is, without the
    ``issue_check: error (…)`` prefix (argparse-shaped usage errors).
    """

    def __init__(self, code: Exit, message: str, *, verbatim: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.verbatim = verbatim
