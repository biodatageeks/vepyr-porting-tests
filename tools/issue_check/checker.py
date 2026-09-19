"""Structural validation of a GitHub issue body.

Two properties are checked, in this order:

1. the body carries a heading whose text matches :data:`ACCEPTANCE_HEADING`
   (``/acceptance criteria/i``);
2. that section lists at least one numbered item, and *every* numbered item is
   command-verifiable.

Rule 2 is what makes the acceptance criteria falsifiable: a prose criterion cannot be
run, so it cannot fail, so it is not a criterion. Headings and numbered items inside
fenced code blocks are ignored, so a body that quotes a template does not pass by
accident.

What counts as command-verifiable
---------------------------------

A backticked span *somewhere* in the item is not enough: ``1. Reviewers agree the
`PINS.toml` wording is clear.`` is prose that happens to name a file. A criterion is
command-verifiable when either

* an **inline code span on the item's lead line or on one of its indented
  continuation lines** looks like an invocation — the indented half is what lets a
  lead-in ("both gates are green:") carry its commands in a nested sub-list, or
* a **fenced block belonging to the item** carries a line that looks like one.

A *flush-left* continuation never contributes, so unrelated prose that trails the last
item cannot satisfy it — and an item ends at a blank line followed by a non-indented,
non-item line (GFM loose-list rules), so such prose is not even part of the item.

A line or span "looks like an invocation" (:func:`looks_like_command`) when it is
**command-shaped**: a path-like head (``./x``, ``../x``, ``/usr/bin/x``, with or
without arguments), or a known runner (:data:`RUNNERS` — ``uv``, ``cargo``, ``gh``,
``git``, ``grep``, ``pytest``, ``test``, ``python``, …) *followed by at least one
argument, flag or path*, or an explicit numeric exit-code assertion (``exits 0``,
``→ exit 1``). A lone runner noun (``find``, ``diff``, ``exit code``) is prose and is
rejected. The runner list is deliberately small, explicit and free of bare English
nouns: a rule nobody can predict is worse than a rule that occasionally asks the
author to write the command out.

Nesting follows GFM columns, not a fixed one: a fenced block or a numbered sub-item
indented to the parent's content column (three spaces under ``1. ``, or more, tabs
included) belongs to that item instead of becoming a criterion of its own.

``<!-- … -->`` comments are stripped before any of this: what GitHub does not render
cannot make a criterion runnable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from issue_check.verdict import Exit, IssueCheckError

__all__ = [
    "ACCEPTANCE_HEADING",
    "RUNNERS",
    "Criterion",
    "Section",
    "check",
    "looks_like_command",
    "sections",
    "strip_html_comments",
]

ACCEPTANCE_HEADING: Final[re.Pattern[str]] = re.compile(
    r"acceptance\s+criteria", re.IGNORECASE
)
"""Heading text that opens the acceptance-criteria section."""

_HEADING: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<text>.+?)\s*#*$")
_FENCE: Final[re.Pattern[str]] = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
_INDENTED_FENCE: Final[re.Pattern[str]] = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
"""Fences inside a list item: the continuation indent is the item's, not column 0-3."""

_ITEM: Final[re.Pattern[str]] = re.compile(
    r"^(?P<indent>\s{0,3})(?P<ordinal>\d+)[.)]\s+(?P<rest>.*)$"
)
_INLINE_CODE: Final[re.Pattern[str]] = re.compile(r"`(?P<code>[^`\n]+)`")
_HTML_COMMENT: Final[re.Pattern[str]] = re.compile(r"<!--.*?-->", re.DOTALL)

RUNNERS: Final[frozenset[str]] = frozenset(
    {
        "awk",
        "bash",
        "cargo",
        "curl",
        "docker",
        "gh",
        "git",
        "grep",
        "jq",
        "make",
        "npm",
        "npx",
        "pytest",
        "python",
        "python3",
        "rg",
        "ruff",
        "sed",
        "sh",
        "test",
        "uv",
        "uvx",
    }
)
"""Executables a criterion may start with without spelling out a path.

Deliberately free of words that are ordinary English nouns or adverbs on their own
(``cat``, ``diff``, ``env``, ``find``, ``just``, ``ls``): a one-word span made of one
of those reads as prose far more often than as an invocation, and spelling out
``./x`` or a longer pipeline costs the author nothing.
"""

_PROMPT: Final[re.Pattern[str]] = re.compile(r"^[$>]\s+")
_EXIT_ASSERTION: Final[re.Pattern[str]] = re.compile(
    r"\bexit(?:s|ed)?\s+(?:code\s+)?\d+\b", re.IGNORECASE
)
"""An explicit exit-code assertion: ``exit 0``, ``exits 1``, ``→ exit code 2``.

The status has to be spelled as a number. ``exit code`` on its own is the English
noun phrase, not an assertion, and must not satisfy the gate.
"""

_PATHISH: Final[re.Pattern[str]] = re.compile(r"^(?:\./|\.\./|/)[\w./+-]")


