"""Controls for the campaign's byte comparison and primary-property selectors."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import port_campaign
import pytest
import report_campaign
from check_campaign import check_via_cli_result
from port_campaign import Verdict, body, classify_run_tests, focus_value


def test_body_md5_ignores_headers_but_preserves_record_order(tmp_path):
    a = tmp_path / "a.vcf"
    b = tmp_path / "b.vcf"
    a.write_bytes(b"##VEP=116.2\n#CHROM\n21\t1\n21\t2\n")
    b.write_bytes(b"##VEP=other\n#CHROM\n21\t1\n21\t2\n")
    assert hashlib.md5(body(a)).digest() == hashlib.md5(body(b)).digest()
    b.write_bytes(b"##VEP=116.2\n#CHROM\n21\t2\n21\t1\n")
    assert hashlib.md5(body(a)).digest() != hashlib.md5(body(b)).digest()


def test_focus_requires_one_matching_feature_and_keeps_zero(tmp_path):
    p = tmp_path / "case.vcf"
    header = '##INFO=<ID=CSQ,Number=.,Type=String,Description="Format: Feature|AF">\n'
    row = "21\t1\t.\tC\tT\t.\t.\tCSQ=TX|0\n"
    p.write_text(header + row)
    focus = {"field": "AF", "where": {"Feature": "TX"}}
    assert focus_value(p, focus) == "0"
    p.write_text(header + row.replace("TX|0", "TX|0,TX|0"))
    with pytest.raises(ValueError, match="selects 2 entries"):
        focus_value(p, focus)
    p.write_text(header + row.replace("TX|0", "OTHER|0"))
    with pytest.raises(ValueError, match="selects 0 entries"):
        focus_value(p, focus)


def test_info_key_match_is_exact(tmp_path):
    p = tmp_path / "case.vcf"
    p.write_text("21\t1\t.\tC\tT\t.\t.\tBCSQ=keep;CSQ=annotation\n")
    assert focus_value(p, {"kind": "info", "key": "BCSQ"}) == ["keep"]
    assert focus_value(p, {"kind": "info", "key": "CSQ"}) == ["annotation"]


# --- ./run_tests --via-cli delegation (#232) ---------------------------------

REPO = Path(__file__).resolve().parent.parent
CASE_ID = "DT-bd53035db0"
DIR_NAME = "align_pheno_values_with_colocated_identifiers_bd5303"
MD5_A = "0" * 32
MD5_B = "f" * 32


def recorded_status(case_id: str) -> str:
    """Return the ``Status`` column of ``case_id`` in the campaign README table."""
    lines = (REPO / "docs/porting/vep1162-merged/README.md").read_text().splitlines()
    header = next(line for line in lines if line.startswith("| Test |"))
    column = [c.strip() for c in header.split("|")].index("Status")
    row = next(line for line in lines if line.startswith(f"| [{case_id}]"))
    return [c.strip() for c in row.split("|")][column]


def campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, stdout: str
) -> tuple[int, dict[str, Any], list[list[str]]]:
    """Run ``main`` on case ``CASE_ID`` with every subprocess stubbed.

    ``./run_tests`` is stubbed to exit ``exit_code`` printing ``stdout``.
    Returns the campaign exit, the recorded case and the ``./run_tests`` argvs.
    """
    cases = json.loads((REPO / "docs/porting/vep1162-merged/cases.json").read_text())
    case = next(c for c in cases if c["id"] == CASE_ID)
    case["status"] = "QUEUED"
    case.pop("result", None)
    root = tmp_path / "root"
    (root / "tests/data").mkdir(parents=True)
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([case]))
    monkeypatch.setattr(port_campaign, "ROOT", root)
    monkeypatch.setattr(port_campaign, "MANIFEST", manifest)
    real = REPO / "tests/data" / DIR_NAME
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        match Path(argv[0]).name:
            case "normalize_input":
                shutil.copy(real / "input.vcf", Path(argv[2]) / "input.vcf")
            case "bless":
                dest = Path(argv[-1])
                shutil.copy(real / "expected_output.vcf", dest / "expected_output.vcf")
                sha = hashlib.sha256((dest / "input.vcf").read_bytes()).hexdigest()
                kwargs["stdout"].write(f"Docker input SHA256 {sha}\n")
            case _ if len(argv) > 2 and argv[1] == "-c":
                calls.append(argv)
                return subprocess.CompletedProcess(argv, exit_code, stdout, "err\n")
            case other:
                raise AssertionError(f"unexpected subprocess {other}")
        return subprocess.CompletedProcess(argv, 0)

    code = port_campaign.main(
        [
            "--vepyr",
            "0.9.0",
            "--vep-cache",
            str(tmp_path / "vep"),
            "--cache-dir",
            str(tmp_path / "hub"),
            "--fasta",
            str(tmp_path / "fa"),
            "--evidence",
            str(tmp_path / "ev"),
        ],
        runner,
    )
    return code, json.loads(manifest.read_text())[0], calls


def test_run_tests_nonzero_fails(tmp_path, monkeypatch):
    code, case, calls = campaign(tmp_path, monkeypatch, 1, "")
    assert len(calls) == 1
    assert calls[0][1] == "-c" and "run_selection" in calls[0][2]
    assert calls[0][3:] == [
        "0.9.0",
        str(tmp_path / "hub"),
        str(tmp_path / "root/tests/data" / DIR_NAME),
    ]
    assert case["status"] != "PASS"
    assert case["result"]["commands"][-1]["exit"] == 1
    assert code != 0


@pytest.mark.parametrize(
    ("exit_code", "stdout", "status"),
    [
        pytest.param(0, "PASS x\n", "PASS", id="a-exit0"),
        pytest.param(
            8,
            f"MISMATCH {DIR_NAME} expected={MD5_A} actual={MD5_B}\n",
            "FAIL",
            id="b-mismatch",
        ),
        pytest.param(2, "usage: run_tests: error\n", "ERROR", id="c-usage"),
        pytest.param(6, "engine error\n", "ERROR", id="d-engine"),
        pytest.param(1, "tests failed\n", "ERROR", id="e-tests-failed"),
        pytest.param(8, "8 without a mismatch line\n", "ERROR", id="f-malformed"),
    ],
)
def test_recorded_verdict(tmp_path, monkeypatch, exit_code, stdout, status):
    assert recorded_status(CASE_ID) == "PASS"
    code, case, _ = campaign(tmp_path, monkeypatch, exit_code, stdout)
    assert case["status"] == case["result"]["status"] == status
    assert (code == 0) == (status == "PASS")
    if status == "PASS":
        assert case["status"] == recorded_status(CASE_ID)
    if status == "FAIL":
        assert case["result"]["vepyr_body_md5"] == MD5_B
    log = Path(case["result"]["commands"][-1]["log"]).read_text()
    assert stdout in log


def test_classify_run_tests_uses_injected_runner():
    def runner(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 8, "MISMATCH d expected=a actual=b\n")

    # md5 values must be 32 hex digits; anything else is a malformed report.
    assert classify_run_tests(["./run_tests"], runner) is Verdict.ERROR


GOOD_LINE = f"MISMATCH {DIR_NAME} expected={MD5_A} actual={MD5_B}"


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
def test_malformed_or_foreign_mismatch_line_is_error(line):
    def runner(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 8, line + "\n")

    argv = ["./run_tests", "--cache-dir", "c", "--via-cli", "--only", f"x/{DIR_NAME}/"]
    assert classify_run_tests(argv, runner) is Verdict.ERROR


def test_mismatch_line_must_name_the_only_directory():
    def runner(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 8, GOOD_LINE + "\n")

    argv = ["./run_tests", "--cache-dir", "c", "--via-cli", "--only", f"x/{DIR_NAME}/"]
    assert classify_run_tests(argv, runner) is Verdict.FAIL
    assert port_campaign.classify(8, GOOD_LINE, DIR_NAME) == (Verdict.FAIL, MD5_B)
    assert port_campaign.classify(8, GOOD_LINE, "other_dir") == (Verdict.ERROR, None)


@pytest.mark.parametrize(
    ("exit_code", "stdout"),
    [
        pytest.param(0, "", id="pass"),
        pytest.param(8, GOOD_LINE + "\n", id="fail"),
        pytest.param(6, "engine\n", id="error"),
    ],
)
def test_campaign_record_passes_check_campaign(
    tmp_path, monkeypatch, exit_code, stdout
):
    _, case, _ = campaign(tmp_path, monkeypatch, exit_code, stdout)
    check_via_cli_result(case)


def test_report_marks_via_cli_focus_not_checked(tmp_path, monkeypatch):
    _, case, _ = campaign(tmp_path, monkeypatch, 0, "")
    manifest = tmp_path / "report" / "cases.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps([case]))
    monkeypatch.setattr(report_campaign, "MANIFEST", manifest)
    report_campaign.main()
    row = manifest.with_name("README.md").read_text().splitlines()[-1]
    assert [c.strip() for c in row.split("|")][4] == "not checked"
