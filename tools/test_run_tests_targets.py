"""Target discovery for ``./run_tests``: data-test directories (issue #80)."""

from __future__ import annotations

from pathlib import Path

from run_tests import tests


def _make(repo: Path, name: str, *, toml: bool = True) -> None:
    directory = repo / tests.DATA_DIR / name
    directory.mkdir(parents=True)
    if toml:
        (directory / "test.toml").write_text(f'name = "{name}"\n')


def test_data_targets_lists_directories_with_test_toml(tmp_path: Path) -> None:
    _make(tmp_path, "zeta")
    _make(tmp_path, "alpha")
    _make(tmp_path, "no_toml", toml=False)
    (tmp_path / tests.DATA_DIR / "stray.txt").write_text("not a test\n")
    (tmp_path / "tests" / "data_legacy.rs").write_text("// per-file test\n")
    assert tests.data_targets(tmp_path) == ("alpha", "zeta")


def test_data_targets_is_empty_without_tests_data(tmp_path: Path) -> None:
    assert tests.data_targets(tmp_path) == ()


def test_data_targets_of_this_repository_excludes_the_selftest_fixture() -> None:
    repo = Path(__file__).resolve().parents[1]
    assert "case" not in tests.data_targets(repo)


def test_cargo_argv_runs_the_generic_runner_once_for_all_data_targets() -> None:
    argv = tests.cargo_argv(["alpha", "zeta"], config=Path("/tmp/patch.toml"))
    assert argv == [
        "cargo",
        "test",
        "--no-fail-fast",
        "--config",
        "/tmp/patch.toml",
        "--test",
        "data_dirs",
    ]


def test_cargo_argv_without_data_targets_names_no_test() -> None:
    assert "--test" not in tests.cargo_argv([])


def test_list_table_prints_directory_names() -> None:
    body = tests.list_table(("alpha", "zeta"))
    assert "data  alpha\n" in body
    assert "run_tests: 2 data-problem target(s)" in body
