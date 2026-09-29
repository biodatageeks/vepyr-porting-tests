"""The ``./pr_status`` gate: is a pull request ready for the owner (#158)?

One JSON document describes the PR: the output of ``gh pr view N --json
headRefOid,labels,comments,reviews,files,closingIssuesReferences`` plus the
top-level keys ``issues`` (the closing issues with their labels) and, only when
``README.md`` is among ``files``, ``readme_diff`` and ``readme``. Real mode
builds that document with ``gh`` reads; fixture mode (``--from-json``) loads it
from a file. Both feed :func:`evaluate`, which returns one :class:`Failure` per
failed check. The tool only reads: it never changes a label, a comment or a
review.

Exit codes: 0 ready, 1 not ready, 2 usage or tool error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

READY: Final = 0
NOT_READY: Final = 1
TOOL_ERROR: Final = 2

STICKY_MARKER: Final = "### pr-status:v1"
VERDICT_MARKER: Final = "### pr-review:v1"
PR_VIEW_FIELDS: Final = (
    "headRefOid,labels,comments,reviews,files,closingIssuesReferences"
)
APPROVE: Final = "APPROVE"
GATE_STATES: Final = frozenset({"state:manual-reviewing", "state:awaiting-merge"})

#: Paths that need a super-review: exact files and directory prefixes.
TIER_FILES: Final = frozenset(
    {"tests/data_dirs.rs", "bless", "tools/vep_flags.toml", "tools/normalize_input"}
)
TIER_DIRS: Final = ("tests/data/", "tools/bless/", "tools/run_tests/")
TIER_SEVERITIES: Final = frozenset({"severity:high", "severity:critical"})
#: Level-2 README sections that state the oracle contract.
TIER_README_SECTIONS: Final = frozenset({"./bless", "One mode: --everything"})
README: Final = "README.md"

_FENCE_OPEN: Final = re.compile(r"^```json\s*$")
_FENCE_CLOSE: Final = re.compile(r"^```\s*$")
_HUNK: Final = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class Check(StrEnum):
    """The check ids; each is also the name of its failing fixture."""

    STICKY_MISSING = "sticky-missing"
    STICKY_DUPLICATE = "sticky-duplicate"
    STICKY_STALE = "sticky-stale"
    SHA_MISMATCH = "sha-mismatch"
    AC_EXIT = "ac-exit"
    VERDICT_MISSING = "verdict-missing"
    VERDICT_CHANGES = "verdict-changes"
    PROBES = "probes"
    MUTATION = "mutation"
    SUPERREVIEW_MISSING = "superreview-missing"
    SUPERREVIEW_MODEL = "superreview-model"
    SUPERREVIEW_BLOCKING = "superreview-blocking"
    STATE_LABEL = "state-label"


class GateError(Exception):
    """Input the gate cannot judge (exit 2): it never guesses."""


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed check, printed as ``FAIL <check>: <reason>``."""

    check: Check
    reason: str

    def __str__(self) -> str:
        return f"FAIL {self.check}: {self.reason}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """One ``### pr-review:v1`` verdict and when it was posted."""

    role: str
    model: str | None
    verdict: str
    sha: str
    probes: object
    mutations: tuple[Mapping[str, Any], ...]
    at: str


def first_line(body: str) -> str:
    """Return the first non-empty line of ``body``, right-stripped."""
    return next((line.rstrip() for line in body.splitlines() if line.strip()), "")


def json_block(body: str) -> object | None:
    """Parse the first fenced ``json`` block of ``body``; ``None`` if absent/invalid."""
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if _FENCE_OPEN.match(line)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if _FENCE_CLOSE.match(lines[i])), None
    )
    if end is None:
        return None
    try:
        return json.loads("\n".join(lines[start + 1 : end]))
    except json.JSONDecodeError:
        return None


def _names(labels: Iterable[Mapping[str, Any]] | None) -> list[str]:
    return [str(label.get("name", "")) for label in labels or ()]


