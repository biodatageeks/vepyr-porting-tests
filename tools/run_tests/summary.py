"""The end-of-run summary block ``./run_tests`` prints before it exits.

Every flag and effective parameter of the invocation, the cache directory,
contigs accumulated on disk, discovered data-test targets, and the full 40-char
resolved vepyr sha the run tested against — whether ``--vepyr REF`` pinned it or
the default ``master`` HEAD supplied it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests.verdict import Exit

__all__ = [
    "DEFAULT_MARK",
    "HEADER",
    "NEVER_FETCHED",
    "SKIPPED_LABEL",
    "UNREADABLE",
    "WHOLE_GENOME",
    "RunSummary",
    "accumulated_contigs",
    "render",
]

HEADER: Final[str] = "== run_tests summary =="
WHOLE_GENOME: Final[str] = "ALL (whole genome)"
NEVER_FETCHED: Final[str] = "(never fetched)"
UNREADABLE: Final[str] = "(PROVENANCE.json unreadable)"
_NONE: Final[str] = "(none)"
DEFAULT_MARK: Final[str] = "(default: no --vepyr given; floating master HEAD)"
SKIPPED_LABEL: Final[str] = "skipped (flavour not selected)"
"""Summary label of the directories the ``--flavours`` filter dropped (#257)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunSummary:
    """Every effective parameter of one ``./run_tests`` invocation."""

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
    vepyr_resolved: str | None = None
    vepyr_default: bool = False
    """``--vepyr`` was omitted and :attr:`vepyr` is the implicit default ref."""
    targets: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    """Directories the ``--flavours`` filter dropped (#257); rendered only when any."""
    freshness: tuple[str, ...] = ()
    """The freshness guard's lines (``old cache: ...`` first); empty when it did not
    run (no cache root, ``--list``, or a usage error)."""


def accumulated_contigs(
    cache_dir: Path, flavours: Sequence[str]
) -> dict[str, list[str] | str]:
    """What ``cache_dir`` holds per flavour, straight out of ``PROVENANCE.json``."""
    from run_tests import fetch
    from run_tests.verdict import RunTestsError

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
    if isinstance(value, str):
        return WHOLE_GENOME if value == "ALL" else value
    return ", ".join(value) if value else _NONE


def render(
    summary: RunSummary, accumulated: Mapping[str, list[str] | str] | None = None
) -> str:
    """Render the block, newline-terminated."""
    requested = (
        ", ".join(summary.add_contigs)
        if summary.add_contigs
        else WHOLE_GENOME
        if summary.cache_dir is not None
        else _NONE
    )
    targets = ", ".join(summary.targets) if summary.targets else _NONE
    vepyr_line = summary.vepyr or _NONE
    skipped = (
        [f"{SKIPPED_LABEL}: {len(summary.skipped)} ({', '.join(summary.skipped)})"]
        if summary.skipped
        else []
    )
    if summary.vepyr_default:
        vepyr_line = f"{vepyr_line} {DEFAULT_MARK}"
    lines = [
        HEADER,
        f"cache dir        : {summary.cache_dir or _NONE}",
        f"flavours         : {', '.join(summary.flavours)}",
        f"contigs requested: {requested}",
        f"vepyr            : {vepyr_line}",
        f"vepyr sha        : {summary.vepyr_resolved or _NONE}",
        f"targets          : {targets}",
        *skipped,
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
    lines += summary.freshness
    lines.append(
        f"outcome          : {summary.outcome.name.lower()} "
        f"(exit {int(summary.outcome)})"
    )
    if summary.detail:
        lines.append(f"detail           : {summary.detail}")
    return "\n".join(lines) + "\n"
