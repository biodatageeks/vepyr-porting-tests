"""Exit codes and the CLI error type for ``./run_tests``.

Cache fetch uses :attr:`Exit.REVISION`, :attr:`Exit.INCOMPLETE` and
:attr:`Exit.VERIFY`; data-test runs use :attr:`Exit.TESTS_FAILED`; engine
resolve/checkout uses :attr:`Exit.ENGINE`; the cache freshness guard (issue #236)
uses :attr:`Exit.STALE_CACHE` when a selected flavour's pin is not the newest Hub
commit of its ``ref``, the Hub HEAD cannot be resolved, or a cache used as-is has no
``PROVENANCE.json`` record for it, unless ``--old-vepyr-cache`` consents.
``--via-cli`` (#231) exits :attr:`Exit.MISMATCH` when every selected run was
compared and at least one body md5 differed from ``[compare] body_md5`` (one
``MISMATCH <dir-name> expected=<md5> actual=<md5>`` line on stdout per such
directory); any other error wins over it with its own code.
"""

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
    """``--via-cli``: all runs compared, at least one body md5 mismatched (#231)."""


class RunTestsError(Exception):
    """A failure with a definite :class:`Exit` code and a one-line message.

    ``verbatim`` marks a message the CLI prints exactly as it is, without the
    ``run_tests: error (…)`` prefix.
    """

    def __init__(self, code: Exit, message: str, *, verbatim: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.verbatim = verbatim
