"""Engine checkout tests: per-sha trees, and reported sha == compiled sha.

Regression cover for #23 — ``.run_tests/src/<repo>`` used to be one mutable
directory that was ``git checkout --detach``'d in place on every run, so two runs
of different revisions shared (and clobbered) one tree.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from run_tests import engine

_MEMBERS: Final[dict[str, tuple[str, ...]]] = {
    "dfbf": ("datafusion/bio-function-vep",),
    "formats": ("datafusion/bio-format-ensembl-cache", "datafusion/bio-format-vcf"),
}
_CRATES: Final[dict[str, tuple[str, ...]]] = {
    "dfbf": ("datafusion-bio-function-vep",),
    "formats": ("datafusion-bio-format-ensembl-cache", "datafusion-bio-format-vcf"),
}


def _git(cwd: Path, *argv: str) -> str:
    out = subprocess.run(
        ["git", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _workspace(root: Path, kind: str, marker: str) -> None:
    members = _MEMBERS[kind]
    root.joinpath("Cargo.toml").write_text(
        "[workspace]\nmembers = [" + ", ".join(f'"{m}"' for m in members) + "]\n",
        encoding="utf-8",
    )
    for member, crate in zip(members, _CRATES[kind], strict=True):
        crate_dir = root / member
        crate_dir.mkdir(parents=True, exist_ok=True)
        crate_dir.joinpath("Cargo.toml").write_text(
            f'[package]\nname = "{crate}"\nversion = "0.0.0"\n', encoding="utf-8"
        )
    root.joinpath("MARKER").write_text(marker, encoding="utf-8")


def _origin(tmp_path: Path, kind: str) -> tuple[Path, str, str]:
    """A local git origin for ``kind`` with two commits; returns (path, sha1, sha2)."""
    root = tmp_path / f"origin-{kind}"
    root.mkdir()
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    shas: list[str] = []
    for marker in ("one", "two"):
        _workspace(root, kind, marker)
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", marker)
        shas.append(_git(root, "rev-parse", "HEAD"))
    return root, shas[0], shas[1]


class _FakeGh:
    """GitHub stub: vepyr sha per ref, and a Cargo.toml pinning local origins."""

    def __init__(self, *, shas: dict[str, str], manifest: dict[str, str]) -> None:
        self.shas = shas
        self.manifest = manifest

    def get(self, path: str) -> Any:
        if path.startswith(f"repos/{engine.VEPYR_REPO}/commits/"):
            ref = path.rsplit("/", 1)[1]
            return {"sha": self.shas[ref]}
        if "contents/Cargo.toml" in path:
            ref = path.rsplit("ref=", 1)[1]
            import base64

            return {
                "encoding": "base64",
                "content": base64.b64encode(self.manifest[ref].encode("utf-8")).decode(
                    "ascii"
                ),
            }
        raise engine.GhError(path, "unexpected")


def _dep(crate: str, url: Path, rev: str) -> str:
    return f"{crate} = {{ git = {json.dumps(url.as_uri())}, rev = {json.dumps(rev)} }}"


def _manifest(*, dfbf_url: Path, dfbf_rev: str, fmt_url: Path, fmt_rev: str) -> str:
    return "\n".join(
        [
            "[dependencies]",
            _dep("datafusion-bio-function-vep", dfbf_url, dfbf_rev),
            _dep("datafusion-bio-format-ensembl-cache", fmt_url, fmt_rev),
            _dep("datafusion-bio-format-vcf", fmt_url, fmt_rev),
            "",
        ]
    )


@pytest.fixture
def ladder(tmp_path: Path) -> dict[str, Any]:
    dfbf, dfbf1, dfbf2 = _origin(tmp_path, "dfbf")
    fmt, fmt1, fmt2 = _origin(tmp_path, "formats")
    manifests = {
        "a" * 40: _manifest(dfbf_url=dfbf, dfbf_rev=dfbf1, fmt_url=fmt, fmt_rev=fmt1),
        "b" * 40: _manifest(dfbf_url=dfbf, dfbf_rev=dfbf2, fmt_url=fmt, fmt_rev=fmt2),
    }
    api = _FakeGh(shas={"old": "a" * 40, "new": "b" * 40}, manifest=manifests)
    return {
        "api": api,
        "src_root": tmp_path / "src",
        "dfbf": (dfbf1, dfbf2),
        "formats": (fmt1, fmt2),
    }


def _run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), **kwargs)


def test_sequential_runs_of_different_revs_keep_independent_trees(
    ladder: dict[str, Any],
) -> None:
    """AC1: two runs, different shas → two trees, neither mutated by the other."""
    src_root: Path = ladder["src_root"]
    old = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    old_dfbf_head = _git(old.dfbf.path, "rev-parse", "HEAD")
    new = engine.resolve("new", api=ladder["api"], src_root=src_root, run=_run)

    assert old.dfbf.path != new.dfbf.path
    assert old.formats.path != new.formats.path
    assert old.dfbf.path.name == ladder["dfbf"][0]
    assert new.dfbf.path.name == ladder["dfbf"][1]
    # The first run's tree is untouched by the second run.
    assert _git(old.dfbf.path, "rev-parse", "HEAD") == old_dfbf_head
    assert (old.dfbf.path / "MARKER").read_text(encoding="utf-8") == "one"
    assert (new.dfbf.path / "MARKER").read_text(encoding="utf-8") == "two"
    assert old.config_text != new.config_text


def test_reported_head_matches_the_checkout_that_was_compiled(
    ladder: dict[str, Any],
) -> None:
    """AC2: every sha the run reports is ``rev-parse HEAD`` of the tree it patched in.

    ``vepyr`` itself is never checked out (the committed ``Cargo.toml`` floats and only
    the dfbf/formats ladder is path-patched), so the trees a run compiles are the
    ladder checkouts named in ``plan.config_text``.
    """
    plan = engine.resolve(
        "new", api=ladder["api"], src_root=ladder["src_root"], run=_run
    )
    assert plan.vepyr_sha == "b" * 40
    for checkout, expected in (
        (plan.dfbf, ladder["dfbf"][1]),
        (plan.formats, ladder["formats"][1]),
    ):
        assert checkout.head == _git(checkout.path, "rev-parse", "HEAD")
        assert checkout.head == expected
        assert checkout.path.name == checkout.head
        assert str(checkout.path) in plan.config_text


def test_same_rev_reuses_its_tree(ladder: dict[str, Any]) -> None:
    """Re-running the same revision reuses the per-sha tree instead of re-cloning."""
    src_root: Path = ladder["src_root"]
    first = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    stamp = first.dfbf.path / "BUILD_ARTIFACT"
    stamp.write_text("kept", encoding="utf-8")
    second = engine.resolve("old", api=ladder["api"], src_root=src_root, run=_run)
    assert second.dfbf.path == first.dfbf.path
    assert stamp.read_text(encoding="utf-8") == "kept"


def _cargo_graph(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Selected path packages and an old git lockfile, independent of Cargo caches."""
    config = tmp_path / "engine.toml"
    crates = (*_CRATES["dfbf"], *_CRATES["formats"])
    config.write_text(
        '[patch."https://example.invalid/engine"]\n'
        + "".join(
            f"{name} = {{ path = {json.dumps(str(tmp_path / name))} }}\n"
            for name in crates
        )
    )
    (tmp_path / "Cargo.lock").write_text(
        "version = 4\n"
        + "".join(
            f'[[package]]\nname = "{name}"\nversion = "0.19.0"\n'
            'source = "git+https://example.invalid/engine?branch=master#'
            + "a" * 40
            + '"\n'
            for name in crates
        )
    )
    packages = [
        dict(
            id=name,
            name=name,
            source=None,
            manifest_path=str(tmp_path / name / "Cargo.toml"),
        )
        for name in crates
    ]
    return config, {
        "packages": packages,
        "resolve": {"nodes": [{"id": p["id"]} for p in packages]},
    }


