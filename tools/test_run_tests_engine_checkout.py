"""Engine-checkout tests: no stale tree on branch refs, and ``--`` hardening (#22).

Everything here runs against throwaway local git repositories standing in for
``biodatageeks/datafusion-bio-*``; the GitHub side is a stub. The regression under
test is that ``_checkout_repo`` used to ``git checkout --detach <rev>`` *after*
fetching, so a cached clone kept resolving ``<rev>`` against its clone-time local
branch ref and silently tested a stale revision.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

import run_tests.engine as engine

_DFBF_MEMBERS: Final[tuple[str, ...]] = ("datafusion-bio-function-vep",)
_FMT_MEMBERS: Final[tuple[str, ...]] = (
    "datafusion-bio-format-ensembl-cache",
    "datafusion-bio-format-vcf",
)


def _git(cwd: Path, *argv: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *argv],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _workspace(root: Path, members: Sequence[str], marker: str) -> None:
    """Write a minimal cargo workspace whose crate dirs match ``members``."""
    names = ", ".join(f'"{m}"' for m in members)
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(f"[workspace]\nmembers = [{names}]\n")
    for member in members:
        crate = root / member
        crate.mkdir(exist_ok=True)
        (crate / "Cargo.toml").write_text(
            f'[package]\nname = "{member}"\nversion = "0.0.0"\n# {marker}\n'
        )


def _make_remote(root: Path, members: Sequence[str], branch: str = "main") -> Path:
    """Create a local git repo on ``branch`` with one commit; return its path."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True)
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "test")
    _workspace(root, members, marker="c1")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    return root


def _advance(root: Path, members: Sequence[str], marker: str) -> str:
    """Add one commit on the current branch; return the new HEAD sha."""
    _workspace(root, members, marker=marker)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", marker)
    return _git(root, "rev-parse", "HEAD")


class _FakeGh:
    """GhApi stub returning a vepyr ``Cargo.toml`` pinned at ``dfbf``/``fmt`` refs."""

    def __init__(
        self,
        *,
        dfbf: Path,
        fmt: Path,
        rev_key: str,
        dfbf_rev: str,
        fmt_rev: str | None = None,
    ) -> None:
        fmt_rev = dfbf_rev if fmt_rev is None else fmt_rev
        self.cargo_toml = (
            '[package]\nname = "vepyr"\nversion = "0.0.0"\n\n[dependencies]\n'
            f'{_DFBF_MEMBERS[0]} = {{ git = "{dfbf}", {rev_key} = "{dfbf_rev}" }}\n'
            f'{_FMT_MEMBERS[0]} = {{ git = "{fmt}", {rev_key} = "{fmt_rev}" }}\n'
            f'{_FMT_MEMBERS[1]} = {{ git = "{fmt}", {rev_key} = "{fmt_rev}" }}\n'
        )

    def get(self, path: str) -> Any:
        if path.startswith(f"repos/{engine.VEPYR_REPO}/commits/"):
            return {"sha": "a" * 40}
        if "contents/Cargo.toml" in path:
            return {
                "encoding": "base64",
                "content": base64.b64encode(self.cargo_toml.encode()).decode(),
            }
        raise engine.GhError(path, "unexpected")


def test_branch_ref_rechecks_out_new_head(tmp_path: Path) -> None:
    """A second run on a tracked branch tests the *new* head, not the cached one."""
    dfbf = _make_remote(tmp_path / "remote-dfbf", _DFBF_MEMBERS)
    fmt = _make_remote(tmp_path / "remote-fmt", _FMT_MEMBERS)
    api = _FakeGh(dfbf=dfbf, fmt=fmt, rev_key="branch", dfbf_rev="main")
    src_root = tmp_path / "src"

    first = engine.resolve("main", api=api, src_root=src_root)
    assert first.dfbf.head == _git(dfbf, "rev-parse", "HEAD")

    dfbf_new = _advance(dfbf, _DFBF_MEMBERS, "c2")
    fmt_new = _advance(fmt, _FMT_MEMBERS, "c2")
    assert dfbf_new != first.dfbf.head

    second = engine.resolve("main", api=api, src_root=src_root)
    # Both the reported sha and the tree actually on disk must be the new head.
    assert second.dfbf.head == dfbf_new
    assert _git(second.dfbf.path, "rev-parse", "HEAD") == dfbf_new
    assert second.formats.head == fmt_new
    assert _git(second.formats.path, "rev-parse", "HEAD") == fmt_new