def collect_verdicts(doc: Mapping[str, Any]) -> list[Verdict]:
    """All well-formed verdicts from issue comments and review bodies, oldest first."""
    sources = [(c, c.get("createdAt") or "") for c in doc.get("comments") or ()]
    sources += [(r, r.get("submittedAt") or "") for r in doc.get("reviews") or ()]
    verdicts: list[Verdict] = []
    for item, at in sources:
        body = str(item.get("body") or "")
        if first_line(body) != VERDICT_MARKER:
            continue
        data = json_block(body)
        if not isinstance(data, dict):
            continue
        verdicts.append(
            Verdict(
                role=str(data.get("role")),
                model=data.get("model"),
                verdict=str(data.get("verdict", "<missing>")),
                sha=str(data.get("sha")),
                probes=data.get("probes"),
                mutations=tuple(
                    m for m in data.get("mutations") or () if isinstance(m, dict)
                ),
                at=str(at),
            )
        )
    return sorted(verdicts, key=lambda v: v.at)


def latest(verdicts: Sequence[Verdict], role: str, head: str) -> Verdict | None:
    """The latest verdict of ``role`` for ``head``; older shas never count."""
    return next(
        (v for v in reversed(verdicts) if v.role == role and v.sha == head), None
    )


def readme_sections(readme: str) -> list[str | None]:
    """Map each 1-based line of ``readme`` (index 0 unused) to its ``## `` section."""
    sections: list[str | None] = [None]
    current: str | None = None
    for line in readme.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        sections.append(current)
    return sections


def readme_in_tier(readme_diff: str, readme: str) -> bool:
    """Does the README diff touch a line of a tier section (part 4 of #158)?"""
    sections = readme_sections(readme)
    headings = {f"## {s}" for s in TIER_README_SECTIONS}

    def tier_at(line_no: int) -> bool:
        return 0 < line_no < len(sections) and sections[line_no] in TIER_README_SECTIONS

    new_line: int | None = None
    for line in readme_diff.splitlines():
        if hunk := _HUNK.match(line):
            new_line = int(hunk.group(1))
            continue
        if new_line is None or line.startswith("\\"):
            continue
        match line[:1]:
            case "+":
                if tier_at(new_line):
                    return True
                new_line += 1
            case "-":
                if line[1:].rstrip() in headings or tier_at(new_line - 1):
                    return True
            case _:
                new_line += 1
    return False


def path_in_tier(path: str) -> bool:
    """Exact file or directory-prefix match against the tier paths."""
    return path in TIER_FILES or path.startswith(TIER_DIRS)


def superreview_required(doc: Mapping[str, Any]) -> bool:
    """Compute the risk tier from ``files``, the closing issues and the README diff."""
    paths = [str(f.get("path", "")) for f in doc.get("files") or ()]
    if any(path_in_tier(p) for p in paths):
        return True
    if any(
        TIER_SEVERITIES & set(_names(i.get("labels"))) for i in doc.get("issues") or ()
    ):
        return True
    if README in paths:
        diff, text = doc.get("readme_diff"), doc.get("readme")
        if not isinstance(diff, str) or not isinstance(text, str):
            raise GateError("README.md changed but readme_diff/readme are missing")
        return readme_in_tier(diff, text)
    return False


def _sticky(doc: Mapping[str, Any]) -> tuple[list[Failure], dict[str, Any] | None]:
    bodies = [
        str(c.get("body") or "")
        for c in doc.get("comments") or ()
        if first_line(str(c.get("body") or "")) == STICKY_MARKER
    ]
    if len(bodies) > 1:
        return [
            Failure(
                Check.STICKY_DUPLICATE,
                f"{len(bodies)} issue comments carry {STICKY_MARKER}",
            )
        ], None
    if not bodies:
        return [
            Failure(
                Check.STICKY_MISSING, f"no issue comment starts with {STICKY_MARKER}"
            )
        ], None
    data = json_block(bodies[0])
    if not isinstance(data, dict):
        return [
            Failure(Check.STICKY_MISSING, "the sticky comment has no valid json block")
        ], None
    return [], data


