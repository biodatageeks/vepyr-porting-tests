"""Tests for ``dt`` (``.claude/skills/impl-vepyr-data-test/scripts/dt``).

``dt`` is a uv script without a ``.py`` suffix, so it is loaded by path and
registered in :data:`sys.modules` before ``exec_module`` (its
``@dataclass(slots=True)`` classes need their module there). ``DT_REPO`` points
at this checkout, so no test depends on the cwd.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

REPO: Final[Path] = Path(__file__).resolve().parent.parent
DT_PATH: Final[Path] = (
    REPO / ".claude" / "skills" / "impl-vepyr-data-test" / "scripts" / "dt"
)
HEADER: Final[str] = (
    "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
)
ROW: Final[str] = "21\t100\trs1\tA\tC\t.\t.\t.\n"
NEGATIVE_LINE: Final[str] = (
    "PASS fixture-match-negative: mutated copy -> FAIL fixture-match first 1: "
    "record 1: input ('21', 100, 'rs1', 'A', 'CA') "
    "!= fixture ('21', 100, 'rs1', 'A', 'C')"
)


def load_dt() -> ModuleType:
    """Import ``dt`` from its path under the module name ``dt``."""
    if (mod := sys.modules.get("dt")) is not None:
        return mod
    loader = importlib.machinery.SourceFileLoader("dt", str(DT_PATH))
    spec = importlib.util.spec_from_loader("dt", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dt"] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture
def dt(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The ``dt`` module with ``DT_REPO`` set to this checkout."""
    monkeypatch.setenv("DT_REPO", str(REPO))
    return load_dt()


@pytest.fixture
def inp(tmp_path: Path) -> Path:
    """A one-record input VCF."""
    path = tmp_path / "input.vcf"
    path.write_text(HEADER + ROW, encoding="utf-8")
    return path


def fixture_match(
    dt: ModuleType, inp: Path, fixture: str | Path, records: str = "1"
) -> int:
    """``dt fixture-match --input inp --fixture fixture --records records``."""
    return dt.main(
        [
            "fixture-match",
            "--input",
            str(inp),
            "--fixture",
            str(fixture),
            "--records",
            records,
        ]
    )


