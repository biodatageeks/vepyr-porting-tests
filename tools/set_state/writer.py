"""The ``./set_state`` label writer: move an issue or a PR along its lifecycle (#158).

Reads the current labels with ``gh <kind> view N --json labels``, refuses any
move that is not in :data:`TRANSITIONS` (or that starts from more than one
``state:*`` label), runs the ``./pr_status`` gate before ``state:awaiting-merge``,
then makes exactly one ``gh <kind> edit`` call and reads the labels back.

Exit codes: 0 done (or dry-run ok); 1 refused; 2 usage or tool error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from pr_status import gate

DONE: Final = 0
REFUSED: Final = 1
TOOL_ERROR: Final = 2

PREFIX: Final = "state:"
NONE: Final = "(none)"


class Kind(StrEnum):
    """What carries the label: a pull request or an issue."""

    PR = "pr"
    ISSUE = "issue"


STATES: Final[dict[Kind, tuple[str, ...]]] = {
    Kind.ISSUE: tuple(
        f"{PREFIX}{s}-issue"
        for s in ("implementing", "auto-reviewing", "fixing", "manual-reviewing")
    ),
    Kind.PR: tuple(
        f"{PREFIX}{s}"
        for s in (
            "implementing",
            "auto-reviewing",
            "fixing",
            "auto-superreviewing",
            "manual-reviewing",
            "awaiting-merge",
        )
    ),
}


@dataclass(frozen=True, slots=True)
class Transition:
    """One legal edge; ``None`` stands for "no state label"."""

    kind: Kind
    old: str | None
    new: str | None

    def __str__(self) -> str:
        return f"{self.kind} {self.old or NONE} -> {self.new or NONE}"


def _edges(kind: Kind, pairs: str) -> list[Transition]:
    """Parse ``"a>b c>d"`` (short names, ``-`` for none) into transitions."""
    suffix = "-issue" if kind is Kind.ISSUE else ""

    def full(short: str) -> str | None:
        return None if short == "-" else f"{PREFIX}{short}{suffix}"

    return [
        Transition(kind, full(a), full(b))
        for a, b in (p.split(">") for p in pairs.split())
    ]


#: The only legal moves, in the order ``--print-transitions`` prints them.
TRANSITIONS: Final[tuple[Transition, ...]] = (
    *_edges(
        Kind.ISSUE,
        "->implementing implementing>auto-reviewing auto-reviewing>fixing "
        "auto-reviewing>manual-reviewing fixing>auto-reviewing manual-reviewing>fixing",
    ),
    *_edges(
        Kind.PR,
        "->implementing implementing>auto-reviewing auto-reviewing>fixing "
        "auto-reviewing>auto-superreviewing auto-reviewing>manual-reviewing "
        "fixing>auto-reviewing auto-superreviewing>fixing "
        "auto-superreviewing>manual-reviewing "
        "manual-reviewing>fixing manual-reviewing>awaiting-merge awaiting-merge>fixing",
    ),
    *_edges(Kind.ISSUE, "manual-reviewing>-"),
)
GATED: Final = f"{PREFIX}awaiting-merge"


class ToolError(Exception):
    """Usage or ``gh`` failure (exit 2)."""


class Refusal(Exception):
    """A move the state machine does not allow (exit 1)."""


def normalise(kind: Kind, state: str) -> str:
    """Accept a state with or without ``state:``; reject unknown/other-kind ones."""
    name = state if state.startswith(PREFIX) else f"{PREFIX}{state}"
    if name not in STATES[kind]:
        raise ToolError(
            f"{state!r} is not a {kind} state (one of {', '.join(STATES[kind])})"
        )
    return name


def plan(kind: Kind, current: Sequence[str], new: str | None) -> Transition:
    """Return the legal move from the ``current`` state labels to ``new``, or refuse."""
    if len(current) > 1:
        raise Refusal(
            f"{len(current)} state labels ({', '.join(current)}); at most one allowed"
        )
    move = Transition(kind, current[0] if current else None, new)
    if move not in TRANSITIONS:
        raise Refusal(f"illegal transition: {move}")
    return move


def edit_args(number: int, move: Transition, repo: str | None) -> list[str]:
    """The one ``gh <kind> edit`` call for ``move`` (without the leading ``gh``)."""
    args = [str(move.kind), "edit", str(number), *(["--repo", repo] if repo else [])]
    if move.new is not None:
        args += ["--add-label", move.new]
    if move.old is not None:
        args += ["--remove-label", move.old]
    return args


def gh(args: Sequence[str]) -> str:
    """Run ``gh``; a non-zero exit or a missing binary is a :class:`ToolError`."""
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise ToolError(f"gh not runnable: {exc}") from exc
    if done.returncode != 0:
        raise ToolError(
            f"gh {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}"
        )
    return done.stdout


def state_labels(kind: Kind, number: int, repo: str | None) -> list[str]:
    """The ``state:*`` labels currently on the issue or PR."""
    out = gh(
        [
            str(kind),
            "view",
            str(number),
            *(["--repo", repo] if repo else []),
            "--json",
            "labels",
        ]
    )
    try:
        labels: Any = json.loads(out).get("labels") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ToolError(f"gh {kind} view printed no labels object") from exc
    return [
        n
        for n in (str(label.get("name", "")) for label in labels)
        if n.startswith(PREFIX)
    ]


def run_gate(number: int, repo: str | None) -> list[gate.Failure]:
    """The ``./pr_status`` gate on the live PR; tool errors become ToolError."""
    try:
        return gate.evaluate(gate.fetch(number, repo))
    except gate.GateError as exc:
        raise ToolError(str(exc)) from exc


def apply(
    kind: Kind, number: int, new: str | None, repo: str | None, dry_run: bool
) -> int:
    """Check and perform one move; prints what it did."""
    move = plan(kind, state_labels(kind, number, repo), new)
    if move.new == GATED and (failures := run_gate(number, repo)):
        for failure in failures:
            print(failure)
        raise Refusal(f"./pr_status {number} is not READY")
    args = edit_args(number, move, repo)
    if dry_run:
        print("DRY-RUN gh " + " ".join(args))
        return DONE
    gh(args)
    after = state_labels(kind, number, repo)
    expected = [move.new] if move.new else []
    if after != expected:
        raise ToolError(f"read-back: state labels are {after}, expected {expected}")
    print(move)
    return DONE


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        print(f"set_state: {message}", file=sys.stderr)
        raise SystemExit(TOOL_ERROR)


def build_parser() -> argparse.ArgumentParser:
    """The CLI described in ``./set_state --help``."""
    parser = _Parser(
        prog="set_state",
        usage="%(prog)s (pr|issue) N STATE [--repo OWNER/REPO] [--dry-run]\n"
        "       %(prog)s issue N --clear [--repo OWNER/REPO] [--dry-run]\n"
        "       %(prog)s --print-transitions",
        description="Move an issue or PR to STATE (with or without the 'state:' "
        "prefix) along the legal transitions; "
        "state:awaiting-merge also needs ./pr_status READY. "
        "Exit 0 done, 1 refused, 2 usage or tool error.",
    )
    parser.add_argument("kind", nargs="?", help="pr or issue")
    parser.add_argument("number", nargs="?", help="issue or PR number")
    parser.add_argument("state", nargs="?", help="target state")
    parser.add_argument(
        "--clear", action="store_true", help="drop the issue's state label"
    )
    parser.add_argument(
        "--repo", metavar="OWNER/REPO", help="passed to gh; default: the clone's repo"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the edit, make none"
    )
    parser.add_argument(
        "--print-transitions", action="store_true", help="print the legal moves"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``./set_state``."""
    args = build_parser().parse_args(argv)
    if args.print_transitions:
        print("\n".join(map(str, TRANSITIONS)))
        return DONE
    try:
        try:
            kind = Kind(args.kind)
        except ValueError as exc:
            raise ToolError(f"kind must be pr or issue, got {args.kind!r}") from exc
        if not (args.number or "").isdigit():
            raise ToolError(f"N must be numeric, got {args.number!r}")
        if args.clear == (args.state is not None):
            raise ToolError("give exactly one of STATE and --clear")
        new = None if args.clear else normalise(kind, args.state)
        return apply(kind, int(args.number), new, args.repo, args.dry_run)
    except Refusal as exc:
        print(f"set_state: refused: {exc}", file=sys.stderr)
        return REFUSED
    except ToolError as exc:
        print(f"set_state: {exc}", file=sys.stderr)
        return TOOL_ERROR
