"""The vepyr cache freshness guard of ``./run_tests`` (issue #236).

The Hub HEAD is injected through ``head_resolver`` (never the network), on top of the
synthetic Hub, fake cargo and fake GitHub of :mod:`test_run_tests_cli`.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_fetch import REVISIONS
from test_run_tests_cli import (
    Harness,
    Outcome,
    _FakeGh,
    _stub_engine,
    _tiny_ladder_toml,
    fresh_head,
)
from test_run_tests_cli import harness as harness  # re-exported pytest fixture

from run_tests import tests
from run_tests.cli import main
from run_tests.fetch import Flavour, HeadResolver, RemoteFile
from run_tests.verdict import Exit

NEWER: str = "f" * 40
"""A Hub HEAD that differs from every pin."""


def newer_head(repo_id: str, ref: str) -> str:
    """A Hub whose ``ref`` moved past every pin."""
    return NEWER


def unreachable_head(repo_id: str, ref: str) -> str:
    """A Hub that cannot be reached."""
    raise ConnectionError("simulated: Hub unreachable")


@dataclass
class HeadSpy:
    """Records every ``(repo_id, ref)`` the guard asks about; answers the pin."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, repo_id: str, ref: str) -> str:
        self.calls.append((repo_id, ref))
        return fresh_head(repo_id, ref)


def _fetch(
    h: Harness, *extra: str, head_resolver: HeadResolver = fresh_head
) -> Outcome:
    """Fetch chr21 of ensembl into the harness root under the given Hub HEAD."""
    return h.run(
        "--cache-dir",
        str(h.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
        *extra,
        head_resolver=head_resolver,
    )


def test_fresh_pin_records_no_old_cache(harness: Harness) -> None:
    result = _fetch(harness)
    assert result.code == int(Exit.OK), result.stderr
    assert "old cache: no" in result.summary
    assert f"pinned {REVISIONS[Flavour.ENSEMBL]}" in result.summary


def test_newer_head_exits_stale(harness: Harness) -> None:
    result = _fetch(harness, head_resolver=newer_head)
    assert result.code == int(Exit.STALE_CACHE) == 7
    assert REVISIONS[Flavour.ENSEMBL] in result.stderr
    assert NEWER in result.stderr
    assert "--old-vepyr-cache" in result.stderr
    assert "outcome          : stale_cache (exit 7)" in result.summary
    assert not harness.root.exists(), "the guard must refuse before any download"


def test_consent_flag_records_old_cache(harness: Harness) -> None:
    result = _fetch(harness, "--old-vepyr-cache", head_resolver=newer_head)
    assert result.code == int(Exit.OK), result.stderr
    assert "old cache: YES (consented)" in result.summary
    assert f"HEAD(main) {NEWER}" in result.summary


def test_hub_unreachable_exits_stale(harness: Harness) -> None:
    result = _fetch(harness, head_resolver=unreachable_head)
    assert result.code == int(Exit.STALE_CACHE)
    assert "freshness unknown" in result.stderr


@pytest.fixture
def fetched_env(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """An ensembl-only fetched root, exported as ``$VEPYR_CACHE_ROOT``."""
    assert _fetch(harness).code == int(Exit.OK)
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    return harness


def test_no_provenance_exits_stale(fetched_env: Harness) -> None:
    # refseq is selected but the root (used as-is) has no PROVENANCE record for it.
    result = fetched_env.run("--flavours", "ensembl,refseq")
    assert result.code == int(Exit.STALE_CACHE), result.stderr
    assert "no PROVENANCE.json record" in result.stderr


def test_no_provenance_with_consent_flag(fetched_env: Harness) -> None:
    result = fetched_env.run("--flavours", "ensembl,refseq", "--old-vepyr-cache")
    assert result.code == int(Exit.OK), result.stderr
    assert "old cache: YES (consented)" in result.summary


def test_dry_run_exits_stale(harness: Harness) -> None:
    downloads: list[str] = []
    listings: list[str] = []

    def downloader(
        repo_id: str, revision: str, allow_patterns: list[str], local_dir: Path
    ) -> None:
        downloads.append(repo_id)

    def lister(repo_id: str, revision: str) -> list[RemoteFile]:
        listings.append(repo_id)
        return harness.hub.lister(repo_id, revision)

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(
            ["--cache-dir", str(harness.root), "--flavours", "ensembl", "--dry-run"],
            lister=lister,
            downloader=downloader,
            fasta_fetcher=lambda url, dest: downloads.append(url),
            cargo_runner=harness.cargo,
            head_resolver=newer_head,
        )
    assert code == int(Exit.STALE_CACHE), err.getvalue()
    assert downloads == [] and listings == []
    assert "dry-run          : yes" in out.getvalue()


def test_selected_flavours_only(harness: Harness) -> None:
    spy = HeadSpy()
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--flavours",
        "merged",
        "--dry-run",
        head_resolver=spy,
    )
    assert result.code == int(Exit.OK), result.stderr
    assert spy.calls == [("biodatageeks/vepyr_116_GRCh38_merged", "main")]


def test_only_still_guarded(
    fetched_env: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_engine(tmp_path / "src", monkeypatch)
    only = tmp_path / "scratch" / "one"
    only.mkdir(parents=True)
    (only / "test.toml").write_text('name = "one"\n', encoding="utf-8")
    result = fetched_env.run(
        "--flavours",
        "ensembl",
        "--vepyr",
        "0.7.0",
        "--only",
        str(only),
        gh_api=_FakeGh(_tiny_ladder_toml()),
        head_resolver=newer_head,
    )
    assert result.code == int(Exit.STALE_CACHE), result.stderr
    assert fetched_env.cargo.calls == [], "the guard must refuse before cargo"


def test_real_resolver_unreachable_hub_exits_stale(tmp_path: Path) -> None:
    """The real ``hub_head_resolver`` against a refused port: exit 7, no traceback."""
    env = {
        **os.environ,
        "HF_ENDPOINT": "http://127.0.0.1:9",  # discard port: connection refused
        "HF_HUB_OFFLINE": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).parent),
    }
    argv = ["--cache-dir", str(tmp_path / "root"), "--flavours", "ensembl", "--dry-run"]
    proc = subprocess.run(
        [sys.executable, "-m", "run_tests", *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == Exit.STALE_CACHE, proc.stderr[-800:]
    assert "Traceback" not in proc.stderr
    assert "freshness unknown" in proc.stderr
    assert not (tmp_path / "root").exists()
