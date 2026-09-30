"""The ``./set_state`` label writer: move an issue or a PR along its lifecycle (#158).

Reads the current labels with ``gh <kind> view N --json labels``, refuses any
move that is not in :data:`TRANSITIONS` (or that starts from more than one
``state:*`` label), runs the ``./pr_status`` gate before a gated state (the
hand-over stage before ``state:manual-reviewing``, #177; the owner's stage before
``state:awaiting-merge``), then makes one ``gh <kind> edit`` call and reads the
labels back.

A PR move is mirrored to the PR's closing issues (#177 part 5): every closing
issue whose single ``state:*`` label is the PR's old state gets the same move,
all or nothing (issues first, then the PR; a failed edit undoes the ones already
made). ``--no-mirror`` moves the PR only. An issue enters the PR family only by
``./set_state issue N implementing``.

Exit codes: 0 done (or dry-run ok); 1 refused; 2 usage or tool error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
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
    # The issue enters the PR family only here (#177 part 5); then it mirrors its PR.
    Transition(Kind.ISSUE, None, f"{PREFIX}implementing"),
    Transition(Kind.ISSUE, f"{PREFIX}manual-reviewing-issue", f"{PREFIX}implementing"),
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
HANDOVER: Final = f"{PREFIX}manual-reviewing"
#: The gated PR states and the ``./pr_status`` stage each one runs first.
GATE_STAGES: Final[dict[str, gate.Stage]] = {HANDOVER: "handover", GATED: "owner"}


class ToolError(Exception):
    """Usage or ``gh`` failure (exit 2)."""


class Refusal(Exception):
    """A move the state machine does not allow (exit 1)."""


#: The states ``./set_state <kind>`` accepts as a target: an issue also takes the
#: PR family, which it reaches only through the legal edges (``implementing``).
TARGETS: Final[dict[Kind, tuple[str, ...]]] = {
    Kind.ISSUE: STATES[Kind.ISSUE] + STATES[Kind.PR],
    Kind.PR: STATES[Kind.PR],
}


def normalise(kind: Kind, state: str) -> str:
    """Accept a state with or without ``state:``; reject unknown/other-kind ones."""
    name = state if state.startswith(PREFIX) else f"{PREFIX}{state}"
    if name not in TARGETS[kind]:
        raise ToolError(
            f"{state!r} is not a {kind} state (one of {', '.join(TARGETS[kind])})"
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


def run_gate(
    number: int, repo: str | None, stage: gate.Stage = "owner"
) -> list[gate.Failure]:
    """The ``./pr_status`` gate at ``stage`` on the live PR; errors are ToolError."""
    try:
        return gate.evaluate(gate.fetch(number, repo), stage=stage)
    except gate.GateError as exc:
        raise ToolError(str(exc)) from exc


@contextmanager
def _gh_reads() -> Iterator[None]:
    """Map the read-only ``gate`` helpers' :class:`gate.GateError` to ToolError.

    ``gate._gh_json`` already turns undecodable and too deeply nested JSON
    (``RecursionError``) into ``GateError``, so no read here ends in a traceback.
    """
    try:
        yield
    except gate.GateError as exc:
        raise ToolError(str(exc)) from exc


def closing_issues(number: int, repo: str | None) -> list[int]:
    """The PR's closing issues as GitHub links them (``closingIssuesReferences``).

    Only closing keywords and manual "Development" links count, never a plain
    ``#N`` mention; a PR whose base is not the default branch has none.
    """
    where = ["--repo", repo] if repo else []
    with _gh_reads():
        doc = gate._gh_json(
            ["pr", "view", str(number), *where, "--json", "closingIssuesReferences"]
        )
    if not isinstance(doc, dict):
        raise ToolError("gh pr view printed no closingIssuesReferences object")
    refs = doc.get("closingIssuesReferences") or []
    try:
        return [int(ref["number"]) for ref in refs]
    except (TypeError, KeyError, ValueError) as exc:
        raise ToolError(f"gh pr view: bad closingIssuesReferences {refs!r}") from exc


def mirror_moves(
    move: Transition, number: int, repo: str | None
) -> list[tuple[int, Transition]]:
    """The issue edits that mirror the PR ``move``; refuse an out-of-sync issue.

    Per closing issue: already at ``move.new`` -> in sync, no edit; at
    ``move.old`` -> the same move; closed, unlabelled or an ``-issue`` label ->
    not opted in, one ``note:`` line on stderr; anything else -> Refusal.
    """
    where = ["--repo", repo] if repo else []
    moves: list[tuple[int, Transition]] = []
    for issue in closing_issues(number, repo):
        with _gh_reads():
            doc = gate._gh_json(
                ["issue", "view", str(issue), *where, "--json", "labels,state"]
            )
        if not isinstance(doc, dict):
            raise ToolError(f"gh issue view {issue} printed no object")
        names = [str(label.get("name", "")) for label in doc.get("labels") or []]
        states = [n for n in names if n.startswith(PREFIX)]
        if str(doc.get("state", "OPEN")).upper() == "CLOSED":
            print(f"note: issue {issue} not mirrored (closed)", file=sys.stderr)
            continue
        match states:
            case []:
                print(f"note: issue {issue} not mirrored ({NONE})", file=sys.stderr)
            case [current] if current in STATES[Kind.ISSUE]:
                print(f"note: issue {issue} not mirrored ({current})", file=sys.stderr)
            case [current] if current == move.new:
                pass  # in sync already (the first move after the issue entry edge)
            case [current] if current == move.old:
                moves.append((issue, Transition(Kind.ISSUE, move.old, move.new)))
            case _:
                raise Refusal(
                    f"issue {issue} is out of sync with pr {number}: state labels "
                    f"{states}, expected {move.old or NONE}; use --no-mirror"
                )
    return moves


def _undo(done: Sequence[tuple[int, Transition]], repo: str | None) -> list[str]:
    """Reverse the edits already made, newest first; return the ones that failed."""
    left: list[str] = []
    for item, move in reversed(done):
        try:
            gh(edit_args(item, Transition(move.kind, move.new, move.old), repo))
        except ToolError as exc:
            left.append(f"{move.kind} {item}: {exc}")
    return left


def apply(
    kind: Kind,
    number: int,
    new: str | None,
    repo: str | None,
    dry_run: bool,
    mirror: bool = True,
) -> int:
    """Check and perform one move (plus its mirror on the closing issues)."""
    move = plan(kind, state_labels(kind, number, repo), new)
    if (
        move.kind is Kind.PR
        and (stage := GATE_STAGES.get(move.new or "")) is not None
        and (failures := run_gate(number, repo, stage))
    ):
        for failure in failures:
            print(failure)
        flag = " --handover" if stage == "handover" else ""
        raise Refusal(f"./pr_status{flag} {number} is not READY")
    edits = mirror_moves(move, number, repo) if kind is Kind.PR and mirror else []
    edits.append((number, move))  # the issues first, the PR last
    if dry_run:
        for item, step in edits:
            print("DRY-RUN gh " + " ".join(edit_args(item, step, repo)))
        return DONE
    done: list[tuple[int, Transition]] = []
    for item, step in edits:
        try:
            gh(edit_args(item, step, repo))
        except ToolError as exc:
            if left := _undo(done, repo):
                raise ToolError(
                    f"{step.kind} {item}: {exc}; undo FAILED, fix by hand: "
                    + "; ".join(left)
                ) from exc
            raise ToolError(f"{step.kind} {item}: {exc}; nothing changed") from exc
        done.append((item, step))
    for item, step in edits:
        after = state_labels(step.kind, item, repo)
        expected = [step.new] if step.new else []
        if after != expected:
            raise ToolError(
                f"read-back: {step.kind} {item} state labels are {after}, "
                f"expected {expected}"
            )
    for item, step in edits:
        print(step if step is move else f"{step} (#{item})")
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
        usage="%(prog)s pr N STATE [--no-mirror] [--repo OWNER/REPO] [--dry-run]\n"
        "       %(prog)s issue N STATE [--repo OWNER/REPO] [--dry-run]\n"
        "       %(prog)s issue N --clear [--repo OWNER/REPO] [--dry-run]\n"
        "       %(prog)s --print-transitions",
        description="Move an issue or PR to STATE (with or without the 'state:' "
        "prefix) along the legal transitions; "
        "state:manual-reviewing needs ./pr_status --handover READY, "
        "state:awaiting-merge needs ./pr_status READY. "
        "A PR move is mirrored to every closing issue that carries the PR's old "
        "state (all or nothing); an issue enters the PR family only by "
        "'issue N implementing'. "
        "Exit 0 done, 1 refused, 2 usage or tool error.",
    )
    parser.add_argument("kind", nargs="?", help="pr or issue")
    parser.add_argument("number", nargs="?", help="issue or PR number")
    parser.add_argument("state", nargs="?", help="target state")
    parser.add_argument(
        "--clear", action="store_true", help="drop the issue's state label"
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="move the PR only; leave its closing issues as they are",
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
        if args.no_mirror and kind is not Kind.PR:
            raise ToolError("--no-mirror applies to a PR move only")
        return apply(
            kind,
            int(args.number),
            new,
            args.repo,
            args.dry_run,
            mirror=not args.no_mirror,
        )
    except Refusal as exc:
        print(f"set_state: refused: {exc}", file=sys.stderr)
        return REFUSED
    except ToolError as exc:
        print(f"set_state: {exc}", file=sys.stderr)
        return TOOL_ERROR
