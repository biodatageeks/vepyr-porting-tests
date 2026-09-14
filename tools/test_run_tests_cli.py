"""CLI contract tests for ``./run_tests`` (issue #3 shell, issue #4 cache fetch).

The fetch path is exercised end to end through :func:`run_tests.cli.main` with the
``lister`` / ``downloader`` / ``fasta_fetcher`` seams filled by the synthetic Hub of
:mod:`test_fetch`, so nothing here touches the network.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_fetch import PINS_TOML, REVISIONS, TINY_FA, FakeHub, build_hub

from run_tests import cli
from run_tests.cli import DEFERRED_MESSAGE, main
from run_tests.fetch import PROVENANCE, Flavour, RemoteFile, bsd_sum
from run_tests.summary import HEADER
from run_tests.verdict import Exit


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """One ``main()`` call: its exit code and everything it printed."""

    code: int
    stdout: str
    stderr: str

    @property
    def summary(self) -> str:
        """The trailing summary block, or ``""`` when none was printed."""
        _, marker, block = self.stdout.partition(HEADER)
        return marker + block


@dataclass(frozen=True, slots=True, kw_only=True)
class Harness:
    """A cache root, a synthetic Hub, and a repository whose PINS.toml points at it."""

    root: Path
    hub: FakeHub
    fasta_fetches: list[str]

    def run(self, *argv: str) -> Outcome:
        """Call :func:`run_tests.cli.main` with the fakes wired in."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                list(argv),
                lister=self.hub.lister,
                downloader=self.hub.downloader,
                fasta_fetcher=self._fasta_fetcher,
            )
        return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())

    def _fasta_fetcher(self, url: str, destination: Path) -> None:
        self.fasta_fetches.append(url)
        destination.write_bytes(_GZ)

    @property
    def provenance(self) -> dict[str, object]:
        """The root's ``PROVENANCE.json``, parsed."""
        return json.loads((self.root / PROVENANCE).read_text())


_GZ: bytes = gzip.compress(TINY_FA)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    """Point the CLI at a throwaway repository root whose PINS.toml is synthetic."""
    repo = tmp_path / "repo"
    repo.mkdir()
    gz = tmp_path / "served.fa.gz"
    gz.write_bytes(_GZ)
    checksum, blocks = bsd_sum(gz)
    (repo / "PINS.toml").write_text(
        PINS_TOML + "\n[grch38_fasta]\n"
        'repo = "https://ftp.ensembl.org/x"\n'
        'ref = "tiny.fa.gz"\n'
        f'sha = "{hashlib.sha256(TINY_FA).hexdigest()}"\n'
        'role = "dataset"\n'
        f'ensembl_sum = "{checksum} {blocks}"\n'
    )
    monkeypatch.setattr(cli, "_repo_root", lambda: repo)
    yield Harness(
        root=tmp_path / "cache",
        hub=build_hub(tmp_path / "hub"),
        fasta_fetches=[],
    )


# --- the shell from issue #3 (unchanged paths) ----------------------------------------


def _bare(argv: list[str]) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, lister=_never, downloader=_never, fasta_fetcher=_never)
    return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())


def _never(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("this path must not touch the Hub")


def test_help_lists_required_flags_and_not_contigs() -> None:
    """AC-1: --help exits 0 and names the shipped flags; no --contigs."""
    result = _bare(["--help"])
    assert result.code == 0
    for flag in (
        "--cache-dir",
        "--add-contigs",
        "--flavours",
        "--vepyr",
        "--list",
        "--dry-run",
        "--verify",
        "--fast",
        "--no-trim-manifests",
    ):
        assert flag in result.stdout, flag
    # Flag name must not appear as an option (metavar text may mention contigs).
    assert "--contigs " not in result.stdout


def test_list_reports_zero_data_problem_targets() -> None:
    """AC-2 / scope: --list exits 0 with an explicit empty target list."""
    result = _bare(["--list"])
    assert result.code == 0
    assert "0 data-problem targets" in result.stdout


def test_bare_invocation_is_deferred_not_success() -> None:
    """AC-2: bare argv must not exit 0 claiming tests passed."""
    result = _bare([])
    assert result.code != 0
    combined = result.stdout + result.stderr
    assert DEFERRED_MESSAGE in combined
    assert "passed" not in combined.lower()


def test_a_glob_in_add_contigs_is_refused_before_anything_is_listed() -> None:
    """An unquoted ``chr*`` must not turn a one-contig fetch into the whole genome."""
    result = _bare(["--cache-dir", "/tmp/x", "--add-contigs", "chr*"])
    assert result.code == int(Exit.USAGE)
    assert "--add-contigs" in result.stderr


# --- issue #4: the fetch dispatch -----------------------------------------------------


def test_cache_dir_with_contigs_fetches_writes_provenance_and_summarises(
    harness: Harness,
) -> None:
    """AC-2/AC-3: a real (mocked) fetch exits 0 and reports what landed on disk."""
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
    )
    assert result.code == int(Exit.OK), result.stderr
    assert (harness.root / PROVENANCE).is_file()
    assert (harness.root / "116_GRCh38_ensembl/variation/chr21.parquet").is_file()
    assert harness.fasta_fetches == ["https://ftp.ensembl.org/x/tiny.fa.gz"], (
        "FASTA is automatic on a real fetch, not a flag"
    )
    assert (harness.root / "fasta/tiny.fa.fai").is_file()
    assert result.summary.startswith(HEADER)
    assert "contigs requested: chr21" in result.summary
    assert "ensembl : chr21" in result.summary
    assert "outcome          : ok (exit 0)" in result.summary


