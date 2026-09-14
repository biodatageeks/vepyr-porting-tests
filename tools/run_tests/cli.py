"""Command line for ``./run_tests`` — argparse shell plus the cache-fetch dispatch.

``--cache-dir`` materialises the pinned VEP 116 corpus through :mod:`run_tests.fetch`
and prints the end-of-run summary (:mod:`run_tests.summary`). Test execution and the
``--vepyr`` engine checkout are still deferred to a later slice, so an invocation
without ``--cache-dir`` keeps refusing.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests import fetch, summary
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
    "--cache-dir materialises the pinned VEP 116 corpus; test runs are not "
    "implemented yet."
)
DEFERRED_MESSAGE: Final[str] = (
    "run_tests: data-problem runs are not implemented yet; "
    "give --cache-dir to materialise the corpus, or --list"
)
LIST_MESSAGE: Final[str] = "set   target\n(none)\nrun_tests: 0 data-problem targets"
_USAGE: Final[str] = "./run_tests [options]"
_HF_XET_HIGH_PERFORMANCE: Final[str] = "HF_XET_HIGH_PERFORMANCE"
"""Exported before the first Hub call under ``--fast``: ``huggingface_hub`` reads its
constants at import time and ``hf_xet`` reads the variable when the download imports
it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Invocation:
    """One parsed command line (exclusions already enforced)."""

    cache_dir: Path | None
    add_contigs: tuple[str, ...] | None
    flavours: tuple[str, ...]
    vepyr: str | None
    list_only: bool
    dry_run: bool
    verify: bool
    fast: bool
    trim_manifests: bool


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
        help="cache directory for the VEP 116 corpus; shards are downloaded into it "
        "(the reference FASTA comes along automatically).",
    )
    data.add_argument(
        "--add-contigs",
        default=None,
        metavar="LIST",
        help="contigs to ADD to --cache-dir (e.g. chr21,chrMT). Accumulates: the "
        "directory is never reset to only this list. Default: whole genome.",
    )
    data.add_argument(
        "--flavours",
        default=DEFAULT_FLAVOURS,
        metavar="LIST",
        help=f"cache flavours to fetch (default: {DEFAULT_FLAVOURS}).",
    )
    data.add_argument(
        "--dry-run",
        action="store_true",
        help="list what the selection names on the Hub and write nothing at all.",
    )
    data.add_argument(
        "--verify",
        action="store_true",
        help="check every fetched shard against the Hub's sha256.",
    )
    data.add_argument(
        "--fast",
        action="store_true",
        help=f"set {_HF_XET_HIGH_PERFORMANCE}=1 for the download.",
    )
    data.add_argument(
        "--no-trim-manifests",
        action="store_true",
        help="leave chrom_manifest.json naming shards that were not fetched "
        "(the engine then fails on a shard that is not on disk).",
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
    """Parse and validate; empty ``--add-contigs`` / ``--flavours`` are usage errors.

    Raises:
        RunTestsError: :attr:`Exit.USAGE` for an empty list, or for a contig name
            carrying a glob metacharacter — an unquoted ``chr*`` would reach the Hub
            patterns and turn a one-contig fetch into a whole-genome one.
    """
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
        bad = [c for c in add_contigs if not fetch.CONTIG_NAME.match(c)]
        if bad:
            raise RunTestsError(
                Exit.USAGE,
                f"--add-contigs: {bad} — names are literal (chr21, chrX, ...), no glob "
                "characters; an unquoted chr* would select the whole genome",
            )
    return Invocation(
        cache_dir=args.cache_dir,
        add_contigs=add_contigs,
        flavours=flavours,
        vepyr=args.vepyr,
        list_only=args.list,
        dry_run=args.dry_run,
        verify=args.verify,
        fast=args.fast,
        trim_manifests=fetch.TRIM_DEFAULT and not args.no_trim_manifests,
    )


def _repo_root() -> Path:
    """Repository root (``tools/run_tests/cli.py`` -> repo)."""
    return Path(__file__).resolve().parents[2]


def _selection(inv: Invocation) -> fetch.Selection:
    """Build the fetch selection from a parsed invocation.

    FASTA is not a flag: every real (non-``--dry-run``) fetch takes the pinned reference
    along, because a cache directory without it is not usable by a test run.

    Raises:
        RunTestsError: :attr:`Exit.USAGE` when ``--flavours`` names something that is
            not a published flavour.
    """
    assert inv.cache_dir is not None
    try:
        flavours = tuple(fetch.Flavour(name) for name in inv.flavours)
    except ValueError as exc:
        raise RunTestsError(Exit.USAGE, f"--flavours: {exc}") from exc
    return fetch.Selection(
        root=inv.cache_dir,
        flavours=flavours,
        contigs=inv.add_contigs,
        fasta=not inv.dry_run,
        trim_manifests=inv.trim_manifests,
        fast=inv.fast,
        verify=inv.verify,
        dry_run=inv.dry_run,
    )


def _summary(inv: Invocation, outcome: Exit, detail: str | None) -> str:
    """Render the end-of-run block for an invocation that reached the fetch path."""
    accumulated = (
        summary.accumulated_contigs(inv.cache_dir, inv.flavours)
        if inv.cache_dir is not None
        else {}
    )
    return summary.render(
        summary.RunSummary(
            cache_dir=inv.cache_dir,
            add_contigs=inv.add_contigs,
            flavours=inv.flavours,
            vepyr=inv.vepyr,
            fasta=inv.cache_dir is not None and not inv.dry_run,
            dry_run=inv.dry_run,
            verify=inv.verify,
            fast=inv.fast,
            trim_manifests=inv.trim_manifests,
            outcome=outcome,
            detail=detail,
        ),
        accumulated,
    )


def _run_fetch(
    inv: Invocation,
    argv: Sequence[str],
    *,
    lister: fetch.Lister,
    downloader: fetch.Downloader,
    fasta_fetcher: Callable[[str, Path], None],
) -> int:
    """Materialise the cache, then print the summary and return the exit code.

    Only a :class:`RunTestsError` — a documented refuse with its own code — is turned
    into a message plus a summary block. Anything else propagates as a real traceback:
    an unexpected crash must not be dressed up as a clean outcome.
    """
    pins_toml = _repo_root() / "PINS.toml"
    detail: str | None = None
    try:
        if inv.fast:
            os.environ[_HF_XET_HIGH_PERFORMANCE] = "1"
        selection = _selection(inv)
        pins, fasta_pin = fetch.load_dataset_pins(pins_toml)
        outcome = fetch.fetch(
            selection,
            pins,
            fasta_pin,
            lister=lister,
            downloader=downloader,
            fasta_fetcher=fasta_fetcher,
            argv=argv,
            pins_toml=pins_toml,
            tool=f"tools/run_tests/fetch.py@{fetch.git_sha(_repo_root())}",
        )
        code = outcome.code
        detail = f"{outcome.fetched} shard(s) fetched, {outcome.present} on disk"
    except RunTestsError as exc:
        code = exc.code
        detail = str(exc)
        print(
            f"run_tests: error ({code.name.lower()}, exit {int(code)}): {exc}",
            file=sys.stderr,
        )
    print(_summary(inv, code, detail), end="")
    return int(code)


def main(
    argv: Sequence[str] | None = None,
    *,
    lister: fetch.Lister = fetch.hub_lister,
    downloader: fetch.Downloader = fetch.hub_downloader,
    fasta_fetcher: Callable[[str, Path], None] = fetch.url_fetcher,
) -> int:
    """Entry point: returns the exit code.

    Args:
        argv: Arguments after the program name; defaults to :data:`sys.argv`.
        lister: Hub listing callable; injected by tests so no test touches the network.
        downloader: Hub download callable; likewise.
        fasta_fetcher: Reference-FASTA transfer callable; likewise.

    Returns:
        The process exit code (see :class:`run_tests.verdict.Exit`).
    """
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
        # parse_args only ever raises USAGE; the fetch path does its own reporting.
        if exc.verbatim:
            print(str(exc), file=sys.stderr)
        else:
            print(f"run_tests: error (usage, exit 2): {exc}", file=sys.stderr)
        return int(Exit.USAGE)
    if inv.list_only:
        print(LIST_MESSAGE)
        return int(Exit.OK)
    if inv.cache_dir is None:
        print(DEFERRED_MESSAGE, file=sys.stderr)
        return int(Exit.USAGE)
    return _run_fetch(
        inv,
        argv,
        lister=lister,
        downloader=downloader,
        fasta_fetcher=fasta_fetcher,
    )
