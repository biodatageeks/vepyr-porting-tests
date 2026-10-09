"""Command line for ``./run_tests`` — fetch, ``--vepyr``, and data-test runs.

``--cache-dir`` materialises the pinned VEP cache 116 corpus. With a cache root
(``--cache-dir`` or ``$VEPYR_CACHE_ROOT``), discovered ``tests/data/<name>/`` test
directories run under a path-patched engine ladder: ``--vepyr REF`` pins the
revision, and omitting it resolves ``biodatageeks/vepyr``'s current ``master`` HEAD
(issue #30). ``--only DIR`` (repeatable) runs just the named data-test directories,
which may live outside ``tests/data``, from a temporary copy (issue #168).
``--via-cli`` annotates the same directories through the user-facing
``python -m vepyr annotate`` built at that REF instead of cargo (issue #231).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from run_tests import engine, fetch, freshness, summary, tests, via_cli
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "DEFAULT_FLAVOURS",
    "DEFAULT_VEPYR_REF",
    "DESCRIPTION",
    "MISSING_CACHE",
    "Invocation",
    "main",
    "parse_args",
]

DEFAULT_FLAVOURS: Final[str] = "ensembl,refseq,merged"
DEFAULT_VEPYR_REF: Final[str] = "master"
"""Ref resolved when ``--vepyr`` is omitted: ``biodatageeks/vepyr`` master HEAD."""
DESCRIPTION: Final[str] = (
    "Entry point for curated data-problem porting tests. "
    "--cache-dir materialises the pinned VEP cache 116 corpus; "
    "with a cache root, discovered data-tests run against --vepyr REF "
    f"(default: biodatageeks/vepyr {DEFAULT_VEPYR_REF!r} HEAD)."
)
MISSING_CACHE: Final[str] = (
    "run_tests: need a cache root: pass --cache-dir DIR or export "
    f"{tests.CACHE_ENV}=DIR (after ./run_tests --cache-dir DIR [--add-contigs LIST])"
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
    only: tuple[Path, ...] = ()
    """``--only`` directories, absolute and validated; empty = all of ``tests/data``."""
    via_cli: bool = False
    """``--via-cli``: run ``python -m vepyr annotate`` instead of cargo (#231)."""
    old_vepyr_cache: bool = False
    """``--old-vepyr-cache``: consent to run on a cache that is not the newest."""
    freshness_report: freshness.FreshnessReport | None = None
    """The freshness guard's report, attached once the guard ran (not parsed)."""

    @property
    def vepyr_ref(self) -> str:
        """Ref to resolve: ``--vepyr`` when given, else :data:`DEFAULT_VEPYR_REF`."""
        return self.vepyr if self.vepyr is not None else DEFAULT_VEPYR_REF


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
        help="cache directory for the VEP cache 116 corpus; shards are downloaded "
        "into it (the reference FASTA comes along automatically). It is also the "
        "$VEPYR_CACHE_ROOT the data-test run reads.",
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
        help="vepyr ref under test (branch, tag, or commit on biodatageeks/vepyr); "
        "materialises that revision's dfbf/formats ladder via cargo path patches. "
        "Examples: a tag carries no 'v' prefix (--vepyr 0.7.0, not v0.7.0); a commit "
        "may be full or short (--vepyr 1f0c3a9 or the full 40-char sha); a branch "
        "works too (--vepyr master). Omitted, data-tests run against "
        f"{DEFAULT_VEPYR_REF}'s current HEAD, whose resolved 40-char sha the run "
        "summary prints either way; pass REF for a pinned, reproducible run.",
    )
    engine_g.add_argument(
        freshness.CONSENT_FLAG,
        action="store_true",
        help="consent to a vepyr cache that is not provably the newest. By default, "
        "before any download or test, each --flavours pin is compared with the Hub "
        "HEAD of its ref; a newer HEAD, an unreachable Hub, or a cache root used "
        "as-is without a PROVENANCE.json record for the flavour exits 7. With this "
        "flag the run proceeds and the summary records 'old cache: YES (consented)'.",
    )
    run = parser.add_argument_group("Run")
    run.add_argument(
        "--list",
        action="store_true",
        help="print data-test directories (tests/data/<name>/) and exit 0.",
    )
    run.add_argument(
        "--only",
        action="append",
        type=Path,
        default=[],
        metavar="DIR",
        help="run only this data-test directory (holds test.toml; may be outside "
        "tests/data, e.g. a scratch copy); repeatable. The directories are copied "
        f"into a temporary ${tests.ROOT_ENV} root, deleted afterwards; tests/data is "
        "never touched. The engine is still --vepyr REF (or its default).",
    )
    run.add_argument(
        "--via-cli",
        action="store_true",
        help="annotate each selected data-test through the user-facing "
        "'python -m vepyr annotate' CLI instead of cargo test --test data_dirs "
        "(a complement, not a replacement). The CLI is built once from "
        "biodatageeks/vepyr at the engine REF that --vepyr REF (default: master "
        f"HEAD) resolves, into <cache root>/{via_cli.BUILD_DIR}/<sha>/. Its argv is "
        "derived from test.toml [vepyr] / [[vepyr_run]]; a value the CLI cannot "
        "express (e.g. buffer_size != 5000) fails that directory with exit 2. "
        "Compare rule: md5 of the output body (every line not starting with '#', "
        "line terminators kept) == [compare] body_md5; prints 'PASS <dir>' or "
        "'MISMATCH <dir> expected=<md5> actual=<md5>' and exits 8 on any mismatch "
        "(any other error wins with its own exit code).",
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
        only=tests.only_dirs(args.only),
        via_cli=args.via_cli,
        old_vepyr_cache=args.old_vepyr_cache,
    )


