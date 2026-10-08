"""Tests for :mod:`campaign.port`, every subprocess stubbed (#232, #235).

The stub runner plays ``tools/normalize_input`` and ``./bless`` by copying the
committed ``input.vcf`` / ``expected_output.vcf`` of real PASS cases, and
``./run_tests`` by returning a chosen exit code and stdout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from campaign.check import record_violations
from campaign.classify import RunOutput
from campaign.model import DEFAULT_SETTINGS, CampaignError, Case, Settings, Status
from campaign.port import PortOptions, render_test_toml, run_campaign

REAL = Settings.load()
CASE_IDS = ("DT-bd53035db0", "DT-5126b4bc21", "DT-9065b6096f")
CAMPAIGN = Path("docs/porting/vep1162-merged")
MD5_A = "0" * 32
MD5_B = "f" * 32


def real_cases() -> list[dict[str, Any]]:
    """The committed manifest."""
    return json.loads(REAL.manifest.read_text())


class StubRunner:
    """A :class:`campaign.port.Runner` that never starts a process.

    ``./run_tests`` returns ``outcome``; ``fail_on`` names the ``(tool, case
    directory)`` call that raises instead (a crash in the middle of a case).
    """

    def __init__(
        self,
        outcome: RunOutput | None = None,
        fail_on: tuple[str, str] | None = None,
        crash: type[BaseException] = KeyboardInterrupt,
    ) -> None:
        """Configure the ``./run_tests`` outcome and the call that raises."""
        self.outcome = outcome or RunOutput(returncode=0, stdout="PASS x\n")
        self.fail_on = fail_on
        self.crash = crash
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], *, cwd: Path, merge_stderr: bool
    ) -> RunOutput:
        """Play the tool named by ``argv[0]``."""
        argv = list(argv)
        self.calls.append(argv)
        tool, work = Path(argv[0]).name, Path(argv[-1])
        if self.fail_on == (tool, work.name):
            raise self.crash(f"stub crash in {tool} {work.name}")
        real = REAL.data_dir / work.name
        match tool:
            case "normalize_input":
                shutil.copy(real / "input.vcf", work / "input.vcf")
                return RunOutput(returncode=0, stdout="normalized\n")
            case "bless":
                shutil.copy(real / "expected_output.vcf", work / "expected_output.vcf")
                sha = hashlib.sha256((work / "input.vcf").read_bytes()).hexdigest()
                return RunOutput(returncode=0, stdout=f"Docker input SHA256 {sha}\n")
            case "run_tests":
                return self.outcome
            case other:
                raise AssertionError(f"unexpected subprocess {other}")


def scratch(tmp_path: Path, ids: Sequence[str] = CASE_IDS) -> Settings:
    """A scratch repository whose manifest holds ``ids`` as queued cases."""
    cases = []
    for raw in real_cases():
        if raw["id"] in ids:
            raw["status"] = "QUEUED"
            raw.pop("result", None)
            cases.append(raw)
    campaign = tmp_path / CAMPAIGN
    campaign.mkdir(parents=True)
    (tmp_path / "tests/data").mkdir(parents=True)
    shutil.copy(DEFAULT_SETTINGS, campaign / DEFAULT_SETTINGS.name)
    (campaign / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    return Settings.load(campaign / DEFAULT_SETTINGS.name)


def options(tmp_path: Path, limit: int = 10) -> PortOptions:
    """Port options pointing at scratch paths."""
    return PortOptions(
        vep_cache=tmp_path / "vep",
        cache_dir=tmp_path / "hub",
        fasta=tmp_path / "fa",
        evidence=tmp_path / "ev",
        limit=limit,
    )


def manifest(settings: Settings) -> list[dict[str, Any]]:
    """The scratch manifest."""
    return json.loads(settings.manifest.read_text())


def dir_name(case_id: str) -> str:
    """``directory_name`` of a committed case."""
    return next(c["directory_name"] for c in real_cases() if c["id"] == case_id)


@pytest.mark.parametrize(
    ("tool", "crash"),
    [
        pytest.param("bless", KeyboardInterrupt, id="ctrl-c-in-bless"),
        pytest.param("run_tests", RuntimeError, id="crash-in-run_tests"),
    ],
)
def test_port_interrupted_resumes(
    tmp_path: Path, tool: str, crash: type[BaseException]
) -> None:
    """A crash in case 2 of 3 keeps case 1, leaves no trace of case 2, and resumes."""
    settings = scratch(tmp_path)
    before = manifest(settings)
    second = dir_name(CASE_IDS[1])
    crashing = StubRunner(fail_on=(tool, second), crash=crash)
    with pytest.raises(crash):
        run_campaign(options(tmp_path), settings, root=tmp_path, runner=crashing)
    after = manifest(settings)
    assert [c["id"] for c in after] == [c["id"] for c in before]
    assert after[1:] == before[1:]
    assert after[0]["id"] == CASE_IDS[0]
    assert after[0]["status"] == "PASS"
    assert {k: v for k, v in after[0].items() if k not in before[0]} == {
        "result": after[0]["result"]
    }
    assert not (settings.data_dir / second).exists()
    assert not (tmp_path / "ev" / second).exists()
    assert sorted(p.name for p in (tmp_path / "tests").iterdir()) == ["data"]
    assert sorted(p.name for p in (tmp_path / "ev").iterdir()) == [
        dir_name(CASE_IDS[0])
    ]
    code = run_campaign(options(tmp_path), settings, root=tmp_path, runner=StubRunner())
    assert code == 0
    assert [c["status"] for c in manifest(settings)] == ["PASS"] * 3
    for case_id in CASE_IDS:
        assert (settings.data_dir / dir_name(case_id) / "expected_output.vcf").is_file()


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        pytest.param(RunOutput(returncode=0, stdout="PASS x\n"), "PASS", id="pass"),
        pytest.param(
            RunOutput(
                returncode=8,
                stdout=f"MISMATCH {dir_name(CASE_IDS[0])} expected={MD5_A} "
                f"actual={MD5_B}\n",
            ),
            "FAIL",
            id="fail",
        ),
        pytest.param(RunOutput(returncode=6, stdout="engine\n"), "ERROR", id="error"),
        pytest.param(
            RunOutput(returncode=8, stdout="8, no line\n"), "ERROR", id="bad8"
        ),
    ],
)
def test_recorded_verdict(tmp_path: Path, outcome: RunOutput, status: str) -> None:
    """The ./run_tests outcome decides status, exit code and vepyr md5."""
    settings = scratch(tmp_path, CASE_IDS[:1])
    runner = StubRunner(outcome)
    code = run_campaign(options(tmp_path), settings, root=tmp_path, runner=runner)
    (case,) = manifest(settings)
    assert case["status"] == case["result"]["status"] == status
    assert (code == 0) == (status == "PASS")
    match status:
        case "PASS":
            assert case["result"]["vepyr_body_md5"] == case["result"]["oracle_body_md5"]
        case "FAIL":
            assert case["result"]["vepyr_body_md5"] == MD5_B
        case _:
            assert "vepyr_body_md5" not in case["result"]
    last = case["result"]["commands"][-1]
    assert last["argv"][:5] == [
        "./run_tests",
        "--cache-dir",
        str(tmp_path / "hub"),
        "--via-cli",
        "--only",
    ]
    assert Path(last["argv"][5]).name == dir_name(CASE_IDS[0])
    assert outcome.stdout in Path(last["log"]).read_text()
    assert record_violations(Case(case)) == []


def test_existing_destination_is_an_error_not_file_exists(tmp_path: Path) -> None:
    """A leftover tests/data/<name> of a queued case is reported, nothing written."""
    settings = scratch(tmp_path, CASE_IDS[:1])
    (settings.data_dir / dir_name(CASE_IDS[0])).mkdir()
    before = settings.manifest.read_text()
    with pytest.raises(CampaignError, match="exists"):
        run_campaign(options(tmp_path), settings, root=tmp_path, runner=StubRunner())
    assert settings.manifest.read_text() == before


def test_failed_step_is_an_error(tmp_path: Path) -> None:
    """A non-zero prerequisite step aborts with CampaignError and no data-test."""

    class FailingNormalize(StubRunner):
        def __call__(
            self, argv: Sequence[str], *, cwd: Path, merge_stderr: bool
        ) -> RunOutput:
            if Path(argv[0]).name == "normalize_input":
                return RunOutput(returncode=1, stdout="bcftools: boom\n")
            return super().__call__(argv, cwd=cwd, merge_stderr=merge_stderr)

    settings = scratch(tmp_path, CASE_IDS[:1])
    with pytest.raises(CampaignError, match="step failed"):
        run_campaign(
            options(tmp_path), settings, root=tmp_path, runner=FailingNormalize()
        )
    assert not (settings.data_dir / dir_name(CASE_IDS[0])).exists()


def test_no_queued_case_is_an_error(tmp_path: Path) -> None:
    """Nothing to port is an explicit error."""
    settings = scratch(tmp_path, ())
    with pytest.raises(CampaignError, match="no qualified cases"):
        run_campaign(options(tmp_path), settings, root=tmp_path, runner=StubRunner())


def test_rendered_test_toml_matches_committed_origin_and_vepyr() -> None:
    """Every executed case: generated [origin]/[vepyr] equal the committed ones."""
    executed = [
        Case(c) for c in real_cases() if c["status"] in {"PASS", "FAIL", "ERROR"}
    ]
    assert executed
    for case in executed:
        rendered = tomllib.loads(render_test_toml(case, default_issue=REAL.issue))
        committed = tomllib.loads(
            (REAL.data_dir / case.directory_name / "test.toml").read_text()
        )
        for table in ("origin", "vepyr"):
            assert rendered[table] == committed[table], (case.id, table)
        assert rendered["name"] == committed["name"] == case.directory_name


def test_rendered_test_toml_text() -> None:
    """Byte-exact layout of a generated test.toml; issue from the case if it has one."""
    raw = next(c for c in real_cases() if c["id"] == CASE_IDS[0])
    text = render_test_toml(Case({**raw, "issue": 999}), default_issue=REAL.issue)
    pinned = json.dumps(raw["source_links"][0])
    description = json.dumps(raw["description"])
    subject = json.dumps(raw["implementation_links"][0]["url"])
    assert text == (
        f'name = "{raw["directory_name"]}"\ndescription = {description}\n\n'
        f"[origin]\nvep_test = {pinned}\nvep_test_pinned = {pinned}\n"
        f"vep_subject = {subject}\nissue = 999\n\n"
        '[vepyr]\nflavour = "merged"\nrequired_contigs = ["chr21"]\n'
        "everything = true\npreserve_record_layout = true\nreference_fasta = true\n\n"
        '[vep]\nextra_flags = ["--merged"]\n\n[compare]\nbody_md5 = ""\n'
    )


def test_status_enum_round_trip() -> None:
    """Statuses written by port are the manifest's strings."""
    assert [str(s) for s in Status] == ["PASS", "FAIL", "ERROR", "QUEUED", "BLOCKED"]
