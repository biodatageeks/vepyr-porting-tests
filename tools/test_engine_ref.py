"""``--vepyr REF`` allowlist: reject malformed refs before any ``gh api`` call.

Covers issue #25 — :func:`run_tests.engine.validate_ref` guards the interpolation
of ``ref`` into ``repos/<repo>/commits/<ref>``; the recording :class:`_SpyGh`
proves rejection happens *before* the API is touched.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from run_tests import engine
from run_tests.verdict import Exit, RunTestsError


class _SpyGh:
    """A :class:`run_tests.engine.GhApi` that records every path it is asked for."""

    def __init__(self, sha: str = "a" * 40) -> None:
        self.sha = sha
        self.paths: list[str] = []

    def get(self, path: str) -> object:
        self.paths.append(path)
        if path.startswith(f"repos/{engine.VEPYR_REPO}/commits/"):
            return {"sha": self.sha}
        if "contents/Cargo.toml" in path:
            return {
                "encoding": "base64",
                "content": base64.b64encode(_LADDER.encode()).decode(),
            }
        raise engine.GhError(path, "unexpected")


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
def test_bad_ref_rejected_before_any_gh_call(ref: str) -> None:
    """A malformed ref raises a usage error and the ``gh`` runner stays untouched."""
    spy = _SpyGh()
    with pytest.raises(RunTestsError) as excinfo:
        engine.resolve(ref, api=spy, src_root=Path("/nonexistent"))
    assert excinfo.value.code is Exit.USAGE
    assert "not a valid git ref" in str(excinfo.value)
    assert spy.paths == [], f"gh api was called for {ref!r}: {spy.paths}"


@pytest.mark.parametrize("ref", GOOD_REFS)
def test_good_ref_accepted(ref: str) -> None:
    """Legitimate branch names (``/`` included), tags and shas pass the allowlist."""
    assert engine.validate_ref(ref) == ref


@pytest.mark.parametrize("ref", GOOD_REFS)
def test_good_ref_still_resolves_to_the_same_sha(
    ref: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid refs reach the commits endpoint verbatim and resolve as before."""

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
    spy = _SpyGh()
    plan = engine.resolve(ref, api=spy, src_root=tmp_path)
    assert plan.vepyr_sha == "a" * 40
    assert plan.ref == ref
    assert spy.paths[0] == f"repos/{engine.VEPYR_REPO}/commits/{ref}"
