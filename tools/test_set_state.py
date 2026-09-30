"""Tests for ``set_state``: every transition, refusals, gate, dry-run, usage errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

from set_state import writer
from set_state.writer import Kind, Transition

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURES: Final = Path(__file__).parent / "fixtures" / "pr_status"
STUB_DIR: Final = Path(__file__).parent / "fixtures" / "gh_stub"
ISSUE: Final = 1
PR: Final = 7


def make_state(tmp_path: Path, labels: list[str], fixture: str = "ready") -> Path:
    """A stub state file: ``fixture`` with ``labels`` on both the PR and issue 1."""
    doc: dict[str, Any] = json.loads(
        (FIXTURES / f"{fixture}.json").read_text(encoding="utf-8")
    )
    doc["labels"] = [{"name": n} for n in ["tooling", *labels]]
    doc["issues"] = [{"number": ISSUE, "labels": doc["labels"]}]
    state = tmp_path / "state"
    state.write_text(json.dumps(doc), encoding="utf-8")
    return state


def run_cli(state: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m set_state`` with the offline ``gh`` stub first on PATH."""
    return subprocess.run(
        [sys.executable, "-m", "set_state", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={
            "PYTHONPATH": str(REPO_ROOT / "tools"),
            "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_STATE": str(state),
        },
        check=False,
    )


def log(state: Path) -> list[str]:
    """The stub's call log."""
    path = state.with_name(state.name + ".log")
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def state_labels(state: Path, kind: Kind) -> list[str]:
    """The ``state:*`` labels in the stub file for ``kind``."""
    doc = json.loads(state.read_text(encoding="utf-8"))
    target = doc if kind is Kind.PR else doc["issues"][0]
    return [lab["name"] for lab in target["labels"] if lab["name"].startswith("state:")]


def test_print_transitions_has_18_lines() -> None:
    lines = [str(t) for t in writer.TRANSITIONS]
    assert len(lines) == len(set(lines)) == 18
    assert lines[0] == "issue (none) -> state:implementing-issue"
    assert lines[-1] == "issue state:manual-reviewing-issue -> (none)"


def test_agents_md_carries_the_table() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
    assert all(str(t) in agents for t in writer.TRANSITIONS)


@pytest.mark.parametrize("move", writer.TRANSITIONS, ids=str)
def test_each_transition_is_applied_with_one_edit(
    tmp_path: Path, move: Transition
) -> None:
    state = make_state(tmp_path, [move.old] if move.old else [])
    number = PR if move.kind is Kind.PR else ISSUE
    target = ["--clear"] if move.new is None else [move.new]
    done = run_cli(state, str(move.kind), str(number), *target)
    assert done.returncode == writer.DONE, done.stdout + done.stderr
    assert state_labels(state, move.kind) == ([move.new] if move.new else [])
    edits = [c for c in log(state) if " edit " in f" {c}"]
    assert edits == [" ".join(writer.edit_args(number, move, None))]


@pytest.mark.parametrize(
    ("kind", "current", "args"),
    [
        ("pr", [], ["awaiting-merge"]),
        ("pr", ["state:implementing"], ["manual-reviewing"]),
        ("pr", ["state:awaiting-merge"], ["implementing"]),
        ("pr", ["state:implementing"], ["--clear"]),
        ("pr", ["state:implementing", "state:fixing"], ["auto-reviewing"]),
        ("pr", ["state:implementing-issue"], ["auto-reviewing"]),
        ("issue", [], ["auto-reviewing-issue"]),
        ("issue", ["state:fixing-issue"], ["--clear"]),
    ],
)
def test_illegal_moves_are_refused_without_edit(
    tmp_path: Path, kind: str, current: list[str], args: list[str]
) -> None:
    state = make_state(tmp_path, current)
    number = str(PR if kind == "pr" else ISSUE)
    assert run_cli(state, kind, number, *args).returncode == writer.REFUSED
    assert not [c for c in log(state) if " edit " in f" {c}"]


def test_awaiting_merge_refused_when_gate_fails(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:manual-reviewing"], fixture="probes")
    done = run_cli(state, "pr", str(PR), "awaiting-merge")
    assert done.returncode == writer.REFUSED
    assert "FAIL probes:" in done.stdout
    assert not [c for c in log(state) if " edit " in f" {c}"]


def test_dry_run_prints_the_edit_and_makes_none(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing"])
    done = run_cli(state, "pr", str(PR), "state:fixing", "--dry-run")
    assert (
        done.stdout == f"DRY-RUN gh pr edit {PR} --add-label state:fixing "
        "--remove-label state:auto-reviewing\n"
    )
    assert state_labels(state, Kind.PR) == ["state:auto-reviewing"]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["pr", "x", "fixing"],
        ["pr", "7", "bogus"],
        ["pr", "7", "implementing-issue"],
        ["issue", "1", "implementing"],
        ["widget", "7", "fixing"],
        ["pr", "7", "fixing", "extra"],
        ["issue", "1", "fixing-issue", "--clear"],
    ],
)
def test_usage_errors_exit_2(tmp_path: Path, args: list[str]) -> None:
    state = make_state(tmp_path, ["state:implementing"])
    assert run_cli(state, *args).returncode == writer.TOOL_ERROR


def test_gh_failure_exits_2(tmp_path: Path) -> None:
    assert (
        run_cli(tmp_path / "missing", "pr", str(PR), "fixing").returncode
        == writer.TOOL_ERROR
    )


def test_repo_flag_is_passed_to_gh() -> None:
    move = Transition(Kind.PR, None, "state:implementing")
    assert writer.edit_args(3, move, "o/r") == [
        "pr",
        "edit",
        "3",
        "--repo",
        "o/r",
        "--add-label",
        "state:implementing",
    ]