def test_prepare_cargo_unlocks_qualified_packages_and_checks_locked_graph(
    tmp_path: Path,
) -> None:
    config, graph = _cargo_graph(tmp_path)
    calls = []

    def cargo(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(argv, 0, json.dumps(graph), "")

    engine.prepare_cargo(tmp_path, config, run=cargo)
    assert [call[1] for call in calls] == ["update", "metadata"]
    specs = [calls[0][i + 1] for i, arg in enumerate(calls[0]) if arg == "--package"]
    assert len(specs) == 3
    assert all(
        s.startswith("git+https://example.invalid/engine?branch=master#")
        and s.endswith("@0.19.0")
        for s in specs
    )
    assert "--locked" in calls[1]
    assert all(call[-2:] == ["--config", str(config)] for call in calls)


@pytest.mark.parametrize(
    "mutation", ["git-source", "wrong-path", "missing", "duplicate", "invalid-json"]
)
def test_prepare_cargo_refuses_an_unproven_engine(
    tmp_path: Path, mutation: str
) -> None:
    from run_tests.verdict import Exit, RunTestsError

    config, graph = _cargo_graph(tmp_path)
    package = graph["packages"][0]
    if mutation == "git-source":
        package["source"] = "git+https://example.invalid/old"
    elif mutation == "wrong-path":
        package["manifest_path"] = str(tmp_path / "wrong" / "Cargo.toml")
    elif mutation == "missing":
        graph["resolve"]["nodes"] = graph["resolve"]["nodes"][1:]
    elif mutation == "duplicate":
        graph["packages"].append(dict(package))

    def cargo(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, "not json" if mutation == "invalid-json" else json.dumps(graph), ""
        )

    with pytest.raises(RunTestsError) as caught:
        engine.prepare_cargo(tmp_path, config, run=cargo)
    assert caught.value.code == Exit.ENGINE
