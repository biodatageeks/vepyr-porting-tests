"""Contract tests for ``python -m issue_check`` against the checked-in fixtures.

Every case goes through :func:`issue_check.cli.main`, so the exit code under test is
the one CI observes. The three fixtures are the gate's own falsifiability proof: one
compliant body, one missing the required section, one whose criteria are prose.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Final

import pytest

from issue_check.checker import check, looks_like_command, sections
from issue_check.cli import main
from issue_check.verdict import Exit, IssueCheckError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "issue_check"
VALID: Final[Path] = FIXTURES / "valid.md"
NO_ACCEPTANCE: Final[Path] = FIXTURES / "no_acceptance_criteria.md"
PROSE_ONLY: Final[Path] = FIXTURES / "prose_only_criteria.md"


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """One ``main()`` call: its exit code and everything it printed."""

    code: int
    stdout: str
    stderr: str


def run(argv: Sequence[str]) -> Outcome:
    """Invoke :func:`issue_check.cli.main` with captured streams."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())


def test_fixtures_exist() -> None:
    """Positive control: all three fixtures are on disk (AC1-AC3 reference them)."""
    assert sorted(p.name for p in FIXTURES.glob("*.md")) == [
        "no_acceptance_criteria.md",
        "prose_only_criteria.md",
        "valid.md",
    ]


def test_valid_fixture_exits_ok() -> None:
    """AC1: a compliant body exits 0 and reports the criteria count."""
    outcome = run(["--body-file", str(VALID)])
    assert outcome.code == int(Exit.OK), outcome
    assert "ok" in outcome.stdout
    assert "3 command-verifiable criterion(s)" in outcome.stdout
    assert outcome.stderr == ""


def test_missing_acceptance_section_names_it() -> None:
    """AC2: a body without the section exits 1 and names the missing section."""
    outcome = run(["--body-file", str(NO_ACCEPTANCE)])
    assert outcome.code == int(Exit.INVALID), outcome
    assert "missing required section 'Acceptance criteria'" in outcome.stderr
    assert outcome.stdout == ""


def test_prose_only_criteria_are_rejected() -> None:
    """AC3: criteria without a backticked command exit 1, listed by ordinal."""
    outcome = run(["--body-file", str(PROSE_ONLY)])
    assert outcome.code == int(Exit.INVALID), outcome
    assert "without a backticked command" in outcome.stderr
    assert "#1" in outcome.stderr
    assert "#3" in outcome.stderr
    assert "#2" not in outcome.stderr


def test_missing_body_file_argument_is_a_usage_error() -> None:
    """No ``--body-file`` at all: exit 2, argparse usage printed verbatim."""
    outcome = run([])
    assert outcome.code == int(Exit.USAGE), outcome
    assert "--body-file" in outcome.stderr
    assert "usage:" in outcome.stderr.lower()


def test_unknown_flag_is_a_usage_error() -> None:
    """Unknown flags and abbreviations are refused (``allow_abbrev=False``)."""
    assert run(["--body-file", str(VALID), "--nope"]).code == int(Exit.USAGE)
    assert run(["--body", str(VALID)]).code == int(Exit.USAGE)


def test_unreadable_body_file_is_a_usage_error(tmp_path: Path) -> None:
    """A ``--body-file`` that does not exist is a usage error, not an invalid issue."""
    outcome = run(["--body-file", str(tmp_path / "absent.md")])
    assert outcome.code == int(Exit.USAGE), outcome
    assert "absent.md" in outcome.stderr


def test_empty_body_is_invalid(tmp_path: Path) -> None:
    """An empty issue body is rejected before any section lookup."""
    body = tmp_path / "empty.md"
    body.write_text("\n \n", encoding="utf-8")
    outcome = run(["--body-file", str(body)])
    assert outcome.code == int(Exit.INVALID), outcome
    assert "empty" in outcome.stderr


def test_section_heading_inside_a_fence_does_not_count() -> None:
    """A quoted template inside a code fence cannot satisfy the section rule."""
    body = "## Problem\n\n```\n## Acceptance criteria\n\n1. `make test`\n```\n"
    assert [s.heading for s in sections(body)] == ["Problem"]
    with pytest.raises(IssueCheckError) as caught:
        check(body, origin="body.md")
    assert caught.value.code is Exit.INVALID


