"""Tests for ``tools/check_test_dir.py`` (#164).

Every fixture is built in ``tmp_path`` from small literal files; one test also
runs the tool on the committed ``tests/data`` tree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

import pytest

from check_test_dir import CheckId, check_dir, discover, main

REPO: Final[Path] = Path(__file__).resolve().parent.parent
DATA: Final[Path] = REPO / "tests" / "data"

HEADER: Final[str] = (
    "##fileformat=VCFv4.2\n##contig=<ID=21>\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
)
DIGEST: Final[str] = "a" * 64
IMAGE: Final[str] = f"ensemblorg/ensembl-vep@sha256:{DIGEST}"


def rec(chrom: str, pos: int) -> str:
    """One minimal input record line."""
    return f"{chrom}\t{pos}\t.\tG\tT\t.\t.\t.\n"


def oracle_vcf(n: int, *, vep_lines: int = 1) -> str:
    """An oracle with ``vep_lines`` ``##VEP=`` header lines and ``n`` body lines."""
    vep = '##VEP="v116" API="v116"\n' * vep_lines
    return (
        "##fileformat=VCFv4.2\n"
        + vep
        + HEADER.split("\n", 1)[1]
        + "".join(f"21\t{100 + i}\t.\tG\tT\t.\t.\tCSQ=x\n" for i in range(n))
    )


def make_test(
    root: Path,
    name: str = "t",
    *,
    records: tuple[tuple[str, int], ...] = (("21", 100),),
    oracle: str | None = None,
    image: str = IMAGE,
) -> Path:
    """Write a passing data-test directory (unless overridden) and return it."""
    d = root / name
    d.mkdir(parents=True)
    (d / "input.vcf").write_text(HEADER + "".join(rec(c, p) for c, p in records))
    (d / "expected_output.vcf").write_text(
        oracle if oracle is not None else oracle_vcf(len(records))
    )
    (d / "test.toml").write_text(f'name = "{name}"\n\n[vep]\nimage = "{image}"\n')
    return d


def failed(d: Path) -> dict[CheckId, str]:
    """``check -> detail`` of every failing check of ``d``."""
    return {f.check: f.detail for f in check_dir(d)}


def run_main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, list[str]]:
    """Run :func:`main` and return its exit code and stdout lines."""
    code = main(list(argv))
    return code, capsys.readouterr().out.splitlines()


# ---------------------------------------------------------------- modes


def test_data_dir_mode_checks_every_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A data root: every subdirectory with a test.toml is checked and reported OK."""
    make_test(tmp_path, "b")
    make_test(tmp_path, "a")
    (tmp_path / "not_a_test").mkdir()
    code, out = run_main(capsys, str(tmp_path))
    assert (code, out) == (0, [f"OK {tmp_path / 'a'}", f"OK {tmp_path / 'b'}"])


def test_data_dir_mode_committed_tree_passes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The committed tests/data tree satisfies every invariant."""
    code, out = run_main(capsys, str(DATA))
    assert code == 0
    assert len(out) == len(discover(DATA)) >= 1
    assert all(line.startswith("OK ") for line in out)


def test_single_dir_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A directory holding test.toml is checked by itself."""
    d = make_test(tmp_path, "one", records=(("21", 100), ("21", 100), ("22", 5)))
    code, out = run_main(capsys, str(d))
    assert (code, out) == (0, [f"OK {d}"])


# ---------------------------------------------------------------- files


def test_stray_file(tmp_path: Path) -> None:
    """Any fourth entry fails ``files``."""
    d = make_test(tmp_path)
    (d / "stray.txt").write_text("x\n")
    assert failed(d) == {CheckId.FILES: "extra ['stray.txt']"}


def test_missing_file(tmp_path: Path) -> None:
    """A missing oracle fails ``files`` and every check that needs the oracle."""
    d = make_test(tmp_path)
    (d / "expected_output.vcf").unlink()
    got = failed(d)
    assert got[CheckId.FILES] == "missing ['expected_output.vcf']"
    assert set(got) == {CheckId.FILES, CheckId.ORACLE_META, CheckId.ONE_TO_ONE}


def test_empty_file(tmp_path: Path) -> None:
    """A zero-byte file fails ``files``."""
    d = make_test(tmp_path)
    (d / "input.vcf").write_bytes(b"")
    got = failed(d)
    assert got[CheckId.FILES] == "empty ['input.vcf']"
    assert CheckId.INPUT_RECORDS in got


# ---------------------------------------------------------------- records and order


def test_zero_records(tmp_path: Path) -> None:
    """Header-only input and oracle: ``input-records`` fails; ``one-to-one`` passes."""
    d = make_test(tmp_path, records=())
    assert failed(d) == {CheckId.INPUT_RECORDS: "input.vcf has no records"}


def test_descending_pos(tmp_path: Path) -> None:
    """POS going down within a contig fails ``order``."""
    d = make_test(tmp_path, records=(("21", 200), ("21", 100)))
    assert set(failed(d)) == {CheckId.ORDER}
    assert "21:100 after 21:200" in failed(d)[CheckId.ORDER]


def test_interleaved_contigs(tmp_path: Path) -> None:
    """A contig that comes back after another one fails ``order``."""
    d = make_test(tmp_path, records=(("21", 100), ("22", 100), ("21", 200)))
    assert set(failed(d)) == {CheckId.ORDER}
    assert "contig 21 interleaved" in failed(d)[CheckId.ORDER]


def test_malformed_record_fails_record_checks(tmp_path: Path) -> None:
    """An unparsable input body line fails the checks that read records."""
    d = make_test(tmp_path)
    with (d / "input.vcf").open("a") as fh:
        fh.write("21\tnot-a-pos\t.\tG\tT\n")
    assert set(failed(d)) == {CheckId.INPUT_RECORDS, CheckId.ORDER, CheckId.ONE_TO_ONE}


# ---------------------------------------------------------------- oracle meta


def test_two_vep_lines(tmp_path: Path) -> None:
    """Two ``##VEP=`` lines fail ``oracle-meta``."""
    d = make_test(tmp_path, oracle=oracle_vcf(1, vep_lines=2))
    assert set(failed(d)) == {CheckId.ORACLE_META}
    assert "2 ##VEP= lines" in failed(d)[CheckId.ORACLE_META]


