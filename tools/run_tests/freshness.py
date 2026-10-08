"""The vepyr cache freshness guard of ``./run_tests`` (issue #236).

"Newest" means the commit the pin's ``ref`` (``main``) points to on the Hub right
now. For each selected flavour the guard compares that HEAD with the ``PINS.toml``
``sha`` (comparison (a)); when the run uses a cache root as-is (no fetch), a
selected flavour without a ``PROVENANCE.json`` record cannot be shown to be the
newest either (case (c)). Comparison (b), disk vs pin, stays with the fetch and the
precheck (exit 3).

The guard never changes what is fetched and never edits ``PINS.toml``: it only
decides whether the run may proceed. A stale pin, an unknown HEAD (Hub
unreachable) or case (c) fails closed with :attr:`Exit.STALE_CACHE` unless
``--old-vepyr-cache`` consents, in which case the summary records it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from run_tests import fetch
from run_tests.verdict import Exit, RunTestsError

__all__ = [
    "CONSENT_FLAG",
    "FlavourFreshness",
    "FreshnessReport",
    "check",
]

CONSENT_FLAG: Final[str] = "--old-vepyr-cache"
_UNKNOWN: Final[str] = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class FlavourFreshness:
    """What the guard established for one selected flavour."""

    flavour: str
    repo_id: str
    ref: str
    pinned: str
    head: str | None
    """The Hub HEAD of :attr:`ref`; ``None`` when it could not be resolved."""
    error: str | None = None
    """Why :attr:`head` is ``None`` (the resolver's exception, one line)."""
    no_provenance: bool = False
    """Case (c): the root is used as-is and has no ``PROVENANCE.json`` record here."""

    @property
    def old(self) -> bool:
        """Not provably the newest: HEAD differs, is unknown, or case (c)."""
        return self.head != self.pinned or self.no_provenance

    def describe(self) -> str:
        """One line: pinned vs HEAD sha and every reason the cache counts as old."""
        notes: list[str] = []
        if self.head is None:
            notes.append(f"freshness unknown: Hub unreachable ({self.error})")
        elif self.head != self.pinned:
            notes.append(f"newer commit on {self.ref}")
        if self.no_provenance:
            notes.append(
                f"no {fetch.PROVENANCE} record: freshness cannot be established"
            )
        tail = f" [{'; '.join(notes)}]" if notes else ""
        return (
            f"{self.flavour}: pinned {self.pinned}, HEAD({self.ref}) "
            f"{self.head or _UNKNOWN}{tail}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessReport:
    """The guard's verdict over every selected flavour."""

    flavours: tuple[FlavourFreshness, ...]
    consented: bool

    @property
    def old(self) -> bool:
        """At least one selected flavour is not provably the newest."""
        return any(f.old for f in self.flavours)

    def summary_lines(self) -> list[str]:
        """``old cache: no`` / ``old cache: YES (consented)`` plus per-flavour shas."""
        head = "old cache: YES (consented)" if self.old else "old cache: no"
        return [head, *(f"  {f.describe()}" for f in self.flavours)]

    def refusal(self) -> RunTestsError:
        """The :attr:`Exit.STALE_CACHE` error naming pinned vs HEAD shas."""
        stale = "; ".join(f.describe() for f in self.flavours if f.old)
        return RunTestsError(
            Exit.STALE_CACHE,
            f"vepyr cache is not provably the newest: {stale}. Bump PINS.toml "
            f"deliberately (separate PR) or pass {CONSENT_FLAG} to use the old cache.",
        )


def _recorded(root: Path) -> frozenset[str]:
    """Flavours with a ``PROVENANCE.json`` record; none when absent or unreadable."""
    try:
        provenance = fetch.read_provenance(root)
    except (RunTestsError, OSError, UnicodeDecodeError):
        return frozenset()
    return frozenset(provenance.datasets) if provenance is not None else frozenset()


def check(
    flavours: Sequence[fetch.Flavour],
    pins: Mapping[fetch.Flavour, fetch.DatasetPin],
    resolver: fetch.HeadResolver,
    *,
    root: Path,
    check_disk: bool,
    consented: bool,
) -> FreshnessReport:
    """Resolve each selected flavour's Hub HEAD once and judge the cache.

    Args:
        flavours: The ``--flavours`` selection; only these are queried.
        pins: Dataset pins from ``PINS.toml``.
        resolver: One call per selected flavour.
        root: The resolved cache root.
        check_disk: Apply case (c), i.e. the run uses ``root`` without fetching.
        consented: ``--old-vepyr-cache`` was given.

    Returns:
        The report; the caller refuses with :meth:`FreshnessReport.refusal` when it
        is :attr:`~FreshnessReport.old` and not :attr:`~FreshnessReport.consented`.
    """
    recorded = _recorded(root) if check_disk else frozenset()
    results: list[FlavourFreshness] = []
    for flavour in flavours:
        pin = pins[flavour]
        head: str | None
        error: str | None = None
        try:
            head = resolver(pin.repo_id, pin.ref)
        except Exception as exc:  # any failure means "unknown": fail closed
            head, error = None, f"{type(exc).__name__}: {exc}".splitlines()[0]
        results.append(
            FlavourFreshness(
                flavour=flavour.value,
                repo_id=pin.repo_id,
                ref=pin.ref,
                pinned=pin.revision,
                head=head,
                error=error,
                no_provenance=check_disk and flavour.value not in recorded,
            )
        )
    return FreshnessReport(flavours=tuple(results), consented=consented)