def test_fenced_command_satisfies_a_criterion() -> None:
    """A criterion whose command sits in a fenced block is accepted."""
    body = (
        "## Acceptance criteria\n\n1. this run passes:\n\n"
        "   ```\n   make test\n   ```\n"
    )
    section = check(body, origin="body.md")
    assert [c.ordinal for c in section.criteria()] == ["1"]


def test_section_without_numbered_items_is_invalid() -> None:
    """A bullet list is not a numbered criterion list."""
    body = "## Acceptance criteria\n\n- `make test` passes\n"
    with pytest.raises(IssueCheckError) as caught:
        check(body, origin="body.md")
    assert "no numbered acceptance criteria" in str(caught.value)


def test_backticked_noun_is_not_a_command() -> None:
    """Review counter-example: a prose criterion naming a file is still prose."""
    body = (
        "## Acceptance criteria\n\n"
        "1. Reviewers agree the `PINS.toml` wording is clear.\n"
        "2. It just works, see `foo.rs`.\n"
    )
    with pytest.raises(IssueCheckError) as caught:
        check(body, origin="body.md")
    assert caught.value.code is Exit.INVALID
    assert "#1" in str(caught.value)
    assert "#2" in str(caught.value)


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ("uv run --frozen pytest -q", True),
        ("./run_tests --list", True),
        ("/usr/bin/env python -V", True),
        ("gh issue view 74", True),
        ("the job exits 0", True),
        ("`make test` → exit 0", True),
        ("PINS.toml", False),
        ("foo.rs", False),
        ("tests/data_frameshift.rs", False),
        ("", False),
    ],
)
def test_looks_like_command(span: str, expected: bool) -> None:
    """The invocation heuristic is explicit about both sides."""
    assert looks_like_command(span) is expected


def test_trailing_prose_does_not_satisfy_the_last_criterion() -> None:
    """Review item 3: a code span in flush-left prose after the list does not count."""
    body = (
        "## Acceptance criteria\n\n"
        "1. The precheck works correctly.\n\n"
        "See `PINS.toml` for background.\n"
    )
    with pytest.raises(IssueCheckError) as caught:
        check(body, origin="body.md")
    assert "without a backticked command: #1" in str(caught.value)


def test_criterion_ends_before_trailing_prose() -> None:
    """The dropped prose is not part of the item at all, not merely ignored."""
    body = (
        "## Acceptance criteria\n\n"
        "1. `make test` exits 0.\n\n"
        "See `PINS.toml` for background.\n"
    )
    (criterion,) = check(body, origin="body.md").criteria()
    assert criterion.lines == ("`make test` exits 0.",)


@pytest.mark.parametrize("indent", ["", " ", "   ", "    ", "      ", "\t"])
def test_fenced_command_at_any_item_indent(indent: str) -> None:
    """Review item 4: a fence indented 4+ spaces under ``1. `` is valid GFM."""
    body = (
        f"## Acceptance criteria\n\n1. this run passes:\n\n"
        f"{indent}```\n{indent}make test\n{indent}```\n"
    )
    section = check(body, origin="body.md")
    assert [c.ordinal for c in section.criteria()] == ["1"]


def test_fenced_prose_is_not_a_command() -> None:
    """A fenced block still has to contain something runnable."""
    body = (
        "## Acceptance criteria\n\n1. it works:\n\n"
        "    ```\n    looks fine to me\n    ```\n"
    )
    with pytest.raises(IssueCheckError) as caught:
        check(body, origin="body.md")
    assert "without a backticked command: #1" in str(caught.value)


def test_non_utf8_body_is_a_clean_usage_error(tmp_path: Path) -> None:
    """Review item 5: no traceback, and INVALID stays distinct from USAGE."""
    body = tmp_path / "garbled.md"
    body.write_bytes(b"## Acceptance criteria\n\n1. \x80bad `make test`\n")
    outcome = run(["--body-file", str(body)])
    assert outcome.code == int(Exit.USAGE), outcome
    assert "not valid UTF-8" in outcome.stderr
    assert "Traceback" not in outcome.stderr
    assert outcome.stdout == ""


def test_real_issue_74_body_would_pass() -> None:
    """Self-consistency: the fixture mirroring this repo's issue style passes."""
    assert check(VALID.read_text(encoding="utf-8"), origin=str(VALID)).heading == (
        "Acceptance criteria"
    )
