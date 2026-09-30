"""Tests for ``tools/check_env.py`` (#170).

No docker, no network: ``docker``, ``bcftools``, ``uv``, ``cargo`` and ``git`` are
tiny shell stubs placed alone on ``PATH``; the caches are built in ``tmp_path``.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

import check_env
import pytest
from check_env import main

from run_tests import fetch

REPO: Final[Path] = Path(__file__).resolve().parent.parent
PIN_BANNER: Final[tuple[str, str]] = (
    check_env._normalize_input()._REQUIRED_VERSION,
    "Using " + check_env._normalize_input()._REQUIRED_HTSLIB,
)

type Stub = Callable[[str, str], Path]


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Stub:
    """Make ``PATH`` a fresh directory; return a writer of ``/bin/sh`` stubs into it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    def write(name: str, body: str) -> Path:
        exe = bin_dir / name
        exe.write_text(f"#!/bin/sh\n{body}\n")
        exe.chmod(0o755)
        return exe

    return write


@pytest.fixture
def healthy(stub: Stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Stub:
    """Every tool present and sane; ``UV_PROJECT_ENVIRONMENT`` outside checkouts."""
    for tool in ("uv", "cargo", "git"):
        stub(tool, "exit 0")
    stub("bcftools", f"printf '%s\\n%s\\n' '{PIN_BANNER[0]}' '{PIN_BANNER[1]}'")
    stub("docker", "echo 27.0.0")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "venv"))
    return stub


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, str]]:
    """Run ``main`` and return its exit code and ``{check name: output line}``."""
    code = main(list(argv))
    lines = capsys.readouterr().out.splitlines()
    by_name = {
        line.split(" ", 1)[1].split(":", 1)[0]: line
        for line in lines
        if not line.split(" ", 1)[1].startswith("summary:")
    }
    return code, by_name


def vepyr_cache(root: Path) -> Path:
    """A minimal cache that satisfies the real ``precheck_cache`` for ``PINS.toml``."""
    pins, fasta_pin = fetch.load_dataset_pins(REPO / "PINS.toml")
    assert fasta_pin is not None
    flavour = fetch.Flavour.ENSEMBL
    record = fetch.FlavourRecord(
        repo_id="x",
        revision=pins[flavour].revision,
        contigs=["chr21"],
        manifests_trimmed=True,
        files=0,
        bytes=0,
    )
    root.mkdir(parents=True)
    fetch.write_provenance(root, fetch.Provenance(datasets={flavour.value: record}))
    (root / fetch.FASTA_DIR).mkdir()
    for suffix in ("", ".fai"):
        (root / fetch.FASTA_DIR / f"{fasta_pin.fa_name}{suffix}").write_text(">21\nA\n")
    return root


