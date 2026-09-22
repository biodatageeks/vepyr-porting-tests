"""Tests for the ``tools/normalize_input`` script.

The script has no ``.py`` suffix -- it is meant to be run, and its whole source
is what issue #85's acceptance criterion 9 greps for a forbidden ``--fasta-ref``
switch. It is therefore loaded here by path rather than imported by name.

Tests that shell out to the real bcftools are skipped when it is absent; the
pure-Python ones (usage errors, the missing-tool message) always run.
"""

from __future__ import annotations

import gzip
import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

SCRIPT: Final[Path] = Path(__file__).resolve().parent / "normalize_input"

MULTIALLELIC_VCF: Final[str] = (
    "##fileformat=VCFv4.2\n"
    "##contig=<ID=chr21,length=46709983>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr21\t100\t.\tC\tT,G\t.\t.\t.\n"
)
"""One record with two ALT alleles -- ``-m -both`` must split it into two."""

requires_bcftools = pytest.mark.skipif(
    shutil.which("bcftools") is None,
    reason="needs bcftools on PATH",
)


def _load() -> ModuleType:
    """Import the extension-less script as a module.

    Returns:
        The executed module object.
    """
    loader = SourceFileLoader("normalize_input", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses resolves annotations via this
    loader.exec_module(module)
    return module


normalize_input = _load()


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run the script in a subprocess, exactly as a shell would.

    Args:
        *argv: Arguments after the program name.

    Returns:
        The finished process, with text streams captured.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def raw_vcf(tmp_path: Path) -> Path:
    """Write the multiallelic scratch VCF and return its path."""
    path = tmp_path / "multi.vcf"
    path.write_text(MULTIALLELIC_VCF, encoding="utf-8")
    return path


def test_the_script_is_executable() -> None:
    """It is invoked as ``tools/normalize_input``, so it must carry +x (AC 2)."""
    assert SCRIPT.stat().st_mode & 0o111, "normalize_input must be executable"


def test_the_source_never_mentions_a_reference_fasta() -> None:
    """``-f`` would left-align indels and move VEP's answers (AC 9).

    This is the literal grep issue #85's AC 9 runs, kept so the criterion has a
    home in the suite. On its own it is *not* a guarantee: the argv is built
    from a tuple, so the source never contains ``norm `` followed by a flag and
    the pattern cannot match even if ``-f`` were added. The falsifiable check is
    :func:`test_the_bcftools_argv_never_carries_a_reference_fasta`.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"norm .*(-f|--fasta-ref)", source) is None


def _capture_norm_argv(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return the argv ``_run_norm`` hands to :func:`subprocess.run`.

    Args:
        monkeypatch: Fixture used to intercept the subprocess call.

    Returns:
        The argument vector, with bcftools never actually started.
    """
    seen: list[str] = []

    def _fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen[:] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(normalize_input.subprocess, "run", _fake_run)
    normalize_input._run_norm(Path("raw.vcf"), Path("out.vcf"))
    return seen


def test_the_bcftools_argv_never_carries_a_reference_fasta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real guarantee: no ``-f``/``--fasta-ref`` reaches bcftools.

    Unlike the source grep of AC 9, this fails if a reference FASTA is ever
    added to ``_NORM_FLAGS`` -- the sabotage that motivated it.
    """
    argv = _capture_norm_argv(monkeypatch)
    assert "-f" not in argv
    assert "--fasta-ref" not in argv


def test_the_bcftools_argv_is_exactly_the_fixed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-m -both` and nothing else, in that order (CLAUDE.md's binding rule)."""
    argv = _capture_norm_argv(monkeypatch)
    assert argv == ["bcftools", "norm", "-m", "-both", "-o", "out.vcf", "raw.vcf"]


@requires_bcftools
def test_two_runs_of_the_same_raw_file_are_byte_identical(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """The stamped version/command headers are the only nondeterminism (AC 2)."""
    assert _run(str(raw_vcf), str(tmp_path / "t1")).returncode == 0
    assert _run(str(raw_vcf), str(tmp_path / "t2")).returncode == 0
    first = (tmp_path / "t1" / "input.vcf").read_bytes()
    assert first == (tmp_path / "t2" / "input.vcf").read_bytes()
    assert b"bcftools_norm" not in first


@requires_bcftools
def test_normalising_its_own_output_changes_nothing(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """Re-running on ``input.vcf`` must be a fixed point (AC 3)."""
    assert _run(str(raw_vcf), str(tmp_path / "t1")).returncode == 0
    once = tmp_path / "t1" / "input.vcf"
    assert _run(str(once), str(tmp_path / "t3")).returncode == 0
    assert once.read_bytes() == (tmp_path / "t3" / "input.vcf").read_bytes()


@requires_bcftools
def test_a_multiallelic_record_is_split_into_one_line_per_allele(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """``-m -both`` is the whole point of the fixed command (AC 4)."""
    assert _run(str(raw_vcf), str(tmp_path / "t1")).returncode == 0
    body = [
        line
        for line in (tmp_path / "t1" / "input.vcf").read_text().splitlines()
        if not line.startswith("#")
    ]
    assert len(body) == 2
    raw_body = [ln for ln in MULTIALLELIC_VCF.splitlines() if not ln.startswith("#")]
    assert len(raw_body) == 1, "negative control: the raw file has one record"


@requires_bcftools
def test_a_gzipped_raw_file_gives_the_same_bytes(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """bcftools detects the container, so the script passes it through (AC 6)."""
    gz = tmp_path / "multi.vcf.gz"
    gz.write_bytes(gzip.compress(raw_vcf.read_bytes()))
    assert _run(str(raw_vcf), str(tmp_path / "t1")).returncode == 0
    assert _run(str(gz), str(tmp_path / "t5")).returncode == 0
    assert (tmp_path / "t1" / "input.vcf").read_bytes() == (
        tmp_path / "t5" / "input.vcf"
    ).read_bytes()


@requires_bcftools
def test_test_toml_records_the_fixed_command_and_the_tool_version(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """The recorded command is the template, not this run's paths (AC 7, 8)."""
    assert _run(str(raw_vcf), str(tmp_path / "t1")).returncode == 0
    lines = (tmp_path / "t1" / "test.toml").read_text(encoding="utf-8").splitlines()
    banner = subprocess.run(
        ["bcftools", "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    assert "[input]" in lines
    assert 'command = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"' in lines
    assert f'bcftools_version = "{banner}"' in lines


@requires_bcftools
def test_an_existing_test_toml_keeps_its_other_tables(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """The script owns ``[input]`` only; #32 and #80 own the rest.

    The comment above ``[vep]`` is the case that matters: ``test.toml`` is
    where the links to the upstream VEP test are recorded, and those links live
    in comments. An end-of-table scan that stops at the next ``[`` swallows
    them, so they are asserted explicitly here rather than implied.
    """
    test_dir = tmp_path / "t7"
    test_dir.mkdir()
    (test_dir / "test.toml").write_text(
        'name = "demo"\n'
        "\n"
        "[input]\n"
        'command = "stale"\n'
        "\n"
        "# The VEP invocation used to build expected.vcf\n"
        "# https://github.com/Ensembl/ensembl-vep/blob/release/116.0/t/AnnotationSource.t\n"
        "[vep]\n"
        'args = "--cache"\n',
        encoding="utf-8",
    )
    assert _run(str(raw_vcf), str(test_dir)).returncode == 0
    text = (test_dir / "test.toml").read_text(encoding="utf-8")
    assert 'name = "demo"' in text
    assert "stale" not in text
    assert 'command = "bcftools norm -m -both -o <out.vcf> <in.vcf.gz>"' in text
    # Both comment lines survive, still attached to [vep], still after a blank
    # separator. A scan that ate the trivia would glue [vep] to the version key.
    assert (
        "\n"
        "# The VEP invocation used to build expected.vcf\n"
        "# https://github.com/Ensembl/ensembl-vep/blob/release/116.0/t/AnnotationSource.t\n"
        "[vep]\n"
        'args = "--cache"\n'
    ) in text
    assert tomllib.loads(text)["vep"]["args"] == "--cache"


def test_a_table_with_no_trivia_before_the_next_one_still_round_trips(
    tmp_path: Path,
) -> None:
    """The backward scan must not run past the table it is replacing."""
    toml_path = tmp_path / "test.toml"
    toml_path.write_text("[input]\ncommand = \"stale\"\n[vep]\n", encoding="utf-8")
    normalize_input.upsert_input_table(toml_path, version="bcftools 1.23")
    text = toml_path.read_text(encoding="utf-8")
    assert "stale" not in text
    assert "[vep]" in text
    assert tomllib.loads(text)["input"]["bcftools_version"] == "bcftools 1.23"


def test_an_empty_input_table_is_replaced_without_eating_the_next_header(
    tmp_path: Path,
) -> None:
    """``end`` may never reach ``start``, even when the table body is blank."""
    toml_path = tmp_path / "test.toml"
    toml_path.write_text("[input]\n\n[vep]\nargs = \"--cache\"\n", encoding="utf-8")
    normalize_input.upsert_input_table(toml_path, version="bcftools 1.23")
    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["input"]["command"] == normalize_input.COMMAND_TEMPLATE
    assert parsed["vep"]["args"] == "--cache"


def test_a_bracket_inside_a_multiline_string_is_refused_not_corrupted(
    tmp_path: Path,
) -> None:
    """The line scan cannot see multi-line strings, so it must refuse to guess.

    A ``[``-starting line inside a multi-line string looks like a table header.
    Before the tomllib guard this truncated the table and wrote a file that no
    longer parsed -- silent corruption of the file #80 reads.
    """
    toml_path = tmp_path / "test.toml"
    original = '[input]\nnotes = """\n[vep] section below is authoritative\n"""\n'
    toml_path.write_text(original, encoding="utf-8")
    assert tomllib.loads(original), "negative control: the input is valid TOML"

    with pytest.raises(normalize_input.NormalizeError, match="not valid TOML"):
        normalize_input.upsert_input_table(toml_path, version="bcftools 1.23")
    assert toml_path.read_text(encoding="utf-8") == original, "left untouched"


def test_an_existing_file_that_is_not_toml_is_refused(tmp_path: Path) -> None:
    """Garbage in must not become differently-shaped garbage out."""
    toml_path = tmp_path / "test.toml"
    toml_path.write_text("this is not = = toml\n", encoding="utf-8")
    with pytest.raises(normalize_input.NormalizeError, match="the existing file"):
        normalize_input.upsert_input_table(toml_path, version="bcftools 1.23")


@requires_bcftools
def test_a_broken_test_toml_makes_the_script_exit_nonzero(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """The refusal reaches the shell as exit 1 with a message naming the file."""
    test_dir = tmp_path / "t9"
    test_dir.mkdir()
    (test_dir / "test.toml").write_text("not = = toml\n", encoding="utf-8")
    done = _run(str(raw_vcf), str(test_dir))
    assert done.returncode == 1
    assert "not valid TOML" in done.stderr


def test_an_extra_flag_is_refused_before_anything_is_written(
    raw_vcf: Path, tmp_path: Path
) -> None:
    """The script defines no options, so argparse rejects ``-f`` (AC 5)."""
    target = tmp_path / "t4"
    done = _run("-f", "ref.fa", str(raw_vcf), str(target))
    assert done.returncode == 2
    assert not (target / "input.vcf").exists()


def test_a_missing_bcftools_is_reported_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clear message and a non-zero code beat a confusing traceback (AC 10)."""
    raw = tmp_path / "multi.vcf"
    raw.write_text(MULTIALLELIC_VCF, encoding="utf-8")
    monkeypatch.setattr(normalize_input.shutil, "which", lambda _name: None)
    target = tmp_path / "t6"
    assert normalize_input.main([str(raw), str(target)]) == 1
    assert "bcftools" in capsys.readouterr().err
    assert not (target / "input.vcf").exists()


def test_a_missing_raw_file_is_reported(tmp_path: Path) -> None:
    """Nothing downstream can recover from a typo'd source path."""
    done = _run(str(tmp_path / "nope.vcf"), str(tmp_path / "t8"))
    assert done.returncode == 1
    assert "nope.vcf" in done.stderr
