"""Plain-git resolution of biodatageeks/vepyr: no ``gh``, no credentials (#69).

``--vepyr REF`` goes through a bare mirror at ``src_root/vepyr/git`` (the same
:func:`run_tests.engine._mirror_sha` the dfbf/formats ladder uses) and
``Cargo.toml`` is read with ``git show <sha>:Cargo.toml``. Everything here runs
against a throwaway local origin reached over ``file://``, so the real transport
(``fetch``, advertised refs, single-sha fetch) is exercised and nothing touches
the network. :func:`make_vepyr_origin` is shared with the CLI tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest

from run_tests import engine
from run_tests.verdict import Exit, RunTestsError

_LADDER: Final[str] = f"""\
[package]
name = "vepyr"
version = "0.0.0"

[dependencies]
datafusion-bio-function-vep = {{ git = "{engine.DFBF_GIT}", rev = "{"b" * 40}" }}
datafusion-bio-format-ensembl-cache = {{ git = "{engine.FORMATS_GIT}", tag = "v0" }}
datafusion-bio-format-vcf = {{ git = "{engine.FORMATS_GIT}", tag = "v0" }}
"""


def _git(cwd: Path, *argv: str) -> str:
    """Run ``git -C cwd argv…`` and return its stripped stdout (raises on failure)."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), *argv], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@dataclass(frozen=True, slots=True, kw_only=True)
class VepyrOrigin:
    """A local stand-in for biodatageeks/vepyr.

    Attributes:
        path: The repository directory.
        shas: Branch name → commit sha, one commit per branch.
    """

    path: Path
    shas: Mapping[str, str]

    @property
    def url(self) -> str:
        """``file://`` URL, so git uses its real transport instead of a local copy."""
        return self.path.as_uri()


def make_vepyr_origin(root: Path, manifests: Mapping[str, str]) -> VepyrOrigin:
    """Create (or reopen) a vepyr origin with one branch per ``manifests`` key.

    Every branch holds a single root commit whose ``Cargo.toml`` is the mapped text
    plus a ``# <branch>`` marker, so distinct branches get distinct shas. Calling it
    again on the same ``root`` returns the existing origin unchanged.

    Args:
        root: Directory for the repository (created if absent).
        manifests: Branch name → ``Cargo.toml`` text.

    Returns:
        The origin with the commit sha of every branch.
    """
    if not (root / ".git").exists():
        root.mkdir(parents=True, exist_ok=True)
        _git(root, "init", "--quiet", "-b", "scratch")
        _git(root, "config", "user.email", "t@example.invalid")
        _git(root, "config", "user.name", "t")
        # GitHub serves any reachable-or-not sha on request; mirror that here.
        _git(root, "config", "uploadpack.allowAnySHA1InWant", "true")
        for branch, text in manifests.items():
            _git(root, "checkout", "--quiet", "--orphan", branch)
            (root / "Cargo.toml").write_text(f"{text}\n# {branch}\n", encoding="utf-8")
            _git(root, "add", "Cargo.toml")
            _git(root, "commit", "--quiet", "-m", branch)
    shas = {
        branch: _git(root, "rev-parse", f"refs/heads/{branch}") for branch in manifests
    }
    return VepyrOrigin(path=root, shas=shas)


@pytest.fixture
def origin(tmp_path: Path) -> VepyrOrigin:
    """An origin with ``master`` and an annotated tag ``0.7.0`` on another commit."""
    made = make_vepyr_origin(
        tmp_path / "vepyr", {"master": _LADDER, "release": _LADDER}
    )
    _git(made.path, "tag", "-a", "0.7.0", "-m", "0.7.0", made.shas["release"])
    return made


def _unadvertised_commit(origin: VepyrOrigin) -> str:
    """A commit reachable from no ref of ``origin``; returns its sha."""
    tree = _git(origin.path, "rev-parse", "refs/heads/master^{tree}")
    sha = _git(origin.path, "commit-tree", tree, "-m", "orphan, like a PR head")
    assert sha not in _git(origin.path, "ls-remote", origin.url)
    return sha


