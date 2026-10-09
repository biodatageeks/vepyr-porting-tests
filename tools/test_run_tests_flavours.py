"""``./run_tests --flavours F`` selects the data-tests too (issue #257).

Offline, as in :mod:`test_run_tests_only` and :mod:`test_run_tests_via_cli`: the
cache is the synthetic one of :mod:`test_run_tests_cli` (fake Hub), GitHub, cargo
and the vepyr CLI are fakes. Two fixture data-tests live in the throwaway repo's
``tests/data``: ``ens`` (``[vepyr] flavour = "ensembl"``) and ``mer``
(``flavour = "merged"``).
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest
from test_run_tests_cli import (
    Harness,
    Outcome,
    _FakeGh,
    _stub_engine,
    _tiny_ladder_toml,
    fresh_head,
)
from test_run_tests_cli import harness as harness  # re-exported pytest fixture
from test_run_tests_only import RootSpy
from test_run_tests_via_cli import GOOD_MD5, HEADER, FakeCli, _builder

from run_tests import cli, tests
from run_tests.verdict import Exit

ALL_FLAVOURS = "ensembl,refseq,merged"
SKIP_LINE = "skipped (flavour not selected): 1 (ens)"


def _data_test(parent: Path, name: str, flavour: str) -> Path:
    """A data-test directory ``parent/name`` with ``[vepyr] flavour = flavour``."""
    d = parent / name
    d.mkdir(parents=True)
    (d / "input.vcf").write_bytes(HEADER)
    (d / "test.toml").write_text(
        f'name = "{name}"\n\n[vepyr]\nflavour = "{flavour}"\n'
        'required_contigs = ["chr21"]\neverything = true\n'
        "preserve_record_layout = true\nreference_fasta = true\n"
        f'\n[compare]\nbody_md5 = "{GOOD_MD5}"\n',
        encoding="utf-8",
    )
    return d


@pytest.fixture
def ready(harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    """Every flavour fetched as ``$VEPYR_CACHE_ROOT``; fixtures ``ens`` and ``mer``."""
    fetched = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        ALL_FLAVOURS,
    )
    assert fetched.code == int(Exit.OK), fetched.stderr
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    data = harness.repo / tests.DATA_DIR
    _data_test(data, "ens", "ensembl")
    _data_test(data, "mer", "merged")
    _stub_engine(tmp_path / "src", monkeypatch)
    return harness


def _run(h: Harness, cargo: object, *argv: str) -> Outcome:
    """``./run_tests --vepyr 0.7.0 <argv>`` with ``cargo`` as the cargo runner."""
    return replace(h, cargo=cargo).run(  # type: ignore[arg-type]
        "--vepyr", "0.7.0", *argv, gh_api=_FakeGh(_tiny_ladder_toml())
    )


def test_flavours_filters_cargo_root(ready: Harness) -> None:
    spy = RootSpy()
    result = _run(ready, spy, "--flavours", "merged")
    assert result.code == int(Exit.OK), result.stderr
    assert {rel.split("/")[0] for rel in spy.seen[0]} == {"mer"}
    argv = spy.calls[0][0]
    assert argv[argv.index("--") :] == ["--", "--exact", tests.RUNNER_TARGET]
    assert "targets          : mer\n" in result.summary


def test_flavours_default_runs_all(ready: Harness) -> None:
    envs: list[dict[str, str]] = []

    def cargo(argv: Sequence[str], env: Mapping[str, str]) -> int:
        envs.append(dict(env))
        return 0

    result = _run(ready, cargo)
    assert result.code == int(Exit.OK), result.stderr
    assert len(envs) == 1
    assert tests.ROOT_ENV not in envs[0], "nothing dropped: tests/data walked as-is"
    assert "targets          : ens, mer\n" in result.summary
    assert tests.FLAVOUR_NOT_SELECTED not in result.stdout


def test_flavours_skip_reported(ready: Harness) -> None:
    result = _run(ready, RootSpy(), "--flavours", "merged")
    assert result.code == int(Exit.OK), result.stderr
    assert SKIP_LINE in result.summary.splitlines()
    listed = _run(ready, RootSpy(), "--list", "--flavours", "merged")
    assert listed.code == int(Exit.OK)
    assert "data  mer" in listed.stdout.splitlines()
    assert "skip  ens  (flavour not selected: ensembl)" in listed.stdout.splitlines()
    assert "data  ens" not in listed.stdout.splitlines()


def test_flavours_intersects_only(ready: Harness) -> None:
    def never(*_a: object) -> int:
        raise AssertionError("nothing is selected: cargo must not run")

    ens = ready.repo / tests.DATA_DIR / "ens"
    result = _run(ready, never, "--only", str(ens), "--flavours", "merged")
    # Open question B, recommended default: an empty selection exits 0.
    assert result.code == int(Exit.OK), result.stderr
    assert SKIP_LINE in result.summary.splitlines()
    assert "targets          : (none)\n" in result.summary
    assert "0 data-problem target(s); nothing to run" in result.summary


def test_flavours_via_cli(ready: Harness) -> None:
    fake = FakeCli()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(
            ["--via-cli", "--vepyr", "0.7.0", "--flavours", "merged"],
            lister=ready.hub.lister,
            downloader=ready.hub.downloader,
            cargo_runner=ready.cargo,
            gh_api=_FakeGh(_tiny_ladder_toml()),
            cli_runner=fake,
            vepyr_builder=_builder,
            head_resolver=fresh_head,
        )
    assert code == int(Exit.OK), err.getvalue()
    annotated = [
        Path(argv[argv.index("--input_file") + 1]).parent.name for argv in fake.calls
    ]
    assert annotated == ["mer"]
    assert SKIP_LINE in out.getvalue().splitlines()
    assert not ready.cargo.calls
