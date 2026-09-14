"""The end-of-run summary block ``./run_tests`` prints before it exits.

Every flag and effective parameter of the invocation, the cache directory, contigs
accumulated on disk, discovered data-test targets, and the resolved ``--vepyr`` sha
when an engine override ran.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests.verdict import Exit

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
WHOLE_GENOME: Final[str] = "ALL (whole genome)"
NEVER_FETCHED: Final[str] = "(never fetched)"
UNREADABLE: Final[str] = "(PROVENANCE.json unreadable)"
_NONE: Final[str] = "(none)"


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
    targets: tuple[str, ...] = ()


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
    if summary.vepyr_resolved:
        vepyr_line = f"{vepyr_line} ({summary.vepyr_resolved[:12]})"
    lines = [
        HEADER,
        f"cache dir        : {summary.cache_dir or _NONE}",
        f"flavours         : {', '.join(summary.flavours)}",
        f"contigs requested: {requested}",
        f"vepyr            : {vepyr_line}",
        f"targets          : {targets}",
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
