"""Tests for ``pr_status``: every check id, skip rules, the tier, real mode (stub)."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

from pr_status import gate

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURES: Final = Path(__file__).parent / "fixtures" / "pr_status"
STUB_DIR: Final = Path(__file__).parent / "fixtures" / "gh_stub"


def fixture(name: str) -> dict[str, Any]:
    """Load one fixture document by its base name."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def ids(doc: dict[str, Any]) -> list[str]:
    """The failed check ids for ``doc``."""
    return [str(f.check) for f in gate.evaluate(doc)]


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m pr_status`` the way the root wrapper does."""
    base = {"PYTHONPATH": str(REPO_ROOT / "tools"), "PATH": os.environ["PATH"]}
    return subprocess.run(
        [sys.executable, "-m", "pr_status", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**base, **(env or {})},
        check=False,
    )


@pytest.mark.parametrize("name", ["ready", "ready-superreviewed"])
def test_valid_fixtures_are_ready(name: str) -> None:
    assert ids(fixture(name)) == []


@pytest.mark.parametrize("check", [c.value for c in gate.Check])
def test_each_failing_fixture_fails_exactly_its_own_check(check: str) -> None:
    assert ids(fixture(check)) == [check]


def test_fixture_set_is_complete() -> None:
    names = {p.stem for p in FIXTURES.glob("*.json")}
    assert names == {"ready", "ready-superreviewed", *(c.value for c in gate.Check)}


def test_empty_ac_table_fails_ac_exit() -> None:
    doc = fixture("ready")
    body = doc["comments"][0]["body"]
    data = gate.json_block(body)
    assert isinstance(data, dict)
    data["ac"] = []
    head, _, _ = body.partition("```json")
    doc["comments"][0]["body"] = f"{head}```json\n{json.dumps(data)}\n```\n"
    # no rows: ac-exit fails; mutation is skipped (nothing to mutate)
    assert ids(doc) == ["ac-exit"]


def test_html_comment_marker_is_not_a_sticky() -> None:
    doc = fixture("ready")
    doc["comments"][0]["body"] = doc["comments"][0]["body"].replace(
        gate.STICKY_MARKER, "<!-- pr-status:v1 -->", 1
    )
    assert ids(doc) == ["sticky-missing"]


def test_marker_in_review_body_is_ignored() -> None:
    doc = fixture("ready")
    doc["reviews"] = [
        {"body": doc["comments"][0]["body"], "submittedAt": "2026-09-29T12:10:00Z"}
    ]
    assert ids(doc) == []


def test_latest_verdict_for_head_wins() -> None:
    doc = fixture("verdict-changes")
    approve = copy.deepcopy(fixture("ready")["comments"][1])
    approve["createdAt"] = "2026-09-29T14:00:00Z"
    doc["comments"].append(approve)
    assert ids(doc) == []


def test_manual_row_is_skipped_by_ac_exit_and_mutation() -> None:
    sticky = gate.json_block(fixture("ready")["comments"][0]["body"])
    assert isinstance(sticky, dict)
    assert any(r["manual"] and r["exit"] is None for r in sticky["ac"])


def test_mutation_naming_unknown_row_fails() -> None:
    doc = fixture("ready")
    body = doc["comments"][1]["body"]
    doc["comments"][1]["body"] = body.replace('"ac": 2,', '"ac": 99,', 1)
    assert ids(doc) == ["mutation"]


@pytest.mark.parametrize(
    ("path", "tier"),
    [
        ("tests/data/x/test.toml", True),
        ("tests/data_dirs.rs", True),
        ("bless", True),
        ("tools/bless/vep.py", True),
        ("tools/vep_flags.toml", True),
        ("tools/normalize_input", True),
        ("tools/run_tests/cli.py", True),
        ("bless.md", False),
        ("tools/test_run_tests_cli.py", False),
        ("docs/dataset-pins.md", False),
        ("tools/normalize_input_notes.md", False),
    ],
)
def test_tier_paths(path: str, tier: bool) -> None:
    assert gate.path_in_tier(path) is tier


@pytest.mark.parametrize(
    ("path", "severity", "expected"),
    [
        ("docs/dataset-pins.md", "severity:high", ["superreview-missing"]),
        ("docs/dataset-pins.md", "severity:critical", ["superreview-missing"]),
        ("tests/data/x/test.toml", "severity:medium", ["superreview-missing"]),
        ("docs/dataset-pins.md", "severity:medium", []),
    ],
)
def test_tier_reads_closing_issue_labels_and_files(
    path: str, severity: str, expected: list[str]
) -> None:
    doc = fixture("ready")
    doc["files"] = [{"path": path}]
    doc["issues"] = [{"number": 1, "labels": [{"name": severity}]}]
    assert ids(doc) == expected


README_TEXT: Final = (
    "# T\nintro\n## ./bless\noracle\n### Sub\nmore\n"
    "## One mode: --everything\nmode\n## Other\nother\n"
)


@pytest.mark.parametrize(
    ("diff", "tier"),
    [
        ("@@ -3,2 +3,2 @@\n ## ./bless\n-oracle\n+oracle edited\n", True),
        ("@@ -5,2 +5,2 @@\n ### Sub\n-more\n+more edited\n", True),
        ("@@ -1,2 +1,2 @@\n # T\n-intro\n+intro edited\n", False),
        ("@@ -9,2 +9,2 @@\n ## Other\n-other\n+other edited\n", False),
        ("@@ -3,2 +3,1 @@\n ## ./bless\n-oracle\n", True),
        ("@@ -8,2 +8,1 @@\n mode\n-## Other\n", True),
        ("@@ -6,2 +6,1 @@\n more\n-## One mode: --everything\n", True),
    ],
)
def test_readme_classification(diff: str, tier: bool) -> None:
    assert gate.readme_in_tier(diff, README_TEXT) is tier


def test_readme_without_diff_is_a_tool_error() -> None:
    doc = fixture("ready")
    doc["files"] = [{"path": "README.md"}]
    with pytest.raises(gate.GateError):
        gate.evaluate(doc)


def test_readme_part_keeps_only_readme() -> None:
    diff = (
        "diff --git a/x b/x\n+x\ndiff --git a/README.md b/README.md\n@@ -1 +1 @@\n+r\n"
    )
    assert (
        gate.readme_part(diff)
        == "diff --git a/README.md b/README.md\n@@ -1 +1 @@\n+r\n"
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("abc",),
        ("--from-json", "/nonexistent/x.json"),
        ("--from-json", str(FIXTURES / "not-json.txt")),
    ],
)
def test_usage_and_input_errors_exit_2(args: tuple[str, ...]) -> None:
    assert run_cli(*args).returncode == gate.TOOL_ERROR


def test_help_exits_0() -> None:
    assert run_cli("--help").returncode == 0


def test_real_mode_reads_only(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.write_text(
        (FIXTURES / "ready.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(state),
    }
    done = run_cli("7", env=env)
    assert (done.returncode, done.stdout) == (0, "READY\n")
    calls = (tmp_path / "state.log").read_text(encoding="utf-8").splitlines()
    assert calls and all(c.startswith(("pr view ", "issue view ")) for c in calls)


def test_real_mode_gh_failure_exits_2(tmp_path: Path) -> None:
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(tmp_path / "missing"),
    }
    assert run_cli("7", env=env).returncode == gate.TOOL_ERROR
