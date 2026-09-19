"""Minimal pre-work gate: does an issue body carry acceptance criteria with code?

The rule, deliberately the whole rule:

1. the body has a Markdown ATX heading whose text matches ``/acceptance criteria/i``;
2. that section — everything up to the next ATX heading — contains at least one
   backtick, i.e. an inline code span or a fenced code block.

That is a *presence* check, not a *meaning* check. It does not parse the criteria, does
not judge whether a backticked span is a command, and **never runs anything it finds**.
It therefore assumes no expected exit code for the criteria themselves: an acceptance
criterion may expect any exit code, or be semi-manual. Judging the criteria stays with
the human reviewer.

Exit codes describe the checker only: ``0`` compliant, ``1`` non-compliant, ``2``
usage error (bad flag, missing/unreadable/non-UTF-8 ``--body-file``).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Final

__all__ = ["INVALID", "OK", "USAGE", "has_acceptance_criteria", "main"]

OK: Final = 0
INVALID: Final = 1
USAGE: Final = 2

_HEADING: Final = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.*?)\s*#*\s*$")
_ACCEPTANCE: Final = re.compile(r"acceptance criteria", re.IGNORECASE)


def has_acceptance_criteria(body: str) -> tuple[bool, str]:
    """Return ``(compliant, one-line reason)`` for an issue *body*."""
    section: list[str] | None = None
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading is None:
            if section is not None:
                section.append(line)
            continue
        if section is not None:
            break
        if _ACCEPTANCE.search(heading["text"]):
            section = []
    if section is None:
        return False, "no heading matching /acceptance criteria/i"
    if not any("`" in line for line in section):
        return False, (
            "the acceptance-criteria section has no code span or fenced code block"
        )
    return True, "acceptance-criteria section present, with code"


def main(argv: list[str] | None = None) -> int:
    """Run the check over ``--body-file`` and return the process exit code."""
    parser = argparse.ArgumentParser(
        prog="issue_check",
        allow_abbrev=False,
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--body-file",
        required=True,
        type=Path,
        metavar="PATH",
        help="file holding the issue body (UTF-8 Markdown)",
    )
    args = parser.parse_args(argv)
    path: Path = args.body_file
    try:
        body = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"issue_check: usage error: {path} is not valid UTF-8")
        return USAGE
    except OSError as exc:
        print(f"issue_check: usage error: cannot read {path}: {exc.strerror}")
        return USAGE
    compliant, reason = has_acceptance_criteria(body)
    label = "ok" if compliant else "not compliant"
    print(f"issue_check: {label}: {path}: {reason}")
    return OK if compliant else INVALID