def _resolve(origin: VepyrOrigin, ref: str, src_root: Path) -> str:
    return engine.resolve_sha(ref, vepyr_git=origin.url, src_root=src_root)


def test_branch_resolves_to_its_commit(origin: VepyrOrigin, tmp_path: Path) -> None:
    assert _resolve(origin, "master", tmp_path / "src") == origin.shas["master"]
    assert (tmp_path / "src" / "vepyr" / "git").is_dir()


def test_annotated_tag_is_peeled_to_the_commit(
    origin: VepyrOrigin, tmp_path: Path
) -> None:
    tag_object = _git(origin.path, "rev-parse", "refs/tags/0.7.0")
    commit = origin.shas["release"]
    assert tag_object != commit, "fixture: 0.7.0 must be an annotated tag"
    assert _resolve(origin, "0.7.0", tmp_path / "src") == commit


def test_unadvertised_sha_is_fetched_and_resolved(
    origin: VepyrOrigin, tmp_path: Path
) -> None:
    sha = _unadvertised_commit(origin)
    assert _resolve(origin, sha, tmp_path / "src") == sha


def test_unknown_ref_exits_engine(origin: VepyrOrigin, tmp_path: Path) -> None:
    with pytest.raises(RunTestsError) as excinfo:
        _resolve(origin, "no-such-ref", tmp_path / "src")
    assert excinfo.value.code is Exit.ENGINE
    assert f"--vepyr no-such-ref: cannot resolve on {engine.VEPYR_REPO}" in str(
        excinfo.value
    )


def test_cargo_toml_is_read_with_git_show(origin: VepyrOrigin, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def spy(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.run(list(argv), **kwargs)

    src = tmp_path / "src"
    sha = engine.resolve_sha("0.7.0", vepyr_git=origin.url, src_root=src, run=spy)
    manifest = engine._read_cargo_toml(sha, src_root=src, run=spy)
    assert manifest["package"]["name"] == "vepyr"
    assert "datafusion-bio-function-vep" in manifest["dependencies"]
    assert [
        "git",
        "-C",
        str(src / "vepyr" / "git"),
        "show",
        f"{sha}:Cargo.toml",
    ] in calls
    assert all(argv[0] == "git" for argv in calls), calls


def test_missing_cargo_toml_exits_engine(origin: VepyrOrigin, tmp_path: Path) -> None:
    empty_tree = _git(origin.path, "hash-object", "-t", "tree", "-w", os.devnull)
    bare = _git(origin.path, "commit-tree", empty_tree, "-m", "no manifest")
    _git(origin.path, "branch", "bare", bare)
    src = tmp_path / "src"
    sha = engine.resolve_sha("bare", vepyr_git=origin.url, src_root=src)
    with pytest.raises(RunTestsError) as excinfo:
        engine._read_cargo_toml(sha, src_root=src, run=subprocess.run)
    assert excinfo.value.code is Exit.ENGINE
    assert "Cargo.toml@" in str(excinfo.value) and "unreadable" in str(excinfo.value)


def test_resolution_needs_no_gh_on_path(
    origin: VepyrOrigin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PATH`` holds only ``git``: branch, tag, sha and ``Cargo.toml`` still work."""
    git = shutil.which("git")
    assert git is not None
    only_git = tmp_path / "bin"
    only_git.mkdir()
    (only_git / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(only_git))
    assert shutil.which("gh") is None, os.environ["PATH"]

    src = tmp_path / "src"
    assert _resolve(origin, "master", src) == origin.shas["master"]
    assert _resolve(origin, "0.7.0", src) == origin.shas["release"]
    orphan = _unadvertised_commit(origin)
    assert _resolve(origin, orphan, src) == orphan
    manifest = engine._read_cargo_toml(orphan, src_root=src, run=subprocess.run)
    assert manifest["package"]["name"] == "vepyr"