def _sticky_checks(sticky: Mapping[str, Any], head: str) -> list[Failure]:
    failures: list[Failure] = []
    rows = [r for r in sticky.get("ac") or () if isinstance(r, dict)]
    if sticky.get("stale") is True:
        failures.append(
            Failure(Check.STICKY_STALE, "the sticky comment is marked stale")
        )
    stale_rows = [r.get("id") for r in rows if r.get("sha") != head]
    if sticky.get("head") != head or stale_rows:
        failures.append(
            Failure(
                Check.SHA_MISMATCH,
                f"sticky head {sticky.get('head')} / rows {stale_rows} "
                f"differ from headRefOid {head}",
            )
        )
    bad = [
        r.get("id")
        for r in rows
        if not r.get("manual") and r.get("exit") != r.get("expected")
    ]
    if not rows or bad:
        failures.append(
            Failure(Check.AC_EXIT, f"AC rows with exit != expected: {bad or 'no rows'}")
        )
    return failures


def _mutation_check(
    rows: Sequence[Mapping[str, Any]], review: Verdict
) -> list[Failure]:
    by_id = {r.get("id"): r for r in rows}
    problems: list[str] = []
    for m in review.mutations:
        row = by_id.get(m.get("ac"))
        if row is None:
            problems.append(f"AC {m.get('ac')} is not a sticky row")
        elif m.get("exit") == row.get("expected"):
            problems.append(f"AC {m.get('ac')} did not fail on its mutation")
    covered = {m.get("ac") for m in review.mutations}
    problems += [
        f"AC {r.get('id')} has no mutation"
        for r in rows
        if not r.get("manual") and r.get("id") not in covered
    ]
    return [Failure(Check.MUTATION, "; ".join(problems))] if problems else []


def evaluate(doc: Mapping[str, Any]) -> list[Failure]:
    """Run every check on one PR document; an empty list means READY.

    Raises :class:`GateError` for input the gate cannot judge.
    """
    head = doc.get("headRefOid")
    if not isinstance(head, str) or not head:
        raise GateError("input has no headRefOid")
    failures, sticky = _sticky(doc)
    rows = [r for r in (sticky or {}).get("ac") or () if isinstance(r, dict)]
    if sticky is not None:
        failures += _sticky_checks(sticky, head)

    verdicts = collect_verdicts(doc)
    review = latest(verdicts, "review", head)
    if review is None:
        failures.append(Failure(Check.VERDICT_MISSING, f"no review verdict for {head}"))
    else:
        if review.verdict != APPROVE:  # fail closed: anything but APPROVE blocks
            failures.append(
                Failure(
                    Check.VERDICT_CHANGES,
                    f"the latest review verdict is {review.verdict!r}, not APPROVE",
                )
            )
        if not isinstance(review.probes, int) or review.probes < 2:
            failures.append(
                Failure(
                    Check.PROBES,
                    f"the review ran {review.probes} own probes, fewer than 2",
                )
            )
        if sticky is not None and rows:  # no rows: ac-exit already reports it
            failures += _mutation_check(rows, review)

    superreview = latest(verdicts, "superreview", head)
    if superreview_required(doc) and superreview is None:
        failures.append(
            Failure(
                Check.SUPERREVIEW_MISSING,
                f"PR is in the risk tier; no super-review for {head}",
            )
        )
    if superreview is not None:
        if review is not None and superreview.model == review.model:
            failures.append(
                Failure(
                    Check.SUPERREVIEW_MODEL,
                    f"super-review model {superreview.model} = review model",
                )
            )
        if superreview.verdict != APPROVE:  # fail closed
            failures.append(
                Failure(
                    Check.SUPERREVIEW_BLOCKING,
                    f"super-review verdict is {superreview.verdict!r}, not APPROVE",
                )
            )

    states = [n for n in _names(doc.get("labels")) if n.startswith("state:")]
    if len(states) != 1 or states[0] not in GATE_STATES:
        failures.append(
            Failure(
                Check.STATE_LABEL,
                f"state labels {states}; need exactly one of {sorted(GATE_STATES)}",
            )
        )
    return failures


