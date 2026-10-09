"""``./run_tests --via-cli`` offline: fake vepyr CLI runner and builder (#231).

The cache is the synthetic one of :mod:`test_run_tests_cli` (fake Hub, no
network); GitHub is a fake that resolves every REF to ``"a" * 40``; the vepyr CLI
is a callable that writes a chosen VCF to ``--output_file`` (no vepyr, no cache).
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_run_tests_cli import (
    Harness,
    Outcome,
    _FakeGh,
    _tiny_ladder_toml,
    fresh_head,
)
from test_run_tests_cli import harness as harness  # re-exported pytest fixture

from run_tests import cli, tests, via_cli
from run_tests.fetch import HeadResolver
from run_tests.verdict import Exit, RunTestsError
from run_tests.via_cli import CliBuild, VepyrBuilder, body_md5

HEADER = b"##fileformat=VCFv4.2\n#CHROM\tPOS\n"
GOOD_BODY = b"21\t100\t.\tA\tG\t.\t.\tCSQ=x\n"
BAD_BODY = b"21\t100\t.\tA\tG\t.\t.\tCSQ=y\n"
GOOD_MD5 = hashlib.md5(GOOD_BODY).hexdigest()
BAD_MD5 = hashlib.md5(BAD_BODY).hexdigest()
MISMATCH_LINE = re.compile(
    r"^MISMATCH (\S+) expected=([0-9a-f]{32}) actual=([0-9a-f]{32})$"
)
BUILD = CliBuild(python=Path("/fake/python"), vepyr_sha="a" * 40, wheel_sha256="f" * 64)


@dataclass
class FakeCli:
    """Fake ``python -m vepyr annotate``: writes ``bodies[dir]`` as its output."""

    bodies: dict[str, bytes] = field(default_factory=dict)
    raise_engine: bool = False
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str]) -> int:
        argv = list(argv)
        self.calls.append(argv)
        if self.raise_engine:
            raise RunTestsError(Exit.ENGINE, "fake engine failure")
        name = Path(argv[argv.index("--input_file") + 1]).parent.name
        out = Path(argv[argv.index("--output_file") + 1])
        out.write_bytes(HEADER + self.bodies.get(name, GOOD_BODY))
        return 0


def _builder(sha: str, root: Path) -> CliBuild:
    assert sha == "a" * 40
    return BUILD


def _data_test(parent: Path, name: str, *, extra: str = "") -> Path:
    d = parent / name
    d.mkdir(parents=True)
    (d / "input.vcf").write_bytes(HEADER)
    (d / "test.toml").write_text(
        f'name = "{name}"\n\n[vepyr]\nflavour = "ensembl"\n'
        'required_contigs = ["chr21"]\neverything = true\n'
        "preserve_record_layout = true\nreference_fasta = true\n"
        f'{extra}\n[compare]\nbody_md5 = "{GOOD_MD5}"\n',
        encoding="utf-8",
    )
    return d


@pytest.fixture
def ready(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """A fetched synthetic cache exported as ``$VEPYR_CACHE_ROOT``."""
    fetched = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
    )
    assert fetched.code == int(Exit.OK), fetched.stderr
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    return harness


def _run(
    h: Harness,
    fake: FakeCli,
    *dirs: Path,
    head_resolver: HeadResolver = fresh_head,
    builder: VepyrBuilder = _builder,
) -> Outcome:
    argv = ["--via-cli", "--vepyr", "0.7.0", "--flavours", "ensembl"]
    for d in dirs:
        argv += ["--only", str(d)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(
            argv,
            lister=h.hub.lister,
            downloader=h.hub.downloader,
            cargo_runner=h.cargo,
            gh_api=_FakeGh(_tiny_ladder_toml()),
            cli_runner=fake,
            vepyr_builder=builder,
            head_resolver=head_resolver,
        )
    return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())


def _mismatches(stdout: str) -> list[tuple[str, str, str]]:
    return [m.groups() for m in map(MISMATCH_LINE.match, stdout.splitlines()) if m]  # type: ignore[misc]


def test_body_md5_rule() -> None:
    vcf = b"##h\n#CHROM\nA\n#mid\nB"
    assert body_md5(vcf) == hashlib.md5(b"A\nB").hexdigest()


@pytest.mark.parametrize("body, status", [(GOOD_BODY, "PASS"), (BAD_BODY, "MISMATCH")])
def test_named_tests_share_one_annotation_but_each_reports_progress(
    tmp_path: Path, body: bytes, status: str
) -> None:
    fixture = _data_test(tmp_path, "fixture")
    config = fixture / "test.toml"
    config.write_text(
        config.read_text()
        + '\n[[tests]]\nid = "fixture"\n[[tests]]\nid = "second_assertion"\n'
    )
    fake = FakeCli(bodies={"fixture": body})
    out = io.StringIO()
    report = via_cli.run_dirs(
        [fixture],
        build=BUILD,
        cache_root=tmp_path,
        fasta=tmp_path / "unused.fa",
        runner=fake,
        out=out,
        err=io.StringIO(),
    )
    assert len(fake.calls) == 1
    assert (report.passed if status == "PASS" else report.mismatched) == [
        "fixture",
        "second_assertion",
    ]
    lines = out.getvalue().splitlines()
    for name in ["fixture", "second_assertion"]:
        assert f"RUN {name}" in lines
        assert sum(line.startswith(f"{status} {name}") for line in lines) == 1
    assert any("1/2 tests" in line for line in lines)
    assert "2/2 tests" in lines[-1]


def test_progress_is_flushed_before_runs_and_counts_every_outcome(
    tmp_path: Path,
) -> None:
    class FlushedOutput(io.StringIO):
        visible = ""

        def flush(self) -> None:
            self.visible = self.getvalue()

    out = FlushedOutput()
    dirs = [_data_test(tmp_path, name) for name in ["multi", "skipped", "bad", "error"]]
    config = dirs[0] / "test.toml"
    config.write_text(
        config.read_text()
        + "\n[[vepyr_run]]\nbuffer_size = 5000\n[[vepyr_run]]\nbuffer_size = 5000\n"
    )
    config = dirs[1] / "test.toml"
    config.write_text(
        'skip_reason = "Unsupported symbolic deletion"\n' + config.read_text()
    )
    fake = FakeCli(bodies={"bad": BAD_BODY})
    multi_runs = 0

    def observe(argv: Sequence[str]) -> int:
        nonlocal multi_runs
        name = Path(argv[argv.index("--input_file") + 1]).parent.name
        assert f"RUN {name}\n" in out.visible
        if name == "multi":
            multi_runs += 1
            assert f"RUN multi | run {multi_runs}/2\n" in out.visible
        return 9 if name == "error" else fake(argv)

    report = via_cli.run_dirs(
        iter(dirs),
        build=BUILD,
        cache_root=tmp_path,
        fasta=tmp_path / "unused.fa",
        runner=observe,
        out=out,
        err=io.StringIO(),
    )
    assert multi_runs == 2
    assert report.passed == ["multi"]
    assert report.mismatched == ["bad"]
    assert [name for name, _ in report.errors] == ["error"]
    assert [name for name, _ in report.skipped] == ["skipped"]
    assert report.code is Exit.ENGINE
    bars = [line for line in out.visible.splitlines() if line.startswith("[")]
    assert [line.split(" tests")[0].split()[-1] for line in bars] == [
        "0/4",
        "1/4",
        "2/4",
        "3/4",
        "4/4",
        "4/4",
    ]
    assert (
        bars[-1]
        == "[####################] 4/4 tests | 1 passed, 2 failed, 1 skipped | DONE"
    )


def test_skip_is_visible_and_removing_it_restores_mismatch(tmp_path: Path) -> None:
    skipped = _data_test(tmp_path, "skipped")
    active = _data_test(tmp_path, "active")
    path = skipped / "test.toml"
    original = path.read_text()
    path.write_text('skip_reason = "Unsupported symbolic deletion"\n' + original)
    fake = FakeCli(bodies={"skipped": BAD_BODY})
    out = io.StringIO()
    kwargs = dict(
        build=BUILD,
        cache_root=tmp_path,
        fasta=tmp_path / "absent.fa",
        runner=fake,
        out=out,
    )
    report = via_cli.run_dirs([skipped, active], **kwargs)
    assert report.code is Exit.OK
    assert report.passed == ["active"]
    assert report.skipped == [("skipped", "Unsupported symbolic deletion")]
    assert len(fake.calls) == 1
    assert "SKIP skipped: Unsupported symbolic deletion" in out.getvalue()
    assert "PASS skipped" not in out.getvalue()
    assert "1 pass" in report.detail and "1 skipped" in report.detail
    path.write_text(original)
    report = via_cli.run_dirs([skipped], **kwargs)
    assert report.code is Exit.MISMATCH
    assert report.mismatched == ["skipped"]
    assert not report.skipped


@pytest.mark.parametrize("value", ['""', '"  "', "true", "42", "[]"])
def test_invalid_skip_is_usage_error(tmp_path: Path, value: str) -> None:
    directory = _data_test(tmp_path, "invalid")
    path = directory / "test.toml"
    path.write_text(f"skip_reason = {value}\n" + path.read_text())
    fake = FakeCli()
    err = io.StringIO()
    report = via_cli.run_dirs(
        [directory],
        build=BUILD,
        cache_root=tmp_path,
        fasta=tmp_path / "absent.fa",
        runner=fake,
        err=err,
    )
    assert report.code is Exit.USAGE
    assert "skip_reason must be a non-empty string" in err.getvalue()
    assert not fake.calls and not report.passed and not report.skipped


def test_a_mismatch_exit_8_and_line(ready: Harness, tmp_path: Path) -> None:
    d = _data_test(tmp_path, "one")
    got = _run(ready, FakeCli(bodies={"one": BAD_BODY}), d)
    assert got.code == int(Exit.MISMATCH) == 8, got.stderr
    assert (
        f"MISMATCH one expected={GOOD_MD5} actual={BAD_MD5}" in got.stdout.splitlines()
    )
    assert not ready.cargo.calls


def test_b_two_mismatches(ready: Harness, tmp_path: Path) -> None:
    dirs = [_data_test(tmp_path, n) for n in ("one", "two")]
    got = _run(ready, FakeCli(bodies={"one": BAD_BODY, "two": BAD_BODY}), *dirs)
    assert got.code == 8
    assert [m[0] for m in _mismatches(got.stdout)] == ["one", "two"]


def test_c_match_exit_0(ready: Harness, tmp_path: Path) -> None:
    d = _data_test(tmp_path, "one")
    fake = FakeCli()
    got = _run(ready, fake, d)
    assert got.code == 0, got.stderr
    assert "MISMATCH" not in got.stdout
    assert "PASS one" in got.stdout.splitlines()
    argv = fake.calls[0]
    assert argv[:4] == ["/fake/python", "-m", "vepyr", "annotate"]
    assert argv[argv.index("--dir_cache") + 1] == str(
        ready.root.resolve() / "116_GRCh38_ensembl"
    )
    assert re.search(r"vepyr sha +: a{40}", got.stdout)


def test_d_unmappable_wins_over_mismatch(ready: Harness, tmp_path: Path) -> None:
    bad = _data_test(tmp_path, "unmappable", extra="buffer_size = 3")
    other = _data_test(tmp_path, "mismatching")
    fake = FakeCli(bodies={"mismatching": BAD_BODY})
    got = _run(ready, fake, bad, other)
    assert got.code == int(Exit.USAGE) == 2
    assert [m[0] for m in _mismatches(got.stdout)] == ["mismatching"]
    assert "unmappable" in got.stderr and "buffer_size" in got.stderr
    assert len(fake.calls) == 1  # the unmappable directory never ran


def test_d_vepyr_run_overrides_each_reported(ready: Harness, tmp_path: Path) -> None:
    runs = "".join(f"\n[[vepyr_run]]\nbuffer_size = {n}\n" for n in (1, 2, 5000))
    d = _data_test(tmp_path, "inv", extra=runs)
    got = _run(ready, FakeCli(), d)
    assert got.code == 2
    assert "MISMATCH" not in got.stdout
    assert "run 1/3" in got.stderr and "run 2/3" in got.stderr
    assert "run 3/3" not in got.stderr


def test_e_engine_error_exit_6(ready: Harness, tmp_path: Path) -> None:
    d = _data_test(tmp_path, "one")
    got = _run(ready, FakeCli(raise_engine=True), d)
    assert got.code == int(Exit.ENGINE) == 6
    assert "MISMATCH" not in got.stdout


def test_nonzero_cli_exit_is_engine(ready: Harness, tmp_path: Path) -> None:
    d = _data_test(tmp_path, "one")
    got = _run(ready, lambda argv: 2, d)  # type: ignore[arg-type]
    assert got.code == 6
    assert "vepyr annotate exited 2" in got.stderr


def test_flag_parsed_and_off_by_default() -> None:
    """Without ``--via-cli`` the invocation keeps the cargo path (``via_cli`` False)."""
    assert cli.parse_args([]).via_cli is False
    assert cli.parse_args(["--via-cli"]).via_cli is True


def test_build_vepyr_once_per_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default builder runs git/uv once, records sha + wheel sha256, then reuses."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[:2] == ["uv", "build"]:
            out = Path(argv[argv.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "vepyr-1.0-cp39-abi3-any.whl").write_bytes(b"wheel")
        if argv[:2] == ["uv", "venv"]:
            py = Path(argv[-1]) / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text("")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(via_cli.subprocess, "run", fake_run)
    sha = "c" * 40
    first = via_cli.build_vepyr(sha, tmp_path)
    assert first.vepyr_sha == sha
    assert first.wheel_sha256 == hashlib.sha256(b"wheel").hexdigest()
    assert any(c[:3] == ["git", "fetch", "-q"] and sha in c for c in calls)
    n = len(calls)
    assert via_cli.build_vepyr(sha, tmp_path) == first
    assert len(calls) == n  # reused, nothing re-run


def test_build_failure_is_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    monkeypatch.setattr(via_cli.subprocess, "run", failing)
    with pytest.raises(RunTestsError) as caught:
        via_cli.build_vepyr("c" * 40, tmp_path)
    assert caught.value.code is Exit.ENGINE


def test_stale_pin_exits_7_before_build_or_run(ready: Harness, tmp_path: Path) -> None:
    """The #236 freshness guard also gates ``--via-cli``: no build, no vepyr run."""
    built: list[str] = []

    def spy_builder(sha: str, root: Path) -> CliBuild:
        built.append(sha)
        return BUILD

    fake = FakeCli()
    got = _run(
        ready,
        fake,
        _data_test(tmp_path, "one"),
        head_resolver=lambda repo_id, ref: "f" * 40,
        builder=spy_builder,
    )
    assert got.code == int(Exit.STALE_CACHE) == 7, got.stderr
    assert not built
    assert not fake.calls
    assert "PASS" not in got.stdout and "MISMATCH" not in got.stdout
