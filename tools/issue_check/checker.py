"""Structural validation of a GitHub issue body.

Two properties are checked, in this order:

1. the body carries a heading whose text matches :data:`ACCEPTANCE_HEADING`
   (``/acceptance criteria/i``);
2. that section lists at least one numbered item, and *every* numbered item carries
   a backticked command — either an inline code span or a fenced block.

Rule 2 is what makes the acceptance criteria falsifiable: a prose criterion cannot be
run, so it cannot fail, so it is not a criterion. Headings and numbered items inside
fenced code blocks are ignored, so a body that quotes a template does not pass by
accident.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from issue_check.verdict import Exit, IssueCheckError

__all__ = [
    "ACCEPTANCE_HEADING",
    "Criterion",
    "Section",
    "check",
    "sections",
]

ACCEPTANCE_HEADING: Final[re.Pattern[str]] = re.compile(
    r"acceptance\s+criteria", re.IGNORECASE
)
"""Heading text that opens the acceptance-criteria section."""

_HEADING: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<text>.+?)\s*#*$")
_FENCE: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_ITEM: Final[re.Pattern[str]] = re.compile(
    r"^\s{0,3}(?P<ordinal>\d+)[.)]\s+(?P<rest>.*)$"
)
_INLINE_CODE: Final[re.Pattern[str]] = re.compile(r"`[^`\n]+`")


@dataclass(frozen=True, slots=True, kw_only=True)
class Criterion:
    """One numbered acceptance criterion and the lines that belong to it."""

    ordinal: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        """The criterion's lines joined back together."""
        return "\n".join(self.lines)

    @property
    def has_command(self) -> bool:
        """Whether the criterion carries a backticked command."""
        return bool(_INLINE_CODE.search(self.text)) or any(
            _FENCE.match(line) for line in self.lines
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    """One markdown section: its heading text and its body lines (fences intact)."""

    heading: str
    lines: tuple[str, ...]

    def criteria(self) -> tuple[Criterion, ...]:
        """The numbered items of this section, code fences excluded."""
        return tuple(_criteria(self.lines))


def _outside_fences(lines: Sequence[str]) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(index, line, in_fence)`` tracking fenced code blocks.

    ``in_fence`` is true for the fence delimiters themselves as well as for the lines
    between them, so callers can skip every fenced line with a single test.
    """
    fence: str | None = None
    for index, line in enumerate(lines):
        match _FENCE.match(line):
            case None:
                yield index, line, fence is not None
            case marker if fence is None:
                fence = marker.group("fence")[0]
                yield index, line, True
            case marker if marker.group("fence")[0] == fence:
                fence = None
                yield index, line, True
            case _:
                yield index, line, True


def sections(body: str) -> tuple[Section, ...]:
    """Split ``body`` into sections (text before the first heading is dropped)."""
    lines = body.splitlines()
    found: list[Section] = []
    current: tuple[str, list[str]] | None = None
    for _, line, in_fence in _outside_fences(lines):
        heading = None if in_fence else _HEADING.match(line)
        if heading is None:
            if current is not None:
                current[1].append(line)
            continue
        if current is not None:
            found.append(Section(heading=current[0], lines=tuple(current[1])))
        current = (heading.group("text"), [])
    if current is not None:
        found.append(Section(heading=current[0], lines=tuple(current[1])))
    return tuple(found)


def _criteria(lines: Sequence[str]) -> Iterator[Criterion]:
    """Group ``lines`` into numbered items; continuation lines stay with their item."""
    ordinal: str | None = None
    buffer: list[str] = []
    for _, line, in_fence in _outside_fences(lines):
        item = None if in_fence else _ITEM.match(line)
        if item is not None:
            if ordinal is not None:
                yield Criterion(ordinal=ordinal, lines=tuple(buffer))
            ordinal, buffer = item.group("ordinal"), [item.group("rest")]
        elif ordinal is not None:
            buffer.append(line)
    if ordinal is not None:
        yield Criterion(ordinal=ordinal, lines=tuple(buffer))


def check(body: str, *, origin: str) -> Section:
    """Validate ``body``; return the acceptance-criteria section when it is compliant.

    Args:
        body: the raw issue body, as ``gh issue view --json body`` hands it over.
        origin: what to name in error messages (the ``--body-file`` path, or the issue).

    Raises:
        IssueCheckError: with :attr:`Exit.INVALID` and a message naming the missing or
            unrunnable part.
    """
    if not body.strip():
        raise IssueCheckError(Exit.INVALID, f"{origin}: issue body is empty")
    found = sections(body)
    acceptance = next(
        (s for s in found if ACCEPTANCE_HEADING.search(s.heading)), None
    )
    if acceptance is None:
        seen = ", ".join(repr(s.heading) for s in found) or "no headings at all"
        raise IssueCheckError(
            Exit.INVALID,
            f"{origin}: missing required section 'Acceptance criteria' "
            f"(headings found: {seen})",
        )
    criteria = acceptance.criteria()
    if not criteria:
        raise IssueCheckError(
            Exit.INVALID,
            f"{origin}: section {acceptance.heading!r} has no numbered acceptance "
            "criteria (expected '1. …' items, each with a backticked command)",
        )
    prose = [c for c in criteria if not c.has_command]
    if prose:
        listed = ", ".join(f"#{c.ordinal}" for c in prose)
        raise IssueCheckError(
            Exit.INVALID,
            f"{origin}: acceptance criteria without a backticked command: {listed} "
            "— every criterion must be verifiable by a command, not by prose",
        )
    return acceptance
