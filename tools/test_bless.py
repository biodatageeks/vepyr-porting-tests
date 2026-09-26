"""Tests for ``./bless`` (``tools/bless``) that need no docker, cache or network."""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Final

import pytest

from bless import BlessError, cli, ensembl, testdir, vep

REPO: Final[Path] = Path(__file__).resolve().parent.parent

INPUT_TABLE: Final[str] = (
    '[input]\ncommand = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"\n'
    'bcftools_version = "bcftools 1.23"\n'
)
BODY: Final[str] = "21\t100\t.\tC\tT\t.\t.\tCSQ=T|x\n"


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """A normalised data-test directory with a blessed-looking oracle."""
    d = tmp_path / "case"
    d.mkdir()
    (d / "input.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\n21\t100\t.\tC\tT\t.\t.\t.\n"
    )
    (d / "expected_output.vcf").write_text("##VEP=v116\n#CHROM\n" + BODY)
    md5 = hashlib.md5(BODY.encode()).hexdigest()
    (d / "test.toml").write_text(
        f'name = "case"\n\n{INPUT_TABLE}\n[vep]\n# kept\n\n'
        f'[compare]\nbody_md5 = "{md5}"\n'
    )
    return d


@pytest.fixture
def complete_cache(tmp_path: Path) -> Path:
    """A directory with the layout of a complete release-116 cache."""
    base = tmp_path / "cache" / ensembl.CACHE_SUBDIR
    for contig in ensembl.REQUIRED_CONTIGS:
        (base / contig).mkdir(parents=True)
    (base / "info.txt").write_text("species\thomo_sapiens\n")
    return tmp_path / "cache"


@pytest.fixture
def fasta(tmp_path: Path) -> Path:
    """A two-record FASTA."""
    fa = tmp_path / "g.fa"
    fa.write_text(">21 dna\nACGT\nAC\n>22\nGGGG\n")
    return fa


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Run ``bless`` in-process and return (exit code, stdout, stderr)."""
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_normalize_command_matches_the_script() -> None:
    """The #85 command duplicated in bless equals the one normalize_input writes."""
    source = (REPO / "tools" / "normalize_input").read_text()
    assert f'COMMAND_TEMPLATE: Final[str] = "{testdir.NORMALIZE_COMMAND}"' in source


