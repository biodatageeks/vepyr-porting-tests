"""Command line for ``./run_tests`` — argparse shell; fetch and runs land in #4."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "DEFAULT_FLAVOURS",
    "DEFERRED_MESSAGE",
    "DESCRIPTION",
    "LIST_MESSAGE",
    "Invocation",
    "main",
    "parse_args",
]

DEFAULT_FLAVOURS: Final[str] = "ensembl,refseq,merged"
DESCRIPTION: Final[str] = (
    "Entry point for curated data-problem porting tests. "
    "Cache fetch and test runs are not implemented yet (see issue #4)."
)
DEFERRED_MESSAGE: Final[str] = (
    "run_tests: cache fetch and data-problem runs are not implemented yet "
    "(see issue #4)"
)
LIST_MESSAGE: Final[str] = (
    "set   target\n"
    "(none)\n"
    "run_tests: 0 data-problem targets"
)
_USAGE: Final[str] = "./run_tests [options]"


@dataclass(frozen=True, slots=True, kw_only=True)
class Invocation:
    """One parsed command line (exclusions already enforced)."""

    cache_dir: Path | None
    add_contigs: tuple[str, ...] | None
    flavours: tuple[str, ...]
    vepyr: str | None
    list_only: bool


class _Parser(argparse.ArgumentParser):
    """argparse that raises :class:`RunTestsError` (2) instead of exiting."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise RunTestsError(
            Exit.USAGE,
            f"{self.format_usage().rstrip()}\n{self.prog}: error: {message}",
            verbatim=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="./run_tests",
        usage=_USAGE,
        description=DESCRIPTION,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    data = parser.add_argument_group("Data")
    data.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="cache directory for the VEP 116 corpus (download lands in issue #4).",
    )
    data.add_argument(
        "--add-contigs",
        default=None,
        metavar="LIST",
        help="contig selection for a later cache fetch into --cache-dir "
        "(e.g. chr21,chrMT). Parsed now; fetch accumulates shards in #4.",
    )
    data.add_argument(
        "--flavours",
        default=DEFAULT_FLAVOURS,
        metavar="LIST",
        help=f"cache flavours to select later (default: {DEFAULT_FLAVOURS}).",
    )
    engine = parser.add_argument_group("Engine")
    engine.add_argument(
        "--vepyr",
        default=None,
        metavar="REF",
        help="vepyr ref under test (branch, tag, version, or commit). "
        "Parsed now; engine checkout lands later.",
    )
    run = parser.add_argument_group("Run")
    run.add_argument(
        "--list",
        action="store_true",
        help="print data-problem targets (currently none) and exit 0.",
    )
    return parser


def parse_args(argv: Sequence[str]) -> Invocation:
    """Parse and validate; empty ``--add-contigs`` / ``--flavours`` are usage errors."""
    parser = _parser()
    args = parser.parse_args(list(argv))
    flavours = tuple(part for part in args.flavours.split(",") if part)
    if not flavours:
        raise RunTestsError(Exit.USAGE, "--flavours given but empty")
    add_contigs: tuple[str, ...] | None
    if args.add_contigs is None:
        add_contigs = None
    else:
        add_contigs = tuple(c for c in args.add_contigs.split(",") if c)
        if not add_contigs:
            raise RunTestsError(Exit.USAGE, "--add-contigs given but empty")
    return Invocation(
        cache_dir=args.cache_dir,
        add_contigs=add_contigs,
        flavours=flavours,
        vepyr=args.vepyr,
        list_only=args.list,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        inv = parse_args(argv)
    except SystemExit as exc:
        # argparse --help / --version exit via SystemExit; turn that into a return code.
        code = exc.code
        if code is None or code is False:
            return int(Exit.OK)
        if code is True:
            return 1
        return int(code)
    except RunTestsError as exc:
        if exc.verbatim:
            print(str(exc), file=sys.stderr)
        else:
            print(f"run_tests: error (usage, exit 2): {exc}", file=sys.stderr)
        return int(Exit.USAGE)
    if inv.list_only:
        print(LIST_MESSAGE)
        return int(Exit.OK)
    print(DEFERRED_MESSAGE, file=sys.stderr)
    return int(Exit.USAGE)