def _repo_root() -> Path:
    """Repository root (``tools/run_tests/cli.py`` -> repo)."""
    return Path(__file__).resolve().parents[2]


def _selection(inv: Invocation, root: Path) -> fetch.Selection:
    """Build the fetch selection for ``root`` (the already-resolved cache root)."""
    try:
        flavours = tuple(fetch.Flavour(name) for name in inv.flavours)
    except ValueError as exc:
        raise RunTestsError(Exit.USAGE, f"--flavours: {exc}") from exc
    return fetch.Selection(
        root=root,
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
    vepyr_effective: str | None = None,
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
            vepyr=vepyr_effective if vepyr_effective is not None else inv.vepyr,
            vepyr_resolved=vepyr_resolved,
            vepyr_default=vepyr_effective is not None and inv.vepyr is None,
            targets=tuple(targets),
            fasta=root is not None and (inv.cache_dir is not None and not inv.dry_run),
            dry_run=inv.dry_run,
            verify=inv.verify,
            fast=inv.fast,
            trim_manifests=inv.trim_manifests,
            outcome=outcome,
            detail=detail,
            freshness=(
                tuple(inv.freshness_report.summary_lines())
                if inv.freshness_report is not None
                else ()
            ),
        ),
        accumulated,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Fetchers:
    """The injectable Hub-side callables, carried as one bundle."""

    lister: fetch.Lister
    downloader: fetch.Downloader
    fasta_fetcher: Callable[[str, Path], None]


def _print_error(exc: RunTestsError) -> None:
    """Report ``exc`` on stderr, verbatim for argparse-shaped usage errors."""
    if exc.verbatim:
        print(str(exc), file=sys.stderr)
    else:
        print(
            f"run_tests: error ({exc.code.name.lower()}, exit {int(exc.code)}): {exc}",
            file=sys.stderr,
        )


def _run_fetch(
    inv: Invocation,
    argv: Sequence[str],
    *,
    cache_root: Path,
    fetchers: _Fetchers,
) -> tuple[Exit, str | None]:
    pins_toml = _repo_root() / "PINS.toml"
    if inv.fast:
        os.environ[_HF_XET_HIGH_PERFORMANCE] = "1"
    selection = _selection(inv, cache_root)
    pins, fasta_pin = fetch.load_dataset_pins(pins_toml)
    outcome = fetch.fetch(
        selection,
        pins,
        fasta_pin,
        lister=fetchers.lister,
        downloader=fetchers.downloader,
        fasta_fetcher=fetchers.fasta_fetcher,
        argv=argv,
        pins_toml=pins_toml,
        tool=f"tools/run_tests/fetch.py@{fetch.git_sha(_repo_root())}",
    )
    return (
        outcome.code,
        f"{outcome.fetched} shard(s) fetched, {outcome.present} on disk",
    )


