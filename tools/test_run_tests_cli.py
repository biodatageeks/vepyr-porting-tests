"""CLI contract tests for ``./run_tests`` (fetch + data-test run wiring).

The fetch path is exercised end to end through :func:`run_tests.cli.main` with the
``lister`` / ``downloader`` / ``fasta_fetcher`` seams filled by the synthetic Hub of
:mod:`test_fetch`. Cargo and GitHub are injected so nothing here touches the network
or spawns a real ``cargo``.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from test_fetch import PINS_TOML, REVISIONS, TINY_FA, FakeHub, build_hub

from run_tests import cli, engine, tests
from run_tests.cli import MISSING_CACHE, MISSING_VEPYR, main
from run_tests.fetch import PROVENANCE, Flavour, RemoteFile, bsd_sum
from run_tests.summary import HEADER
from run_tests.verdict import Exit, RunTestsError


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """One ``main()`` call: its exit code and everything it printed."""

    code: int
    stdout: str
    stderr: str

    @property
    def summary(self) -> str:
        _, marker, block = self.stdout.partition(HEADER)
        return marker + block


@dataclass
class CargoLog:
    """Records cargo argv invocations for assertions."""

    calls: list[tuple[list[str], dict[str, str]]] = field(default_factory=list)
    test_exit_code: int = 0

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> int:
        self.calls.append((list(argv), dict(env)))
        return self.test_exit_code


@dataclass(frozen=True, slots=True, kw_only=True)
class Harness:
    """A cache root, a synthetic Hub, and a repository whose PINS.toml points at it."""

    root: Path
    hub: FakeHub
    fasta_fetches: list[str]
    cargo: CargoLog
    repo: Path

    def run(self, *argv: str, gh_api: engine.GhApi | None = None) -> Outcome:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                list(argv),
                lister=self.hub.lister,
                downloader=self.hub.downloader,
                fasta_fetcher=self._fasta_fetcher,
                cargo_runner=self.cargo,
                gh_api=gh_api,
            )
        return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())

    def _fasta_fetcher(self, url: str, destination: Path) -> None:
        self.fasta_fetches.append(url)
        destination.write_bytes(_GZ)

    @property
    def provenance(self) -> dict[str, object]:
        return json.loads((self.root / PROVENANCE).read_text())


_GZ: bytes = gzip.compress(TINY_FA)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    """Point the CLI at a throwaway repository root whose PINS.toml is synthetic."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
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
        cargo=CargoLog(),
        repo=repo,
    )


def _bare(argv: list[str], **kwargs: object) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(
            argv,
            lister=_never,
            downloader=_never,
            fasta_fetcher=_never,
            **kwargs,  # type: ignore[arg-type]
        )
    return Outcome(code=code, stdout=out.getvalue(), stderr=err.getvalue())