def test_a_second_run_accumulates_and_the_summary_shows_both_contigs(
    harness: Harness,
) -> None:
    """AC-3: the effective set is the accumulated one, not the last argv list."""
    common = ["--cache-dir", str(harness.root), "--flavours", "ensembl"]
    assert harness.run(*common, "--add-contigs", "chr21").code == int(Exit.OK)
    result = harness.run(*common, "--add-contigs", "chr22")
    assert result.code == int(Exit.OK), result.stderr
    assert (harness.root / "116_GRCh38_ensembl/exon/chr21.parquet").is_file()
    assert "contigs requested: chr22" in result.summary
    assert "ensembl : chr21, chr22" in result.summary


def test_dry_run_lists_and_writes_nothing(harness: Harness) -> None:
    """AC-1: --dry-run names the Hub files and does not create the root."""
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
        "--dry-run",
    )
    assert result.code == int(Exit.OK), result.stderr
    assert not harness.root.exists(), "dry-run must not create the cache directory"
    assert harness.hub.calls == [] and harness.fasta_fetches == []
    assert "116_GRCh38_ensembl/variation/chr21.parquet" in result.stdout
    assert "dry-run          : yes" in result.summary
    assert "fasta            : no" in result.summary
    assert "(none)" in result.summary, "nothing accumulated on disk yet"


def test_a_revision_clash_surfaces_exit_3_through_main(harness: Harness) -> None:
    """AC-2 step 3: a root holding another revision is refused, with the summary."""
    common = ["--cache-dir", str(harness.root), "--flavours", "ensembl"]
    assert harness.run(*common, "--add-contigs", "chr21").code == int(Exit.OK)
    path = harness.root / PROVENANCE
    path.write_text(path.read_text().replace(REVISIONS[Flavour.ENSEMBL], "0" * 40))
    result = harness.run(*common, "--add-contigs", "chr21")
    assert result.code == int(Exit.REVISION)
    assert "revision on disk" in result.stderr
    assert "run_tests: error (revision, exit 3)" in result.stderr
    assert "outcome          : revision (exit 3)" in result.summary


def test_a_contig_missing_from_one_entity_is_refused_with_exit_4(
    harness: Harness,
) -> None:
    """AC-4's caveat, enforced: an entity with no shard for any requested contig is
    refused (like chrMT alone on the real dataset, where motif/regulatory carry no
    chrMT shard; the synthetic hub stands a missing chr22 shard in for that case)."""
    motif = harness.hub.repo_dir("biodatageeks/vepyr_116_GRCh38_ensembl") / "motif"
    (motif / "chr22.parquet").unlink()
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr22",
        "--flavours",
        "ensembl",
    )
    assert result.code == int(Exit.INCOMPLETE)
    assert "motif" in result.stderr
    assert "outcome          : incomplete (exit 4)" in result.summary
    assert not harness.root.exists()


def test_an_unknown_flavour_is_a_usage_error_on_the_fetch_path(
    harness: Harness,
) -> None:
    result = harness.run("--cache-dir", str(harness.root), "--flavours", "bogus")
    assert result.code == int(Exit.USAGE)
    assert "bogus" in result.stderr
    assert "outcome          : usage (exit 2)" in result.summary


def test_no_trim_manifests_leaves_the_manifest_verbatim(harness: Harness) -> None:
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
        "--no-trim-manifests",
    )
    assert result.code == int(Exit.OK), result.stderr
    manifest = json.loads(
        (harness.root / "116_GRCh38_ensembl/exon/chrom_manifest.json").read_text()
    )
    assert [e["chrom"] for e in manifest] == ["chr1", "chr21", "chr22"]
    assert "trim manifests   : no" in result.summary


def test_verify_is_passed_through_and_catches_a_corrupted_shard(
    harness: Harness,
) -> None:
    common = ["--cache-dir", str(harness.root), "--flavours", "ensembl"]
    assert harness.run(*common, "--add-contigs", "chr21", "--verify").code == int(
        Exit.OK
    )
    (harness.root / "116_GRCh38_ensembl/variation/chr21.parquet").write_bytes(b"bad")
    result = harness.run(*common, "--add-contigs", "chr21", "--verify")
    assert result.code == int(Exit.VERIFY)
    assert "outcome          : verify (exit 5)" in result.summary


def test_fast_exports_the_env_before_the_first_hub_call(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--fast`` must be in the environment before huggingface_hub is first touched."""
    monkeypatch.delenv("HF_XET_HIGH_PERFORMANCE", raising=False)
    seen: list[str | None] = []
    real: Callable[[str, str], list[RemoteFile]] = harness.hub.lister

    def spy(repo_id: str, revision: str) -> list[RemoteFile]:
        seen.append(os.environ.get("HF_XET_HIGH_PERFORMANCE"))
        return real(repo_id, revision)

    harness.hub.lister = spy  # type: ignore[method-assign]
    common = [
        "--cache-dir",
        str(harness.root),
        "--add-contigs",
        "chr21",
        "--flavours",
        "ensembl",
        "--dry-run",
    ]
    assert harness.run(*common, "--fast").code == int(Exit.OK)
    assert seen == ["1"]
    monkeypatch.delenv("HF_XET_HIGH_PERFORMANCE", raising=False)
    seen.clear()
    assert harness.run(*common).code == int(Exit.OK)
    assert seen == [None], "without --fast nothing is exported (control)"