def _default_cargo(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """Run cargo from the repo root (manifests + ``--config``)."""
    merged = {**os.environ, **dict(env)}
    completed = subprocess.run(list(argv), env=merged, cwd=_repo_root(), check=False)
    return int(completed.returncode)


def _resolve_cache_root(inv: Invocation) -> Path | None:
    """Resolve the cache root to an absolute path, exactly once.

    ``--cache-dir`` wins over ``$VEPYR_CACHE_ROOT``. The result is always absolute:
    the precheck runs in the caller's cwd while cargo is spawned with
    ``cwd=_repo_root()``, so a relative root would name two different directories
    on the two sides of the run.

    Returns:
        The absolute cache root, or ``None`` when neither source supplies one.
    """
    match (inv.cache_dir, os.environ.get(tests.CACHE_ENV)):
        case (Path() as explicit, _):
            return explicit.resolve()
        case (None, str() as raw) if raw:
            return Path(raw).resolve()
        case _:
            return None


def _run_data_tests(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    cargo_runner: CargoRunner,
    gh_api: engine.GhApi | None,
) -> tuple[Exit, str | None, str | None]:
    """Precheck + engine + cargo. Returns ``(code, detail, vepyr_resolved)``.

    With ``--only`` the directories are copied into a temporary root that cargo
    walks via ``$DATA_DIRS_ROOT``, and only the exact ``data_dirs`` test runs.
    """
    repo = _repo_root()
    pins_toml = repo / "PINS.toml"
    tests.precheck_cache(cache_root, pins_toml=pins_toml)
    plan, config_path = engine.materialise(inv.vepyr_ref, repo_root=repo, api=gh_api)
    argv = tests.cargo_argv(targets, config=config_path, exact=bool(inv.only))
    env = {tests.CACHE_ENV: str(cache_root)}
    with ExitStack() as stack:
        if inv.only:
            env[tests.ROOT_ENV] = str(stack.enter_context(tests.only_root(inv.only)))
        stack.enter_context(engine.LockGuard(repo).held())
        # No `cargo update -p …` pre-step (issue #21): cargo re-locks the patched
        # packages by itself when `--config` carries the `[patch]` path tables, and
        # bare `-p <crate>` specs were ambiguous whenever the lockfile held the same
        # crate name under two sources.
        engine.stage(f"cargo test starting ({len(targets)} target(s)) ...")
        code = cargo_runner(argv, env)
    if code == 0:
        return Exit.OK, f"cargo test ok ({len(targets)} target(s))", plan.vepyr_sha
    return (
        Exit.TESTS_FAILED,
        f"cargo test exited {code} ({len(targets)} target(s))",
        plan.vepyr_sha,
    )


def _run_via_cli(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    cli_runner: via_cli.CliRunner,
    vepyr_builder: via_cli.VepyrBuilder,
    gh_api: engine.GhApi | None,
) -> tuple[Exit, str | None, str | None]:
    """Precheck + resolve REF + build the CLI + annotate. ``(code, detail, sha)``."""
    repo = _repo_root()
    pins_toml = repo / "PINS.toml"
    tests.precheck_cache(cache_root, pins_toml=pins_toml)
    _, fasta_pin = fetch.load_dataset_pins(pins_toml)
    assert fasta_pin is not None  # precheck_cache refuses a PINS.toml without it
    fasta = cache_root / fetch.FASTA_DIR / fasta_pin.fa_name
    sha = engine.resolve_sha(gh_api or engine.GhCli(), inv.vepyr_ref)
    build = vepyr_builder(sha, cache_root)
    dirs = inv.only or tuple(repo / tests.DATA_DIR / name for name in targets)
    report = via_cli.run_dirs(
        dirs, build=build, cache_root=cache_root, fasta=fasta, runner=cli_runner
    )
    return report.code, f"{report.detail}; {build.label}", sha


def _via_cli_phase(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    cli_runner: via_cli.CliRunner,
    vepyr_builder: via_cli.VepyrBuilder,
    gh_api: engine.GhApi | None,
) -> int:
    """``--via-cli``: like :func:`_data_test_phase`, through the vepyr CLI (#231)."""
    vepyr_resolved: str | None = None
    try:
        code, detail, vepyr_resolved = _run_via_cli(
            inv,
            cache_root=cache_root,
            targets=targets,
            cli_runner=cli_runner,
            vepyr_builder=vepyr_builder,
            gh_api=gh_api,
        )
    except RunTestsError as exc:
        code, detail = exc.code, str(exc)
        _print_error(exc)
    print(
        _summary(
            inv,
            code,
            detail,
            cache_dir=cache_root,
            targets=targets,
            vepyr_resolved=vepyr_resolved,
            vepyr_effective=inv.vepyr_ref,
        ),
        end="",
    )
    return int(code)


def _parse_phase(argv: Sequence[str]) -> Invocation | int:
    """Parse ``argv``; on a usage failure report it and return the exit code."""
    try:
        return parse_args(argv)
    except SystemExit as exc:
        match exc.code:
            case None | False:
                return int(Exit.OK)
            case True:
                return 1
            case code:
                return int(code)
    except RunTestsError as exc:
        _print_error(exc)
        return int(Exit.USAGE)


def _dry_run_phase(
    inv: Invocation,
    argv: Sequence[str],
    *,
    cache_root: Path,
    targets: Sequence[str],
    fetchers: _Fetchers,
) -> int:
    """Fetch-only ``--dry-run``: list the selection, touch neither engine nor cargo."""
    try:
        code, detail = _run_fetch(inv, argv, cache_root=cache_root, fetchers=fetchers)
    except RunTestsError as exc:
        code, detail = exc.code, str(exc)
        _print_error(exc)
    print(_summary(inv, code, detail, cache_dir=cache_root, targets=targets), end="")
    return int(code)


def _fetch_phase(
    inv: Invocation,
    argv: Sequence[str],
    *,
    cache_root: Path,
    targets: Sequence[str],
    fetchers: _Fetchers,
) -> str | int | None:
    """Materialise ``--cache-dir``.

    Returns:
        The fetch detail line on success, or an ``int`` exit code when the run is
        over (the summary has then already been printed).
    """
    try:
        code, detail = _run_fetch(inv, argv, cache_root=cache_root, fetchers=fetchers)
    except RunTestsError as exc:
        _print_error(exc)
        print(
            _summary(inv, exc.code, str(exc), targets=targets, cache_dir=cache_root),
            end="",
        )
        return int(exc.code)
    if code is not Exit.OK:
        print(
            _summary(inv, code, detail, targets=targets, cache_dir=cache_root), end=""
        )
        return int(code)
    return detail


def _no_targets_phase(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    detail: str | None,
    fetched: bool,
) -> int:
    """Nothing to run: fetch (if any) succeeded, but no ``tests/data/<name>/``."""
    if not fetched:
        # Env-only with zero targets: still prove the cache looks sane.
        try:
            tests.precheck_cache(cache_root, pins_toml=_repo_root() / "PINS.toml")
        except RunTestsError as exc:
            _print_error(exc)
            print(
                _summary(
                    inv, exc.code, str(exc), cache_dir=cache_root, targets=targets
                ),
                end="",
            )
            return int(exc.code)
    zero_detail = "0 data-problem target(s); nothing to run"
    if detail:
        zero_detail = f"{detail}; {zero_detail}"
    print(
        _summary(inv, Exit.OK, zero_detail, cache_dir=cache_root, targets=targets),
        end="",
    )
    return int(Exit.OK)


def _data_test_phase(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    cargo_runner: CargoRunner,
    gh_api: engine.GhApi | None,
) -> int:
    """Run the discovered data-tests under the patched engine ladder.

    ``--vepyr`` is optional: omitted, :attr:`Invocation.vepyr_ref` falls back to
    :data:`DEFAULT_VEPYR_REF` and the resolver dereferences that branch's current
    HEAD, whose full sha the summary prints (issue #30).
    """
    vepyr_resolved: str | None = None
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
        _print_error(exc)
    print(
        _summary(
            inv,
            code,
            detail,
            cache_dir=cache_root,
            targets=targets,
            vepyr_resolved=vepyr_resolved,
            vepyr_effective=inv.vepyr_ref,
        ),
        end="",
    )
    return int(code)


def _freshness_phase(
    inv: Invocation,
    *,
    cache_root: Path,
    targets: Sequence[str],
    head_resolver: fetch.HeadResolver,
) -> Invocation | int:
    """Run the freshness guard before any download or data-test (issue #236).

    Case (c) (no ``PROVENANCE.json`` record) applies only when the root is used
    as-is: a ``--cache-dir`` fetch or a ``--dry-run`` writes or lists the pinned
    revision itself, so there only the pin-vs-HEAD comparison applies.

    Returns:
        ``inv`` with the report attached, or the exit code once the run is refused
        (the summary has then already been printed).
    """
    try:
        flavours = tuple(fetch.Flavour(name) for name in inv.flavours)
    except ValueError as exc:
        error = RunTestsError(Exit.USAGE, f"--flavours: {exc}")
        _print_error(error)
        print(
            _summary(
                inv, error.code, str(error), cache_dir=cache_root, targets=targets
            ),
            end="",
        )
        return int(error.code)
    try:
        pins, _ = fetch.load_dataset_pins(_repo_root() / "PINS.toml")
    except RunTestsError as exc:
        _print_error(exc)
        print(
            _summary(inv, exc.code, str(exc), cache_dir=cache_root, targets=targets),
            end="",
        )
        return int(exc.code)
    report = freshness.check(
        flavours,
        pins,
        head_resolver,
        root=cache_root,
        check_disk=inv.cache_dir is None and not inv.dry_run,
        consented=inv.old_vepyr_cache,
    )
    guarded = replace(inv, freshness_report=report)
    if report.old and not report.consented:
        refusal = report.refusal()
        _print_error(refusal)
        print(
            _summary(
                guarded,
                refusal.code,
                str(refusal),
                cache_dir=cache_root,
                targets=targets,
            ),
            end="",
        )
        return int(refusal.code)
    return guarded


def main(
    argv: Sequence[str] | None = None,
    *,
    lister: fetch.Lister = fetch.hub_lister,
    downloader: fetch.Downloader = fetch.hub_downloader,
    fasta_fetcher: Callable[[str, Path], None] = fetch.url_fetcher,
    cargo_runner: CargoRunner = _default_cargo,
    gh_api: engine.GhApi | None = None,
    cli_runner: via_cli.CliRunner = via_cli.default_runner,
    vepyr_builder: via_cli.VepyrBuilder = via_cli.build_vepyr,
    head_resolver: fetch.HeadResolver = fetch.hub_head_resolver,
) -> int:
    """Entry point: parse -> resolve -> guard -> fetch -> dispatch; the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parsed = _parse_phase(argv)
    if isinstance(parsed, int):
        return parsed
    inv = parsed
    fetchers = _Fetchers(
        lister=lister, downloader=downloader, fasta_fetcher=fasta_fetcher
    )
    targets = (
        tuple(path.name for path in inv.only)
        if inv.only
        else tests.data_targets(_repo_root())
    )

    if inv.list_only:
        print(tests.list_table(targets), end="")
        return int(Exit.OK)

    cache_root = _resolve_cache_root(inv)

    # Freshness guard (#236): before any download or data-test, also under
    # --dry-run and --only; only the --flavours selection is queried.
    if cache_root is not None:
        guarded = _freshness_phase(
            inv, cache_root=cache_root, targets=targets, head_resolver=head_resolver
        )
        if isinstance(guarded, int):
            return guarded
        inv = guarded

    # Fetch-only dry-run: no cargo / no engine. Gated on the *resolved* root so
    # ``$VEPYR_CACHE_ROOT`` honours --dry-run exactly like --cache-dir (#27).
    if cache_root is not None and inv.dry_run:
        return _dry_run_phase(
            inv, argv, cache_root=cache_root, targets=targets, fetchers=fetchers
        )

    detail: str | None = None
    fetched = inv.cache_dir is not None
    if fetched:
        assert cache_root is not None
        outcome = _fetch_phase(
            inv, argv, cache_root=cache_root, targets=targets, fetchers=fetchers
        )
        if isinstance(outcome, int):
            return outcome
        detail = outcome

    if cache_root is None:
        print(MISSING_CACHE, file=sys.stderr)
        return int(Exit.USAGE)
    if not targets:
        return _no_targets_phase(
            inv,
            cache_root=cache_root,
            targets=targets,
            detail=detail,
            fetched=fetched,
        )
    if inv.via_cli:
        return _via_cli_phase(
            inv,
            cache_root=cache_root,
            targets=targets,
            cli_runner=cli_runner,
            vepyr_builder=vepyr_builder,
            gh_api=gh_api,
        )
    return _data_test_phase(
        inv,
        cache_root=cache_root,
        targets=targets,
        cargo_runner=cargo_runner,
        gh_api=gh_api,
    )
