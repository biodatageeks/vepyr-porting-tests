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
ISSUE_FIXTURES: Final = Path(__file__).parent / "fixtures" / "issue_status"
STUB_DIR: Final = Path(__file__).parent / "fixtures" / "gh_stub"
ISSUE: Final = 1
PR: Final = 7


def make_state(
    tmp_path: Path,
    labels: list[str],
    fixture: str = "ready",
    issue_fixture: str = "ready",
) -> Path:
    """A stub state file: ``fixture`` with ``labels`` on the PR and on issue 1.

    Issue 1 (the PR's closing issue) gets only the non-state and ``-issue``
    labels, so a PR move finds it not opted in and does not mirror (#177 part 5).
    Its ``body`` and ``comments`` come from the ``issue_status`` fixture
    ``issue_fixture`` (READY by default), so the gated issue hand-over to
    ``state:manual-reviewing-issue`` passes ``./issue_status`` (#178).
    """
    issue_doc: dict[str, Any] = json.loads(
        (ISSUE_FIXTURES / f"{issue_fixture}.json").read_text(encoding="utf-8")
    )
    doc: dict[str, Any] = json.loads(
        (FIXTURES / f"{fixture}.json").read_text(encoding="utf-8")
    )
    doc["labels"] = [{"name": n} for n in ["tooling", *labels]]
    doc["issues"] = [
        {
            "number": ISSUE,
            "body": issue_doc["body"],
            "comments": issue_doc["comments"],
            "labels": [
                lab
                for lab in doc["labels"]
                if not lab["name"].startswith("state:")
                or lab["name"].endswith("-issue")
            ],
        }
    ]
    state = tmp_path / "state"
    state.write_text(json.dumps(doc), encoding="utf-8")
    return state


