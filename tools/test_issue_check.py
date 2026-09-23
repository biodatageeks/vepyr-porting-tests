"""Tests for ``issue_check``: the five cases the minimal rule can distinguish."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from issue_check import checker

FIXTURES: Final = Path(__file__).parent / "fixtures" / "issue_check"
REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m issue_check`` the way the workflow does."""
    return subprocess.run(
        [sys.executable, "-m", "issue_check", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT / "tools"), "PATH": "/usr/bin:/bin"},
        check=False,
    )


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("valid.md", checker.OK),
        ("no_acceptance_criteria.md", checker.INVALID),
        ("prose_only_criteria.md", checker.INVALID),
        ("subsection_criteria.md", checker.OK),
        ("subsection_prose_only.md", checker.INVALID),
    ],
)
def test_fixtures(fixture: str, expected: int) -> None:
    """Each fixture lands on its documented exit code."""
    result = run_cli("--body-file", str(FIXTURES / fixture))
    assert result.returncode == expected, result.stdout + result.stderr


def test_empty_file_is_non_compliant(tmp_path: Path) -> None:
    """An empty body has no heading at all, so it is rejected (exit 1)."""
    body = tmp_path / "empty.md"
    body.write_text("", encoding="utf-8")
    result = run_cli("--body-file", str(body))
    assert result.returncode == checker.INVALID
    assert "acceptance criteria" in result.stdout


def test_non_utf8_body_is_a_usage_error(tmp_path: Path) -> None:
    """A non-UTF-8 file is the checker's own problem: exit 2, clean message."""
    body = tmp_path / "latin1.md"
    body.write_bytes(b"## Acceptance criteria\n1. `caf\xe9 --check` exits 0.\n")
    result = run_cli("--body-file", str(body))
    assert result.returncode == checker.USAGE
    expected = f"issue_check: usage error: {body} is not valid UTF-8"
    assert result.stderr.strip() == expected
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_subsection_text_and_bodies_belong_to_the_section() -> None:
    """A deeper heading does not close the section; its text and body count."""
    assert checker.has_acceptance_criteria(
        "## Acceptance criteria\n\nlead-in\n\n### AC-1 — no `gh` invocation\n"
    )[0]
    assert checker.has_acceptance_criteria(
        "## Acceptance criteria\n\nlead-in\n\n### AC-1\n\n`make test` exits 0.\n"
    )[0]


def test_section_ends_at_a_heading_of_the_same_or_higher_level() -> None:
    """Code after the next same-or-higher heading is outside the section."""
    for closing in ("## Out of scope", "# Out of scope"):
        compliant, reason = checker.has_acceptance_criteria(
            f"## Acceptance criteria\n\nprose only\n\n{closing}\n\n`make test`\n"
        )
        assert not compliant, closing
        assert "no code span" in reason
