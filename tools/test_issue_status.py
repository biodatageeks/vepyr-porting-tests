"""Tests for ``issue_status``: check ids, body pinning, schema, real mode (stub)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest

from issue_status import gate

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURES: Final = Path(__file__).parent / "fixtures" / "issue_status"
STUB_DIR: Final = Path(__file__).parent / "fixtures" / "gh_stub"

type Doc = dict[str, Any]


def fixture(name: str) -> Doc:
    """Load one fixture document by its base name."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def ids(doc: Doc) -> list[str]:
    """The failed check ids for ``doc``."""
    return [str(f.check) for f in gate.evaluate(doc)]


def block(doc: Doc, marker: str, index: int = 0) -> dict[str, Any]:
    """The json block of the ``index``-th comment carrying ``marker``."""
    bodies = [
        c["body"] for c in doc["comments"] if gate.first_line(c["body"]) == marker
    ]
    data = gate.json_block(bodies[index])
    assert isinstance(data, dict)
    return data


def rewrite(doc: Doc, marker: str, edit: Callable[[dict[str, Any]], None]) -> Doc:
    """A copy of ``doc`` with ``edit`` applied to every ``marker`` json block."""
    out = copy.deepcopy(doc)
    for comment in out["comments"]:
        if gate.first_line(comment["body"]) == marker:
            data = gate.json_block(comment["body"])
            assert isinstance(data, dict)
            edit(data)
            comment["body"] = f"{marker}\n\n```json\n{json.dumps(data)}\n```\n"
    return out


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m issue_status`` the way the root wrapper does."""
    base = {"PYTHONPATH": str(REPO_ROOT / "tools"), "PATH": os.environ["PATH"]}
    return subprocess.run(
        [sys.executable, "-m", "issue_status", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**base, **(env or {})},
        check=False,
    )


# --- the fixture set --------------------------------------------------------


def test_ready_fixture_is_ready() -> None:
    assert ids(fixture("ready")) == []


def test_ready_fixture_is_before_the_handover() -> None:
    """``ready.json`` carries the pre-move state, so the gate passes before it."""
    names = [lab["name"] for lab in fixture("ready")["labels"]]
    assert "state:auto-reviewing-issue" in names


@pytest.mark.parametrize("check", [c.value for c in gate.Check])
def test_each_failing_fixture_fails_exactly_its_own_check(check: str) -> None:
    assert ids(fixture(check)) == [check]


def test_fixture_set_is_complete() -> None:
    names = {p.stem for p in FIXTURES.glob("*.json")}
    assert names == {"ready", *(c.value for c in gate.Check)}
    assert (FIXTURES / "not-json.txt").is_file()


# --- body pinning: a verdict or a status for another body never counts ------


def test_stale_body_after_an_edit_fails_status_and_verdict() -> None:
    doc = fixture("ready")
    doc["body"] += "\nOne more line after the review.\n"
    assert ids(doc) == ["status-stale", "verdict-missing"]


def test_stale_body_sha_in_status_alone_is_status_stale() -> None:
    doc = rewrite(
        fixture("ready"), gate.STATUS_MARKER, lambda d: d.update(body_sha256="0" * 64)
    )
    assert ids(doc) == ["status-stale"]


def test_older_verdict_never_counts() -> None:
    """The verdict-missing fixture has a CLEAN verdict, but for an older body."""
    doc = fixture("verdict-missing")
    old = block(doc, gate.VERDICT_MARKER)
    assert old["verdict"] == gate.CLEAN
    assert old["body_sha256"] != gate.body_sha256(doc["body"])
    assert ids(doc) == ["verdict-missing"]


def test_older_verdict_clean_does_not_mask_current_findings() -> None:
    doc = fixture("ready")
    current = gate.body_sha256(doc["body"])
    doc = rewrite(doc, gate.VERDICT_MARKER, lambda d: d.update(verdict="FINDINGS"))
    clean_for_old = rewrite(
        fixture("ready"),
        gate.VERDICT_MARKER,
        lambda d: d.update(body_sha256="1" * 64, verdict="CLEAN"),
    )
    later = clean_for_old["comments"][-1] | {"createdAt": "2026-10-02T00:00:00Z"}
    doc["comments"].append(later)
    assert block(doc, gate.VERDICT_MARKER)["body_sha256"] == current
    assert ids(doc) == ["verdict-findings"]


def test_latest_current_verdict_wins() -> None:
    doc = fixture("verdict-findings")  # CLEAN, then FINDINGS
    assert ids(doc) == ["verdict-findings"]
    doc["comments"].reverse()  # order by createdAt, not by list position
    assert ids(doc) == ["verdict-findings"]


def test_body_sha256_is_of_the_utf8_body_with_nothing_appended() -> None:
    assert gate.body_sha256("ż") == hashlib.sha256(b"\xc5\xbc").hexdigest()
    assert gate.body_sha256("a") != gate.body_sha256("a\n")


# --- each check, beyond its fixture -----------------------------------------


def test_any_verdict_word_but_clean_blocks() -> None:
    doc = rewrite(
        fixture("ready"), gate.VERDICT_MARKER, lambda d: d.update(verdict="APPROVE")
    )
    assert ids(doc) == ["verdict-findings"]


def test_status_without_json_block_is_status_missing() -> None:
    doc = fixture("ready")
    for c in doc["comments"]:
        if gate.first_line(c["body"]) == gate.STATUS_MARKER:
            c["body"] = f"{gate.STATUS_MARKER}\n\nno block here\n"
    assert ids(doc) == ["status-missing"]


def test_marker_must_be_the_first_non_empty_line() -> None:
    doc = fixture("ready")
    for c in doc["comments"]:
        if gate.first_line(c["body"]) == gate.STATUS_MARKER:
            c["body"] = "Quoting it:\n" + c["body"]
    assert ids(doc) == ["status-missing"]


def test_no_ac_rows_gates_nothing() -> None:
    doc = rewrite(fixture("ready"), gate.STATUS_MARKER, lambda d: d.update(ac=[]))
    assert ids(doc) == ["ac-passes-on-master"]


def test_regression_guard_may_pass_on_master() -> None:
    rows = block(fixture("ready"), gate.STATUS_MARKER)["ac"]
    assert any(
        r["regression_guard"] and r["master_exit"] == r["expected"] for r in rows
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.update(ac_dry_run=d["ac_dry_run"][:1]),  # an AC not dry-run
        lambda d: d.update(master="f" * 40),  # another master
    ],
    ids=["missing-id", "other-master"],
)
def test_dry_run_mismatch_variants(edit: Callable[[dict[str, Any]], None]) -> None:
    assert ids(rewrite(fixture("ready"), gate.VERDICT_MARKER, edit)) == [
        "dry-run-mismatch"
    ]


@pytest.mark.parametrize(
    "labels",
    [
        ["state:manual-reviewing-issue"],
        ["state:auto-reviewing-issue"],
    ],
)
def test_state_label_accepts_both_handover_states(labels: list[str]) -> None:
    doc = fixture("ready")
    doc["labels"] = [{"name": n} for n in ["tooling", *labels]]
    assert ids(doc) == []


@pytest.mark.parametrize(
    "labels",
    [
        [],
        ["state:auto-reviewing-issue", "state:manual-reviewing-issue"],
        ["state:implementing"],
    ],
)
def test_state_label_refuses_other_sets(labels: list[str]) -> None:
    doc = fixture("ready")
    doc["labels"] = [{"name": n} for n in labels]
    assert ids(doc) == ["state-label"]


# --- malformed input is a tool error (exit 2), never a verdict --------------


@pytest.mark.parametrize(
    ("marker", "edit"),
    [
        (gate.STATUS_MARKER, lambda d: d.pop("body_sha256")),
        (gate.STATUS_MARKER, lambda d: d.update(stale="false")),
        (gate.STATUS_MARKER, lambda d: d.update(issue_check=True)),
        (gate.STATUS_MARKER, lambda d: d.update(master="abc")),
        (gate.STATUS_MARKER, lambda d: d.update(v=2)),
        (gate.STATUS_MARKER, lambda d: d["ac"].append(dict(d["ac"][0]))),
        (gate.STATUS_MARKER, lambda d: d["ac"][0].pop("regression_guard")),
        (gate.VERDICT_MARKER, lambda d: d.update(role="review")),
        (gate.VERDICT_MARKER, lambda d: d.pop("verdict")),
        (gate.VERDICT_MARKER, lambda d: d.update(body_sha256="ABC")),
        (gate.VERDICT_MARKER, lambda d: d.update(ac_dry_run=[{"id": 1}])),
    ],
)
def test_malformed_blocks_raise(
    marker: str, edit: Callable[[dict[str, Any]], None]
) -> None:
    with pytest.raises(gate.GateError):
        gate.evaluate(rewrite(fixture("ready"), marker, edit))


def test_verdict_without_json_block_raises() -> None:
    doc = fixture("ready")
    doc["comments"].append(
        {
            "body": f"{gate.VERDICT_MARKER}\n\nCLEAN, trust me.\n",
            "createdAt": "2026-10-02T00:00:00Z",
        }
    )
    with pytest.raises(gate.GateError):
        gate.evaluate(doc)


@pytest.mark.parametrize("key", ["body", "labels", "comments"])
def test_document_missing_key_raises(key: str) -> None:
    doc = fixture("ready")
    del doc[key]
    with pytest.raises(gate.GateError):
        gate.evaluate(doc)


def test_cli_not_json_exits_2() -> None:
    done = run_cli("--from-json", str(FIXTURES / "not-json.txt"))
    assert done.returncode == gate.TOOL_ERROR
    assert done.stdout == ""


def test_cli_malformed_block_exits_2(tmp_path: Path) -> None:
    doc = rewrite(fixture("ready"), gate.STATUS_MARKER, lambda d: d.update(stale=None))
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run_cli("--from-json", str(path)).returncode == gate.TOOL_ERROR


@pytest.mark.parametrize("args", [[], ["abc"], ["1", "--from-json", "x"]])
def test_cli_usage_errors_exit_2(args: list[str]) -> None:
    assert run_cli(*args).returncode == gate.TOOL_ERROR


def test_cli_ready_and_not_ready() -> None:
    ready = run_cli("--from-json", str(FIXTURES / "ready.json"))
    assert (ready.returncode, ready.stdout) == (gate.READY, "READY\n")
    stale = run_cli("--from-json", str(FIXTURES / "status-stale.json"))
    assert stale.returncode == 1
    assert stale.stdout.startswith("FAIL status-stale: ")


# --- real mode through the offline gh stub ----------------------------------


def stub_env(tmp_path: Path, doc: Doc) -> tuple[dict[str, str], Path]:
    """Environment that serves ``doc`` as issue 123 through the gh stub."""
    state = tmp_path / "state"
    state.write_text(json.dumps({"issues": [doc]}), encoding="utf-8")
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(state),
    }
    return env, state


def test_real_mode_reads_one_issue_view(tmp_path: Path) -> None:
    env, state = stub_env(tmp_path, fixture("ready"))
    done = run_cli("123", "--repo", "o/r", env=env)
    assert (done.returncode, done.stdout) == (gate.READY, "READY\n"), done.stderr
    calls = state.with_name("state.log").read_text(encoding="utf-8").splitlines()
    assert calls == [f"issue view 123 --repo o/r --json {gate.ISSUE_VIEW_FIELDS}"]


def test_real_mode_not_ready(tmp_path: Path) -> None:
    env, _ = stub_env(tmp_path, fixture("status-missing"))
    done = run_cli("123", env=env)
    assert done.returncode == 1
    assert done.stdout.startswith("FAIL status-missing: ")


def test_real_mode_gh_failure_exits_2(tmp_path: Path) -> None:
    env, _ = stub_env(tmp_path, fixture("ready"))
    assert run_cli("999", env=env).returncode == gate.TOOL_ERROR
