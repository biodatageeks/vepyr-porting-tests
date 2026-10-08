"""Tests for :mod:`campaign.check`: one negative control per independent invariant.

Each control copies a PASS and a FAIL case of the committed campaign into a
scratch repository, alters one byte of one committed file or recorded field,
and requires ``check`` to report a violation naming the case and
``./campaign check`` to exit 1 (also under ``python -O``).
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from campaign.__main__ import main
from campaign.check import (
    check,
    manifest_violations,
    record_violations,
    via_cli_record_violations,
)
from campaign.model import DEFAULT_SETTINGS, ROOT, Case, Settings

PASS_ID = "DT-bd53035db0"
FAIL_ID = "DT-1d6dc19f25"
CAMPAIGN = Path("docs/porting/vep1162-merged")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repository holding the PASS and FAIL case and their files."""
    real = Settings.load()
    cases = [c.raw for c in map(Case, json.loads(real.manifest.read_text()))]
    picked = [c for c in cases if c["id"] in {PASS_ID, FAIL_ID}]
    campaign = tmp_path / CAMPAIGN
    campaign.mkdir(parents=True)
    shutil.copy(DEFAULT_SETTINGS, campaign / DEFAULT_SETTINGS.name)
    (campaign / "cases.json").write_text(json.dumps(picked, indent=2) + "\n")
    for case in picked:
        name = case["directory_name"]
        shutil.copytree(real.data_dir / name, tmp_path / "tests/data" / name)
    shutil.copytree(real.failures / FAIL_ID, campaign / "failures" / FAIL_ID)
    return tmp_path


def settings_of(repo: Path) -> Settings:
    """Settings of the scratch repository."""
    return Settings.load(repo / CAMPAIGN / DEFAULT_SETTINGS.name)


def case_dir(repo: Path, case_id: str) -> Path:
    """``tests/data/<name>`` of ``case_id`` in the scratch repository."""
    cases = json.loads((repo / CAMPAIGN / "cases.json").read_text())
    name = next(c["directory_name"] for c in cases if c["id"] == case_id)
    return repo / "tests/data" / name


def flip_body_byte(path: Path) -> None:
    """Change one byte of the first body line (the POS digit stays a digit)."""
    lines = path.read_bytes().splitlines(keepends=True)
    i = next(i for i, line in enumerate(lines) if not line.startswith(b"#"))
    lines[i] = lines[i].replace(b"\t.\t", b"\tX\t", 1)
    path.write_bytes(b"".join(lines))


def edit_manifest(
    repo: Path, case_id: str, edit: Callable[[dict[str, Any]], None]
) -> None:
    """Apply ``edit`` to one case of the scratch manifest."""
    path = repo / CAMPAIGN / "cases.json"
    cases = json.loads(path.read_text())
    edit(next(c for c in cases if c["id"] == case_id))
    path.write_text(json.dumps(cases, indent=2) + "\n")


def violations(repo: Path) -> list[str]:
    """Violations of the scratch repository."""
    return check(settings_of(repo)).violations