def looks_like_command(text: str) -> bool:
    """Whether ``text`` reads as a runnable invocation rather than prose.

    The text must be *command-shaped*, which means one of:

    * a path-like head (``./run_tests``, ``/usr/bin/env …``) — a path to an
      executable is an invocation with or without arguments;
    * a :data:`RUNNERS` head followed by at least one argument, flag or path — a bare
      runner noun (``find``, ``diff``, ``test``) is not enough;
    * an explicit exit-code assertion (:data:`_EXIT_ASSERTION`), e.g. ``exits 0`` or
      ``→ exit 1``, which names a runnable outcome even when the head is quoted.

    >>> looks_like_command("uv run pytest -q")
    True
    >>> looks_like_command("./run_tests")
    True
    >>> looks_like_command("find")
    False
    >>> looks_like_command("exit code")
    False
    >>> looks_like_command("PINS.toml")
    False
    """
    stripped = _PROMPT.sub("", text.strip())
    if not stripped:
        return False
    if _EXIT_ASSERTION.search(stripped):
        return True
    head, _, rest = stripped.partition(" ")
    if _PATHISH.match(head):
        return True
    return head in RUNNERS and bool(rest.strip())


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
        """Whether the criterion is command-verifiable.

        Inline code spans count on the item's own first line and on its **indented**
        continuation lines — which is where a nested sub-list ("both gates are green:"
        followed by two indented sub-items) puts the commands. Fenced blocks belonging
        to the item count wherever they sit. Unindented lazy continuation prose does
        not, which is what keeps a backticked noun in trailing prose out.
        """
        if any(
            looks_like_command(match.group("code"))
            for line in self.span_lines
            for match in _INLINE_CODE.finditer(line)
        ):
            return True
        return any(looks_like_command(line) for line in self.fenced_lines)

    @property
    def span_lines(self) -> tuple[str, ...]:
        """Lines whose inline code spans may satisfy the criterion.

        The lead line plus every indented continuation line (sub-items, wrapped
        sub-item text) — never a flush-left continuation.
        """
        return tuple(
            line
            for index, line in enumerate(self.lines)
            if index == 0 or line[:1].isspace()
        )

    @property
    def fenced_lines(self) -> tuple[str, ...]:
        """The content lines of every fenced block inside this criterion."""
        return tuple(
            line
            for _, line, in_fence in _outside_fences(self.lines, fence=_INDENTED_FENCE)
            if in_fence and not _INDENTED_FENCE.match(line)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    """One markdown section: its heading text and its body lines (fences intact)."""

    heading: str
    lines: tuple[str, ...]

    def criteria(self) -> tuple[Criterion, ...]:
        """The numbered items of this section, code fences excluded."""
        return tuple(_criteria(self.lines))


def _outside_fences(
    lines: Sequence[str], *, fence: re.Pattern[str] = _FENCE
) -> Iterator[tuple[int, str, bool]]:
    """Yield ``(index, line, in_fence)`` tracking fenced code blocks.

    ``in_fence`` is true for the fence delimiters themselves as well as for the lines
    between them, so callers can skip every fenced line with a single test. ``fence``
    selects how much leading indentation opens a fence: :data:`_FENCE` at top level,
    :data:`_INDENTED_FENCE` inside a list item.
    """
    open_marker: str | None = None
    for index, line in enumerate(lines):
        match fence.match(line):
            case None:
                yield index, line, open_marker is not None
            case marker if open_marker is None:
                open_marker = marker.group("fence")[0]
                yield index, line, True
            case marker if marker.group("fence")[0] == open_marker:
                open_marker = None
                yield index, line, True
            case _:
                yield index, line, True


def strip_html_comments(body: str) -> str:
    """Remove ``<!-- … -->`` comments, keeping every line break they spanned.

    GitHub renders nothing for them, so a command hidden in a comment must not satisfy
    the gate. Newlines are preserved so that line-based structure (items, fences,
    blank-line item boundaries) is unaffected by the removal.
    """
    return _HTML_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), body)


def sections(body: str) -> tuple[Section, ...]:
    """Split ``body`` into sections (text before the first heading is dropped).

    HTML comments are stripped first: what GitHub does not render cannot be a
    criterion's command.
    """
    lines = strip_html_comments(body).splitlines()
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


def _indent_width(item: re.Match[str]) -> int:
    """Visual indentation of a matched list item, tabs expanded to four columns."""
    return len(item.group("indent").expandtabs(4))


def _criteria(lines: Sequence[str]) -> Iterator[Criterion]:
    """Group ``lines`` into numbered items.

    An item owns its indented continuation lines, including fenced blocks at any
    indentation. It **ends** at a blank line followed by a non-indented line that is
    not itself an item (GFM loose-list rules), so prose trailing the list belongs to
    nobody — it cannot make the last criterion look verifiable.

    A numbered line indented to (or past) the open item's *content* column is a
    **nested sub-item**, not a sibling: under ``1. `` the content column is 3, so
    ``   1. `` and ``    1. `` alike belong to criterion 1 and are never promoted to
    top-level criteria — which would otherwise report duplicate ordinals.
    """
    ordinal: str | None = None
    content_column = 0
    buffer: list[str] = []
    blanks: list[str] = []

    def flush() -> Iterator[Criterion]:
        nonlocal ordinal, buffer, blanks
        if ordinal is not None:
            yield Criterion(ordinal=ordinal, lines=tuple(buffer))
        ordinal, buffer, blanks = None, [], []

    for _, line, in_fence in _outside_fences(lines, fence=_INDENTED_FENCE):
        item = None if in_fence else _ITEM.match(line)
        if item is not None and ordinal is not None and _indent_width(item) >= (
            content_column
        ):
            item = None  # nested sub-item: a continuation of the open criterion
        if item is not None:
            yield from flush()
            ordinal, buffer = item.group("ordinal"), [item.group("rest")]
            content_column = _indent_width(item) + (
                item.start("rest") - item.start("ordinal")
            )
        elif ordinal is None:
            continue
        elif not in_fence and not line.strip():
            blanks.append(line)
        elif in_fence or line[:1].isspace():
            buffer.extend(blanks)
            blanks.clear()
            buffer.append(line)
        elif blanks:  # blank line, then flush-left prose: the list is over
            yield from flush()
        else:  # lazy continuation of the current item
            buffer.append(line)
    yield from flush()


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
