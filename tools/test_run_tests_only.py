"""``./run_tests --only DIR``: chosen data-test directories on a scratch root (#168).

Cargo and GitHub are faked as in :mod:`test_run_tests_cli` (no network, no real
``cargo``): the fake runner records argv and env and, while it runs, what the
``$DATA_DIRS_ROOT`` it was given holds.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from test_run_tests_cli import (
    Harness,
    Outcome,
    _FakeGh,
    _stub_engine,
    _tiny_ladder_toml,
)
from test_run_tests_cli import harness as harness  # re-exported pytest fixture

from run_tests import tests
from run_tests.verdict import Exit

SHA_LINE = re.compile(r"^vepyr sha +: [0-9a-f]{40}$", re.MULTILINE)


@dataclass
class RootSpy:
    """Fake cargo: records each call and a snapshot of ``$DATA_DIRS_ROOT``."""

    exit_code: int = 0
    calls: list[tuple[list[str], dict[str, str]]] = field(default_factory=list)
    seen: list[dict[str, bytes]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> int:
        self.calls.append((list(argv), dict(env)))
        root = Path(env[tests.ROOT_ENV])
        self.seen.append(
            {
                str(p.relative_to(root)): p.read_bytes()
                for p in sorted(root.rglob("*"))
                if p.is_file()
            }
        )
        return self.exit_code

    @property
    def root(self) -> Path:
        """The ``$DATA_DIRS_ROOT`` of the (only) call."""
        assert len(self.calls) == 1, self.calls
        return Path(self.calls[0][1][tests.ROOT_ENV])


def _data_test(parent: Path, name: str) -> Path:
    """A minimal data-test directory ``parent/name`` (``test.toml`` + ``input.vcf``)."""
    d = parent / name
    d.mkdir(parents=True)
    (d / "test.toml").write_text(f'name = "{name}"\n', encoding="utf-8")
    (d / "input.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    return d


def _tree(d: Path) -> dict[str, str]:
    """Relative path -> sha256 of every file under ``d``."""
    return {
        str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def ready(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Harness:
    """A fetched cache as ``$VEPYR_CACHE_ROOT``, a stubbed engine, one real target."""
    fetched = harness.run(
        "--cache-dir", str(harness.root), "--add-contigs", "chr21",
        "--flavours", "ensembl",
    )
    assert fetched.code == int(Exit.OK), fetched.stderr
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    _data_test(harness.repo / tests.DATA_DIR, "in_repo")
    _stub_engine(tmp_path / "src", monkeypatch)
    return harness


def _run(h: Harness, spy: RootSpy, *argv: str) -> Outcome:
    """``./run_tests --flavours ensembl --vepyr 0.7.0 <argv>`` with ``spy`` as cargo."""
    return replace(h, cargo=spy).run(  # type: ignore[arg-type]
        "--flavours", "ensembl", "--vepyr", "0.7.0", *argv,
        gh_api=_FakeGh(_tiny_ladder_toml()),
    )


def test_only_scratch_root(ready: Harness, tmp_path: Path) -> None:
    a = _data_test(tmp_path / "elsewhere", "alpha")
    b = _data_test(tmp_path / "other", "beta")
    real = ready.repo / tests.DATA_DIR
    before = _tree(real)
    spy = RootSpy()
    result = _run(ready, spy, "--only", str(a), "--only", str(b))
    assert result.code == int(Exit.OK), result.stderr
    root = spy.root
    assert root.resolve() != real.resolve()
    assert not root.resolve().is_relative_to(ready.repo.resolve())
    assert spy.seen == [
        {
            f"{d.name}/{rel}": (d / rel).read_bytes()
            for d in (a, b)
            for rel in _tree(d)
        }
    ]
    assert _tree(real) == before


def test_only_name_filter(ready: Harness, tmp_path: Path) -> None:
    spy = RootSpy()
    result = _run(ready, spy, "--only", str(_data_test(tmp_path, "alpha")))
    assert result.code == int(Exit.OK), result.stderr
    argv = spy.calls[0][0]
    assert argv[argv.index("--test") + 1] == tests.RUNNER_TARGET
    assert argv[argv.index("--") :] == ["--", "--exact", tests.RUNNER_TARGET]


def test_only_name_filter_absent_without_only(ready: Harness) -> None:
    log: list[list[str]] = []

    def cargo(argv: Sequence[str], env: Mapping[str, str]) -> int:
        log.append(list(argv))
        assert tests.ROOT_ENV not in env
        return 0

    result = replace(ready, cargo=cargo).run(  # type: ignore[arg-type]
        "--flavours", "ensembl", "--vepyr", "0.7.0",
        gh_api=_FakeGh(_tiny_ladder_toml()),
    )
    assert result.code == int(Exit.OK), result.stderr
    assert "--exact" not in log[0]
    assert "targets          : in_repo" in result.summary


def test_only_summary_sha(ready: Harness, tmp_path: Path) -> None:
    a = _data_test(tmp_path / "x", "alpha")
    b = _data_test(tmp_path / "y", "beta")
    result = _run(ready, RootSpy(), "--only", str(b), "--only", str(a))
    assert result.code == int(Exit.OK), result.stderr
    assert "targets          : beta, alpha\n" in result.summary
    assert "in_repo" not in result.summary
    assert len(SHA_LINE.findall(result.summary)) == 1


@pytest.mark.parametrize(
    ("cargo_exit", "want"), [(0, Exit.OK), (101, Exit.TESTS_FAILED)]
)
def test_only_cleanup(
    ready: Harness, tmp_path: Path, cargo_exit: int, want: Exit
) -> None:
    spy = RootSpy(exit_code=cargo_exit)
    result = _run(ready, spy, "--only", str(_data_test(tmp_path, "alpha")))
    assert result.code == int(want), result.stderr
    assert spy.seen[0], "the root held the copy while cargo ran"
    assert not spy.root.exists()


def test_only_cargo_failure(ready: Harness, tmp_path: Path) -> None:
    result = _run(
        ready, RootSpy(exit_code=101), "--only", str(_data_test(tmp_path, "alpha"))
    )
    assert result.code == int(Exit.TESTS_FAILED)
    assert "outcome          : tests_failed (exit 1)" in result.summary
    assert "cargo test exited 101 (1 target(s))" in result.summary


@pytest.mark.parametrize(
    "case", ["missing", "no_test_toml", "duplicate_basename", "same_dir_twice"]
)
def test_only_usage(
    harness: Harness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.delenv(tests.CACHE_ENV, raising=False)  # no missing-cache exit 2
    empty = tmp_path / "empty"
    empty.mkdir()
    one = _data_test(tmp_path / "a", "same")
    argv = {
        "missing": ["--only", str(tmp_path / "nope")],
        "no_test_toml": ["--only", str(empty)],
        "duplicate_basename": [
            "--only", str(one), "--only", str(_data_test(tmp_path / "b", "same")),
        ],
        "same_dir_twice": ["--only", str(one), "--only", str(one)],
    }[case]

    def never(*_a: object) -> int:
        raise AssertionError("cargo must not run on a usage error")

    result = replace(harness, cargo=never).run(*argv)  # type: ignore[arg-type]
    assert result.code == int(Exit.USAGE)
    assert tests.NOT_A_DATA_TEST in result.stderr
    assert "need a cache root" not in result.stderr