def test_clean_copy_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The unmodified copy has no violation and prints the summary."""
    assert violations(repo) == []
    settings = str(repo / CAMPAIGN / DEFAULT_SETTINGS.name)
    assert main(["--settings", settings, "check"]) == 0
    assert capsys.readouterr().out == "2 executed ports; FAIL=1, PASS=1\n"


def _alter_input(repo: Path) -> str:
    (case_dir(repo, PASS_ID) / "input.vcf").write_bytes(
        (case_dir(repo, PASS_ID) / "input.vcf").read_bytes() + b"\n"
    )
    return PASS_ID


def _alter_oracle(repo: Path) -> str:
    flip_body_byte(case_dir(repo, PASS_ID) / "expected_output.vcf")
    return PASS_ID


def _alter_compare_md5(repo: Path) -> str:
    toml = case_dir(repo, FAIL_ID) / "test.toml"
    text = toml.read_text()
    marker = 'body_md5 = "'
    i = text.index(marker) + len(marker)
    toml.write_text(text[:i] + ("0" if text[i] != "0" else "1") + text[i + 1 :])
    return FAIL_ID


def _alter_recorded_vepyr_md5(repo: Path) -> str:
    def zero(case: dict[str, Any]) -> None:
        case["result"]["vepyr_body_md5"] = "0" * 32

    edit_manifest(repo, FAIL_ID, zero)
    return FAIL_ID


def _alter_vepyr_output(repo: Path) -> str:
    flip_body_byte(repo / CAMPAIGN / "failures" / FAIL_ID / "actual_output.vcf")
    return FAIL_ID


def _remove_vepyr_output(repo: Path) -> str:
    (repo / CAMPAIGN / "failures" / FAIL_ID / "actual_output.vcf").unlink()
    return FAIL_ID


def _alter_pass_vepyr_md5(repo: Path) -> str:
    def zero(case: dict[str, Any]) -> None:
        case["result"]["vepyr_body_md5"] = "0" * 32

    edit_manifest(repo, PASS_ID, zero)
    return PASS_ID


@pytest.mark.parametrize(
    ("alter", "needle"),
    [
        pytest.param(_alter_input, "input.vcf sha256", id="input-byte"),
        pytest.param(_alter_oracle, "oracle body md5", id="oracle-body-byte"),
        pytest.param(_alter_compare_md5, "[compare] body_md5", id="compare-md5"),
        pytest.param(_alter_recorded_vepyr_md5, "re-read vepyr output", id="vepyr-md5"),
        pytest.param(_alter_vepyr_output, "re-read vepyr output", id="vepyr-output"),
        pytest.param(_remove_vepyr_output, "not committed", id="vepyr-output-gone"),
        pytest.param(_alter_pass_vepyr_md5, "PASS but vepyr_body_md5", id="pass-md5"),
    ],
)
def test_one_altered_byte_fails_naming_the_case(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    alter: Callable[[Path], str],
    needle: str,
) -> None:
    """Each independent invariant catches its own corruption; exit 1 names the case."""
    case_id = alter(repo)
    found = violations(repo)
    assert any(v.startswith(f"{case_id}: ") and needle in v for v in found), found
    settings = str(repo / CAMPAIGN / DEFAULT_SETTINGS.name)
    assert main(["--settings", settings, "check"]) == 1
    assert case_id in capsys.readouterr().out


def test_python_optimize_still_fails(repo: Path) -> None:
    """``python -O`` strips asserts; the check uses none, so it still exits 1."""
    _alter_recorded_vepyr_md5(repo)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "tools")}
    proc = subprocess.run(
        [
            sys.executable,
            "-O",
            "-m",
            "campaign",
            "--settings",
            str(repo / CAMPAIGN / DEFAULT_SETTINGS.name),
            "check",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert FAIL_ID in proc.stdout


def test_manifest_invariants() -> None:
    """Duplicate IDs, unknown statuses, unexplained blocks and queued cases."""
    cases = [
        Case({"id": "A", "status": "PASS"}),
        Case({"id": "A", "status": "WEIRD"}),
        Case({"id": "B", "status": "BLOCKED"}),
        Case({"id": "C", "status": "QUEUED"}),
    ]
    found = manifest_violations(cases, require_complete=True)
    assert found == [
        "A: duplicate case ID",
        "A: unknown status 'WEIRD'",
        "B: BLOCKED without block_reason",
        "1 cases still queued",
    ]
    assert manifest_violations(cases[2:3], require_complete=False) == [
        "B: BLOCKED without block_reason"
    ]


def test_require_normalized(repo: Path) -> None:
    """``--require-normalized`` reports a case without the input-identity audit."""

    def drop(case: dict[str, Any]) -> None:
        case["result"].pop("normalization_verified")

    edit_manifest(repo, PASS_ID, drop)
    assert violations(repo) == []
    found = check(settings_of(repo), require_normalized=True).violations
    assert found == [f"{PASS_ID}: input normalization not verified"]


# --- records made through ./run_tests --via-cli (#232) -----------------------

ORACLE = "a" * 32


def via_cli(status: str, exit_code: int, vepyr_md5: str | None) -> dict[str, Any]:
    """A minimal case recorded through ``./run_tests --via-cli``."""
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
    "PASS": via_cli("PASS", 0, ORACLE),
    "FAIL": via_cli("FAIL", 8, "b" * 32),
    "ERROR": via_cli("ERROR", 6, None),
}


@pytest.mark.parametrize("status", GOOD)
def test_valid_via_cli_record_passes(status: str) -> None:
    """Well-formed PASS/FAIL/ERROR records have no structural violation."""
    assert record_violations(Case(copy.deepcopy(GOOD[status]))) == []


def test_missing_verdict_fails() -> None:
    """A ./run_tests record must carry its verdict."""
    raw = copy.deepcopy(GOOD["PASS"])
    del raw["result"]["commands"][2]["verdict"]
    assert "DT-x: last command has no verdict" in record_violations(Case(raw))


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("PASS", 1), ("PASS", 8), ("FAIL", 1), ("FAIL", 0), ("ERROR", 0)],
)
def test_wrong_exit_for_verdict_fails(status: str, exit_code: int) -> None:
    """The exit code must match the verdict (0 PASS, 8 FAIL, other ERROR)."""
    raw = copy.deepcopy(GOOD[status])
    raw["result"]["commands"][2]["exit"] = exit_code
    found = via_cli_record_violations(Case(raw))
    assert found
    assert all(v.startswith("DT-x: ") for v in found)


def test_verdict_disagreeing_with_status_fails() -> None:
    """verdict, result.status and status must agree."""
    raw = copy.deepcopy(GOOD["PASS"])
    raw["result"]["commands"][2]["verdict"] = "FAIL"
    assert "DT-x: verdict, result.status and status disagree" in (
        via_cli_record_violations(Case(raw))
    )
