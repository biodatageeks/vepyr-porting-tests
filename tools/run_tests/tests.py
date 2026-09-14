"""Discover data-test targets and precheck ``$VEPYR_CACHE_ROOT`` before cargo.

Targets are the stems of ``tests/data_*.rs`` (not ``common_compile``). Precheck mirrors
the fail-loud repairs in ``tests/common/cache.rs``: provenance vs ``PINS.toml``, FASTA,
and — when a target declares contigs later — shard presence. Until pilots land, precheck
covers provenance + FASTA only.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from run_tests import fetch
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "CACHE_ENV",
    "FASTA_NAME",
    "cargo_argv",
    "data_targets",
    "list_table",
    "precheck_cache",
]

CACHE_ENV: Final[str] = "VEPYR_CACHE_ROOT"
FASTA_NAME: Final[str] = "Homo_sapiens.GRCh38.dna.primary_assembly.fa"
_DATA_GLOB: Final[str] = "data_*.rs"


def data_targets(repo_root: Path) -> tuple[str, ...]:
    """Sorted stems of ``tests/data_*.rs``."""
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return ()
    return tuple(
        sorted(path.stem for path in tests_dir.glob(_DATA_GLOB) if path.is_file())
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


def cargo_argv(targets: Sequence[str], *, config: Path | None = None) -> list[str]:
    """``cargo test --no-fail-fast [--config …] --test t1 --test t2 …``."""
    argv = ["cargo", "test", "--no-fail-fast"]
    if config is not None:
        argv += ["--config", str(config)]
    for name in targets:
        argv += ["--test", name]
    return argv


def _pin_revision(pins_toml: Path, flavour: str) -> str:
    pins, _ = fetch.load_dataset_pins(pins_toml)
    try:
        return pins[fetch.Flavour(flavour)].revision
    except (KeyError, ValueError) as exc:
        raise RunTestsError(
            Exit.USAGE, f"precheck: unknown flavour {flavour!r} in PINS.toml"
        ) from exc


def precheck_cache(
    root: Path,
    *,
    pins_toml: Path,
    flavours: Sequence[str] = ("ensembl", "refseq", "merged"),
) -> None:
    """Fail loud when ``root`` is not a usable ``./run_tests --cache-dir`` product.

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
    for flavour in flavours:
        record = provenance.datasets.get(flavour)
        if record is None:
            raise RunTestsError(
                Exit.INCOMPLETE,
                f"flavour {flavour} never fetched into {root}. "
                f"Run: ./run_tests --cache-dir {root} --flavours {flavour} "
                f"[--add-contigs LIST]",
            )
        expected = _pin_revision(pins_toml, flavour)
        if record.revision != expected:
            raise RunTestsError(
                Exit.REVISION,
                f"flavour {flavour}: revision on disk {record.revision} != "
                f"PINS.toml {expected}. "
                f"Run: ./run_tests --cache-dir <other> --flavours {flavour} "
                f"and set {CACHE_ENV}=<other>",
            )
    fasta = root / "fasta" / FASTA_NAME
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
