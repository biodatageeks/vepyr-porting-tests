"""Controls for ``check_campaign.check_via_cli_result`` (records from #232 runs)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from check_campaign import check_via_cli_result

ORACLE = "a" * 32


def record(status: str, exit_code: int, vepyr_md5: str | None) -> dict[str, Any]:
    """Build a minimal new-shape case recorded through ``./run_tests --via-cli``."""
    result: dict[str, Any] = {
        "commands": [
            {"argv": ["normalize_input"], "exit": 0},
            {"argv": ["bless"], "exit": 0},
            {
                "argv": ["./run_tests", "--cache-dir", "c", "--via-cli", "--only", "d"],
                "exit": exit_code,
                "verdict": status,
            },
        ],
        "oracle_body_md5": ORACLE,
        "runnable": status != "ERROR",
        "status": status,
    }
    if vepyr_md5 is not None:
        result["vepyr_body_md5"] = vepyr_md5
    return {"id": "DT-x", "status": status, "result": result}


GOOD = {
    "PASS": record("PASS", 0, ORACLE),
    "FAIL": record("FAIL", 8, "b" * 32),
    "ERROR": record("ERROR", 6, None),
}


@pytest.mark.parametrize("status", GOOD)
def test_valid_new_shape_passes(status):
    check_via_cli_result(copy.deepcopy(GOOD[status]))


def test_missing_verdict_fails():
    case = copy.deepcopy(GOOD["PASS"])
    del case["result"]["commands"][2]["verdict"]
    with pytest.raises(KeyError):
        check_via_cli_result(case)


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("PASS", 1), ("PASS", 8), ("FAIL", 1), ("FAIL", 0), ("ERROR", 0)],
)
def test_wrong_exit_for_verdict_fails(status, exit_code):
    case = copy.deepcopy(GOOD[status])
    case["result"]["commands"][2]["exit"] = exit_code
    with pytest.raises(AssertionError):
        check_via_cli_result(case)


def test_verdict_disagreeing_with_status_fails():
    case = copy.deepcopy(GOOD["PASS"])
    case["result"]["commands"][2]["verdict"] = "FAIL"
    with pytest.raises(AssertionError):
        check_via_cli_result(case)