def test_no_vep_line(tmp_path: Path) -> None:
    """No ``##VEP=`` line fails ``oracle-meta``."""
    d = make_test(tmp_path, oracle=oracle_vcf(1, vep_lines=0))
    assert set(failed(d)) == {CheckId.ORACLE_META}


@pytest.mark.parametrize(
    "image",
    [
        pytest.param(
            f"ensemblorg/ensembl-vep@sha256:{DIGEST}0", id="image_long_digest"
        ),
        pytest.param(f"other/ensembl-vep@sha256:{DIGEST}", id="image_other_repo"),
    ],
)
def test_image_not_pinned(tmp_path: Path, image: str) -> None:
    """``[vep] image`` must be the VEP repository pinned by a full sha256 digest."""
    d = make_test(tmp_path, image=image)
    assert set(failed(d)) == {CheckId.ORACLE_META}
    assert repr(image) in failed(d)[CheckId.ORACLE_META]


def test_image_tag_not_digest(tmp_path: Path) -> None:
    """A tag instead of a digest fails ``oracle-meta``."""
    d = make_test(tmp_path, image="ensemblorg/ensembl-vep:release_116.0")
    assert set(failed(d)) == {CheckId.ORACLE_META}


def test_image_short_digest(tmp_path: Path) -> None:
    """A 63-hex digest fails ``oracle-meta``."""
    d = make_test(tmp_path, image=f"ensemblorg/ensembl-vep@sha256:{DIGEST[:63]}")
    assert set(failed(d)) == {CheckId.ORACLE_META}


def test_image_missing(tmp_path: Path) -> None:
    """No ``[vep]`` table at all fails ``oracle-meta``."""
    d = make_test(tmp_path)
    (d / "test.toml").write_text('name = "t"\n')
    assert failed(d) == {
        CheckId.ORACLE_META: "[vep] image = None, "
        "want ensemblorg/ensembl-vep@sha256:<64 hex>"
    }


# ---------------------------------------------------------------- one-to-one


def test_line_count_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two input records, one oracle line: ``one-to-one`` fails with both counts."""
    d = make_test(tmp_path, records=(("21", 100), ("21", 200)), oracle=oracle_vcf(1))
    code, out = run_main(capsys, str(d))
    assert (code, out) == (
        1,
        [f"FAIL {d} one-to-one: input 2 records, oracle 1 body lines"],
    )


def test_reports_all_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every failing check is reported, not only the first; other dirs still run."""
    make_test(tmp_path, "good")
    d = make_test(
        tmp_path,
        "bad",
        records=(("21", 200), ("21", 100)),
        oracle=oracle_vcf(1, vep_lines=0),
        image="ensemblorg/ensembl-vep:116",
    )
    (d / "stray.txt").write_text("x\n")
    code, out = run_main(capsys, str(tmp_path))
    assert code == 1
    assert [line.split(" ", 2)[:2] for line in out] == [["FAIL", str(d)]] * 4 + [
        ["OK", str(tmp_path / "good")]
    ]
    assert [line.split(" ")[2].rstrip(":") for line in out[:4]] == [
        "files",
        "order",
        "oracle-meta",
        "one-to-one",
    ]


# ---------------------------------------------------------------- guards and usage


def test_no_tests_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No test.toml anywhere exits 1: checking nothing is not a pass."""
    (tmp_path / "sub").mkdir()
    assert main([str(tmp_path)]) == 1
    assert "no data-test directory" in capsys.readouterr().err


def test_not_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing path or a file exits 1."""
    (tmp_path / "f").write_text("x")
    assert main([str(tmp_path / "does-not-exist")]) == 1
    assert main([str(tmp_path / "f")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_never_writes(tmp_path: Path) -> None:
    """Checking good and bad directories leaves every byte and mtime unchanged."""
    make_test(tmp_path, "good")
    bad = make_test(tmp_path, "bad", records=())
    (bad / "stray.txt").write_text("x\n")

    def snapshot() -> dict[Path, tuple[int, str]]:
        return {
            p: (
                p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir",
            )
            for p in sorted(tmp_path.rglob("*"))
        }

    before = snapshot()
    assert main([str(tmp_path)]) == 1
    assert snapshot() == before


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--skip", "one-to-one"], id="skip"),
        pytest.param(["--not-one-to-one"], id="not_one_to_one"),
        pytest.param(["a", "b"], id="two_dirs"),
    ],
)
def test_skip_flag_is_usage_error(tmp_path: Path, argv: list[str]) -> None:
    """No way to skip a check: such flags are argparse usage errors (exit 2)."""
    d = make_test(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main([*argv, str(d)])
    assert exc.value.code == 2


def test_default_dir_is_tests_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without DIR the tool checks ``tests/data`` relative to the cwd."""
    monkeypatch.chdir(REPO)
    code, out = run_main(capsys)
    assert code == 0
    assert out[0].startswith("OK tests/data/")
