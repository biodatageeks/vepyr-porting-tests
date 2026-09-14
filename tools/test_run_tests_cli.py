"""CLI contract tests for ``./run_tests`` (issue #3 shell)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

from run_tests.cli import DEFERRED_MESSAGE, main


def test_help_lists_required_flags_and_not_contigs() -> None:
    """AC-1: --help exits 0 and names the shipped flags; no --contigs."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--help"])
    assert code == 0
    text = out.getvalue()
    for flag in ("--cache-dir", "--add-contigs", "--flavours", "--vepyr", "--list"):
        assert flag in text, flag
    # Flag name must not appear as an option (metavar text may mention contigs).
    assert "--contigs" not in text


def test_list_reports_zero_data_problem_targets() -> None:
    """AC-2 / scope: --list exits 0 with an explicit empty target list."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--list"])
    assert code == 0
    assert "0 data-problem targets" in out.getvalue()


def test_bare_invocation_is_deferred_not_success() -> None:
    """AC-2: bare argv must not exit 0 claiming tests passed."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([])
    assert code != 0
    combined = out.getvalue() + err.getvalue()
    assert DEFERRED_MESSAGE in combined
    assert "passed" not in combined.lower()


def test_cache_dir_alone_is_deferred_not_success() -> None:
    """AC-2: a cache-backed-looking argv refuses until issue #4."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--cache-dir", "/tmp/x"])
    assert code != 0
    combined = out.getvalue() + err.getvalue()
    assert DEFERRED_MESSAGE in combined
    assert "passed" not in combined.lower()