def test_fixture_match_exit0(
    dt: ModuleType, inp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fixture_match(dt, inp, inp) == 0
    out = capsys.readouterr().out
    assert "tools/fixture_match" in out  # the echoed `$ ...` line: dt spawns the tool
    assert [ln for ln in out.splitlines() if ln.startswith("PASS")] == [
        "PASS fixture-match first 1: CHROM,POS,ID,REF,ALT identical",
        NEGATIVE_LINE,
        "PASS summary: 2/2 checks passed",
    ]


def test_fixture_match_exit1(dt: ModuleType, inp: Path, tmp_path: Path) -> None:
    fx = tmp_path / "fx.vcf"
    fx.write_text(HEADER + ROW.replace("rs1", "rsX"), encoding="utf-8")
    assert fixture_match(dt, inp, fx) == 1


def test_fixture_match_exit2(dt: ModuleType, inp: Path) -> None:
    with pytest.raises(SystemExit) as e:  # dt's own argparse: N >= 1
        fixture_match(dt, inp, inp, "0")
    assert e.value.code == 2
    assert fixture_match(dt, inp, "git:x:y:z") == 2  # the tool's exit 2 passes through


def test_fixture_match_exit3(
    dt: ModuleType, inp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.run

    def fake(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        if args and Path(args[0]).name == "fixture_match":
            raise OSError("tool cannot start")
        return real(args, *a, **kw)

    monkeypatch.setattr(dt.subprocess, "run", fake)
    assert fixture_match(dt, inp, inp) == 3
    monkeypatch.setattr(dt.subprocess, "run", real)
    assert fixture_match(dt, inp, "http://127.0.0.1:9/x.vcf") == 3


# ---------------------------------------------------------------- verify runner (#168)

VERIFY_DIR: Final[Path] = REPO / "tests" / "data" / "intergenic_variant_single_record"
PASSING_STEPS: Final[tuple[str, ...]] = (
    "check_structure",
    "check_mode",
    "check_normalised",
    "check_md5",
    "refcheck_summary",
    "check_bless",
)


@pytest.fixture
def runner_calls(
    dt: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[list[str]]:
    """``dt verify`` with all but the runner step passing; ``run_tests`` argv recorded.

    The config is a ``DT_CONFIG`` tmp file and the cwd is this checkout. The
    non-runner steps need the machine config, the FASTA and Docker, so they are
    stubbed to pass; ``subprocess.run`` is faked only for ``run_tests`` (dt's
    ``git`` calls stay real).
    """
    config = tmp_path / "dt.toml"
    config.write_text(
        f'vepyr_cache_root = "{tmp_path / "cache"}"\n'
        f'cargo_target_root = "{tmp_path / "targets"}"\n'
        f'scratch_root = "{tmp_path / "scratch"}"\n'
        f'main_checkout = "{tmp_path / "main"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DT_CONFIG", str(config))
    monkeypatch.chdir(REPO)
    for name in PASSING_STEPS:
        monkeypatch.setattr(dt, name, lambda *_a, _n=name: dt.Check(_n, True))
    return []


def fake_run_tests(
    dt: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[str]],
    results: list[int | OSError],
) -> None:
    """Answer successive ``run_tests`` calls with ``results`` (exit code or error)."""
    real = subprocess.run
    slug = VERIFY_DIR.name

    def fake(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        if not (args and args[0].endswith("run_tests")):
            return real(args, *a, **kw)
        calls.append(list(args))
        match results[len(calls) - 1]:
            case OSError() as err:
                raise err
            case 0:
                out = "vepyr sha        : " + "a" * 40 + "\n"
                return subprocess.CompletedProcess(args, 0, out, "")
            case code:
                out = f"[{slug}] body md5 mismatch\n"
                return subprocess.CompletedProcess(args, code, out, "")

    monkeypatch.setattr(dt.subprocess, "run", fake)


def verify(dt: ModuleType, *extra: str) -> int:
    """``dt verify <intergenic_variant_single_record> <extra>``."""
    return dt.main(["verify", str(VERIFY_DIR), *extra])


def test_verify_runner_exit0(
    dt: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    runner_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_run_tests(dt, monkeypatch, runner_calls, [0, 1])
    assert verify(dt, "--vepyr", "master") == 0
    out = capsys.readouterr().out
    assert len(runner_calls) == 2
    for argv, copy in zip(runner_calls, ("pos", "neg"), strict=True):
        assert argv[0] == str(REPO / "run_tests")
        assert argv[1] == "--only" and argv[3:] == ["--vepyr", "master"]
        only = Path(argv[2])
        assert only.name == VERIFY_DIR.name and only.parent.name == copy
        assert not only.is_relative_to(REPO)
    assert "PASS runner: exit 0; vepyr sha : " + "a" * 40 in out
    assert "PASS runner-negative: exit 1, 1 'body md5 mismatch' block(s)" in out
    assert "PASS summary: 8/8 checks passed" in out


def test_verify_runner_exit1(
    dt: ModuleType, monkeypatch: pytest.MonkeyPatch, runner_calls: list[list[str]]
) -> None:
    fake_run_tests(dt, monkeypatch, runner_calls, [1])
    assert verify(dt, "--vepyr", "master") == 1
    assert len(runner_calls) == 1  # negative control not run after a failed positive


def test_verify_runner_exit2(
    dt: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    runner_calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_run_tests(dt, monkeypatch, runner_calls, [])
    assert verify(dt) == 2
    assert "--vepyr" in capsys.readouterr().err
    assert runner_calls == []
    assert verify(dt, "--no-cargo") == 0  # --no-cargo needs no --vepyr


def test_verify_runner_exit3(
    dt: ModuleType, monkeypatch: pytest.MonkeyPatch, runner_calls: list[list[str]]
) -> None:
    fake_run_tests(dt, monkeypatch, runner_calls, [OSError("cannot start")])
    assert verify(dt, "--vepyr", "master") == 3