def test_check_passes_and_catches_drift(
    test_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check: 0 on a matching oracle, 1 with a message after one appended byte."""
    assert run(["--check", str(test_dir)], capsys)[0] == 0
    with (test_dir / "expected_output.vcf").open("a") as fh:
        fh.write("x")
    code, _, err = run(["--check", str(test_dir)], capsys)
    assert code == 1
    assert "drifted" in err and err.count("\n") == 1


def test_check_needs_no_docker(
    test_dir: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check never looks for docker."""
    monkeypatch.setattr(vep, "require_docker", lambda: pytest.fail("docker consulted"))
    assert run(["--check", str(test_dir)], capsys)[0] == 0


@pytest.mark.parametrize(
    ("flags", "named"),
    [
        (["--vep-fasta", "/x.fa"], ["--vep-cache-dir", "--download-vep-cache-to-dir"]),
        (["--vep-cache-dir", "/c"], ["--vep-fasta", "--download-vep-fasta-to"]),
        (
            [
                "--vep-cache-dir",
                "/c",
                "--download-vep-cache-to-dir",
                "/d",
                "--vep-fasta",
                "/x.fa",
            ],
            ["--vep-cache-dir", "--download-vep-cache-to-dir"],
        ),
        (
            [
                "--vep-cache-dir",
                "/c",
                "--vep-fasta",
                "/x.fa",
                "--download-vep-fasta-to",
                "/y.fa",
            ],
            ["--vep-fasta", "--download-vep-fasta-to"],
        ),
    ],
)
def test_flag_pairs(flags: list[str], named: list[str], test_dir: Path, capsys) -> None:
    """Neither or both flags of a pair: exit 1, one line naming both flags."""
    code, _, err = run([*flags, str(test_dir)], capsys)
    assert code == 1
    assert err.count("\n") == 1
    assert all(n in err for n in named)


def test_incomplete_cache_is_refused_and_untouched(
    tmp_path: Path, test_dir: Path, fasta: Path, capsys
) -> None:
    """An empty cache dir exits 1 as incomplete and stays empty."""
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err = run(
        ["--vep-cache-dir", str(empty), "--vep-fasta", str(fasta), str(test_dir)],
        capsys,
    )
    assert code == 1 and "not a complete" in err
    assert list(empty.iterdir()) == []


def test_partial_cache_names_missing_contigs(complete_cache: Path) -> None:
    """A missing contig directory is named."""
    (complete_cache / ensembl.CACHE_SUBDIR / "MT").rmdir()
    assert ensembl.missing_cache_parts(complete_cache) == [
        f"{ensembl.CACHE_SUBDIR}/MT/"
    ]


def test_missing_docker(
    complete_cache: Path, fasta: Path, test_dir: Path, capsys, monkeypatch
) -> None:
    """No docker on PATH: exit 1, message names docker."""
    monkeypatch.setenv("PATH", "/nonexistent")
    code, _, err = run(
        [
            "--vep-cache-dir",
            str(complete_cache),
            "--vep-fasta",
            str(fasta),
            str(test_dir),
        ],
        capsys,
    )
    assert code == 1 and "docker" in err


def test_unreadable_fasta(
    complete_cache: Path, tmp_path: Path, test_dir: Path, capsys
) -> None:
    """A missing --vep-fasta exits 1 naming the flag."""
    code, _, err = run(
        [
            "--vep-cache-dir",
            str(complete_cache),
            "--vep-fasta",
            str(tmp_path / "no.fa"),
            str(test_dir),
        ],
        capsys,
    )
    assert code == 1 and "--vep-fasta" in err


def test_input_provenance_required(
    complete_cache: Path, fasta: Path, test_dir: Path, capsys
) -> None:
    """No #85 command in [input]: refused, naming test.toml and [input]."""
    toml = test_dir / "test.toml"
    toml.write_text(
        toml.read_text().replace(
            'command = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"\n', ""
        )
    )
    code, _, err = run(
        [
            "--vep-cache-dir",
            str(complete_cache),
            "--vep-fasta",
            str(fasta),
            str(test_dir),
        ],
        capsys,
    )
    assert code == 1
    assert "test.toml" in err and "[input]" in err and "command" in err


def test_dry_run_prints_fixed_command(test_dir: Path, capsys) -> None:
    """--dry-run prints the tag and the fixed VEP command once each."""
    code, out, _ = run(
        ["--dry-run", "--vep-cache-dir", "/c", "--vep-fasta", "/x.fa", str(test_dir)],
        capsys,
    )
    assert code == 0
    assert (
        out.count("--species homo_sapiens --cache_version 116 --assembly GRCh38") == 1
    )
    assert out.count(vep.IMAGE_TAG) == 1


def test_reproduce_requires_check(test_dir: Path, capsys) -> None:
    """--reproduce alone is refused with a readable line."""
    code, _, err = run(
        ["--reproduce", "--vep-cache-dir", "/c", "--vep-fasta", "/x.fa", str(test_dir)],
        capsys,
    )
    assert code == 1 and "--check" in err


def test_set_keys_keeps_comments_and_round_trips(test_dir: Path) -> None:
    """set_keys fills existing and new tables without touching anything else."""
    text = (test_dir / "test.toml").read_text()
    out = testdir.set_keys(
        text,
        {
            "vep": {"image": "ensemblorg/ensembl-vep@sha256:ab", "date": "2026-09-23"},
            "compare": {"body_md5": "0" * 32},
        },
    )
    assert "# kept\n" in out
    parsed = tomllib.loads(out)
    assert parsed["vep"] == {
        "image": "ensemblorg/ensembl-vep@sha256:ab",
        "date": "2026-09-23",
    }
    assert parsed["compare"]["body_md5"] == "0" * 32
    assert len(re.findall(r"^body_md5 = ", out, re.M)) == 1
    assert re.search(r'^image = "ensemblorg/ensembl-vep@sha256:', out, re.M)


def test_set_keys_appends_missing_table() -> None:
    """A missing table is appended."""
    out = testdir.set_keys('name = "x"\n', {"compare": {"body_md5": "f" * 32}})
    assert tomllib.loads(out) == {"name": "x", "compare": {"body_md5": "f" * 32}}


def test_fai_matches_samtools_format(fasta: Path) -> None:
    """The built .fai has samtools faidx's five columns."""
    fai = ensembl.ensure_fai(fasta)
    assert fai.read_text() == "21\t6\t8\t4\t5\n22\t4\t20\t4\t5\n"


def test_wrapper_runs_without_path(test_dir: Path) -> None:
    """./bless --check works with PATH=/nonexistent (issue #32 AC9)."""
    done = subprocess.run(
        [str(REPO / "bless"), "--check", str(test_dir)],
        env={"PATH": "/nonexistent", "HOME": str(Path.home())},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_bless_error_is_runtime_error() -> None:
    """BlessError stays catchable as RuntimeError."""
    assert issubclass(BlessError, RuntimeError)


def _fake_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the docker lookup and digest resolution (no daemon needed)."""
    monkeypatch.setattr(vep, "require_docker", lambda: "docker")
    monkeypatch.setattr(
        vep, "resolve_digest", lambda _d, _r: f"{vep.IMAGE_REPO}@sha256:{'a' * 64}"
    )


def test_default_docker_work_dir_is_repo_dot_bless() -> None:
    """The default is <repo-root>/.bless/, located from the source, not the cwd."""
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "tools" / "bless" / "cli.py").is_file()
    assert cli.DEFAULT_DOCKER_WORK_DIR == repo_root / ".bless"
    assert ".bless/" in (repo_root / ".gitignore").read_text().splitlines()


@pytest.mark.parametrize("override", [False, True], ids=["default", "override"])
def test_docker_work_dir_resolution(
    override: bool,
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry run and real run both place the work dir under the resolved root.

    Without the flag the root is ``cli.DEFAULT_DOCKER_WORK_DIR`` (patched to a tmp
    path so the test never writes into the checkout); with it, the flag's path,
    a relative one resolved against the cwd.
    """
    default_root = tmp_path / "default-root"
    monkeypatch.setattr(cli, "DEFAULT_DOCKER_WORK_DIR", default_root)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "override" if override else default_root
    flags = ["--docker-work-dir", "override"] if override else []
    base = ["--vep-cache-dir", str(complete_cache), "--vep-fasta", str(fasta)]

    code, out, _ = run([*flags, "--dry-run", *base, str(test_dir)], capsys)
    assert code == 0
    assert f"{root}/bless-XXXXXX:{vep.WORK_MOUNT}" in out

    _fake_docker(monkeypatch)
    probed: list[list[Path]] = []

    def refuse(_docker: str, _image: str, paths: list[Path]) -> None:
        probed.append(paths)
        raise BlessError("stop before VEP")

    monkeypatch.setattr(vep, "require_mountable", refuse)
    code, _, err = run([*flags, *base, str(test_dir)], capsys)
    assert code == 1 and "stop before VEP" in err
    work = probed[0][-1]
    assert work.parent == root and work.name.startswith("bless-")
    assert not work.exists()  # removed on failure


def test_unshared_docker_work_dir_gets_the_mount_error(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmountable --docker-work-dir fails with require_mountable's own message."""
    _fake_docker(monkeypatch)
    unshared = tmp_path / "unshared"

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        host = Path(argv[argv.index("-v") + 1].rsplit(":", 2)[0])
        if host.is_relative_to(unshared):
            return subprocess.CompletedProcess(
                argv,
                125,
                "",
                "docker: Error response from daemon: Mounts denied: \n"
                f"The path {host} is not shared from the host and is not known "
                "to Docker.\n",
            )
        return subprocess.CompletedProcess(
            argv, 0, "\n".join(p.name for p in host.iterdir()), ""
        )

    monkeypatch.setattr(vep.subprocess, "run", fake_run)
    code, _, err = run(
        [
            "--docker-work-dir",
            str(unshared),
            "--vep-cache-dir",
            str(complete_cache),
            "--vep-fasta",
            str(fasta),
            str(test_dir),
        ],
        capsys,
    )
    assert code == 1
    assert err.startswith(f"bless: docker cannot mount {unshared}/bless-")
    assert "is not shared from the host" in err
    assert "Settings > Resources > File sharing" in err


PINNED: Final[str] = f"{vep.IMAGE_REPO}@sha256:{'a' * 64}"


def _fake_vep_container(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    """Fake docker end to end: record each ``docker run`` argv, write an oracle.

    The fake container writes ``expected_output.vcf`` with :data:`BODY` into the
    bind-mounted work directory, so a bless or reproduce completes without docker.
    """
    _fake_docker(monkeypatch)
    monkeypatch.setattr(vep, "require_mountable", lambda _d, _i, _p: None)

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        work = next(
            Path(v.removesuffix(f":{vep.WORK_MOUNT}"))
            for v in argv
            if v.endswith(f":{vep.WORK_MOUNT}")
        )
        (work / testdir.ORACLE_NAME).write_text("##VEP=v116\n#CHROM\n" + BODY)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(vep.subprocess, "run", fake_run)


def _vep_part(argv: list[str]) -> list[str]:
    """The VEP argv inside a ``docker run`` argv (everything after the image)."""
    return argv[argv.index(PINNED) + 1 :]


def _set_vep(test_dir: Path, command: str) -> None:
    """Give ``test_dir`` a pinned image and the recorded ``command``."""
    toml = test_dir / "test.toml"
    toml.write_text(
        testdir.set_keys(
            toml.read_text(), {"vep": {"image": PINNED, "command": command}}
        )
    )


def test_vep_flag_recorded_in_command(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bless with --vep-flag records exactly the argv VEP ran with."""
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    code, _, err = run(
        [
            "--docker-work-dir",
            str(tmp_path / "w"),
            "--vep-cache-dir",
            str(complete_cache),
            "--vep-fasta",
            str(fasta),
            "--vep-flag=--check_existing",
            str(test_dir),
        ],
        capsys,
    )
    assert code == 0, err
    ran = _vep_part(calls[-1])
    assert ran == [*vep.VEP_ARGV, "--check_existing"]
    recorded = tomllib.loads((test_dir / "test.toml").read_text())["vep"]["command"]
    assert recorded == shlex.join(ran)


def _set_flags(test_dir: Path, flags: list[str], command: str | None = None) -> None:
    """Record ``flags`` as ``[vep] extra_flags`` and a matching (or given) command."""
    toml = test_dir / "test.toml"
    toml.write_text(
        testdir.set_keys(
            toml.read_text(),
            {
                "vep": {
                    "extra_flags": flags,
                    "image": PINNED,
                    "command": vep.vep_command(tuple(flags))
                    if command is None
                    else command,
                }
            },
        )
    )


def _bless_argv(test_dir: Path, cache: Path, fasta: Path, tmp_path: Path) -> list[str]:
    """Arguments of a real (faked-docker) bless of ``test_dir``."""
    return [
        "--docker-work-dir",
        str(tmp_path / "w"),
        "--vep-cache-dir",
        str(cache),
        "--vep-fasta",
        str(fasta),
        str(test_dir),
    ]


def test_bless_records_extra_flags_list(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--vep-flag= writes [vep] extra_flags as a list; no flag writes no key."""
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    argv = _bless_argv(test_dir, complete_cache, fasta, tmp_path)
    code, _, err = run(argv, capsys)
    assert code == 0, err
    assert (
        "extra_flags" not in tomllib.loads((test_dir / "test.toml").read_text())["vep"]
    )
    code, _, err = run(["--vep-flag=--check_existing", *argv], capsys)
    assert code == 0, err
    text = (test_dir / "test.toml").read_text()
    assert 'extra_flags = ["--check_existing"]\n' in text
    recorded = tomllib.loads(text)["vep"]
    assert recorded["extra_flags"] == ["--check_existing"]
    assert recorded["command"] == vep.vep_command(("--check_existing",))
    assert _vep_part(calls[-1]) == [*vep.VEP_ARGV, "--check_existing"]


def test_reproduce_reads_extra_flags_list(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--check --reproduce passes [vep] extra_flags, in order, to docker."""
    _set_flags(test_dir, ["--check_existing"])
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    code, _, err = run(
        [
            "--check",
            "--reproduce",
            *_bless_argv(test_dir, complete_cache, fasta, tmp_path),
        ],
        capsys,
    )
    assert code == 0, err
    assert _vep_part(calls[-1]) == [*vep.VEP_ARGV, "--check_existing"]


def test_reproduce_refuses_tampered_command(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A [vep] command not generated from [vep] extra_flags exits 1, no run.

    The image is validly pinned and extra_flags is allowed, so the command is
    the only cause; the untampered file exits 0.
    """
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    argv = [
        "--check",
        "--reproduce",
        *_bless_argv(test_dir, complete_cache, fasta, tmp_path),
    ]
    for command in (
        vep.VEP_COMMAND,  # valid-looking, but drops the listed flag
        "vep --offline --cache --fa /x",  # not the fixed prefix
        vep.VEP_COMMAND.replace("--offline ", "") + " --check_existing",
        vep.VEP_COMMAND + " '--check_existing'",  # quoted: not canonical
        vep.VEP_COMMAND + "  --check_existing",  # extra whitespace
        vep.vep_command(("--check_existing",)) + " ",  # trailing whitespace
    ):
        _set_flags(test_dir, ["--check_existing"], command)
        code, _, err = run(argv, capsys)
        assert code == 1, command
        assert "test.toml" in err and "[vep] command" in err, err
    assert calls == []
    _set_flags(test_dir, ["--check_existing"])
    code, _, err = run(argv, capsys)
    assert code == 0, err


def test_reproduce_refuses_unlisted_extra_flag(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_flags outside the allowlist, repeated or mistyped exits 1, no run.

    ``command`` is regenerated to match, so the list is the only cause.
    """
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    argv = [
        "--check",
        "--reproduce",
        *_bless_argv(test_dir, complete_cache, fasta, tmp_path),
    ]
    for flags in (["--fa"], ["--check_existing", "--check_existing"]):
        _set_flags(test_dir, flags)
        code, _, err = run(argv, capsys)
        assert code == 1, flags
        assert "test.toml" in err and "[vep] extra_flags" in err, err
    toml = test_dir / "test.toml"
    good = toml.read_text().replace(
        'extra_flags = ["--check_existing", "--check_existing"]',
        'extra_flags = ["--check_existing"]',
    )
    for bad in ('extra_flags = "--check_existing"', "extra_flags = [1]"):
        toml.write_text(good.replace('extra_flags = ["--check_existing"]', bad))
        code, _, err = run(argv, capsys)
        assert code == 1 and "must be an array of strings" in err, err
    assert calls == []
    _set_flags(test_dir, ["--check_existing"])
    code, _, err = run(argv, capsys)
    assert code == 0, err


def test_cli_flag_and_list_must_agree(
    complete_cache: Path,
    fasta: Path,
    test_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-bless uses the recorded list; a typed flag must equal it."""
    calls: list[list[str]] = []
    _fake_vep_container(monkeypatch, calls)
    argv = _bless_argv(test_dir, complete_cache, fasta, tmp_path)
    code, _, err = run(["--vep-flag=--check_existing", *argv], capsys)
    assert code == 0, err
    assert tomllib.loads((test_dir / "test.toml").read_text())["vep"][
        "extra_flags"
    ] == ["--check_existing"]
    code, _, err = run(["--vep-flag=--check_existing", *argv], capsys)
    assert code == 0, err
    code, _, err = run(argv, capsys)  # no CLI flag: the list is used
    assert code == 0, err
    assert _vep_part(calls[-1]) == [*vep.VEP_ARGV, "--check_existing"]
    toml = test_dir / "test.toml"
    # A second allowed flag, so that the recorded list is valid but differs.
    monkeypatch.setattr(vep, "ALLOWED_VEP_FLAGS", ("--check_existing", "--x"))
    _set_flags(test_dir, ["--x"])
    before = toml.read_bytes()
    code, _, err = run(["--vep-flag=--check_existing", *argv], capsys)
    assert code == 1, err
    assert "--vep-flag=" in err and "[vep] extra_flags" in err, err
    assert toml.read_bytes() == before


def test_check_refuses_typed_flags(
    test_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--vep-flag with --check (plain or --reproduce) exits 1, naming the option.

    The image is validly pinned and the command is the fixed one, so the typed
    flag is the only reason for the refusal.
    """
    _set_vep(test_dir, vep.VEP_COMMAND)
    base = ["--vep-flag=--check_existing", str(test_dir)]
    for mode in (["--check"], ["--check", "--reproduce", "--vep-cache-dir", "/c"]):
        code, _, err = run([*mode, "--vep-fasta", "/x.fa", *base], capsys)
        assert code == 1 and err.startswith("bless: --vep-flag is refused with --check")
    code, _, err = run(["--check", str(test_dir)], capsys)
    assert code == 0, err


def test_vep_flag_rejections(capsys: pytest.CaptureFixture[str]) -> None:
    """parse_extra accepts only exact allowlist tokens, each once.

    Also: every allowlist entry is a boolean ``--name`` flag, and the bare
    ``--vep-flag <flag>`` form is refused with a message naming the ``=`` form.
    """
    for allowed in vep.ALLOWED_VEP_FLAGS:
        assert allowed.startswith("--"), allowed
        assert "=" not in allowed and not any(c.isspace() for c in allowed), allowed
    with pytest.raises(SystemExit) as stop:
        cli.main(["--dry-run", "--vep-flag", "--check_existing", "x"])
    assert stop.value.code == 2
    assert "--vep-flag needs the = form" in capsys.readouterr().err
    assert vep.parse_extra(["--check_existing"]) == ("--check_existing",)
    assert vep.parse_extra([]) == ()
    for flag in (
        "--fa",
        "--input_f",
        "-i",
        "--input_file",
        "--output_file",
        "--fasta",
        "--force",
        "--vcf",
        "--not_a_vep_flag",
        "--check_existing=1",
    ):
        with pytest.raises(BlessError) as exc:
            vep.parse_extra([flag])
        assert str(exc.value) == (
            f"--vep-flag: {flag} is not allowed; allowed: --check_existing"
        )
    with pytest.raises(BlessError, match="given twice"):
        vep.parse_extra(["--check_existing", "--check_existing"])