def _never(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("this path must not touch the Hub")


def test_help_lists_required_flags_and_not_contigs() -> None:
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
    assert "--contigs " not in result.stdout


def test_list_reports_zero_data_problem_targets() -> None:
    result = _bare(["--list"])
    assert result.code == 0
    assert "0 data-problem target(s)" in result.stdout
    assert "(none)" in result.stdout


def test_list_reports_discovered_data_targets(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    (harness.repo / "tests" / "data_example.rs").write_text("// stub\n")
    result = harness.run("--list")
    assert result.code == 0
    assert "data  data_example" in result.stdout
    assert "1 data-problem target(s)" in result.stdout


def test_bare_invocation_needs_cache_root() -> None:
    result = _bare([])
    assert result.code == int(Exit.USAGE)
    assert MISSING_CACHE in result.stderr


def test_a_glob_in_add_contigs_is_refused_before_anything_is_listed() -> None:
    result = _bare(["--cache-dir", "/tmp/x", "--add-contigs", "chr*"])
    assert result.code == int(Exit.USAGE)
    assert "--add-contigs" in result.stderr


def test_cache_dir_with_contigs_fetches_writes_provenance_and_summarises(
    harness: Harness,
) -> None:
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
    assert harness.fasta_fetches == ["https://ftp.ensembl.org/x/tiny.fa.gz"]
    assert (harness.root / "fasta/tiny.fa.fai").is_file()
    assert result.summary.startswith(HEADER)
    assert "contigs requested: chr21" in result.summary
    assert "ensembl : chr21" in result.summary
    assert "0 data-problem target(s)" in result.summary
    assert "outcome          : ok (exit 0)" in result.summary
    assert harness.cargo.calls == []


def test_a_second_run_accumulates_and_the_summary_shows_both_contigs(
    harness: Harness,
) -> None:
    common = ["--cache-dir", str(harness.root), "--flavours", "ensembl"]
    assert harness.run(*common, "--add-contigs", "chr21").code == int(Exit.OK)
    result = harness.run(*common, "--add-contigs", "chr22")
    assert result.code == int(Exit.OK), result.stderr
    assert (harness.root / "116_GRCh38_ensembl/exon/chr21.parquet").is_file()
    assert "contigs requested: chr22" in result.summary
    assert "ensembl : chr21, chr22" in result.summary


def test_dry_run_lists_and_writes_nothing(harness: Harness) -> None:
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
    assert not harness.root.exists()
    assert harness.hub.calls == [] and harness.fasta_fetches == []
    assert "116_GRCh38_ensembl/variation/chr21.parquet" in result.stdout
    assert "dry-run          : yes" in result.summary
    assert "fasta            : no" in result.summary


def test_env_cache_root_honours_dry_run(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for #27: ``$VEPYR_CACHE_ROOT`` + ``--dry-run`` is a true no-op.

    Pre-fix the guard read ``inv.cache_dir is not None``, so the env-supplied root fell
    through to a real engine checkout and a real ``cargo test``.
    """
    harness.root.mkdir()
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    (harness.repo / "tests" / "data_pilot.rs").write_text("// stub\n")

    def snapshot() -> set[Path]:
        return {
            path.relative_to(tmp_path)
            for base in (harness.root, harness.repo)
            for path in base.rglob("*")
        }

    before = snapshot()
    result = harness.run(
        "--flavours",
        "ensembl",
        "--add-contigs",
        "chr21",
        "--dry-run",
        "--vepyr",
        "0.7.0",
    )
    assert result.code == int(Exit.OK), result.stderr
    assert snapshot() == before, "dry-run must not write under the cache root or repo"
    assert harness.cargo.calls == [], "dry-run must not invoke cargo"
    assert harness.fasta_fetches == []
    assert "dry-run          : yes" in result.summary
    assert "fasta            : no" in result.summary


def test_a_revision_clash_surfaces_exit_3_through_main(harness: Harness) -> None:
    common = ["--cache-dir", str(harness.root), "--flavours", "ensembl"]
    assert harness.run(*common, "--add-contigs", "chr21").code == int(Exit.OK)
    path = harness.root / PROVENANCE
    path.write_text(path.read_text().replace(REVISIONS[Flavour.ENSEMBL], "0" * 40))
    result = harness.run(*common, "--add-contigs", "chr21")
    assert result.code == int(Exit.REVISION)
    assert "revision on disk" in result.stderr
    assert "outcome          : revision (exit 3)" in result.summary


def test_a_contig_missing_from_one_entity_is_refused_with_exit_4(
    harness: Harness,
) -> None:
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
    assert seen == [None]


def test_targets_without_vepyr_are_usage_errors(harness: Harness) -> None:
    assert (
        harness.run(
            "--cache-dir",
            str(harness.root),
            "--add-contigs",
            "chr21",
            "--flavours",
            "ensembl",
        ).code
        == int(Exit.OK)
    )
    (harness.repo / "tests" / "data_pilot.rs").write_text("// stub\n")
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--flavours",
        "ensembl",
    )
    assert result.code == int(Exit.USAGE)
    assert MISSING_VEPYR in result.stderr


class _FakeGh:
    """Minimal GhApi: returns a fixed vepyr Cargo.toml ladder."""

    def __init__(self, cargo_toml: str, sha: str = "a" * 40) -> None:
        self.sha = sha
        self.cargo_toml = cargo_toml

    def get(self, path: str) -> object:
        if path.startswith(f"repos/{engine.VEPYR_REPO}/commits/"):
            return {"sha": self.sha}
        if "contents/Cargo.toml" in path:
            import base64

            return {
                "encoding": "base64",
                "content": base64.b64encode(self.cargo_toml.encode()).decode(),
            }
        raise engine.GhError(path, "unexpected")


def _tiny_ladder_toml() -> str:
    rev = "b" * 40
    dfbf = (
        f'datafusion-bio-function-vep = {{ git = "{engine.DFBF_GIT}", '
        f'rev = "{rev}", features = ["cache-builder"] }}'
    )
    return f"""
[package]
name = "vepyr"
version = "0.0.0"

[dependencies]
{dfbf}
datafusion-bio-format-ensembl-cache = {{ git = "{engine.FORMATS_GIT}", tag = "v0.0.0" }}
datafusion-bio-format-vcf = {{ git = "{engine.FORMATS_GIT}", tag = "v0.0.0" }}
"""


def _write_crate(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.0.0"\n'
    )


def test_vepyr_run_invokes_cargo_with_cache_env(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert (
        harness.run(
            "--cache-dir",
            str(harness.root),
            "--add-contigs",
            "chr21",
            "--flavours",
            "ensembl",
        ).code
        == int(Exit.OK)
    )
    (harness.repo / "tests" / "data_pilot.rs").write_text("// stub\n")

    src = tmp_path / "src"
    dfbf = src / "datafusion-bio-functions" / "datafusion" / "bio-function-vep"
    formats = src / "datafusion-bio-formats" / "datafusion"
    fmt_cache = formats / "bio-format-ensembl-cache"
    fmt_vcf = formats / "bio-format-vcf"
    _write_crate(dfbf, "datafusion-bio-function-vep")
    _write_crate(fmt_cache, "datafusion-bio-format-ensembl-cache")
    _write_crate(fmt_vcf, "datafusion-bio-format-vcf")
    (src / "datafusion-bio-functions" / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["datafusion/bio-function-vep"]\n'
    )
    (src / "datafusion-bio-formats" / "Cargo.toml").write_text(
        "[workspace]\nmembers = ["
        '"datafusion/bio-format-ensembl-cache", '
        '"datafusion/bio-format-vcf"]\n'
    )

    def fake_checkout(**kwargs: object) -> engine.Checkout:
        name = str(kwargs["name"])
        target = Path(str(kwargs["target"]))
        return engine.Checkout(
            name=name,
            path=target,
            git_url=str(kwargs["git_url"]),
            rev=str(kwargs["rev"]),
            head="d" * 40,
        )

    monkeypatch.setattr(engine, "default_src_root", lambda environ=None: src)
    monkeypatch.setattr(engine, "_checkout_repo", fake_checkout)

    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--flavours",
        "ensembl",
        "--vepyr",
        "0.7.0",
        gh_api=_FakeGh(_tiny_ladder_toml()),
    )
    assert result.code == int(Exit.OK), result.stderr
    assert harness.cargo.calls, "cargo should have been invoked"
    cargo_test = [c for c in harness.cargo.calls if c[0][:2] == ["cargo", "test"]]
    assert cargo_test
    argv, env = cargo_test[0]
    assert "--test" in argv and "data_pilot" in argv
    assert env[tests.CACHE_ENV] == str(harness.root)
    assert "targets          : data_pilot" in result.summary
    # Issue #21: no `cargo update -p <bare crate name>` pre-step — the path
    # `[patch]` tables re-lock the ladder on their own, and bare specs were
    # ambiguous whenever one crate name resolved to two sources.
    assert not [c for c in harness.cargo.calls if c[0][:2] == ["cargo", "update"]]


def test_cargo_failure_is_exit_1(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        harness.run(
            "--cache-dir",
            str(harness.root),
            "--add-contigs",
            "chr21",
            "--flavours",
            "ensembl",
        ).code
        == int(Exit.OK)
    )
    (harness.repo / "tests" / "data_pilot.rs").write_text("// stub\n")
    harness.cargo.test_exit_code = 1

    src = harness.repo / ".run_tests" / "src"
    ensembl_cache = "datafusion-bio-format-ensembl-cache"
    for root, members in (
        (
            src / "datafusion-bio-functions",
            [("datafusion/bio-function-vep", "datafusion-bio-function-vep")],
        ),
        (
            src / "datafusion-bio-formats",
            [
                ("datafusion/bio-format-ensembl-cache", ensembl_cache),
                ("datafusion/bio-format-vcf", "datafusion-bio-format-vcf"),
            ],
        ),
    ):
        member_paths = []
        for rel, name in members:
            path = root / rel
            _write_crate(path, name)
            member_paths.append(rel)
        (root / "Cargo.toml").write_text(
            "[workspace]\nmembers = ["
            + ", ".join(f'"{m}"' for m in member_paths)
            + "]\n"
        )

    def fake_checkout(**kwargs: object) -> engine.Checkout:
        target = Path(str(kwargs["target"]))
        return engine.Checkout(
            name=str(kwargs["name"]),
            path=target,
            git_url=str(kwargs["git_url"]),
            rev=str(kwargs["rev"]),
            head="e" * 40,
        )

    monkeypatch.setattr(engine, "default_src_root", lambda environ=None: src)
    monkeypatch.setattr(engine, "_checkout_repo", fake_checkout)
    result = harness.run(
        "--cache-dir",
        str(harness.root),
        "--flavours",
        "ensembl",
        "--vepyr",
        "0.7.0",
        gh_api=_FakeGh(_tiny_ladder_toml()),
    )
    assert result.code == int(Exit.TESTS_FAILED)
    assert "outcome          : tests_failed (exit 1)" in result.summary


def test_env_cache_root_runs_without_fetch(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        harness.run(
            "--cache-dir",
            str(harness.root),
            "--add-contigs",
            "chr21",
            "--flavours",
            "ensembl",
        ).code
        == int(Exit.OK)
    )
    monkeypatch.setenv(tests.CACHE_ENV, str(harness.root))
    result = harness.run("--flavours", "ensembl")
    assert result.code == int(Exit.OK), result.stderr
    assert "0 data-problem target(s)" in result.summary


def _stub_engine(src: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lay out the three patched workspaces under ``src`` and stub the checkouts."""
    ensembl_cache = "datafusion-bio-format-ensembl-cache"
    for root, members in (
        (
            src / "datafusion-bio-functions",
            [("datafusion/bio-function-vep", "datafusion-bio-function-vep")],
        ),
        (
            src / "datafusion-bio-formats",
            [
                ("datafusion/bio-format-ensembl-cache", ensembl_cache),
                ("datafusion/bio-format-vcf", "datafusion-bio-format-vcf"),
            ],
        ),
    ):
        for rel, name in members:
            _write_crate(root / rel, name)
        (root / "Cargo.toml").write_text(
            "[workspace]\nmembers = ["
            + ", ".join(f'"{rel}"' for rel, _ in members)
            + "]\n"
        )

    def fake_checkout(**kwargs: object) -> engine.Checkout:
        return engine.Checkout(
            name=str(kwargs["name"]),
            path=Path(str(kwargs["target"])),
            git_url=str(kwargs["git_url"]),
            rev=str(kwargs["rev"]),
            head="f" * 40,
        )

    monkeypatch.setattr(engine, "default_src_root", lambda environ=None: src)
    monkeypatch.setattr(engine, "_checkout_repo", fake_checkout)


def test_relative_cache_dir_prechecks_the_directory_cargo_is_given(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative ``--cache-dir`` used from outside the repo root must not fork.

    Regression for #26: the precheck ran in the caller's cwd while cargo is spawned
    with ``cwd=_repo_root()``, so both sides must see the *same absolute* root.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert elsewhere.resolve() != harness.repo.resolve()

    relative = "./relative-cache"
    assert (
        harness.run(
            "--cache-dir", relative, "--add-contigs", "chr21", "--flavours", "ensembl"
        ).code
        == int(Exit.OK)
    )
    assert (elsewhere / "relative-cache").is_dir()

    (harness.repo / "tests" / "data_pilot.rs").write_text("// stub\n")
    _stub_engine(tmp_path / "src", monkeypatch)

    prechecked: list[Path] = []
    real_precheck = tests.precheck_cache

    def spy(root: Path, **kwargs: object) -> None:
        prechecked.append(root)
        real_precheck(root, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli.tests, "precheck_cache", spy)

    result = harness.run(
        "--cache-dir",
        relative,
        "--flavours",
        "ensembl",
        "--vepyr",
        "0.7.0",
        gh_api=_FakeGh(_tiny_ladder_toml()),
    )
    assert result.code == int(Exit.OK), result.stderr

    cargo_test = [c for c in harness.cargo.calls if c[0][:2] == ["cargo", "test"]]
    assert cargo_test, "cargo test should have been invoked"
    env_root = cargo_test[0][1][tests.CACHE_ENV]
    assert prechecked, "the precheck should have run"
    assert Path(env_root).is_absolute()
    assert str(prechecked[-1]) == env_root
    assert Path(env_root) == (elsewhere / "relative-cache").resolve()


def test_absolute_cache_dir_and_env_root_are_unchanged(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2: absolute ``--cache-dir`` / ``$VEPYR_CACHE_ROOT`` behave as before."""
    absolute = harness.root.resolve()
    inv = cli.parse_args(["--cache-dir", str(absolute), "--flavours", "ensembl"])
    assert cli._resolve_cache_root(inv) == absolute

    env_only = cli.parse_args(["--flavours", "ensembl"])
    monkeypatch.setenv(tests.CACHE_ENV, str(absolute))
    assert cli._resolve_cache_root(env_only) == absolute
    monkeypatch.delenv(tests.CACHE_ENV)
    assert cli._resolve_cache_root(env_only) is None


def test_precheck_follows_a_bumped_fasta_pin_name(harness: Harness) -> None:
    """Bumping ``[grch38_fasta].ref`` moves precheck's FASTA name with the pin.

    The regression this pins down: a hardcoded basename made a complete cache laid
    out under the newly pinned name report as missing, unfixable by re-fetching.
    """
    assert (
        harness.run(
            "--cache-dir",
            str(harness.root),
            "--add-contigs",
            "chr21",
            "--flavours",
            "ensembl",
        ).code
        == int(Exit.OK)
    )
    fasta_dir = harness.root / "fasta"
    for suffix in ("", ".fai"):
        (fasta_dir / f"tiny.fa{suffix}").rename(fasta_dir / f"bumped.fa{suffix}")

    pins_toml = harness.repo / "PINS.toml"
    pins_toml.write_text(
        pins_toml.read_text().replace('ref = "tiny.fa.gz"', 'ref = "bumped.fa.gz"')
    )
    assert 'ref = "bumped.fa.gz"' in pins_toml.read_text()

    tests.precheck_cache(harness.root, pins_toml=pins_toml, flavours=["ensembl"])

    (fasta_dir / "bumped.fa").rename(fasta_dir / "tiny.fa")
    with pytest.raises(RunTestsError) as excinfo:
        tests.precheck_cache(harness.root, pins_toml=pins_toml, flavours=["ensembl"])
    assert excinfo.value.code is Exit.INCOMPLETE
    assert "bumped.fa" in str(excinfo.value)
