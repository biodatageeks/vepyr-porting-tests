"""Tests for :mod:`campaign.classify` (the #231 mismatch contract, #232, #235)."""

from __future__ import annotations

import pytest

from campaign.classify import RunOutput, classify, only_dir_name
from campaign.model import Status

DIR_NAME = "align_pheno_values_with_colocated_identifiers_bd5303"
MD5_A = "0" * 32
MD5_B = "f" * 32
GOOD_LINE = f"MISMATCH {DIR_NAME} expected={MD5_A} actual={MD5_B}"


def test_classify_pass_on_exit_0() -> None:
    """Exit 0 is PASS with no vepyr md5 (vepyr's body equals the oracle)."""
    got = classify(RunOutput(returncode=0, stdout=f"PASS {DIR_NAME}\n"), DIR_NAME)
    assert got.status is Status.PASS
    assert got.actual_md5 is None


def test_classify_fail_on_exit_8_with_mismatch_line() -> None:
    """Exit 8 with a well-formed line naming the directory is FAIL; actual md5 kept."""
    got = classify(RunOutput(returncode=8, stdout=GOOD_LINE + "\n"), DIR_NAME)
    assert (got.status, got.actual_md5) == (Status.FAIL, MD5_B)
    assert classify(RunOutput(returncode=8, stdout=GOOD_LINE)).status is Status.FAIL


@pytest.mark.parametrize(
    ("code", "stdout"),
    [
        pytest.param(1, "tests failed\n", id="tests-failed"),
        pytest.param(2, "usage: run_tests: error\n", id="usage"),
        pytest.param(6, "engine: no output\n", id="engine-missing-output"),
        pytest.param(8, "8 without a mismatch line\n", id="exit8-no-line"),
        pytest.param(1, GOOD_LINE + "\n", id="mismatch-line-wrong-exit"),
    ],
)
def test_classify_error_on_other_exits(code: int, stdout: str) -> None:
    """Every other non-zero exit, and exit 8 without a valid line, is ERROR."""
    got = classify(RunOutput(returncode=code, stdout=stdout), DIR_NAME)
    assert got.status is Status.ERROR
    assert got.actual_md5 is None


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(GOOD_LINE[:-1], id="short-md5"),
        pytest.param(GOOD_LINE + " ", id="trailing-space"),
        pytest.param(GOOD_LINE.replace(MD5_B, "F" * 32), id="uppercase-hex"),
        pytest.param(" " + GOOD_LINE, id="indented"),
        pytest.param(GOOD_LINE.replace(DIR_NAME, "other_dir"), id="other-dir"),
    ],
)
def test_classify_error_on_malformed_or_foreign_mismatch_line(line: str) -> None:
    """A malformed line, or one naming another directory, makes exit 8 ERROR."""
    assert classify(RunOutput(returncode=8, stdout=line + "\n"), DIR_NAME).status is (
        Status.ERROR
    )


def test_classify_reads_stdout_only() -> None:
    """A MISMATCH line on stderr does not count (ERROR)."""
    run = RunOutput(returncode=8, stdout="", stderr=GOOD_LINE + "\n")
    assert classify(run, DIR_NAME).status is Status.ERROR


def test_only_dir_name() -> None:
    """The basename of the ``--only`` value, trailing slash tolerated."""
    assert only_dir_name(["./run_tests", "--only", f"x/{DIR_NAME}/"]) == DIR_NAME
    assert only_dir_name(["./run_tests", "--only"]) is None
    assert only_dir_name(["./run_tests"]) is None
