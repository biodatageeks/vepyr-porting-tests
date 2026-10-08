"""Pure classification of a ``./run_tests --via-cli --only <dir>`` run (#231, #232).

Exit 0 is ``PASS``. Exit 8 (``Exit.MISMATCH``) with at least one well-formed
``MISMATCH <dir> expected=<md5> actual=<md5>`` line on stdout naming the
``--only`` directory is ``FAIL``, and that line's ``actual`` is vepyr's body
md5. Exit 8 without such a line (a malformed report) and every other non-zero
exit are ``ERROR``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from campaign.model import Status

__all__ = ["MISMATCH_EXIT", "Classification", "RunOutput", "classify", "only_dir_name"]

MISMATCH_EXIT: Final[int] = 8
"""``Exit.MISMATCH`` of ``./run_tests --via-cli`` (the #231 mismatch contract)."""

_MISMATCH: Final[re.Pattern[str]] = re.compile(
    r"^MISMATCH (\S+) expected=([0-9a-f]{32}) actual=([0-9a-f]{32})$"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RunOutput:
    """What one subprocess produced: exit code and captured text."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class Classification:
    """The campaign status of a ``./run_tests`` run and vepyr's body md5 if known."""

    status: Status
    actual_md5: str | None = None


def only_dir_name(argv: Sequence[str]) -> str | None:
    """Return the basename of the ``--only`` value in ``argv``, if any."""
    args = list(argv)
    if "--only" in args and (i := args.index("--only") + 1) < len(args):
        return Path(args[i]).name
    return None


def classify(run: RunOutput, dir_name: str | None = None) -> Classification:
    """Map a ``./run_tests --via-cli`` run to ``PASS``, ``FAIL`` or ``ERROR``.

    Args:
        run: The run's exit code and stdout (``MISMATCH`` lines are read from
            stdout only).
        dir_name: The ``--only`` directory basename a ``MISMATCH`` line must
            name; ``None`` accepts any name.
    """
    mismatches = [
        m
        for line in run.stdout.splitlines()
        if (m := _MISMATCH.match(line)) and dir_name in (None, m[1])
    ]
    match run.returncode:
        case 0:
            return Classification(status=Status.PASS)
        case code if code == MISMATCH_EXIT and mismatches:
            return Classification(status=Status.FAIL, actual_md5=mismatches[0][3])
        case _:
            return Classification(status=Status.ERROR)
