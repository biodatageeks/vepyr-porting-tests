"""The end-of-run summary block ``./run_tests`` prints before it exits.

For now this is an *architectural* snapshot, as issue #4 asks: every flag and effective
parameter of the invocation, the cache directory, and — read back from
``PROVENANCE.json`` after the run — the contigs **accumulated** in that directory per
flavour, next to the contigs this run **requested**. The two differ on purpose:
``--add-contigs`` adds shards and never resets the directory, so the accumulated set is
the one a later test run actually gets.

The block is pure text built from a :class:`RunSummary` plus one read of the cache
directory (:func:`accumulated_contigs`), so later slices (engine checkout, test
execution) can add sections without replacing it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "HEADER",
    "NEVER_FETCHED",
    "UNREADABLE",
    "WHOLE_GENOME",
    "RunSummary",
    "accumulated_contigs",
    "render",
]

HEADER: Final[str] = "== run_tests summary =="
"""First line of the block; tests and later slices anchor on it."""

WHOLE_GENOME: Final[str] = "ALL (whole genome)"
"""Rendered for a flavour fetched without ``--add-contigs``."""
NEVER_FETCHED: Final[str] = "(never fetched)"
"""Rendered for a flavour with no record in ``PROVENANCE.json``."""
UNREADABLE: Final[str] = "(PROVENANCE.json unreadable)"
"""Rendered when the directory holds a provenance file this tool cannot parse."""

_NONE: Final[str] = "(none)"


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSummary:
    """Every effective parameter of one ``./run_tests`` invocation.

    Attributes:
        cache_dir: ``--cache-dir``, or ``None`` when no cache work was requested.
        add_contigs: ``--add-contigs`` as parsed, or ``None`` for the whole genome.
        flavours: ``--flavours``, already split.
        vepyr: ``--vepyr``; parsed but inert until the engine slice lands.
        fasta: Whether the reference FASTA was part of this run (automatic on a real
            fetch, never a flag).
        dry_run: ``--dry-run`` — nothing was written.
        verify: ``--verify``.
        fast: ``--fast``.
        trim_manifests: The effective trim setting (``--no-trim-manifests`` inverts it).
        outcome: The exit code the invocation is about to return.
        detail: One line of context for the outcome (an error message, or the fetch's
            own ``ok: …`` line); omitted when there is nothing to add.
    """

    cache_dir: Path | None
    add_contigs: tuple[str, ...] | None
    flavours: tuple[str, ...]
    vepyr: str | None
    fasta: bool
    dry_run: bool
    verify: bool
    fast: bool
    trim_manifests: bool
    outcome: Exit
    detail: str | None = None


def accumulated_contigs(
    cache_dir: Path, flavours: Sequence[str]
) -> dict[str, list[str] | str]:
    """What ``cache_dir`` holds per flavour, straight out of ``PROVENANCE.json``.

    Never raises: the summary reports the directory as it finds it, including a fresh
    directory or a provenance file it cannot parse. Refusing the run over that is
    :mod:`run_tests.fetch`'s job, and it already happened by the time this is called.

    Args:
        cache_dir: The ``--cache-dir`` root.
        flavours: Flavour names to report, in the order the user gave them.

    Returns:
        Flavour name to either the accumulated contig list, ``"ALL"``,
        :data:`NEVER_FETCHED`, or :data:`UNREADABLE`.
    """
    from run_tests import fetch

    try:
        provenance = fetch.read_provenance(cache_dir)
    except (RunTestsError, OSError, UnicodeDecodeError):
        return {flavour: UNREADABLE for flavour in flavours}
    datasets = provenance.datasets if provenance is not None else {}
    result: dict[str, list[str] | str] = {}
    for flavour in flavours:
        record = datasets.get(flavour)
        result[flavour] = NEVER_FETCHED if record is None else record.contigs
    return result


def _render_contigs(value: list[str] | str) -> str:
    """One flavour's accumulated set as a single line."""
    if isinstance(value, str):
        return WHOLE_GENOME if value == "ALL" else value
    return ", ".join(value) if value else _NONE


def render(
    summary: RunSummary, accumulated: Mapping[str, list[str] | str] | None = None
) -> str:
    """Render the block, newline-terminated.

    Args:
        summary: The invocation's effective parameters and outcome.
        accumulated: Result of :func:`accumulated_contigs`; ``None`` (or empty) when no
            cache directory was touched, which renders the section as ``(none)``.

    Returns:
        The summary block, ending in a newline.
    """
    requested = (
        ", ".join(summary.add_contigs)
        if summary.add_contigs
        else WHOLE_GENOME
        if summary.cache_dir is not None
        else _NONE
    )
    lines = [
        HEADER,
        f"cache dir        : {summary.cache_dir or _NONE}",
        f"flavours         : {', '.join(summary.flavours)}",
        f"contigs requested: {requested}",
        f"vepyr            : {summary.vepyr or _NONE}",
        f"fasta            : {'yes' if summary.fasta else 'no'}",
        f"dry-run          : {'yes' if summary.dry_run else 'no'}",
        f"verify           : {'yes' if summary.verify else 'no'}",
        f"fast             : {'yes' if summary.fast else 'no'}",
        f"trim manifests   : {'yes' if summary.trim_manifests else 'no'}",
        "contigs effective (accumulated on disk):",
    ]
    if accumulated:
        lines += [
            f"  {flavour:<8}: {_render_contigs(value)}"
            for flavour, value in accumulated.items()
        ]
    else:
        lines.append(f"  {_NONE}")
    lines.append(
        f"outcome          : {summary.outcome.name.lower()} "
        f"(exit {int(summary.outcome)})"
    )
    if summary.detail:
        lines.append(f"detail           : {summary.detail}")
    return "\n".join(lines) + "\n"
