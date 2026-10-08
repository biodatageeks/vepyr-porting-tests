"""``test.toml`` ``[vepyr]`` -> ``vepyr annotate`` argv: the one mapping (#231).

The Rust loader (``tests/data_dirs.rs``) maps ``[vepyr]`` by hand onto
``AnnotateVcfConfig``. ``./run_tests --via-cli`` instead drives the user-facing
``python -m vepyr annotate`` command line, and this module is the only place that
translates a data-test's settings into that argv. It is pure: no I/O, no
subprocess, so every key is unit-tested (``tools/test_run_tests_cli_argv.py``).

Mapping (vepyr CLI checked at ``biodatageeks/vepyr`` af305aff):

========================== ==================================================
``[vepyr]`` key            ``vepyr annotate`` argv
========================== ==================================================
``flavour``                ``--dir_cache <root>/116_GRCh38_<flavour>``
``everything = true``      the everything flag (always on in the CLI)
``reference_fasta = true`` ``--fasta <root>/fasta/<pinned fa>`` (required)
``preserve_record_layout`` ``true`` = CLI default, no flag
``buffer_size``            ``5000`` = CLI default, no flag
``required_contigs``       no flag (a cache precondition, not a setting)
========================== ==================================================

Anything else (``everything = false``, ``reference_fasta = false``,
``preserve_record_layout = false``, ``buffer_size != 5000``, an unknown flavour or
an unknown key) raises :class:`UnmappableKey`: the CLI cannot express it, and the
value is never silently dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "CACHE_VERSION",
    "CLI_BUFFER_SIZE",
    "FLAVOURS",
    "VEPYR_KEYS",
    "UnmappableKey",
    "annotate_argv",
    "cache_dir",
    "effective_runs",
]

CLI_BUFFER_SIZE: Final[int] = 5000
"""``vepyr.annotate(buffer_size=...)`` default; the CLI has no flag to change it."""
CACHE_VERSION: Final[str] = "116"
FLAVOURS: Final[frozenset[str]] = frozenset({"ensembl", "refseq", "merged"})
VEPYR_KEYS: Final[frozenset[str]] = frozenset(
    {
        "flavour",
        "required_contigs",
        "everything",
        "preserve_record_layout",
        "reference_fasta",
        "buffer_size",
    }
)
"""The ``[vepyr]`` keys of the ``tests/data_dirs.rs`` schema."""


@dataclass
class UnmappableKey(Exception):
    """A ``[vepyr]`` key/value the ``vepyr annotate`` CLI cannot express.

    Attributes:
        key: The ``[vepyr]`` key.
        value: Its (effective) value.
        reason: Why the CLI cannot express it.
    """

    key: str
    value: object
    reason: str

    def __str__(self) -> str:
        return f"UnmappableKey: {self.key} = {self.value!r}: {self.reason}"


def effective_runs(
    vepyr: Mapping[str, object], runs: Sequence[Mapping[str, object]] = ()
) -> list[dict[str, object]]:
    """The effective settings of every run: ``[vepyr]`` overlaid by each override.

    Args:
        vepyr: The ``[vepyr]`` table.
        runs: The ``[[vepyr_run]]`` tables (each overrides ``[vepyr]`` keys).

    Returns:
        One merged mapping per run; just ``[vepyr]`` when there are no overrides.
    """
    if not runs:
        return [dict(vepyr)]
    return [{**vepyr, **run} for run in runs]


def cache_dir(cache_root: Path, flavour: object) -> Path:
    """``<cache_root>/116_GRCh38_<flavour>``, the directory ``--dir_cache`` names.

    Raises:
        UnmappableKey: ``flavour`` is not one of :data:`FLAVOURS`.
    """
    match flavour:
        case str() as name if name in FLAVOURS:
            return cache_root / f"{CACHE_VERSION}_GRCh38_{name}"
        case _:
            raise UnmappableKey("flavour", flavour, f"not one of {sorted(FLAVOURS)}")


def _require(settings: Mapping[str, object], key: str, wanted: object) -> None:
    """Accept ``key`` when it is absent or equals ``wanted`` (the CLI's fixed value)."""
    value = settings.get(key, wanted)
    # `True == 1` in Python: compare types too, so `everything = 1` is not `true`.
    if type(value) is not type(wanted) or value != wanted:
        raise UnmappableKey(
            key, value, f"the vepyr CLI has no flag for it; only {wanted!r} is fixed"
        )


def annotate_argv(
    settings: Mapping[str, object],
    *,
    input_vcf: Path,
    output_vcf: Path,
    cache_root: Path,
    fasta: Path,
) -> list[str]:
    """The ``vepyr annotate ...`` argv (after ``python -m vepyr``) for one run.

    Args:
        settings: One run's effective ``[vepyr]`` settings (:func:`effective_runs`).
        input_vcf: The data-test's normalised ``input.vcf``.
        output_vcf: Where vepyr writes its VCF.
        cache_root: The ``./run_tests --cache-dir`` root (Hub layout).
        fasta: The reference FASTA under ``cache_root``.

    Returns:
        ``["annotate", "--input_file", ..., "--no_progress"]``.

    Raises:
        UnmappableKey: For an unknown key or a value the CLI cannot express.
    """
    for key in sorted(settings):
        if key not in VEPYR_KEYS:
            raise UnmappableKey(key, settings[key], "unknown [vepyr] key")
    if "flavour" not in settings:
        raise UnmappableKey("flavour", None, "missing; it picks the cache directory")
    _require(settings, "everything", True)
    _require(settings, "reference_fasta", True)
    _require(settings, "preserve_record_layout", True)
    _require(settings, "buffer_size", CLI_BUFFER_SIZE)
    return [
        "annotate",
        "--input_file",
        str(input_vcf),
        "--output_file",
        str(output_vcf),
        "--dir_cache",
        str(cache_dir(cache_root, settings["flavour"])),
        "--fasta",
        str(fasta),
        "--cache_version",
        CACHE_VERSION,
        "--everything",
        "--no_progress",
    ]