def run_cli(
    state: Path, *args: str, fail: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m set_state`` with the offline ``gh`` stub first on PATH.

    ``fail`` sets ``GH_STUB_FAIL``: the stub call starting with it exits 2.
    """
    return subprocess.run(
        [sys.executable, "-m", "set_state", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={
            "PYTHONPATH": str(REPO_ROOT / "tools"),
            "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_STATE": str(state),
            **({"GH_STUB_FAIL": fail} if fail else {}),
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


def test_print_transitions_has_20_lines() -> None:
    lines = [str(t) for t in writer.TRANSITIONS]
    assert len(lines) == len(set(lines)) == 20
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


def test_issue_gate_allows_the_handover_when_ready(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing-issue"])
    done = run_cli(state, "issue", str(ISSUE), "manual-reviewing-issue")
    assert done.returncode == writer.DONE, done.stdout + done.stderr
    assert state_labels(state, Kind.ISSUE) == ["state:manual-reviewing-issue"]
    calls = log(state)
    gate_read = f"issue view {ISSUE} --json number,body,labels,comments"
    edit = [c for c in calls if " edit " in f" {c}"]
    assert gate_read in calls
    assert calls.index(gate_read) < calls.index(edit[0])
    assert len(edit) == 1


@pytest.mark.parametrize(
    "fixture",
    [
        "status-missing",
        "status-stale",
        "verdict-missing",
        "verdict-findings",
        "dry-run-mismatch",
    ],
)
def test_issue_gate_refuses_the_handover_without_ready(
    tmp_path: Path, fixture: str
) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing-issue"], issue_fixture=fixture)
    done = run_cli(state, "issue", str(ISSUE), "manual-reviewing-issue")
    assert done.returncode == writer.REFUSED
    assert f"FAIL {fixture}:" in done.stdout
    assert "./issue_status 1 is not READY" in done.stderr
    assert not [c for c in log(state) if " edit " in f" {c}"]
    assert state_labels(state, Kind.ISSUE) == ["state:auto-reviewing-issue"]


def test_issue_gate_refuses_a_dry_run_too(tmp_path: Path) -> None:
    state = make_state(
        tmp_path, ["state:auto-reviewing-issue"], issue_fixture="verdict-missing"
    )
    done = run_cli(state, "issue", str(ISSUE), "manual-reviewing-issue", "--dry-run")
    assert done.returncode == writer.REFUSED
    assert "DRY-RUN" not in done.stdout


def test_issue_gate_malformed_record_is_a_tool_error(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing-issue"])
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["issues"][0]["comments"].append(
        {
            "body": "### issue-review:v1\n\nno block\n",
            "createdAt": "2026-10-09T00:00:00Z",
        }
    )
    state.write_text(json.dumps(doc), encoding="utf-8")
    done = run_cli(state, "issue", str(ISSUE), "manual-reviewing-issue")
    assert done.returncode == writer.TOOL_ERROR
    assert not [c for c in log(state) if " edit " in f" {c}"]


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("state:implementing-issue", "auto-reviewing-issue"),
        ("state:auto-reviewing-issue", "fixing-issue"),
        ("state:manual-reviewing-issue", "fixing-issue"),
    ],
)
def test_issue_gate_leaves_other_issue_moves_ungated(
    tmp_path: Path, current: str, target: str
) -> None:
    state = make_state(tmp_path, [current], issue_fixture="status-missing")
    done = run_cli(state, "issue", str(ISSUE), target)
    assert done.returncode == writer.DONE, done.stdout + done.stderr
    assert not [c for c in log(state) if "number,body,labels,comments" in c]


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
        ["issue", "1", "bogus-issue"],
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


# --- the hand-over gate on state:manual-reviewing (#177) ---


@pytest.mark.parametrize(
    ("current", "fixture"),
    [
        ("state:auto-reviewing", "ready"),
        ("state:auto-superreviewing", "ready-superreviewed"),
    ],
)
def test_handover_move_passes_the_gate_with_one_edit(
    tmp_path: Path, current: str, fixture: str
) -> None:
    state = make_state(tmp_path, [current], fixture=fixture)
    done = run_cli(state, "pr", str(PR), "manual-reviewing")
    assert done.returncode == writer.DONE, done.stderr
    assert [c for c in log(state) if c.startswith("pr edit ")] == [
        f"pr edit {PR} --add-label state:manual-reviewing --remove-label {current}"
    ]
    assert state_labels(state, Kind.PR) == ["state:manual-reviewing"]


@pytest.mark.parametrize("fixture", ["sha-mismatch", "superreview-missing", "probes"])
def test_handover_move_refused_when_gate_fails(tmp_path: Path, fixture: str) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing"], fixture=fixture)
    done = run_cli(state, "pr", str(PR), "manual-reviewing")
    assert done.returncode == writer.REFUSED
    assert f"FAIL {fixture}:" in done.stdout
    assert "./pr_status --handover 7 is not READY" in done.stderr
    assert not [c for c in log(state) if " edit " in f" {c}"]
    assert state_labels(state, Kind.PR) == ["state:auto-reviewing"]


def test_handover_gate_leaves_other_moves_ungated(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing"], fixture="sha-mismatch")
    assert run_cli(state, "pr", str(PR), "fixing").returncode == writer.DONE
    assert state_labels(state, Kind.PR) == ["state:fixing"]


def test_handover_illegal_transition_is_refused_before_the_gate(
    tmp_path: Path,
) -> None:
    state = make_state(tmp_path, ["state:implementing"])
    done = run_cli(state, "pr", str(PR), "manual-reviewing")
    assert done.returncode == writer.REFUSED
    assert "illegal transition" in done.stderr
    assert log(state) == [f"pr view {PR} --json labels"]


def test_handover_then_owner_stage_walk(tmp_path: Path) -> None:
    state = make_state(tmp_path, ["state:auto-reviewing"])
    assert run_cli(state, "pr", str(PR), "manual-reviewing").returncode == 0
    assert run_cli(state, "pr", str(PR), "awaiting-merge").returncode == 0
    assert state_labels(state, Kind.PR) == ["state:awaiting-merge"]
    assert len([c for c in log(state) if c.startswith("pr edit ")]) == 2


def test_handover_stages_map_gated_states() -> None:
    assert writer.GATE_STAGES == {
        "state:manual-reviewing": "handover",
        "state:awaiting-merge": "owner",
    }


# --- the issue mirrors its PR (#177 part 5) ---


def mirror_state(
    tmp_path: Path, pr: str | None, *issues: tuple[int, list[str]] | dict[str, Any]
) -> Path:
    """A stub state: ``ready.json`` with PR state ``pr`` and the given closing issues.

    Each issue is ``(number, state labels)`` or a raw ``issues[]`` entry.
    """
    doc: dict[str, Any] = json.loads(
        (FIXTURES / "ready.json").read_text(encoding="utf-8")
    )
    doc["labels"] = [{"name": n} for n in ["tooling", *([pr] if pr else [])]]
    doc["issues"] = [
        entry
        if isinstance(entry, dict)
        else {
            "number": entry[0],
            "labels": [{"name": n} for n in ["tooling", *entry[1]]],
        }
        for entry in issues
    ]
    state = tmp_path / "state"
    state.write_text(json.dumps(doc), encoding="utf-8")
    return state


def issue_states(state: Path) -> dict[int, list[str]]:
    """The ``state:*`` labels of every issue in the stub file."""
    doc = json.loads(state.read_text(encoding="utf-8"))
    return {
        entry["number"]: [
            lab["name"] for lab in entry["labels"] if lab["name"].startswith("state:")
        ]
        for entry in doc["issues"]
    }


def edit(kind: Kind, number: int, old: str | None, new: str | None) -> str:
    """The logged ``gh`` call of one label edit (``writer.edit_args``, space-joined)."""
    return " ".join(writer.edit_args(number, Transition(kind, old, new), None))


IMPL: Final = "state:implementing"
AUTO: Final = "state:auto-reviewing"


def edits(state: Path) -> list[str]:
    """The ``edit`` calls in the stub log."""
    return [c for c in log(state) if " edit " in f" {c}"]


def test_mirror_moves_the_closing_issue_with_the_pr(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, ["state:implementing"]))
    done = run_cli(state, "pr", str(PR), "auto-reviewing")
    assert done.returncode == writer.DONE, done.stderr
    assert edits(state) == [
        edit(Kind.ISSUE, 158, IMPL, AUTO),
        edit(Kind.PR, PR, IMPL, AUTO),
    ]
    assert issue_states(state) == {158: ["state:auto-reviewing"]}
    assert state_labels(state, Kind.PR) == ["state:auto-reviewing"]


def test_mirror_to_each_of_several_closing_issues(tmp_path: Path) -> None:
    state = mirror_state(
        tmp_path, "state:fixing", (158, ["state:fixing"]), (159, ["state:fixing"])
    )
    assert run_cli(state, "pr", str(PR), "auto-reviewing").returncode == writer.DONE
    assert issue_states(state) == {
        158: ["state:auto-reviewing"],
        159: ["state:auto-reviewing"],
    }


@pytest.mark.parametrize("fail", ["issue edit", "pr edit", "issue edit 159"])
def test_mirror_failed_edit_changes_nothing(tmp_path: Path, fail: str) -> None:
    state = mirror_state(
        tmp_path,
        "state:implementing",
        (158, ["state:implementing"]),
        (159, ["state:implementing"]),
    )
    done = run_cli(state, "pr", str(PR), "auto-reviewing", fail=fail)
    assert done.returncode == writer.TOOL_ERROR
    assert "nothing changed" in done.stderr
    assert state_labels(state, Kind.PR) == ["state:implementing"]
    assert issue_states(state) == {
        158: ["state:implementing"],
        159: ["state:implementing"],
    }


def test_mirror_undo_is_the_inverse_edit(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, ["state:implementing"]))
    run_cli(state, "pr", str(PR), "auto-reviewing", fail="pr edit")
    assert edits(state)[-1] == edit(Kind.ISSUE, 158, AUTO, IMPL)


@pytest.mark.parametrize(
    ("labels", "shown"),
    [
        ([], "(none)"),
        (["state:manual-reviewing-issue"], "state:manual-reviewing-issue"),
    ],
)
def test_mirror_skips_an_issue_not_opted_in(
    tmp_path: Path, labels: list[str], shown: str
) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, labels))
    done = run_cli(state, "pr", str(PR), "auto-reviewing")
    assert done.returncode == writer.DONE
    assert f"note: issue 158 not mirrored ({shown})" in done.stderr
    assert [c for c in edits(state) if c.startswith("issue ")] == []
    assert issue_states(state) == {158: labels}


def test_mirror_skips_a_closed_issue(tmp_path: Path) -> None:
    closed = {
        "number": 158,
        "state": "CLOSED",
        "labels": [{"name": "state:implementing"}],
    }
    state = mirror_state(tmp_path, "state:implementing", closed)
    done = run_cli(state, "pr", str(PR), "auto-reviewing")
    assert done.returncode == writer.DONE
    assert "note: issue 158 not mirrored (closed)" in done.stderr
    assert issue_states(state) == {158: ["state:implementing"]}


def test_mirror_leaves_an_issue_in_sync_on_the_first_move(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, None, (158, ["state:implementing"]))
    done = run_cli(state, "pr", str(PR), "implementing")
    assert done.returncode == writer.DONE, done.stderr
    assert edits(state) == [f"pr edit {PR} --add-label state:implementing"]


def test_mirror_without_closing_issue_edits_the_pr_only(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, "state:implementing")
    assert run_cli(state, "pr", str(PR), "auto-reviewing").returncode == writer.DONE
    assert len(edits(state)) == 1


@pytest.mark.parametrize(
    "labels", [["state:fixing"], ["state:implementing", "state:fixing"]]
)
def test_mirror_refuses_an_out_of_sync_issue_without_edit(
    tmp_path: Path, labels: list[str]
) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, labels))
    done = run_cli(state, "pr", str(PR), "auto-reviewing")
    assert done.returncode == writer.REFUSED
    assert "issue 158" in done.stderr and "--no-mirror" in done.stderr
    assert edits(state) == []


def test_no_mirror_moves_the_pr_only(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, ["state:fixing"]))
    done = run_cli(state, "pr", str(PR), "auto-reviewing", "--no-mirror")
    assert done.returncode == writer.DONE
    assert edits(state) == [edit(Kind.PR, PR, IMPL, AUTO)]
    assert not [c for c in log(state) if "closingIssuesReferences" in c]


def test_no_mirror_is_a_usage_error_for_an_issue(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, None, (158, []))
    done = run_cli(state, "issue", "158", "implementing", "--no-mirror")
    assert done.returncode == writer.TOOL_ERROR


def test_mirror_dry_run_prints_every_edit_and_makes_none(tmp_path: Path) -> None:
    state = mirror_state(tmp_path, "state:implementing", (158, ["state:implementing"]))
    done = run_cli(state, "pr", str(PR), "auto-reviewing", "--dry-run")
    assert done.returncode == writer.DONE
    assert done.stdout.splitlines() == [
        f"DRY-RUN gh {edit(Kind.ISSUE, 158, IMPL, AUTO)}",
        f"DRY-RUN gh {edit(Kind.PR, PR, IMPL, AUTO)}",
    ]
    assert edits(state) == []


def test_mirror_waits_for_the_gate(tmp_path: Path) -> None:
    state = mirror_state(
        tmp_path, "state:manual-reviewing", (158, ["state:manual-reviewing"])
    )
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["headRefOid"] = "c" * 40
    state.write_text(json.dumps(doc), encoding="utf-8")
    done = run_cli(state, "pr", str(PR), "awaiting-merge")
    assert done.returncode == writer.REFUSED
    assert edits(state) == []
    assert issue_states(state) == {158: ["state:manual-reviewing"]}


@pytest.mark.parametrize("current", [None, "state:manual-reviewing-issue"])
def test_mirror_entry_issue_edges_into_implementing(
    tmp_path: Path, current: str | None
) -> None:
    state = mirror_state(tmp_path, None, (158, [current] if current else []))
    done = run_cli(state, "issue", "158", "implementing")
    assert done.returncode == writer.DONE, done.stderr
    assert issue_states(state) == {158: ["state:implementing"]}


@pytest.mark.parametrize("target", ["auto-reviewing", "awaiting-merge", "fixing"])
def test_mirror_entry_other_pr_states_unreachable_for_an_issue(
    tmp_path: Path, target: str
) -> None:
    state = mirror_state(tmp_path, None, (158, ["state:implementing"]))
    assert run_cli(state, "issue", "158", target).returncode == writer.REFUSED
    assert edits(state) == []


@pytest.mark.parametrize("call", ["closingIssuesReferences", "labels,state"])
def test_mirror_deep_json_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    def fake_gh(args: list[str]) -> str:
        if args[-1] == call:
            return "[" * 100000
        if args[-1] == "closingIssuesReferences":
            return json.dumps({"closingIssuesReferences": [{"number": 158}]})
        raise AssertionError(args)

    monkeypatch.setattr(writer.gate, "_gh", fake_gh)
    move = Transition(Kind.PR, "state:implementing", "state:auto-reviewing")
    with pytest.raises(writer.ToolError, match="nested too deeply"):
        writer.mirror_moves(move, PR, None)
