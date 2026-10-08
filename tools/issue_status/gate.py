"""The ``./issue_status`` gate: may an issue be handed over to the owner (#178)?

The issue-side counterpart of ``./pr_status`` (#158). One JSON document
describes the issue: the output of ``gh issue view N --json
number,body,labels,comments``. Real mode builds it with one ``gh`` read;
fixture mode (``--from-json``) loads it from a file. Both feed
:func:`evaluate`, which returns one :class:`Failure` per failed check. The tool
only reads: it never changes a label, a comment or the body.

Two comments carry the record, each identified by its first non-empty line and
holding one fenced ``json`` block:

- exactly one sticky ``### issue-status:v1`` (the writer's AC table:
  ``v``, ``issue``, ``body_sha256``, ``master``, ``issue_check``, ``stale``,
  ``ac[{id, cmd, master_exit, expected, regression_guard}]``);
- one ``### issue-review:v1`` per fresh review (``v``, ``role`` =
  ``issue-review``, ``model``, ``verdict`` ``CLEAN``/``FINDINGS``,
  ``body_sha256``, ``master``, ``issue_check``, ``ac_dry_run[{id, exit}]``).

A record is pinned to the body by ``body_sha256``: the sha256 of the UTF-8
bytes of the ``body`` string ``gh issue view N --json body`` returns. A verdict
for any other body never counts. Verdicts are ordered by ``createdAt``; on equal
timestamps the comment later in ``comments`` is the later one (stable sort). A
status comment without a valid block is ``status-missing`` (exit 1); a verdict
comment without a valid, schema-conforming block is exit 2 naming the comment
(a pre-#178 review is re-headed ``### issue-review-legacy:v0``). The parsing
helpers and the exit codes are those of :mod:`pr_status.gate`;
``./pr_status`` itself is unchanged.

Exit codes: 0 ready, 1 not ready, 2 usage or tool error.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pr_status.gate import (
    READY,
    TOOL_ERROR,
    GateError,
    _fail,
    _gh_json,
    _names,
    _want_bool,
    _want_dicts,
    _want_int,
    _want_str,
    first_line,
    json_block,
    load,
    report,
)

__all__ = [
    "READY",
    "STATUS_MARKER",
    "TOOL_ERROR",
    "VERDICT_MARKER",
    "Check",
    "Failure",
    "GateError",
    "Verdict",
    "body_sha256",
    "evaluate",
    "fetch",
    "main",
]

STATUS_MARKER: Final = "### issue-status:v1"
VERDICT_MARKER: Final = "### issue-review:v1"
#: The heading a pre-#178 review comment is re-headed to; the gate ignores it.
LEGACY_MARKER: Final = "### issue-review-legacy:v0"
ISSUE_VIEW_FIELDS: Final = "number,body,labels,comments"
ROLE: Final = "issue-review"
CLEAN: Final = "CLEAN"
FINDINGS: Final = "FINDINGS"
#: Both accepted, so the gate can pass *before* the hand-over move (#177's
#: circular ``state-label`` rule is avoided by design).
GATE_STATES: Final = frozenset(
    {"state:auto-reviewing-issue", "state:manual-reviewing-issue"}
)

_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_GIT_SHA: Final = re.compile(r"[0-9a-f]{40}")


class Check(StrEnum):
    """The check ids; each is also the name of its failing fixture."""

    STATUS_MISSING = "status-missing"
    STATUS_DUPLICATE = "status-duplicate"
    STATUS_STALE = "status-stale"
    ISSUE_CHECK = "issue-check"
    AC_PASSES_ON_MASTER = "ac-passes-on-master"
    VERDICT_MISSING = "verdict-missing"
    VERDICT_FINDINGS = "verdict-findings"
    DRY_RUN_MISMATCH = "dry-run-mismatch"
    STATE_LABEL = "state-label"


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed check, printed as ``FAIL <check>: <reason>``."""

    check: Check
    reason: str

    def __str__(self) -> str:
        return f"FAIL {self.check}: {self.reason}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """One ``### issue-review:v1`` verdict and when it was posted."""

    model: str
    verdict: str
    body_sha256: str
    master: str
    dry_run: Mapping[int, int]
    at: str


