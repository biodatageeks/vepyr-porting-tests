"""Command line for ``./run_tests`` — fetch, ``--vepyr``, and data-test runs.

``--cache-dir`` materialises the pinned VEP 116 corpus. With a cache root
(``--cache-dir`` or ``$VEPYR_CACHE_ROOT``) and ``--vepyr REF``, discovered
``tests/data_*.rs`` targets run under a path-patched engine ladder.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests import engine, fetch, summary, tests
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "DEFAULT_FLAVOURS",
    "DESCRIPTION",
    "MISSING_CACHE",
    "MISSING_VEPYR",
    "Invocation",
    "main",
    "parse_args",
]

DEFAULT_FLAVOURS: Final[str] = "ensembl,refseq,merged"
DESCRIPTION: Final[str] = (
    "Entry point for curated data-problem porting tests. "
    "--cache-dir materialises the pinned VEP 116 corpus; "
    "with a cache root and --vepyr REF, discovered data-tests run."
)
MISSING_CACHE: Final[str] = (
    "run_tests: need a cache root: pass --cache-dir DIR or export "
    f"{tests.CACHE_ENV}=DIR (after ./run_tests --cache-dir DIR [--add-contigs LIST])"
)
MISSING_VEPYR: Final[str] = (
    "run_tests: data-test runs require --vepyr REF "
    "(tag, branch, or commit on biodatageeks/vepyr)"
)
_USAGE: Final[str] = "./run_tests [options]"
_HF_XET_HIGH_PERFORMANCE: Final[str] = "HF_XET_HIGH_PERFORMANCE"

CargoRunner = Callable[[Sequence[str], Mapping[str, str]], int]
"""``(argv, env) -> exit code``; injected by tests so no cargo is spawned."""


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
        "(the reference FASTA comes along automatically). Also used as the run root.",
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
    engine_g = parser.add_argument_group("Engine")
    engine_g.add_argument(
        "--vepyr",
        default=None,
        metavar="REF",
        help="vepyr ref under test (branch, tag, or commit on biodatageeks/vepyr). "
        "Required when running data-tests; materialises that revision's dfbf/formats "
        "ladder via cargo path patches.",
    )
    run = parser.add_argument_group("Run")
    run.add_argument(
        "--list",
        action="store_true",
        help="print data-problem targets (tests/data_*.rs) and exit 0.",
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


def _summary(
    inv: Invocation,
    outcome: Exit,
    detail: str | None,
    *,
    cache_dir: Path | None = None,
    targets: Sequence[str] = (),
    vepyr_resolved: str | None = None,
) -> str:
    root = cache_dir if cache_dir is not None else inv.cache_dir
    accumulated = (
        summary.accumulated_contigs(root, inv.flavours) if root is not None else {}
    )
    return summary.render(
        summary.RunSummary(
            cache_dir=root,
            add_contigs=inv.add_contigs,
            flavours=inv.flavours,
            vepyr=inv.vepyr,
            vepyr_resolved=vepyr_resolved,
            targets=tuple(targets),
            fasta=root is not None and (inv.cache_dir is not None and not inv.dry_run),
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
) -> tuple[Exit, str | None]:
    pins_toml = _repo_root() / "PINS.toml"
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
    return (
        outcome.code,
        f"{outcome.fetched} shard(s) fetched, {outcome.present} on disk",
    )


def _default_cargo(argv: Sequence[str], env: Mapping[str, str]) -> int:
    merged = {**os.environ, **dict(env)}
    completed = subprocess.run(list(argv), env=merged, check=False)
    return int(completed.returncode)


def _resolve_cache_root(inv: Invocation) -> Path | None:
    if inv.cache_dir is not None:
        return inv.cache_dir
    raw = os.environ.get(tests.CACHE_ENV)
    if raw:
        return Path(raw)
    return None


def _run_data_tests(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    cargo_runner: CargoRunner,
    gh_api: engine.GhApi | None,
) -> tuple[Exit, str | None, str | None]:
    """Precheck + engine + cargo. Returns ``(code, detail, vepyr_resolved)``."""
    assert inv.vepyr is not None
    repo = _repo_root()
    pins_toml = repo / "PINS.toml"
    tests.precheck_cache(cache_root, pins_toml=pins_toml, flavours=inv.flavours)
    plan, config_path = engine.materialise(
        inv.vepyr, repo_root=repo, api=gh_api
    )
    argv = tests.cargo_argv(targets, config=config_path)
    env = {tests.CACHE_ENV: str(cache_root)}
    with engine.LockGuard(repo).held():
        # Unlock patched packages so path patches apply (cargo keeps locked =version).
        unlock = [
            "cargo",
            "update",
            "--config",
            str(config_path),
            "-p",
            "datafusion-bio-function-vep",
            "-p",
            "datafusion-bio-format-ensembl-cache",
            "-p",
            "datafusion-bio-format-vcf",
        ]
        unlock_code = cargo_runner(unlock, env)
        if unlock_code != 0:
            return (
                Exit.ENGINE,
                f"cargo update for engine patches exited {unlock_code}",
                plan.vepyr_sha,
            )
        code = cargo_runner(argv, env)
    if code == 0:
        return Exit.OK, f"cargo test ok ({len(targets)} target(s))", plan.vepyr_sha
    return (
        Exit.TESTS_FAILED,
        f"cargo test exited {code} ({len(targets)} target(s))",
        plan.vepyr_sha,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    lister: fetch.Lister = fetch.hub_lister,
    downloader: fetch.Downloader = fetch.hub_downloader,
    fasta_fetcher: Callable[[str, Path], None] = fetch.url_fetcher,
    cargo_runner: CargoRunner = _default_cargo,
    gh_api: engine.GhApi | None = None,
) -> int:
    """Entry point: returns the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        inv = parse_args(argv)
    except SystemExit as exc:
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

    repo = _repo_root()
    targets = tests.data_targets(repo)

    if inv.list_only:
        print(tests.list_table(targets), end="")
        return int(Exit.OK)

    # Fetch-only dry-run: no cargo / no engine.
    if inv.cache_dir is not None and inv.dry_run:
        try:
            code, detail = _run_fetch(
                inv,
                argv,
                lister=lister,
                downloader=downloader,
                fasta_fetcher=fasta_fetcher,
            )
        except RunTestsError as exc:
            code, detail = exc.code, str(exc)
            print(
                f"run_tests: error ({code.name.lower()}, exit {int(code)}): {exc}",
                file=sys.stderr,
            )
        print(_summary(inv, code, detail, targets=targets), end="")
        return int(code)

    fetched = False
    detail: str | None = None
    code = Exit.OK
    vepyr_resolved: str | None = None

    if inv.cache_dir is not None:
        try:
            code, detail = _run_fetch(
                inv,
                argv,
                lister=lister,
                downloader=downloader,
                fasta_fetcher=fasta_fetcher,
            )
            fetched = True
        except RunTestsError as exc:
            code, detail = exc.code, str(exc)
            print(
                f"run_tests: error ({code.name.lower()}, exit {int(code)}): {exc}",
                file=sys.stderr,
            )
            print(_summary(inv, code, detail, targets=targets), end="")
            return int(code)
        if code is not Exit.OK:
            print(_summary(inv, code, detail, targets=targets), end="")
            return int(code)

    cache_root = _resolve_cache_root(inv)
    if cache_root is None:
        print(MISSING_CACHE, file=sys.stderr)
        return int(Exit.USAGE)

    # No data-tests yet: fetch (if any) succeeded; nothing to run.
    if not targets:
        if not fetched:
            # Env-only with zero targets: still prove the cache looks sane.
            try:
                tests.precheck_cache(
                    cache_root,
                    pins_toml=repo / "PINS.toml",
                    flavours=inv.flavours,
                )
            except RunTestsError as exc:
                print(
                    f"run_tests: error ({exc.code.name.lower()}, "
                    f"exit {int(exc.code)}): {exc}",
                    file=sys.stderr,
                )
                print(
                    _summary(
                        inv,
                        exc.code,
                        str(exc),
                        cache_dir=cache_root,
                        targets=targets,
                    ),
                    end="",
                )
                return int(exc.code)
        zero_detail = "0 data-problem target(s); nothing to run"
        if detail:
            zero_detail = f"{detail}; {zero_detail}"
        print(
            _summary(
                inv,
                Exit.OK,
                zero_detail,
                cache_dir=cache_root,
                targets=targets,
            ),
            end="",
        )
        return int(Exit.OK)

    if inv.vepyr is None:
        print(MISSING_VEPYR, file=sys.stderr)
        print(
            _summary(
                inv,
                Exit.USAGE,
                MISSING_VEPYR,
                cache_dir=cache_root,
                targets=targets,
            ),
            end="",
        )
        return int(Exit.USAGE)

    try:
        code, detail, vepyr_resolved = _run_data_tests(
            inv,
            cache_root=cache_root,
            targets=targets,
            cargo_runner=cargo_runner,
            gh_api=gh_api,
        )
    except RunTestsError as exc:
        code, detail = exc.code, str(exc)
        print(
            f"run_tests: error ({code.name.lower()}, exit {int(code)}): {exc}",
            file=sys.stderr,
        )

    print(
        _summary(
            inv,
            code,
            detail,
            cache_dir=cache_root,
            targets=targets,
            vepyr_resolved=vepyr_resolved,
        ),
        end="",
    )
    return int(code)
