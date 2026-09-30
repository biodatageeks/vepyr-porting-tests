"""Tests for ``dt`` (``.claude/skills/impl-vepyr-data-test/scripts/dt``).

``dt`` is a uv script without a ``.py`` suffix, so it is loaded by path and
registered in :data:`sys.modules` before ``exec_module`` (its
``@dataclass(slots=True)`` classes need their module there). The ``dt`` fixture
points ``DT_REPO`` at this checkout, so the fixture-match tests (#171) do not
depend on the cwd.

``dt env`` (#170) delegates the tool, pin, docker, ``UV_PROJECT_ENVIRONMENT``
and cache checks to the repo's ``./check_env``. Its tests unset ``DT_REPO`` and
run ``dt env`` against a throwaway checkout (the cwd) whose ``check_env`` is a
stub that records its argv and exits with a chosen code.
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


def _git(cwd: Path, *args: str) -> None:
    """Run git quietly in ``cwd`` with a fixed identity."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(dt: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal checkout (cwd) with a stub ``check_env`` and a complete dt config.

    The stub exits with ``$CHECK_ENV_EXIT`` and writes its argv to ``argv.txt``.
    """
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tests").mkdir()
    for rel in ("bless", "tools/normalize_input", "tests/data_dirs.rs"):
        (repo / rel).write_text("")
    stub = repo / "check_env"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {tmp_path}/argv.txt\n'
        'echo "PASS stub: ok"\nexit "${CHECK_ENV_EXIT:-0}"\n'
    )
    stub.chmod(0o755)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    paths = {k: tmp_path / k for k in ("vep_cache", "fa", "docker", "vepyr", "main")}
    for key in ("vep_cache", "docker", "vepyr", "main"):
        paths[key].mkdir()
    paths["fa"].write_text(">21\nA\n")
    config = tmp_path / "local.toml"
    config.write_text(
        f'vep_cache_dir = "{paths["vep_cache"]}"\nvep_fasta = "{paths["fa"]}"\n'
        f'docker_shared_root = "{paths["docker"]}"\n'
        f'vepyr_cache_root = "{paths["vepyr"]}"\n'
        f'cargo_target_root = "{tmp_path / "targets"}"\n'
        f'main_checkout = "{paths["main"]}"\n'
    )
    monkeypatch.setenv("DT_CONFIG", str(config))
    for var in ("DT_REPO", "DT_ALLOW_MAIN", *(f"DT_{k.upper()}" for k in dt.KEYS)):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(repo)
    return repo


@pytest.mark.parametrize(("tool_exit", "dt_exit"), [(0, 0), (1, 1), (2, 2)])
def test_env_forwards_check_env_exit(
    tool_exit: int,
    dt_exit: int,
    dt: ModuleType,
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """exit0/exit1/exit2: dt keeps the tool's verdict and prints its lines in full."""
    monkeypatch.setenv("CHECK_ENV_EXIT", str(tool_exit))
    assert dt.main(["env"]) == dt_exit
    out = capsys.readouterr().out
    assert "PASS stub: ok" in out
    assert f"env check_env: exit {tool_exit}" in out
    argv = (checkout.parent / "argv.txt").read_text().split()
    assert argv == [
        "--vepyr-cache-root",
        str(checkout.parent / "vepyr"),
        "--vep-cache-dir",
        str(checkout.parent / "vep_cache"),
        "--vep-fasta",
        str(checkout.parent / "fa"),
    ]


def test_env_tool_exit3_gives_exit3(
    dt: ModuleType, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exit3: the tool's own "unexpected" code stays dt's exit 3."""
    monkeypatch.setenv("CHECK_ENV_EXIT", "3")
    assert dt.main(["env"]) == 3


def test_env_exit3_when_check_env_cannot_start(
    dt: ModuleType, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exit3: ``./check_env`` cannot be started; only that call fails, not dt's git."""
    real = subprocess.run

    def fake(argv: list[str], *args: object, **kwargs: object) -> object:
        if str(argv[0]).endswith("check_env"):
            raise OSError("exec format error")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake)
    assert dt.main(["env"]) == 3


def test_dt_owns_no_prerequisite_checks(dt: ModuleType) -> None:
    """The checks moved to ``./check_env``; dt keeps no copy (nor the pin)."""
    for name in ("tool_version_check", "docker_check", "uv_env_check", "VEPYR_FASTA"):
        assert not hasattr(dt, name)