def body_sha256(body: str) -> str:
    """The sha256 (hex) of the UTF-8 bytes of ``body``, nothing appended."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- input schema: every field the gate reads is type-checked (fail closed) ---


def _want_hex(
    obj: Mapping[str, Any], key: str, where: str, pattern: re.Pattern[str]
) -> str:
    value = _want_str(obj, key, where)
    if not pattern.fullmatch(value):
        _fail(
            where, f"{key!r} must be lowercase hex of {pattern.pattern}, got {value!r}"
        )
    return value


def _want_version(data: Mapping[str, Any], where: str) -> None:
    if _want_int(data, "v", where) != 1:
        _fail(where, f"unsupported version {data['v']}")


def validate_doc(doc: Mapping[str, Any]) -> None:
    """Type-check the issue document (``body``, ``labels``, ``comments``)."""
    if not isinstance(doc.get("body"), str):
        _fail("document", f"'body' must be a string, got {doc.get('body')!r}")
    for i, label in enumerate(_want_dicts(doc, "labels", "document")):
        _want_str(label, "name", f"labels[{i}]")
    for i, comment in enumerate(_want_dicts(doc, "comments", "document")):
        where = f"comments[{i}]"
        if not isinstance(comment.get("body"), str):
            _fail(where, f"'body' must be a string, got {comment.get('body')!r}")
        _want_str(comment, "createdAt", where)


def validate_status(data: Mapping[str, Any]) -> None:
    """Type-check the status json block and its ``ac`` rows (unique ids)."""
    where = STATUS_MARKER
    _want_version(data, where)
    _want_int(data, "issue", where)
    _want_hex(data, "body_sha256", where, _SHA256)
    _want_hex(data, "master", where, _GIT_SHA)
    _want_int(data, "issue_check", where)
    _want_bool(data, "stale", where)
    seen: set[int] = set()
    for i, row in enumerate(_want_dicts(data, "ac", where)):
        at = f"{where} ac[{i}]"
        if (row_id := _want_int(row, "id", at)) in seen:
            _fail(at, f"duplicate AC id {row_id}")
        seen.add(row_id)
        _want_str(row, "cmd", at)
        _want_int(row, "master_exit", at)
        _want_int(row, "expected", at)
        _want_bool(row, "regression_guard", at)


def validate_verdict(data: Mapping[str, Any], where: str) -> None:
    """Type-check one verdict block; an unknown ``verdict`` word is not CLEAN."""
    _want_version(data, where)
    if _want_str(data, "role", where) != ROLE:
        _fail(where, f"'role' must be {ROLE!r}")
    _want_str(data, "model", where)
    _want_str(data, "verdict", where)
    _want_hex(data, "body_sha256", where, _SHA256)
    _want_hex(data, "master", where, _GIT_SHA)
    _want_int(data, "issue_check", where)
    seen: set[int] = set()
    for i, run in enumerate(_want_dicts(data, "ac_dry_run", where)):
        at = f"{where} ac_dry_run[{i}]"
        if (run_id := _want_int(run, "id", at)) in seen:
            _fail(at, f"duplicate AC id {run_id}")
        seen.add(run_id)
        _want_int(run, "exit", at)


def _bodies(doc: Mapping[str, Any], marker: str) -> list[tuple[str, str]]:
    """``(body, createdAt)`` of every comment whose first line is ``marker``."""
    return [
        (c["body"], c["createdAt"])
        for c in doc["comments"]
        if first_line(c["body"]) == marker
    ]


def _verdict_where(comment: Mapping[str, Any]) -> str:
    """Name a verdict comment so the user can find it: its time and its URL."""
    url = comment.get("url")
    return f"verdict posted {comment['createdAt']}" + (
        f" ({url})" if isinstance(url, str) and url else ""
    )


def collect_verdicts(doc: Mapping[str, Any]) -> list[Verdict]:
    """All verdicts, oldest first; a verdict without a valid block is exit 2.

    Ties on ``createdAt`` keep the comments' order (a stable sort), so with equal
    timestamps the comment later in the list counts as the later verdict. A
    comment headed ``### issue-review:v1`` that does not follow this schema (e.g.
    a review written before #178) is a tool error naming that comment: re-head it
    as :data:`LEGACY_MARKER` or delete it.
    """
    verdicts: list[Verdict] = []
    for comment in doc["comments"]:
        body, at = comment["body"], comment["createdAt"]
        if first_line(body) != VERDICT_MARKER:
            continue
        where = _verdict_where(comment)
        try:
            data = json_block(body)
            if not isinstance(data, dict):
                _fail(where, "no valid json object block")
            validate_verdict(data, where)
        except GateError as exc:
            raise GateError(
                f"{exc}; if this is a review written before #178, change its first "
                f"line to {LEGACY_MARKER} (or delete it)"
            ) from exc
        verdicts.append(
            Verdict(
                model=data["model"],
                verdict=data["verdict"],
                body_sha256=data["body_sha256"],
                master=data["master"],
                dry_run={r["id"]: r["exit"] for r in data["ac_dry_run"]},
                at=at,
            )
        )
    return sorted(verdicts, key=lambda v: v.at)


def latest(verdicts: Sequence[Verdict], body_sha: str) -> Verdict | None:
    """The latest verdict for the body ``body_sha``; older bodies never count."""
    return next((v for v in reversed(verdicts) if v.body_sha256 == body_sha), None)


def _status(doc: Mapping[str, Any]) -> tuple[list[Failure], dict[str, Any] | None]:
    bodies = _bodies(doc, STATUS_MARKER)
    match bodies:
        case []:
            return [
                Failure(
                    Check.STATUS_MISSING,
                    f"no issue comment starts with {STATUS_MARKER}",
                )
            ], None
        case [(body, _)]:
            data = json_block(body)
            if not isinstance(data, dict):
                return [
                    Failure(
                        Check.STATUS_MISSING,
                        "the status comment has no valid json block",
                    )
                ], None
            validate_status(data)
            return [], data
        case _:
            return [
                Failure(
                    Check.STATUS_DUPLICATE,
                    f"{len(bodies)} issue comments carry {STATUS_MARKER}",
                )
            ], None


def _status_checks(status: Mapping[str, Any], current: str) -> list[Failure]:
    failures: list[Failure] = []
    if status["stale"]:
        failures.append(
            Failure(Check.STATUS_STALE, "the status comment is marked stale")
        )
    elif status["body_sha256"] != current:
        failures.append(
            Failure(
                Check.STATUS_STALE,
                f"status body_sha256 {status['body_sha256']} differs from the "
                f"current body's {current}",
            )
        )
    if status["issue_check"] != 0:
        failures.append(
            Failure(
                Check.ISSUE_CHECK,
                f"./issue_check --body-file exited {status['issue_check']}, not 0",
            )
        )
    rows = status["ac"]
    gating_nothing = [
        r["id"]
        for r in rows
        if r["master_exit"] == r["expected"] and not r["regression_guard"]
    ]
    if not rows or gating_nothing:
        failures.append(
            Failure(
                Check.AC_PASSES_ON_MASTER,
                "AC rows that already pass on master and are no regression guard: "
                f"{gating_nothing or 'no rows'}",
            )
        )
    return failures


def _dry_run_check(status: Mapping[str, Any], review: Verdict) -> list[Failure]:
    problems: list[str] = []
    if review.master != status["master"]:
        problems.append(
            f"verdict master {review.master} != status master {status['master']}"
        )
    for row in status["ac"]:
        match review.dry_run.get(row["id"]):
            case None:
                problems.append(f"AC {row['id']} has no dry run")
            case code if code != row["master_exit"]:
                problems.append(
                    f"AC {row['id']} dry run exited {code}, "
                    f"status says {row['master_exit']}"
                )
    return [Failure(Check.DRY_RUN_MISMATCH, "; ".join(problems))] if problems else []


def evaluate(doc: Mapping[str, Any]) -> list[Failure]:
    """Run every check on one issue document; an empty list means READY.

    Raises :class:`GateError` for input the gate cannot judge.
    """
    validate_doc(doc)
    current = body_sha256(doc["body"])
    failures, status = _status(doc)
    if status is not None:
        failures += _status_checks(status, current)

    review = latest(collect_verdicts(doc), current)
    if review is None:
        failures.append(
            Failure(
                Check.VERDICT_MISSING, f"no {VERDICT_MARKER} verdict for body {current}"
            )
        )
    else:
        if review.verdict != CLEAN:  # fail closed: anything but CLEAN blocks
            failures.append(
                Failure(
                    Check.VERDICT_FINDINGS,
                    f"the latest verdict for this body is {review.verdict!r}, "
                    f"not {CLEAN}",
                )
            )
        if status is not None:
            failures += _dry_run_check(status, review)

    states = [n for n in _names(doc["labels"]) if n.startswith("state:")]
    if len(states) != 1 or states[0] not in GATE_STATES:
        failures.append(
            Failure(
                Check.STATE_LABEL,
                f"state labels {states}; need exactly one of {sorted(GATE_STATES)}",
            )
        )
    return failures


def fetch(number: int, repo: str | None = None) -> dict[str, Any]:
    """Build the gate's input document for issue ``number`` with one ``gh`` read."""
    where = ["--repo", repo] if repo else []
    doc = _gh_json(["issue", "view", str(number), *where, "--json", ISSUE_VIEW_FIELDS])
    if not isinstance(doc, dict):
        raise GateError("gh issue view returned no object")
    return doc


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse's own exit code is also 2
        self.print_usage(sys.stderr)
        print(f"issue_status: {message}", file=sys.stderr)
        raise SystemExit(TOOL_ERROR)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: ``./issue_status (N | --from-json FILE) [--repo OWNER/REPO]``."""
    parser = _Parser(
        prog="issue_status",
        description="Read-only gate: may issue N be handed over to the owner "
        "(state:manual-reviewing-issue)? Prints one FAIL line per failed check, "
        "or READY. Exit 0 ready, 1 not ready, 2 usage or tool error.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "number", nargs="?", help="issue number (real mode, gh reads only)"
    )
    source.add_argument(
        "--from-json",
        type=Path,
        metavar="FILE",
        help="gate input document (fixture mode)",
    )
    parser.add_argument(
        "--repo", metavar="OWNER/REPO", help="passed to gh; default: the clone's repo"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``./issue_status``."""
    args = build_parser().parse_args(argv)
    try:
        if args.from_json is not None:
            doc = load(args.from_json)
        elif args.number.isdigit():
            doc = fetch(int(args.number), args.repo)
        else:
            print(
                f"issue_status: issue number must be numeric, got {args.number!r}",
                file=sys.stderr,
            )
            return TOOL_ERROR
        try:
            failures = evaluate(doc)
        except (TypeError, AttributeError, ValueError, KeyError) as exc:
            raise GateError(f"malformed input: {type(exc).__name__}: {exc}") from exc
        return report(failures)  # type: ignore[arg-type]  # same str() contract
    except GateError as exc:
        print(f"issue_status: {exc}", file=sys.stderr)
        return TOOL_ERROR
