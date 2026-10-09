"""``--vepyr REF`` allowlist: reject malformed refs before any ``git`` call.

Covers issue #25 — :func:`run_tests.engine.validate_ref` guards the interpolation
of ``ref`` into the ``git`` argv that resolves it (#69: plain git, no ``gh``); the
recording :class:`_SpyGit` runner proves rejection happens *before* any subprocess.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from run_tests import engine
from run_tests.verdict import Exit, RunTestsError


class _SpyGit:
    """A :data:`run_tests.engine.Runner` that records argv and fakes the vepyr mirror.

    ``rev-parse`` answers ``sha``; ``show <sha>:Cargo.toml`` answers the ladder.
    """

    def __init__(self, sha: str = "a" * 40) -> None:
        self.sha = sha
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        assert argv[0] == "git", argv
        out = ""
        if "rev-parse" in argv:
            out = self.sha
        elif "show" in argv:
            assert argv[-1] == f"{self.sha}:Cargo.toml", argv
            out = _LADDER
        return subprocess.CompletedProcess(argv, 0, out, "")


_LADDER = f"""
[package]
name = "vepyr"
version = "0.0.0"

[dependencies]
datafusion-bio-function-vep = {{ git = "{engine.DFBF_GIT}", rev = "{"b" * 40}" }}
datafusion-bio-format-ensembl-cache = {{ git = "{engine.FORMATS_GIT}", tag = "v0" }}
datafusion-bio-format-vcf = {{ git = "{engine.FORMATS_GIT}", tag = "v0" }}
"""

BAD_REFS: list[str] = [
    "..",
    "../../etc/passwd",
    "main/../../../repos/other/commits/main",
    "feature/..%2f",
    "-rf",
    "--vepyr",
    "master?foo=1",
    "master#frag",
    "master&x=1",
    "refs/heads/master?ref=evil",
    "",
    "master with space",
    "master\n",
    ".hidden",
    "feature/.hidden",
    "master.lock",
    "/master",
    "master/",
    "a//b",
    "master.",
    "a" * 256,
]

GOOD_REFS: list[str] = [
    "master",
    "main",
    "feature/x",
    "harness/issue-14-15-run-tests",
    "release/v0.7.0",
    "v0.7.0",
    "0.7.0",
    "a" * 40,
    "a1b2c3d",
    "user+tag",
    "refs/heads/main",
]


@pytest.mark.parametrize("ref", BAD_REFS)
def test_bad_ref_rejected_before_any_git_call(ref: str) -> None:
    """A malformed ref raises a usage error and the ``git`` runner stays untouched."""
    spy = _SpyGit()
    with pytest.raises(RunTestsError) as excinfo:
        engine.resolve(ref, src_root=Path("/nonexistent"), run=spy)
    assert excinfo.value.code is Exit.USAGE
    assert "not a valid git ref" in str(excinfo.value)
    assert spy.calls == [], f"git was called for {ref!r}: {spy.calls}"


@pytest.mark.parametrize("ref", BAD_REFS)
def test_bad_ref_rejected_by_resolve_sha_before_any_git_call(ref: str) -> None:
    """The public :func:`run_tests.engine.resolve_sha` (``--via-cli``) guards too."""
    spy = _SpyGit()
    with pytest.raises(RunTestsError) as excinfo:
        engine.resolve_sha(ref, src_root=Path("/nonexistent"), run=spy)
    assert excinfo.value.code is Exit.USAGE
    assert spy.calls == [], f"git was called for {ref!r}: {spy.calls}"


@pytest.mark.parametrize("ref", GOOD_REFS)
def test_good_ref_accepted(ref: str) -> None:
    """Legitimate branch names (``/`` included), tags and shas pass the allowlist."""
    assert engine.validate_ref(ref) == ref


@pytest.mark.parametrize("ref", GOOD_REFS)
def test_good_ref_still_resolves_to_the_same_sha(
    ref: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid refs reach ``git rev-parse`` verbatim and resolve as before."""

    def fake_checkout(**kwargs: object) -> engine.Checkout:
        return engine.Checkout(
            name=str(kwargs["name"]),
            path=Path(str(kwargs["target"])),
            git_url=str(kwargs["git_url"]),
            rev=str(kwargs["rev"]),
            head="d" * 40,
        )

    monkeypatch.setattr(engine, "_checkout_repo", fake_checkout)
    monkeypatch.setattr(engine, "engine_toml", lambda **_: "# stub\n")
    spy = _SpyGit()
    plan = engine.resolve(ref, src_root=tmp_path, run=spy)
    assert plan.vepyr_sha == "a" * 40
    assert plan.ref == ref
    rev_parse = next(argv for argv in spy.calls if "rev-parse" in argv)
    assert rev_parse[-2:] == ["--end-of-options", f"{ref}^{{commit}}"]
    assert rev_parse[:3] == ["git", "-C", str(tmp_path / "vepyr" / "git")]