@pytest.mark.parametrize("rev_key", ["rev", "tag", "branch"])
def test_ref_kinds_resolve_to_the_tip(tmp_path: Path, rev_key: str) -> None:
    """sha, tag and branch refs all land on the same commit, with ``--`` in argv."""
    dfbf = _make_remote(tmp_path / "remote-dfbf", _DFBF_MEMBERS)
    fmt = _make_remote(tmp_path / "remote-fmt", _FMT_MEMBERS)
    head_dfbf = _git(dfbf, "rev-parse", "HEAD")
    head_fmt = _git(fmt, "rev-parse", "HEAD")
    _git(dfbf, "tag", "v0.0.0")
    _git(fmt, "tag", "v0.0.0")
    rev = {"rev": head_dfbf, "tag": "v0.0.0", "branch": "main"}[rev_key]
    fmt_rev = {"rev": head_fmt, "tag": "v0.0.0", "branch": "main"}[rev_key]
    api = _FakeGh(dfbf=dfbf, fmt=fmt, rev_key=rev_key, dfbf_rev=rev, fmt_rev=fmt_rev)

    plan = engine.resolve("ref", api=api, src_root=tmp_path / "src")
    assert plan.dfbf.head == head_dfbf
    assert _git(plan.dfbf.path, "rev-parse", "HEAD") == head_dfbf
    assert plan.formats.head == head_fmt


_LFS_POINTER_MAGIC: Final[str] = "version https://git-lfs.github.com/spec/v1"


def _add_lfs_blob(root: Path, rel: str, content: bytes) -> str:
    """Commit ``content`` at ``rel`` through git-lfs; return the new HEAD sha."""
    subprocess.run(
        ["git", "-C", str(root), "lfs", "install", "--local"],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "lfs", "track", rel)
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "lfs")
    return _git(root, "rev-parse", "HEAD")


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs not on PATH")
def test_checkout_leaves_lfs_files_as_pointers(tmp_path: Path) -> None:
    """Ladder checkouts skip smudging, so LFS blobs never need a server (#61).

    The fixture repo tracks ``vep-benchmark/data/golden/cache/chr1.parquet`` through
    real git-lfs. Without ``GIT_LFS_SKIP_SMUDGE=1`` the checkout would replace the
    pointer with the real bytes (and, against a real remote, download them).

    Relies on ``filter.lfs.smudge`` being configured (globally or per-user, from
    ``git lfs install``) so the checked-out tree — a plain ``--shared`` clone, not
    the fixture repo itself — actually runs the filter either way; without that
    config this would pass vacuously. ``test_clone_and_checkout_carry_skip_smudge_env``
    below asserts the env var directly and does not depend on that.
    """
    real = b"not-really-parquet " * 64
    rel = "vep-benchmark/data/golden/cache/chr1.parquet"
    dfbf = _make_remote(tmp_path / "remote-dfbf", _DFBF_MEMBERS)
    fmt = _make_remote(tmp_path / "remote-fmt", _FMT_MEMBERS)
    _add_lfs_blob(dfbf, rel, real)
    head = _git(dfbf, "rev-parse", "HEAD")
    api = _FakeGh(
        dfbf=dfbf,
        fmt=fmt,
        rev_key="rev",
        dfbf_rev=head,
        fmt_rev=_git(fmt, "rev-parse", "HEAD"),
    )

    plan = engine.resolve("ref", api=api, src_root=tmp_path / "src")
    assert plan.dfbf.head == head
    blob = plan.dfbf.path / rel
    assert blob.exists()
    text = blob.read_text()
    assert text.startswith(_LFS_POINTER_MAGIC), text[:200]
    assert blob.read_bytes() != real


def test_clone_and_checkout_carry_skip_smudge_env(tmp_path: Path) -> None:
    """The two working-tree-materialising git calls pass ``GIT_LFS_SKIP_SMUDGE=1``."""
    calls: list[tuple[list[str], dict[str, Any]]] = []
    sha = "b" * 40

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, sha, "")

    engine._checkout_repo(
        name="dfbf",
        git_url="https://example.invalid/x.git",
        rev=sha,
        target=tmp_path / "dfbf",
        run=fake_run,
        offline=False,
    )

    shared_clone = [k for a, k in calls if "clone" in a and "--shared" in a]
    detach = [k for a, k in calls if "checkout" in a and "--detach" in a]
    assert shared_clone and detach, calls
    for kwargs in (*shared_clone, *detach):
        assert kwargs["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
    # Untouched calls keep today's environment (no ``env=`` kwarg at all).
    plain = [k for a, k in calls if "rev-parse" in a]
    assert plain and all("env" not in k for k in plain)


@pytest.mark.parametrize("ref_kind", ["sha", "tag", "branch"])
def test_dash_dash_separator_is_behaviour_neutral(
    tmp_path: Path, ref_kind: str
) -> None:
    """``checkout --detach <rev>`` and ``… <rev> --`` agree for every valid ref."""
    remote = _make_remote(tmp_path / "remote", _DFBF_MEMBERS)
    _git(remote, "tag", "v1")
    sha = _git(remote, "rev-parse", "HEAD")
    rev = {"sha": sha, "tag": "v1", "branch": "main"}[ref_kind]

    shas: list[str] = []
    for i, extra in enumerate(([], ["--"])):
        clone = tmp_path / f"clone{i}"
        subprocess.run(
            ["git", "clone", "-q", "--no-checkout", "--", str(remote), str(clone)],
            check=True,
        )
        _git(clone, "checkout", "--quiet", "--detach", rev, *extra)
        shas.append(_git(clone, "rev-parse", "HEAD"))
    assert shas == [sha, sha]
