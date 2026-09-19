"""``LockGuard`` crash safety: a SIGKILL must not leave ``Cargo.lock`` rewritten.

The guard restores through git, so the pristine lockfile lives in the index rather
than in process memory. These tests drive a real git repository and a real child
process that dies to ``SIGKILL`` mid-run — nothing here is mocked away.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from run_tests.engine import LockGuard

PRISTINE: str = '# pristine\n[[package]]\nname = "vepyr"\n'
PATCHED: str = '# patched by the run\n[[package]]\nname = "patched"\n'

#: Child program: enter the guard, rewrite ``Cargo.lock``, then die to SIGKILL
#: inside the "cargo test" window — no ``finally`` block ever runs.
_KILLED_RUN: str = f"""
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from run_tests.engine import LockGuard
from pathlib import Path

repo = Path(sys.argv[2])
with LockGuard(repo).held():
    (repo / "Cargo.lock").write_text({PATCHED!r})
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
"""


def _git(repo: Path, *argv: str) -> str:
    """Run ``git`` in ``repo`` and return its stdout, asserting success."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _porcelain(repo: Path) -> str:
    """``git status --porcelain Cargo.lock`` — empty means the lockfile is clean."""
    return _git(repo, "status", "--porcelain", "--", "Cargo.lock").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository with a committed ``Cargo.lock``."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "Cargo.lock").write_text(PRISTINE)
    _git(root, "add", "Cargo.lock")
    _git(root, "commit", "-qm", "lock")
    assert _porcelain(root) == ""
    return root


def _tools_dir() -> str:
    return str(Path(__file__).resolve().parent)


def test_sigkill_mid_run_is_repaired_by_the_next_invocation(repo: Path) -> None:
    """AC1: a hard kill leaves it dirty; the next guarded run cleans it up."""
    killed = subprocess.run(
        [sys.executable, "-c", _KILLED_RUN, _tools_dir(), str(repo)],
        capture_output=True,
        text=True,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    # The kill skipped every ``finally``: the lockfile is left rewritten.
    assert (repo / "Cargo.lock").read_text() == PATCHED
    assert _porcelain(repo) != ""

    # Next invocation — no manual recovery step.
    with LockGuard(repo).held():
        pass
    assert _porcelain(repo) == ""
    assert (repo / "Cargo.lock").read_text() == PRISTINE


def test_the_sweep_happens_on_entry_not_only_on_exit(repo: Path) -> None:
    """The recovered lockfile is pristine *inside* the next run's window."""
    (repo / "Cargo.lock").write_text(PATCHED)
    with LockGuard(repo).held():
        assert (repo / "Cargo.lock").read_text() == PRISTINE
        assert _porcelain(repo) == ""


def test_a_normal_run_leaves_the_lockfile_git_clean(repo: Path) -> None:
    """AC2: a completed run restores the committed lockfile."""
    with LockGuard(repo).held():
        (repo / "Cargo.lock").write_text(PATCHED)
    assert _porcelain(repo) == ""
    assert (repo / "Cargo.lock").read_text() == PRISTINE


def test_a_failing_run_leaves_the_lockfile_git_clean(repo: Path) -> None:
    """AC2: an exception propagating out of the block still restores."""
    with pytest.raises(RuntimeError, match="cargo test"):
        with LockGuard(repo).held():
            (repo / "Cargo.lock").write_text(PATCHED)
            raise RuntimeError("cargo test exited 101")
    assert _porcelain(repo) == ""
    assert (repo / "Cargo.lock").read_text() == PRISTINE


def test_a_deleted_lockfile_is_restored_too(repo: Path) -> None:
    """Deleting the lockfile inside the window is a rewrite like any other."""
    with LockGuard(repo).held():
        (repo / "Cargo.lock").unlink()
    assert _porcelain(repo) == ""
    assert (repo / "Cargo.lock").read_text() == PRISTINE


def test_outside_git_the_in_memory_snapshot_still_guards(tmp_path: Path) -> None:
    """No git checkout: fall back to the snapshot rather than doing nothing."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / "Cargo.lock").write_text(PRISTINE)
    guard = LockGuard(root)
    assert guard.tracked is False
    with guard.held():
        (root / "Cargo.lock").write_text(PATCHED)
    assert (root / "Cargo.lock").read_text() == PRISTINE


def test_outside_git_a_lockfile_created_in_the_window_is_removed(
    tmp_path: Path,
) -> None:
    """No lockfile on entry means no lockfile on exit."""
    root = tmp_path / "plain"
    root.mkdir()
    guard = LockGuard(root)
    with guard.held():
        (root / "Cargo.lock").write_text(PATCHED)
    assert not (root / "Cargo.lock").exists()
