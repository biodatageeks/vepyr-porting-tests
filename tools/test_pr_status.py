"""Tests for ``pr_status``: every check id, skip rules, the tier, real mode (stub)."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections.abc import Callable
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


#: The only ``gh`` calls real mode makes on a PR without README changes.
REAL_READS: Final = ("pr view ", "issue view ", "api --paginate --slurp ")


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
    assert calls and all(c.startswith(REAL_READS) for c in calls)


def test_real_mode_gh_failure_exits_2(tmp_path: Path) -> None:
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(tmp_path / "missing"),
    }
    assert run_cli("7", env=env).returncode == gate.TOOL_ERROR


def _set_verdict(body: str, value: str | None) -> str:
    """Replace (or drop, for ``None``) the ``verdict`` key inside a verdict body."""
    old = '"verdict": "APPROVE",'
    assert old in body
    return body.replace(old, "" if value is None else f'"verdict": "{value}",', 1)


@pytest.mark.parametrize("value", ["REQUEST_CHANGES", None, "approve"])
def test_review_verdict_other_than_approve_blocks(value: str | None) -> None:
    doc = fixture("ready")
    doc["comments"][1]["body"] = _set_verdict(doc["comments"][1]["body"], value)
    assert ids(doc) == ["verdict-changes"]
    assert ids(fixture("ready")) == []  # control: the unmodified fixture is READY


@pytest.mark.parametrize("value", ["NOPE", None])
def test_superreview_verdict_other_than_approve_blocks(value: str | None) -> None:
    doc = fixture("ready-superreviewed")
    doc["reviews"][0]["body"] = _set_verdict(doc["reviews"][0]["body"], value)
    assert ids(doc) == ["superreview-blocking"]
    assert ids(fixture("ready-superreviewed")) == []


def _write(tmp_path: Path, doc: object) -> str:
    path = tmp_path / "x"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_malformed_ac_list_is_a_tool_error(tmp_path: Path) -> None:
    doc = fixture("ready")
    doc["comments"][0]["body"] = doc["comments"][0]["body"].replace(
        '"ac": [', '"ac": 5, "x": [', 1
    )
    done = run_cli("--from-json", _write(tmp_path, doc))
    assert done.returncode == gate.TOOL_ERROR
    assert "Traceback" not in done.stderr and "malformed input" in done.stderr


def test_null_comment_is_a_tool_error(tmp_path: Path) -> None:
    doc = fixture("ready")
    doc["comments"].append(None)
    done = run_cli("--from-json", _write(tmp_path, doc))
    assert done.returncode == gate.TOOL_ERROR
    assert "Traceback" not in done.stderr
    assert run_cli("--from-json", _write(tmp_path, fixture("ready"))).returncode == 0


def _edit_json(body: str, edit: Callable[[dict[str, Any]], None]) -> str:
    """Apply ``edit`` to the json block of ``body`` and re-render it."""
    data = gate.json_block(body)
    assert isinstance(data, dict)
    edit(data)
    head, _, _ = body.partition("```json")
    return f"{head}```json\n{json.dumps(data, indent=2)}\n```\n"


def _sticky_edit(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    doc = fixture("ready")
    doc["comments"][0]["body"] = _edit_json(doc["comments"][0]["body"], edit)
    return doc


def _verdict_edit(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    doc = fixture("ready")
    doc["comments"][1]["body"] = _edit_json(doc["comments"][1]["body"], edit)
    return doc


def _row(i: int, **kw: object) -> Callable[[dict[str, Any]], None]:
    def edit(d: dict[str, Any]) -> None:
        for k, v in kw.items():
            if v is _DROP:
                d["ac"][i].pop(k)
            else:
                d["ac"][i][k] = v

    return edit


_DROP: Final = object()

MALFORMED: Final[dict[str, Callable[[], dict[str, Any]]]] = {
    # sticky rows (review 5364175079 finding 1 and the self-audit)
    "row-no-exit-no-expected": lambda: _sticky_edit(
        _row(0, exit=_DROP, expected=_DROP)
    ),
    "row-exit-null": lambda: _sticky_edit(_row(0, exit=None, expected=None)),
    "row-exit-str": lambda: _sticky_edit(_row(0, exit="0")),
    "row-expected-bool": lambda: _sticky_edit(_row(0, expected=False)),
    "row-manual-str": lambda: _sticky_edit(_row(0, manual="false")),
    "row-manual-missing": lambda: _sticky_edit(_row(0, manual=_DROP)),
    "row-id-bool": lambda: _sticky_edit(_row(0, id=True)),
    "row-id-duplicate": lambda: _sticky_edit(_row(1, id=1)),
    "row-sha-null": lambda: _sticky_edit(_row(0, sha=None)),
    "row-evidence-bogus": lambda: _sticky_edit(_row(0, evidence="partial")),
    "row-manual-with-exit": lambda: _sticky_edit(_row(2, exit=0)),
    "row-manual-no-reviewer": lambda: _sticky_edit(_row(2, reviewer=_DROP)),
    "row-not-object": lambda: _sticky_edit(lambda d: d["ac"].append(5)),
    "sticky-stale-str": lambda: _sticky_edit(lambda d: d.update(stale="true")),
    "sticky-stale-missing": lambda: _sticky_edit(lambda d: d.pop("stale")),
    "sticky-ac-not-list": lambda: _sticky_edit(lambda d: d.update(ac=5)),
    "sticky-head-missing": lambda: _sticky_edit(lambda d: d.pop("head")),
    "sticky-version": lambda: _sticky_edit(lambda d: d.update(v=2)),
    # verdicts (finding 2 and the self-audit)
    "mutation-no-exit": lambda: _verdict_edit(lambda d: d["mutations"][0].pop("exit")),
    "mutation-exit-str": lambda: _verdict_edit(
        lambda d: d["mutations"][0].update(exit="0")
    ),
    "mutation-ac-str": lambda: _verdict_edit(
        lambda d: d["mutations"][0].update(ac="1")
    ),
    "mutations-missing": lambda: _verdict_edit(lambda d: d.pop("mutations")),
    "mutations-not-list": lambda: _verdict_edit(lambda d: d.update(mutations={})),
    "probes-str": lambda: _verdict_edit(lambda d: d.update(probes="3")),
    "probes-bool": lambda: _verdict_edit(lambda d: d.update(probes=True)),
    "probes-missing": lambda: _verdict_edit(lambda d: d.pop("probes")),
    "verdict-not-str": lambda: _verdict_edit(lambda d: d.update(verdict=True)),
    "role-unknown": lambda: _verdict_edit(lambda d: d.update(role="reviewer")),
    "model-missing": lambda: _verdict_edit(lambda d: d.pop("model")),
    "verdict-sha-null": lambda: _verdict_edit(lambda d: d.update(sha=None)),
    # the PR document
    "labels-null": lambda: {**fixture("ready"), "labels": None},
    "label-name-int": lambda: {**fixture("ready"), "labels": [{"name": 1}]},
    "comment-body-null": lambda: {
        **fixture("ready"),
        "comments": [*fixture("ready")["comments"], {"body": None, "createdAt": "t"}],
    },
    "comment-no-createdAt": lambda: {
        **fixture("ready"),
        "comments": [*fixture("ready")["comments"], {"body": "x"}],
    },
    "files-missing": lambda: {
        k: v for k, v in fixture("ready").items() if k != "files"
    },
    "file-path-null": lambda: {**fixture("ready"), "files": [{"path": None}]},
    "issues-missing": lambda: {
        k: v for k, v in fixture("ready").items() if k != "issues"
    },
    "issue-number-str": lambda: {
        **fixture("ready"),
        "issues": [{"number": "158", "labels": []}],
    },
    "head-empty": lambda: {**fixture("ready"), "headRefOid": ""},
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_field_is_a_tool_error(case: str) -> None:
    with pytest.raises(gate.GateError, match="malformed input"):
        gate.evaluate(MALFORMED[case]())
    assert ids(fixture("ready")) == []  # control: the unmodified fixture is READY


def test_malformed_field_exits_2_on_the_cli(tmp_path: Path) -> None:
    done = run_cli("--from-json", _write(tmp_path, MALFORMED["row-manual-str"]()))
    assert done.returncode == gate.TOOL_ERROR
    assert "Traceback" not in done.stderr and "'manual'" in done.stderr


# --- the hand-over stage (#177) ---


def _relabel(doc: dict[str, Any], *states: str) -> dict[str, Any]:
    doc["labels"] = [{"name": "tooling"}, *({"name": s} for s in states)]
    return doc


def handover_ids(doc: dict[str, Any]) -> list[str]:
    """The failed check ids for ``doc`` in the hand-over stage."""
    return [str(f.check) for f in gate.evaluate(doc, stage="handover")]


@pytest.mark.parametrize(
    ("name", "state"),
    [
        ("ready", "state:auto-reviewing"),
        ("ready", "state:auto-superreviewing"),
        ("ready-superreviewed", "state:auto-superreviewing"),
    ],
)
def test_handover_stage_is_ready_before_the_move(name: str, state: str) -> None:
    assert handover_ids(_relabel(fixture(name), state)) == []
    assert ids(_relabel(fixture(name), state)) == ["state-label"]  # owner's stage


@pytest.mark.parametrize(
    "states",
    [
        (),
        ("state:implementing",),
        ("state:fixing",),
        ("state:manual-reviewing",),
        ("state:awaiting-merge",),
        ("state:auto-reviewing-issue",),
        ("state:auto-reviewing", "state:manual-reviewing"),
    ],
    ids=lambda s: ",".join(s) or "none",
)
def test_handover_stage_rejects_other_states(states: tuple[str, ...]) -> None:
    assert handover_ids(_relabel(fixture("ready"), *states)) == ["state-label"]


@pytest.mark.parametrize(
    "check", [c.value for c in gate.Check if c is not gate.Check.STATE_LABEL]
)
def test_handover_stage_keeps_every_other_check(check: str) -> None:
    doc = _relabel(fixture(check), "state:auto-reviewing")
    assert handover_ids(doc) == [check]


def test_handover_flag_on_the_cli(tmp_path: Path) -> None:
    path = _write(tmp_path, _relabel(fixture("ready"), "state:auto-reviewing"))
    assert run_cli("--handover", "--from-json", path).stdout == "READY\n"
    owner = run_cli("--from-json", path)
    assert owner.returncode == gate.NOT_READY
    assert owner.stdout.startswith("FAIL state-label:")
    assert "--handover" in run_cli("--help").stdout


def test_handover_real_mode_reads_only(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.write_text(
        json.dumps(_relabel(fixture("ready"), "state:auto-reviewing")),
        encoding="utf-8",
    )
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(state),
    }
    done = run_cli("--handover", "7", env=env)
    assert (done.returncode, done.stdout) == (0, "READY\n")
    calls = (tmp_path / "state.log").read_text(encoding="utf-8").splitlines()
    assert calls and all(c.startswith(REAL_READS) for c in calls)


# --- deeply nested JSON (#177) ---

DEEP: Final = "[" * 100_000


def _deep_block(doc: dict[str, Any], marker: str) -> dict[str, Any]:
    for item in doc["comments"]:
        if gate.first_line(item["body"]) == marker:
            item["body"] = f"{marker}\n```json\n{DEEP}\n```\n"
            return doc
    raise AssertionError(f"no {marker} comment")


def test_recursion_in_json_block_is_invalid() -> None:
    assert gate.json_block(f"```json\n{DEEP}\n```") is None


def test_recursion_in_document_is_a_tool_error(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text(DEEP, encoding="utf-8")
    with pytest.raises(gate.GateError, match="nested too deeply"):
        gate.load(path)
    done = run_cli("--from-json", str(path))
    assert done.returncode == gate.TOOL_ERROR
    assert done.stderr.startswith("pr_status: cannot read ")
    assert "Traceback" not in done.stderr


def test_recursion_in_sticky_block_is_sticky_missing(tmp_path: Path) -> None:
    doc = _deep_block(fixture("ready"), gate.STICKY_MARKER)
    assert ids(doc) == ["sticky-missing"]
    done = run_cli("--from-json", _write(tmp_path, doc))
    assert done.returncode == gate.NOT_READY
    assert "Traceback" not in done.stderr


def test_recursion_in_verdict_block_is_a_tool_error(tmp_path: Path) -> None:
    doc = _deep_block(fixture("ready"), gate.VERDICT_MARKER)
    with pytest.raises(gate.GateError, match="no valid json object block"):
        gate.evaluate(doc)
    done = run_cli("--from-json", _write(tmp_path, doc))
    assert done.returncode == gate.TOOL_ERROR
    assert done.stderr.startswith("pr_status: malformed input: verdict posted ")
    assert "Traceback" not in done.stderr


def test_recursion_in_gh_output_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "_gh", lambda args: DEEP)
    with pytest.raises(gate.GateError, match="nested too deeply"):
        gate.fetch(7)


# --- large PRs: complete file list, no gh pr diff (#249) ---

TIER_PATH: Final = "tests/data/x/test.toml"


def _large(
    base: str, size: int = 420, tier_at: int | None = None, readme: bool = True
) -> dict[str, Any]:
    """``base`` with ``size`` changed files: ``docs/f<i>.md`` fillers, optionally
    ``README.md`` (first, with a non-tier diff) and ``TIER_PATH`` at index ``tier_at``.
    """
    doc = fixture(base)
    paths = [f"docs/f{i}.md" for i in range(size)]
    if tier_at is not None:
        paths[tier_at] = TIER_PATH
    if readme:
        paths[0] = gate.README
        doc["readme"] = README_TEXT
        doc["readme_diff"] = "@@ -1,2 +1,2 @@\n # T\n-intro\n+intro edited\n"
    doc["files"] = [{"path": p} for p in paths]
    return doc


def _run_stub(
    tmp_path: Path, doc: dict[str, Any], **extra: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run ``./pr_status 7`` on the gh stub serving ``doc``; return the gh calls."""
    state = tmp_path / "state"
    state.write_text(json.dumps(doc), encoding="utf-8")
    env = {
        "PATH": f"{STUB_DIR}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": str(state),
        **extra,
    }
    done = run_cli("7", env=env)
    log = tmp_path / "state.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