def test_no_optional_flags_three_skips_exit0(
    healthy: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = run(capsys)
    assert code == 0
    assert [n for n, line in lines.items() if line.startswith("SKIP")] == [
        "vepyr cache",
        "vep cache",
        "vep fasta",
    ]
    assert sum(line.startswith("PASS") for line in lines.values()) == 6


def test_wrong_bcftools_banner_fails(
    healthy: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy("bcftools", "printf 'bcftools 1.22\\nUsing htslib 1.22\\n'")
    code, lines = run(capsys)
    assert code == 1
    assert lines["bcftools pin"].startswith(
        "FAIL bcftools pin: bcftools version mismatch"
    )


def test_wrong_htslib_fails(healthy: Stub, capsys: pytest.CaptureFixture[str]) -> None:
    healthy("bcftools", f"printf '%s\\nUsing htslib 0.0\\n' '{PIN_BANNER[0]}'")
    code, lines = run(capsys)
    assert code == 1 and "htslib version mismatch" in lines["bcftools pin"]


@pytest.mark.parametrize("tool", ["uv", "cargo", "git", "bcftools", "docker"])
def test_missing_tool_fails(
    tool: str, healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bin" / tool).unlink()
    code, lines = run(capsys)
    assert code == 1
    name = {"bcftools": "bcftools pin", "docker": "docker daemon"}.get(
        tool, f"tool {tool}"
    )
    assert lines[name].startswith(f"FAIL {name}:") and "not on PATH" in lines[name]


def test_docker_daemon_down_fails(
    healthy: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy("docker", "echo 'Cannot connect' >&2; exit 1")
    code, lines = run(capsys)
    assert code == 1 and "daemon does not answer" in lines["docker daemon"]


def test_hung_docker_times_out_quickly(
    healthy: Stub, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy("docker", "exec /bin/sleep 30")
    start = time.monotonic()
    code, lines = run(capsys, "--docker-timeout", "0.5")
    assert time.monotonic() - start < 10
    assert code == 1 and lines["docker daemon"].startswith("FAIL docker daemon:")
    assert "timed out" in lines["docker daemon"]


def test_require_docker_default_has_no_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """``./bless`` keeps calling ``require_docker()`` without a limit."""
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "27\n", "")

    monkeypatch.setattr(check_env.shutil, "which", lambda _: "/x/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert check_env.require_docker() == "/x/docker"
    assert seen["timeout"] is None


def test_hollow_vepyr_cache_fails_naming_provenance(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hollow = tmp_path / "hollow"
    (hollow / "fasta").mkdir(parents=True)
    (hollow / "116_GRCh38_ensembl").mkdir()
    for suffix in ("", ".fai"):
        (
            hollow / "fasta" / f"Homo_sapiens.GRCh38.dna.primary_assembly.fa{suffix}"
        ).touch()
    code, lines = run(capsys, "--vepyr-cache-root", str(hollow))
    assert code == 1
    assert (
        lines["vepyr cache"].startswith("FAIL vepyr cache:")
        and "PROVENANCE.json" in lines["vepyr cache"]
    )


def test_missing_vepyr_cache_fails(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = run(capsys, "--vepyr-cache-root", str(tmp_path / "nope"))
    assert code == 1 and "is not a directory" in lines["vepyr cache"]


def test_real_precheck_cache_fixture_passes(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = vepyr_cache(tmp_path / "cache")
    code, lines = run(capsys, "--vepyr-cache-root", str(root))
    assert code == 0 and lines["vepyr cache"].startswith("PASS vepyr cache:")


def test_vepyr_cache_wrong_revision_fails(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = vepyr_cache(tmp_path / "cache")
    prov = root / fetch.PROVENANCE
    pins, _ = fetch.load_dataset_pins(REPO / "PINS.toml")
    prov.write_text(
        prov.read_text().replace(pins[fetch.Flavour.ENSEMBL].revision, "0" * 40)
    )
    code, lines = run(capsys, "--vepyr-cache-root", str(root))
    assert code == 1 and "PINS.toml" in lines["vepyr cache"]


def test_vep_fasta_checks(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fa = tmp_path / "ref.fa"
    fa.touch()
    assert run(capsys, "--vep-fasta", str(fa))[0] == 1  # empty file: not FASTA
    fa.write_text(">21\nACGT\n")
    code, lines = run(capsys, "--vep-fasta", str(fa))
    assert code == 1 and ".fai" in lines["vep fasta"]  # no index
    Path(f"{fa}.fai").write_text("21\t4\t4\t4\t5\n")
    code, lines = run(capsys, "--vep-fasta", str(fa))
    assert code == 0 and lines["vep fasta"].startswith("PASS vep fasta:")


def test_hollow_vep_cache_fails(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = run(capsys, "--vep-cache-dir", str(tmp_path))
    assert (
        code == 1
        and lines["vep cache"].startswith("FAIL vep cache:")
        and "missing" in lines["vep cache"]
    )


@pytest.mark.parametrize("where", ["unset", "relative", "inside"])
def test_bad_uv_environment_exits_2(
    where: str,
    healthy: Stub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    match where:
        case "unset":
            monkeypatch.delenv("UV_PROJECT_ENVIRONMENT")
        case "relative":
            monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "relvenv")
        case "inside":
            repo = tmp_path / "repo"
            repo.mkdir()
            subprocess.run(
                ["/usr/bin/env", "-i", "git", "init", "-q", str(repo)], check=True
            )
            monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(repo / "sub" / "venv"))
    code, lines = run(capsys)
    assert code == 2
    assert lines["UV_PROJECT_ENVIRONMENT"].startswith("FAIL UV_PROJECT_ENVIRONMENT:")
    assert not (tmp_path / "repo" / "sub").exists()  # nothing created


def test_uv_environment_outside_passes(
    healthy: Stub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = run(capsys)
    assert (
        code == 0
        and lines["UV_PROJECT_ENVIRONMENT"]
        == f"PASS UV_PROJECT_ENVIRONMENT: {tmp_path / 'venv'}"
    )


def test_uv_environment_in_this_checkout_is_caught() -> None:
    check = check_env.uv_environment_check(
        {"UV_PROJECT_ENVIRONMENT": str(REPO / ".venv")}
    )
    assert check.status is check_env.Status.FAIL and str(REPO) in check.detail


def test_unexpected_error_exits_3(
    healthy: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object) -> list[check_env.Check]:
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(check_env, "run_checks", boom)
    assert main([]) == 3


def test_no_pin_literal_in_check_env() -> None:
    """The pin has exactly one home, ``tools/normalize_input``."""
    text = (REPO / "tools" / "check_env.py").read_text()
    assert (
        PIN_BANNER[0] not in text and PIN_BANNER[1].removeprefix("Using ") not in text
    )
