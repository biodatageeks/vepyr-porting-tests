"""Tests for the skill helper ``dt`` (``impl-vepyr-data-test/scripts/dt``).

``dt`` has no ``.py`` suffix, so it is loaded by path. Each switched subcommand
(#161) gets its tests here; ``dt env`` (#170) delegates the tool, pin, docker,
``UV_PROJECT_ENVIRONMENT`` and cache checks to the repo's ``./check_env``. The
tests run ``dt env`` against a throwaway checkout whose ``check_env`` is a stub
that records its argv and exits with a chosen code.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

REPO: Final[Path] = Path(__file__).resolve().parent.parent
DT: Final[Path] = (
    REPO / ".claude" / "skills" / "impl-vepyr-data-test" / "scripts" / "dt"
)


def _load() -> ModuleType:
    """Import ``dt`` by path as module ``dt``."""
    loader = SourceFileLoader("dt", str(DT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses resolves annotations via this
    loader.exec_module(module)
    return module


dt = _load()


def _git(cwd: Path, *args: str) -> None:
    """Run git quietly in ``cwd`` with a fixed identity."""
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exit3: the tool's own "unexpected" code stays dt's exit 3."""
    monkeypatch.setenv("CHECK_ENV_EXIT", "3")
    assert dt.main(["env"]) == 3


def test_env_exit3_when_check_env_cannot_start(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exit3: ``./check_env`` cannot be started; only that call fails, not dt's git."""
    real = subprocess.run

    def fake(argv: list[str], *args: object, **kwargs: object) -> object:
        if str(argv[0]).endswith("check_env"):
            raise OSError("exec format error")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake)
    assert dt.main(["env"]) == 3


def test_dt_owns_no_prerequisite_checks() -> None:
    """The checks moved to ``./check_env``; dt keeps no copy (nor the pin)."""
    for name in ("tool_version_check", "docker_check", "uv_env_check", "VEPYR_FASTA"):
        assert not hasattr(dt, name)
