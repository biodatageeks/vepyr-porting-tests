"""Discover data-test targets and precheck ``$VEPYR_CACHE_ROOT`` before cargo.

Targets are the data-test directories ``tests/data/<name>/`` that hold a
``test.toml`` (issue #80). All of them run under the one generic cargo test target
``tests/data_dirs.rs``, so the cargo invocation names that target once. Precheck mirrors
the fail-loud repairs in ``tests/common/cache.rs``: provenance vs ``PINS.toml``, FASTA,
and — when a target declares contigs later — shard presence. Until pilots land, precheck
covers provenance + FASTA only.

``./run_tests --only DIR`` (issue #168) runs chosen data-test directories instead:
:func:`only_root` copies them into a fresh temporary root that ``$DATA_DIRS_ROOT``
names, and :func:`cargo_argv` then filters on the exact test name ``data_dirs`` so
the ``selftest`` test (which honours the same override) never walks that root.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from run_tests import fetch
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "CACHE_ENV",
    "NOT_A_DATA_TEST",
    "ROOT_ENV",
    "cargo_argv",
    "data_targets",
    "list_table",
    "only_dirs",
    "only_root",
    "precheck_cache",
]

CACHE_ENV: Final[str] = "VEPYR_CACHE_ROOT"
DATA_DIR: Final[str] = "tests/data"
"""Where data-test directories live, relative to the repository root."""
RUNNER_TARGET: Final[str] = "data_dirs"
"""The cargo test target (``tests/data_dirs.rs``) that walks :data:`DATA_DIR`."""
ROOT_ENV: Final[str] = "DATA_DIRS_ROOT"
"""Overrides the directory :data:`RUNNER_TARGET` walks (``tests/data_dirs.rs``)."""
NOT_A_DATA_TEST: Final[str] = "not a data-test directory"
"""Fixed phrase of every ``--only`` usage error (callers grep for it)."""
_TEST_TOML: Final[str] = "test.toml"


def data_targets(repo_root: Path) -> tuple[str, ...]:
    """Sorted names of the data-test directories ``tests/data/<name>/``.

    A directory counts only when it holds a ``test.toml``: that file is what the
    generic runner loads, so a directory without one is not a test.

    Args:
        repo_root: Repository root.

    Returns:
        Directory names, sorted; empty when ``tests/data`` does not exist.
    """
    data_dir = repo_root / DATA_DIR
    if not data_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path.name
            for path in data_dir.iterdir()
            if path.is_dir() and (path / _TEST_TOML).is_file()
        )
    )


def list_table(targets: Sequence[str]) -> str:
    """``--list`` body: header, one target per line (or ``(none)``), count footer."""
    lines = ["set   target"]
    if targets:
        lines += [f"data  {name}" for name in targets]
    else:
        lines.append("(none)")
    lines.append(f"run_tests: {len(targets)} data-problem target(s)")
    return "\n".join(lines) + "\n"


def cargo_argv(
    targets: Sequence[str], *, config: Path | None = None, exact: bool = False
) -> list[str]:
    """``cargo test --no-fail-fast [--config …] --test data_dirs [-- --exact …]``.

    Every data-test directory runs inside the one :data:`RUNNER_TARGET`, so the
    directories themselves are not cargo arguments; with no directories there is
    nothing to run and no ``--test`` is added.

    Args:
        targets: Data-test directory names, as :func:`data_targets` returns them.
        config: Optional cargo ``--config`` file (the engine ``[patch]`` tables).
        exact: Run only the test named :data:`RUNNER_TARGET` (``--only``): the
            binary's ``selftest`` also honours :data:`ROOT_ENV` and would walk the
            scratch root against its synthetic cache.

    Returns:
        The argv to execute.
    """
    argv = ["cargo", "test", "--no-fail-fast"]
    if config is not None:
        argv += ["--config", str(config)]
    if targets:
        argv += ["--test", RUNNER_TARGET]
        if exact:
            argv += ["--", "--exact", RUNNER_TARGET]
    return argv


def only_dirs(raw: Sequence[Path]) -> tuple[Path, ...]:
    """Validate ``--only``: existing data-test directories with distinct basenames.

    Args:
        raw: The ``--only`` values as given (relative to the caller's cwd).

    Returns:
        The directories, resolved to absolute paths, in the order given.

    Raises:
        RunTestsError: exit 2 with :data:`NOT_A_DATA_TEST` in the message for a
            missing directory, one without ``test.toml``, or a basename already
            given (the copies would collide in the scratch root).
    """
    seen: dict[str, Path] = {}
    for given in raw:
        path = given.expanduser().resolve()
        if not (path / _TEST_TOML).is_file():
            why = "no such directory" if not path.is_dir() else f"no {_TEST_TOML}"
            raise RunTestsError(
                Exit.USAGE, f"--only {given}: {NOT_A_DATA_TEST} ({why})"
            )
        if (first := seen.get(path.name)) is not None:
            raise RunTestsError(
                Exit.USAGE,
                f"--only {given}: {NOT_A_DATA_TEST} set: basename {path.name!r} "
                f"is already given by {first} (the copies would collide)",
            )
        seen[path.name] = path
    return tuple(seen.values())


@contextmanager
def only_root(dirs: Sequence[Path]) -> Iterator[Path]:
    """A fresh temporary root holding a copy of each of ``dirs``, deleted afterwards.

    The real ``tests/data`` is never touched: cargo walks this root through
    :data:`ROOT_ENV`. The root is removed on success, on failure and on error.

    Args:
        dirs: Validated data-test directories (see :func:`only_dirs`).

    Yields:
        The temporary root; ``root/<basename>/`` mirrors each directory.
    """
    root = Path(tempfile.mkdtemp(prefix="run_tests-only-"))
    try:
        for src in dirs:
            shutil.copytree(src, root / src.name)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _pin_revision(pins: Mapping[fetch.Flavour, fetch.DatasetPin], flavour: str) -> str:
    """Pinned revision of ``flavour``, or exit 2 when ``PINS.toml`` never pinned it."""
    try:
        return pins[fetch.Flavour(flavour)].revision
    except (KeyError, ValueError) as exc:
        raise RunTestsError(
            Exit.USAGE, f"precheck: unknown flavour {flavour!r} in PINS.toml"
        ) from exc


def _pinned_fasta_name(fasta_pin: fetch.FastaPin | None, pins_toml: Path) -> str:
    """Uncompressed FASTA basename the fetch writes, straight off the pin.

    One source of truth with :func:`run_tests.fetch.fetch_fasta`: bumping
    ``[grch38_fasta].ref`` moves both the fetch and this precheck together.
    """
    if fasta_pin is None:
        raise RunTestsError(
            Exit.USAGE,
            f"{pins_toml}: no [{fetch.FASTA_PIN}] pin — "
            "precheck cannot name the reference FASTA",
        )
    return fasta_pin.fa_name


def precheck_cache(
    root: Path,
    *,
    pins_toml: Path,
    flavours: Sequence[str] | None = None,
) -> None:
    """Fail loud when ``root`` is not a usable ``./run_tests --cache-dir`` product.

    When ``flavours`` is ``None``, every flavour recorded in ``PROVENANCE.json`` is
    checked (so a chr21-only ensembl fetch is not refused for missing refseq/merged).

    Raises:
        RunTestsError: exit 3 on revision clash, exit 4 on missing provenance/FASTA.
    """
    if not root.is_dir():
        raise RunTestsError(
            Exit.INCOMPLETE,
            f"{CACHE_ENV}={root} is not a directory. "
            f"Run: ./run_tests --cache-dir {root} [--add-contigs LIST]",
        )
    provenance_path = root / fetch.PROVENANCE
    if not provenance_path.is_file():
        raise RunTestsError(
            Exit.INCOMPLETE,
            f"{provenance_path} missing. "
            f"Run: ./run_tests --cache-dir {root} [--add-contigs LIST]",
        )
    try:
        provenance = fetch.read_provenance(root)
    except RunTestsError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunTestsError(
            Exit.INCOMPLETE, f"{provenance_path} unreadable: {exc}"
        ) from exc
    if provenance is None:
        raise RunTestsError(
            Exit.INCOMPLETE,
            f"{provenance_path} missing. "
            f"Run: ./run_tests --cache-dir {root} [--add-contigs LIST]",
        )
    check_flavours: tuple[str, ...]
    if flavours is None:
        check_flavours = tuple(provenance.datasets)
        if not check_flavours:
            raise RunTestsError(
                Exit.INCOMPLETE,
                f"{provenance_path} has no datasets. "
                f"Run: ./run_tests --cache-dir {root} [--add-contigs LIST]",
            )
    else:
        check_flavours = tuple(flavours)
    pins, fasta_pin = fetch.load_dataset_pins(pins_toml)
    fasta_name = _pinned_fasta_name(fasta_pin, pins_toml)
    for flavour in check_flavours:
        record = provenance.datasets.get(flavour)
        if record is None:
            raise RunTestsError(
                Exit.INCOMPLETE,
                f"flavour {flavour} never fetched into {root}. "
                f"Run: ./run_tests --cache-dir {root} --flavours {flavour} "
                f"[--add-contigs LIST]",
            )
        expected = _pin_revision(pins, flavour)
        if record.revision != expected:
            raise RunTestsError(
                Exit.REVISION,
                f"flavour {flavour}: revision on disk {record.revision} != "
                f"PINS.toml {expected}. "
                f"Run: ./run_tests --cache-dir <other> --flavours {flavour} "
                f"and set {CACHE_ENV}=<other>",
            )
        for name in fetch.CACHE_METADATA:
            metadata = root / fetch.Flavour(flavour).dir_name / name
            if not metadata.is_file():
                raise RunTestsError(
                    Exit.INCOMPLETE,
                    f"cache metadata missing at {metadata}. "
                    f"Run: ./run_tests --cache-dir {root} --flavours {flavour} "
                    "--add-contigs <required-contigs>",
                )
    fasta = root / fetch.FASTA_DIR / fasta_name
    fai = Path(str(fasta) + ".fai")
    if not fasta.is_file() or not fai.is_file():
        raise RunTestsError(
            Exit.INCOMPLETE,
            f"reference FASTA missing at {fasta} (need .fa and .fai). "
            f"Run: ./run_tests --cache-dir {root}",
        )


def read_pins_flavour_keys(pins_toml: Path) -> tuple[str, ...]:
    """Flavour keys present as ``hf_cache_*`` tables (for tests)."""
    data = tomllib.loads(pins_toml.read_text(encoding="utf-8"))
    keys = []
    for name in data:
        if name.startswith("hf_cache_"):
            keys.append(name.removeprefix("hf_cache_"))
    return tuple(keys)