def _gh(args: Sequence[str]) -> str:
    """Run one read-only ``gh`` call and return stdout; failures raise GateError."""
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise GateError(f"gh not runnable: {exc}") from exc
    if done.returncode != 0:
        raise GateError(
            f"gh {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}"
        )
    return done.stdout


def _gh_json(args: Sequence[str]) -> Any:
    try:
        return json.loads(_gh(args))
    except json.JSONDecodeError as exc:
        raise GateError(f"gh {' '.join(args)} printed no JSON") from exc


def readme_part(diff: str) -> str:
    """Keep only the ``README.md`` part of a multi-file diff (one-file diff: whole)."""
    if not any(line.startswith("diff --git ") for line in diff.splitlines()):
        return diff
    keep: list[str] = []
    inside = False
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            inside = line.rstrip().endswith(f" b/{README}")
        if inside:
            keep.append(line)
    return "".join(keep)


def fetch(number: int, repo: str | None = None) -> dict[str, Any]:
    """Build the gate's input document for PR ``number`` with ``gh`` reads only."""
    where = ["--repo", repo] if repo else []
    doc = _gh_json(["pr", "view", str(number), *where, "--json", PR_VIEW_FIELDS])
    if not isinstance(doc, dict):
        raise GateError("gh pr view returned no object")
    refs = [r.get("number") for r in doc.get("closingIssuesReferences") or ()]
    doc["issues"] = [
        _gh_json(["issue", "view", str(n), *where, "--json", "number,labels"])
        for n in refs
    ]
    if README in (str(f.get("path", "")) for f in doc.get("files") or ()):
        doc["readme_diff"] = readme_part(_gh(["pr", "diff", str(number), *where]))
        slug = repo or "{owner}/{repo}"
        doc["readme"] = _gh(
            [
                "api",
                "-H",
                "Accept: application/vnd.github.raw",
                f"repos/{slug}/contents/{README}?ref={doc['headRefOid']}",
            ]
        )
    return doc


def load(path: Path) -> dict[str, Any]:
    """Read a fixture document; unreadable or non-JSON input raises GateError."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise GateError(f"{path}: top level is not an object")
    return doc


def report(failures: Sequence[Failure]) -> int:
    """Print every failure (or ``READY``) and return the exit code."""
    for failure in failures:
        print(failure)
    if failures:
        return NOT_READY
    print("READY")
    return READY


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse's own exit code is also 2
        self.print_usage(sys.stderr)
        print(f"pr_status: {message}", file=sys.stderr)
        raise SystemExit(TOOL_ERROR)


def build_parser() -> argparse.ArgumentParser:
    """The CLI: ``./pr_status N`` or ``./pr_status --from-json FILE``."""
    parser = _Parser(
        prog="pr_status",
        description="Read-only gate: is PR N ready for the owner? Prints one FAIL "
        "line per failed check, or READY. "
        "Exit 0 ready, 1 not ready, 2 usage or tool error.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "number", nargs="?", help="PR number (real mode, gh reads only)"
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
    """Entry point of ``./pr_status``."""
    args = build_parser().parse_args(argv)
    try:
        if args.from_json is not None:
            doc = load(args.from_json)
        elif args.number.isdigit():
            doc = fetch(int(args.number), args.repo)
        else:
            print(
                f"pr_status: PR number must be numeric, got {args.number!r}",
                file=sys.stderr,
            )
            return TOOL_ERROR
        try:
            failures = evaluate(doc)
        except (TypeError, AttributeError, ValueError, KeyError) as exc:
            raise GateError(f"malformed input: {type(exc).__name__}: {exc}") from exc
        return report(failures)
    except GateError as exc:
        print(f"pr_status: {exc}", file=sys.stderr)
        return TOOL_ERROR
