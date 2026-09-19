"""Command line for ``python -m issue_check`` — the pre-work issue gate.

``--body-file PATH`` reads an issue body (in CI: what ``gh issue view`` wrote to disk)
and validates its structure with :func:`issue_check.checker.check`. Exit 0 means the
issue may be worked on; exit 1 names the missing or unrunnable part; exit 2 is a usage
error. The check is deliberately falsifiable — see ``tools/fixtures/issue_check``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from issue_check.checker import check
from issue_check.verdict import Exit, IssueCheckError

__all__ = ["DESCRIPTION", "Invocation", "main", "parse_args"]

DESCRIPTION: Final[str] = (
    "Validate the structure of a GitHub issue body: it must carry an "
    "'Acceptance criteria' section whose numbered items each name a backticked, "
    "runnable command."
)
_PROG: Final[str] = "python -m issue_check"
_USAGE: Final[str] = "python -m issue_check --body-file PATH"


@dataclass(frozen=True, slots=True, kw_only=True)
class Invocation:
    """One parsed command line."""

    body_file: Path


class _Parser(argparse.ArgumentParser):
    """argparse that raises :class:`IssueCheckError` (2) instead of exiting."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise IssueCheckError(
            Exit.USAGE,
            f"{self.format_usage().rstrip()}\n{self.prog}: error: {message}",
            verbatim=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=_PROG,
        usage=_USAGE,
        description=DESCRIPTION,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--body-file",
        type=Path,
        required=True,
        metavar="PATH",
        help="file holding the raw issue body (markdown), e.g. what "
        "`gh issue view N --json body -q .body` wrote.",
    )
    return parser


def parse_args(argv: Sequence[str]) -> Invocation:
    """Parse ``argv`` into an :class:`Invocation`."""
    args = _parser().parse_args(list(argv))
    return Invocation(body_file=args.body_file)


def _read(path: Path) -> str:
    """Read ``path`` as UTF-8, mapping I/O problems to :attr:`Exit.USAGE`."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IssueCheckError(
            Exit.USAGE, f"--body-file {path}: {exc.strerror or exc}"
        ) from exc


def _print_error(exc: IssueCheckError) -> None:
    """Report ``exc`` on stderr, verbatim for argparse-shaped usage errors."""
    if exc.verbatim:
        print(str(exc), file=sys.stderr)
    else:
        print(
            f"issue_check: error ({exc.code.name.lower()}, "
            f"exit {int(exc.code)}): {exc}",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse -> read -> check; returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        inv = parse_args(argv)
        section = check(_read(inv.body_file), origin=str(inv.body_file))
    except IssueCheckError as exc:
        _print_error(exc)
        return int(exc.code)
    except SystemExit as exc:  # argparse --help / --version
        match exc.code:
            case None | False:
                return int(Exit.OK)
            case True:
                return 1
            case code:
                return int(code)
    count = len(section.criteria())
    print(
        f"issue_check: ok — {inv.body_file}: section {section.heading!r} with "
        f"{count} command-verifiable criterion(s)"
    )
    return int(Exit.OK)
