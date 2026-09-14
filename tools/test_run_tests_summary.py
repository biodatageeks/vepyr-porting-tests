"""Tests for :mod:`run_tests.summary` — the architectural end-of-run block."""

from __future__ import annotations

import json
from pathlib import Path

from run_tests.summary import (
    HEADER,
    NEVER_FETCHED,
    UNREADABLE,
    WHOLE_GENOME,
    RunSummary,
    accumulated_contigs,
    render,
)
from run_tests.verdict import Exit


def _summary(**overrides: object) -> RunSummary:
    base: dict[str, object] = {
        "cache_dir": Path("/cache"),
        "add_contigs": ("chr21",),
        "flavours": ("ensembl",),
        "vepyr": None,
        "fasta": True,
        "dry_run": False,
        "verify": False,
        "fast": False,
        "trim_manifests": True,
        "outcome": Exit.OK,
        "detail": None,
    }
    base.update(overrides)
    return RunSummary(**base)  # type: ignore[arg-type]


def _fields(block: str) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in block.splitlines())
        if value
    }


def test_every_effective_flag_is_expanded() -> None:
    """AC-3: the block names every parameter in force, not only the ones given."""
    block = render(
        _summary(
            vepyr="main",
            verify=True,
            fast=True,
            trim_manifests=False,
            add_contigs=("chr21", "chrMT"),
        ),
        {"ensembl": ["chr21", "chrMT"]},
    )
    assert block.startswith(HEADER) and block.endswith("\n")
    fields = _fields(block)
    assert fields["cache dir"] == "/cache"
    assert fields["flavours"] == "ensembl"
    assert fields["contigs requested"] == "chr21, chrMT"
    assert fields["vepyr"] == "main"
    assert fields["fasta"] == "yes"
    assert fields["dry-run"] == "no"
    assert fields["verify"] == "yes"
    assert fields["fast"] == "yes"
    assert fields["trim manifests"] == "no"
    assert fields["outcome"] == "ok (exit 0)"


def test_accumulated_contigs_are_reported_next_to_the_requested_ones() -> None:
    """AC-3: the effective set is the accumulated one, not this run's argv."""
    block = render(_summary(add_contigs=("chr15",)), {"ensembl": ["chr21", "chr15"]})
    assert "contigs requested: chr15" in block
    assert "ensembl : chr21, chr15" in block


def test_a_whole_genome_run_renders_all_on_both_lines() -> None:
    block = render(_summary(add_contigs=None), {"ensembl": "ALL"})
    assert block.count(WHOLE_GENOME) == 2


def test_a_refuse_renders_its_code_and_detail() -> None:
    block = render(
        _summary(outcome=Exit.REVISION, detail="revision on disk 000 != PINS abc"), {}
    )
    assert "outcome          : revision (exit 3)" in block
    assert "detail           : revision on disk 000 != PINS abc" in block
    assert "(none)" in block, "no cache reading happened, the section says so"


def test_a_bare_invocation_renders_none_everywhere() -> None:
    block = render(
        _summary(cache_dir=None, add_contigs=None, fasta=False, outcome=Exit.USAGE)
    )
    fields = _fields(block)
    assert fields["cache dir"] == "(none)"
    assert fields["contigs requested"] == "(none)"
    assert fields["fasta"] == "no"


def test_accumulated_contigs_reads_provenance(tmp_path: Path) -> None:
    """A fresh directory, a recorded flavour, and one never fetched, in one read."""
    assert accumulated_contigs(tmp_path, ("ensembl",)) == {"ensembl": NEVER_FETCHED}
    (tmp_path / "PROVENANCE.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "written_at": "",
                "tool": "",
                "pins_toml_sha256": "",
                "datasets": {
                    "ensembl": {
                        "repo_id": "org/repo",
                        "revision": "a" * 40,
                        "contigs": ["chr21", "chr22"],
                        "manifests_trimmed": True,
                        "files": 15,
                        "bytes": 1,
                    }
                },
                "fasta": None,
                "runs": [],
            }
        )
    )
    assert accumulated_contigs(tmp_path, ("ensembl", "refseq")) == {
        "ensembl": ["chr21", "chr22"],
        "refseq": NEVER_FETCHED,
    }


def test_an_unreadable_provenance_is_reported_not_raised(tmp_path: Path) -> None:
    """The summary describes the directory it found; refusing is the fetch's job."""
    (tmp_path / "PROVENANCE.json").write_text("{ not json")
    assert accumulated_contigs(tmp_path, ("ensembl",)) == {"ensembl": UNREADABLE}
    assert UNREADABLE in render(_summary(), accumulated_contigs(tmp_path, ("ensembl",)))
