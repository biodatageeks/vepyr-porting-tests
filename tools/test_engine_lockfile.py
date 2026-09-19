"""Lockfile invariant for the engine ladder (issue #21).

One crate name must map to exactly one ``source`` in ``Cargo.lock``. Two sources for
the same ``datafusion-bio-format-*`` crate (the dev-dependency's floating checkout
plus the revision ``datafusion-bio-function-vep`` pins) made every ``cargo update -p
<bare name>`` abort with ``specification ... is ambiguous``, which killed each real
``./run_tests --vepyr REF`` run before ``cargo test`` was ever reached.

Two levels:

* :func:`test_committed_lockfile_has_one_source_per_crate` — pure lockfile reading,
  always runs, guards the committed graph. It reads ``git show HEAD:Cargo.lock``
  rather than the working-tree file, so a lockfile left rewritten by a killed
  ``--vepyr`` run cannot make the invariant vacuously true.
* :func:`test_vepyr_patched_lockfile_has_one_source_per_crate` — **integration**: a
  real ``cargo`` (not the faked runner of ``test_run_tests_cli.py``) re-resolves the
  graph under the real ``--vepyr`` ``[patch]`` config and the invariant is re-checked
  on the lockfile cargo produced. Opt-in via ``RUN_TESTS_INTEGRATION=1`` because it
  needs network, ``gh`` and a git checkout of the ladder.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Final

import pytest

from run_tests import engine

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
LADDER: Final[re.Pattern[str]] = re.compile(r"^datafusion-bio-(?:format|function)-")
INTEGRATION_ENV: Final[str] = "RUN_TESTS_INTEGRATION"
VEPYR_REF: Final[str] = os.environ.get("RUN_TESTS_VEPYR_REF", "master")


def ladder_sources(lock_text: str) -> dict[str, set[str]]:
    """Ladder crate name → the distinct ``source`` values the lockfile gives it.

    A path-patched package carries no ``source`` key; it is recorded as ``"path"``.
    """
    lock = tomllib.loads(lock_text)
    sources: defaultdict[str, set[str]] = defaultdict(set)
    for package in lock.get("package", []):
        name = package.get("name", "")
        if LADDER.match(name):
            sources[name].add(package.get("source", "path"))
    return dict(sources)


def duplicates(lock_text: str) -> dict[str, set[str]]:
    """Ladder crates that appear under more than one source — must always be empty."""
    return {n: s for n, s in ladder_sources(lock_text).items() if len(s) > 1}


def committed_lock_text() -> str:
    """``HEAD:Cargo.lock`` — the *committed* lockfile, never the working tree copy.

    A `--vepyr` run rewrites the working-tree lockfile (path-patched packages carry
    no ``source`` key), and a run killed before :class:`engine.LockGuard` restores it
    leaves that rewrite on disk. Reading the file would then make the invariant below
    vacuously true, so the committed blob is read through git; outside a checkout, or
    when ``Cargo.lock`` is untracked, the file is the only thing there is.
    """
    completed = subprocess.run(
        ["git", "show", "HEAD:Cargo.lock"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    return (REPO_ROOT / "Cargo.lock").read_text(encoding="utf-8")


def test_committed_lockfile_has_one_source_per_crate() -> None:
    """The committed graph carries exactly one source per ladder crate."""
    text = committed_lock_text()
    found = ladder_sources(text)
    assert found, "Cargo.lock lists no datafusion-bio-* packages"
    assert duplicates(text) == {}


def test_duplicate_detector_is_falsifiable() -> None:
    """Positive control: a hand-built two-source lockfile is reported as duplicate."""
    git = "git+https://github.com/biodatageeks/datafusion-bio-formats.git"
    text = "\n".join(
        (
            "version = 4",
            "[[package]]",
            'name = "datafusion-bio-format-vcf"',
            'version = "1.12.1"',
            f'source = "{git}?tag=v1.12.1#419be985"',
            "[[package]]",
            'name = "datafusion-bio-format-vcf"',
            'version = "1.12.1"',
            f'source = "{git}?branch=master#0ea6f0ee"',
        )
    )
    assert set(duplicates(text)) == {"datafusion-bio-format-vcf"}


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get(INTEGRATION_ENV) != "1",
    reason=f"set {INTEGRATION_ENV}=1 (needs real cargo, gh and network)",
)
def test_vepyr_patched_lockfile_has_one_source_per_crate() -> None:
    """Real cargo under the real ``--vepyr`` patch config: graph stays single-source."""
    for tool in ("cargo", "gh", "git"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH")
    plan, config_path = engine.materialise(VEPYR_REF, repo_root=REPO_ROOT)
    lock_path = REPO_ROOT / "Cargo.lock"
    with engine.LockGuard(REPO_ROOT).held():
        completed = subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version",
                "1",
                "--config",
                str(config_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        assert "is ambiguous" not in completed.stderr, completed.stderr[-2000:]
        resolved = lock_path.read_text(encoding="utf-8")
    assert duplicates(resolved) == {}, (
        f"{plan.label}: duplicate ladder sources {duplicates(resolved)}"
    )


def _crate(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "Cargo.toml").write_text(f'[package]\nname = "{name}"\nversion = "0.0.0"\n')


def test_engine_toml_patches_every_formats_crate(tmp_path: Path) -> None:
    """Siblings such as ``-core`` are patched too, or the graph keeps a git copy."""
    dfbf_root, fmt_root = tmp_path / "dfbf", tmp_path / "formats"
    _crate(dfbf_root / "vep", "datafusion-bio-function-vep")
    (dfbf_root / "Cargo.toml").write_text('[workspace]\nmembers = ["vep"]\n')
    names = (
        "datafusion-bio-format-core",
        "datafusion-bio-format-ensembl-cache",
        "datafusion-bio-format-gff",
        "datafusion-bio-format-vcf",
    )
    for rel, name in zip("abcd", names, strict=True):
        _crate(fmt_root / rel, name)
    (fmt_root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a", "b", "c", "d"]\n'
    )

    def checkout(name: str, path: Path) -> engine.Checkout:
        return engine.Checkout(
            name=name, path=path, git_url="x", rev="r", head="0" * 40
        )

    text = engine.engine_toml(
        dfbf=checkout("dfbf", dfbf_root), formats=checkout("formats", fmt_root)
    )
    for name in names:
        assert re.search(rf"^{re.escape(name)} *= ", text, re.MULTILINE), name