def test_stub_emulates_github_limits(tmp_path: Path) -> None:
    """The stub caps ``pr view`` files at 100 and refuses ``pr diff`` above 300."""
    state = tmp_path / "state"
    state.write_text(json.dumps(_large("ready", tier_at=350)), encoding="utf-8")
    env = {**os.environ, "GH_STUB_STATE": str(state)}
    gh = str(STUB_DIR / "gh")
    view = subprocess.run(
        [gh, "pr", "view", "7", "--json", "files,changedFiles"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    out = json.loads(view.stdout)
    assert (len(out["files"]), out["changedFiles"]) == (100, 420)
    diff = subprocess.run(
        [gh, "pr", "diff", "7"], capture_output=True, text=True, env=env, check=False
    )
    assert diff.returncode == 1 and "HTTP 406" in diff.stderr


def test_large_pr_420_files_gets_a_verdict_without_pr_diff(tmp_path: Path) -> None:
    """AC 1: 420 files incl. README and a tier path -> a verdict, never ``pr diff``."""
    done, calls = _run_stub(tmp_path, _large("ready-superreviewed", tier_at=200))
    assert (done.returncode, done.stdout, done.stderr) == (gate.READY, "READY\n", "")
    assert not any(c.startswith("pr diff") for c in calls)
    assert any("contents/README.md" in c for c in calls)  # README diff was classified
    # The same PR without its super-review is NOT_READY, not a tool error.
    done, _ = _run_stub(tmp_path, _large("ready", tier_at=200))
    assert done.returncode == gate.NOT_READY
    assert done.stdout.startswith("FAIL superreview-missing:")


def test_incomplete_file_list_is_a_tool_error(tmp_path: Path) -> None:
    """AC 2: 419 of 420 entries -> exit 2 naming both counts, no verdict."""
    done, _ = _run_stub(tmp_path, _large("ready"), GH_STUB_DROP_FILES="1")
    assert (done.returncode, done.stdout) == (gate.TOOL_ERROR, "")
    assert "file list incomplete for PR 7: got 419 of 420 files" in done.stderr
    # Positive control: the full list from the same stub gives a verdict.
    assert _run_stub(tmp_path, _large("ready"))[0].returncode == gate.READY


def test_incomplete_file_list_readme_without_patch_is_a_tool_error(
    tmp_path: Path,
) -> None:
    """AC 2: README.md listed but GitHub sent no ``patch`` -> exit 2."""
    done, _ = _run_stub(tmp_path, _large("ready", size=3), GH_STUB_NO_PATCH="1")
    assert (done.returncode, done.stdout) == (gate.TOOL_ERROR, "")
    assert "README.md changed in PR 7 but GitHub sent no patch" in done.stderr


def test_tier_beyond_first_100_requires_superreview(tmp_path: Path) -> None:
    """AC 3: the only tier path is entry 350 of 420 -> super-review required.

    Mutation: reading ``files`` from ``pr view`` (capped at 100) makes this fail.
    """
    doc = _large("ready", tier_at=349, readme=False)
    done, _ = _run_stub(tmp_path, doc)
    assert done.returncode == gate.NOT_READY
    assert [line.split(":")[0] for line in done.stdout.splitlines()] == [
        "FAIL superreview-missing"
    ]
    # Control: without that path the same 420-file PR is READY.
    done, _ = _run_stub(tmp_path, _large("ready", readme=False))
    assert (done.returncode, done.stdout) == (gate.READY, "READY\n")


def test_duplicate_filenames_are_a_tool_error(tmp_path: Path) -> None:
    """Super-review #252: changedFiles=3 with pages [[a, a, b]] -> exit 2.

    Mutation: removing the uniqueness check in ``fetch`` makes this READY.
    """
    doc = fixture("ready")
    doc["files"] = [{"path": p} for p in ("docs/a.md", "docs/a.md", "docs/b.md")]
    doc["changedFiles"] = 3
    done, _ = _run_stub(tmp_path, doc)
    assert (done.returncode, done.stdout, done.stderr) == (
        gate.TOOL_ERROR,
        "",
        (
            "pr_status: file list for PR 7 repeats filenames (docs/a.md x2): "
            "2 unique of 3 entries; review the tier by hand with "
            "git diff --name-only origin/master...<head>\n"
        ),
    )
    # Control: three unique names with the same count give a verdict.
    doc["files"][1] = {"path": "docs/c.md"}
    assert _run_stub(tmp_path, doc)[0].returncode == gate.READY


@pytest.mark.parametrize("bad", [True, False, "3", 3.0, None])
def test_changed_files_must_be_a_plain_int(tmp_path: Path, bad: object) -> None:
    """A bool (an ``int`` subclass) or non-int ``changedFiles`` -> exit 2."""
    doc = _large("ready", size=1, readme=False)
    doc["changedFiles"] = bad
    done, _ = _run_stub(tmp_path, doc)
    assert (done.returncode, done.stdout) == (gate.TOOL_ERROR, "")
    assert f"changedFiles is {bad!r}, not a number" in done.stderr


def test_head_moved_during_reads_is_a_tool_error(tmp_path: Path) -> None:
    """Finding 4 (#252): head re-read after the file list differs -> exit 2.

    Mutation: dropping the ``_same_head`` call in ``fetch`` makes this READY.
    """
    doc = fixture("ready")
    done, calls = _run_stub(tmp_path, doc, GH_STUB_HEAD_AFTER="f" * 40)
    assert (done.returncode, done.stdout) == (gate.TOOL_ERROR, "")
    assert done.stderr == (
        f"pr_status: PR 7 head moved from {doc['headRefOid']} to {'f' * 40} "
        "while reading its files; re-run ./pr_status\n"
    )
    files_at = next(i for i, c in enumerate(calls) if "/files" in c)
    assert calls[-1].endswith("--json headRefOid") and len(calls) - 1 > files_at
    # Control: the same head on the re-read gives a verdict.
    assert _run_stub(tmp_path, doc)[0].returncode == gate.READY
